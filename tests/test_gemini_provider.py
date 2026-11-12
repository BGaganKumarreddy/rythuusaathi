from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
import unittest
from unittest.mock import patch

import httpx2
from agent_framework import Content, Message
from openai import AsyncOpenAI
from pydantic import BaseModel

from rythusaathi.inference import (
    InferenceDiagnosticCode,
    InferenceRequest,
    InferenceStage,
    ProviderCredential,
    ProviderFailure,
    ProviderFailureKind,
    RateLimitScope,
)
from rythusaathi.inference.providers.gemini import (
    DEFAULT_MODEL_ID,
    GEMINI_BASE_URL,
    GeminiProvider,
    GeminiProviderConfig,
    _parse_duration_string,
    _parse_retry_after,
    estimate_input_tokens,
    extract_usage_counts,
)


CANARY = "synthetic-" + "gemini-" + "credential-canary"


def completion(content: str | None, *, model: str = DEFAULT_MODEL_ID, tool_calls=None):
    message = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-gemini-fixture",
        "object": "chat.completion",
        "created": 1,
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 15, "completion_tokens": 25, "total_tokens": 40},
    }


def mock_sdk_factory(handler, captures, clients=None):
    def create(api_key: str, base_url: str, timeout: float) -> AsyncOpenAI:
        captures.append({"auth_seen": bool(api_key), "base_url": base_url, "timeout": timeout})
        transport = httpx2.MockTransport(handler)
        http_client = httpx2.AsyncClient(transport=transport, timeout=timeout)
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=0,
            http_client=http_client,
        )
        if clients is not None:
            clients.append(client)
        return client
    return create


class GeminiProviderTests(unittest.IsolatedAsyncioTestCase):
    def test_config_defaults_and_validation(self):
        config = GeminiProviderConfig()
        self.assertEqual(config.model_id, "gemini-3.8-flash")
        self.assertEqual(config.base_url, GEMINI_BASE_URL)
        self.assertEqual(config.timeout_seconds, 60.0)
        self.assertEqual(config.connect_timeout_seconds, 10.0)
        self.assertEqual(config.read_timeout_seconds, 60.0)
        self.assertEqual(config.write_timeout_seconds, 10.0)
        self.assertEqual(config.pool_timeout_seconds, 10.0)
        self.assertEqual(config.model_context_window, 1_048_576)
        self.assertEqual(config.max_supported_output_tokens, 65_536)
        self.assertEqual(config.default_max_output_tokens, 1024)
        self.assertIsNone(config.thinking_level)
        self.assertIsNone(config.rpm_limit)
        self.assertIsNone(config.tpm_limit_tokens)
        self.assertEqual(config.tpm_headroom_tokens, 0)
        self.assertIsNone(config.rpd_limit)
        self.assertEqual(config.max_retries, 1)
        self.assertFalse(config.has_quota_limits)

        with self.assertRaises(ValueError):
            GeminiProviderConfig(model_id="")
        with self.assertRaises(ValueError):
            GeminiProviderConfig(base_url="")
        with self.assertRaises(ValueError):
            GeminiProviderConfig(timeout_seconds=0)
        with self.assertRaises(ValueError):
            GeminiProviderConfig(connect_timeout_seconds=0)
        with self.assertRaises(ValueError):
            GeminiProviderConfig(read_timeout_seconds=-1)
        with self.assertRaises(ValueError):
            GeminiProviderConfig(write_timeout_seconds=0)
        with self.assertRaises(ValueError):
            GeminiProviderConfig(pool_timeout_seconds=0)
        with self.assertRaises(ValueError):
            GeminiProviderConfig(model_context_window=500)
        with self.assertRaises(ValueError):
            GeminiProviderConfig(max_supported_output_tokens=0)
        with self.assertRaises(ValueError):
            GeminiProviderConfig(default_max_output_tokens=0)
        with self.assertRaises(ValueError):
            GeminiProviderConfig(default_max_output_tokens=100_000)
        with self.assertRaises(ValueError):
            GeminiProviderConfig(rpm_limit=0)
        with self.assertRaises(ValueError):
            GeminiProviderConfig(tpm_limit_tokens=0)
        with self.assertRaises(ValueError):
            GeminiProviderConfig(tpm_headroom_tokens=-1)
        with self.assertRaises(ValueError):
            GeminiProviderConfig(tpm_limit_tokens=1000, tpm_headroom_tokens=1000)
        with self.assertRaises(ValueError):
            GeminiProviderConfig(rpd_limit=0)
        with self.assertRaises(ValueError):
            GeminiProviderConfig(thinking_level="")
        with self.assertRaises(ValueError):
            GeminiProviderConfig(max_retries=-1)
        with self.assertRaises(ValueError):
            GeminiProviderConfig(max_retries=True)

    def test_config_free_tier(self):
        # Baseline overridable AI Studio free tier guidelines
        config = GeminiProviderConfig.free_tier()
        self.assertEqual(config.rpm_limit, 15)
        self.assertEqual(config.tpm_limit_tokens, 1_000_000)
        self.assertEqual(config.tpm_headroom_tokens, 10_000)
        self.assertEqual(config.rpd_limit, 1500)
        self.assertEqual(config.default_max_output_tokens, 1024)
        self.assertIsNone(config.thinking_level)
        self.assertEqual(config.max_retries, 1)
        self.assertTrue(config.has_quota_limits)

        # Overridden with specific AI Studio project plan limits
        custom_free = GeminiProviderConfig.free_tier(
            rpm_limit=30,
            tpm_limit_tokens=2_000_000,
            tpm_headroom_tokens=20_000,
            rpd_limit=2500,
            thinking_level="minimal",
        )
        self.assertEqual(custom_free.rpm_limit, 30)
        self.assertEqual(custom_free.tpm_limit_tokens, 2_000_000)
        self.assertEqual(custom_free.tpm_headroom_tokens, 20_000)
        self.assertEqual(custom_free.rpd_limit, 2500)
        self.assertEqual(custom_free.thinking_level, "minimal")

        # Configured with unknown limits preserved (None)
        unknown_free = GeminiProviderConfig.free_tier(
            rpm_limit=None,
            tpm_limit_tokens=None,
            rpd_limit=None,
        )
        self.assertIsNone(unknown_free.rpm_limit)
        self.assertIsNone(unknown_free.tpm_limit_tokens)
        self.assertEqual(unknown_free.tpm_headroom_tokens, 0)
        self.assertIsNone(unknown_free.rpd_limit)
        self.assertFalse(unknown_free.has_quota_limits)

    def test_config_from_environment(self):
        # 1. Clean environment: quotas are None (unknown)
        with patch.dict(os.environ, {}, clear=True):
            config = GeminiProviderConfig.from_environment()
            self.assertIsNone(config.rpm_limit)
            self.assertIsNone(config.tpm_limit_tokens)
            self.assertEqual(config.tpm_headroom_tokens, 0)
            self.assertIsNone(config.rpd_limit)
            self.assertEqual(config.default_max_output_tokens, 1024)

        # 2. GEMINI_TIER="free"
        with patch.dict(os.environ, {"GEMINI_TIER": "free"}, clear=True):
            free_config = GeminiProviderConfig.from_environment()
            self.assertEqual(free_config.rpm_limit, 15)
            self.assertEqual(free_config.tpm_limit_tokens, 1_000_000)
            self.assertEqual(free_config.tpm_headroom_tokens, 10_000)
            self.assertEqual(free_config.rpd_limit, 1500)

        # 3. Explicit project quota overrides
        env = {
            "GEMINI_MODEL_ID": "custom-gemini-model",
            "GEMINI_TIMEOUT_SECONDS": "45.5",
            "GEMINI_RPM_LIMIT": "20",
            "GEMINI_TPM_LIMIT_TOKENS": "500000",
            "GEMINI_TPM_HEADROOM_TOKENS": "5000",
            "GEMINI_RPD_LIMIT": "2000",
            "GEMINI_MAX_OUTPUT_TOKENS": "2048",
            "GEMINI_BASE_URL": "https://custom.endpoint/v1/",
        }
        with patch.dict(os.environ, env, clear=True):
            custom_config = GeminiProviderConfig.from_environment()
            self.assertEqual(custom_config.model_id, "custom-gemini-model")
            self.assertEqual(custom_config.timeout_seconds, 45.5)
            self.assertEqual(custom_config.rpm_limit, 20)
            self.assertEqual(custom_config.tpm_limit_tokens, 500000)
            self.assertEqual(custom_config.tpm_headroom_tokens, 5000)
            self.assertEqual(custom_config.rpd_limit, 2000)
            self.assertEqual(custom_config.default_max_output_tokens, 2048)
            self.assertEqual(custom_config.base_url, "https://custom.endpoint/v1/")

    async def test_unknown_quota_handling(self):
        """When project quotas are unknown/unconfigured, requests proceed without
        local rate-limit rejections, and usage truthfully reports None for budget limits."""
        async def handler(_request):
            return httpx2.Response(200, json=completion('{"result": "ok"}'))

        provider = GeminiProvider(GeminiProviderConfig(), sdk_client_factory=mock_sdk_factory(handler, []))
        resp = await provider.complete(
            InferenceRequest([Message("user", ["safe prompt"])], agent_name="crop"),
            ProviderCredential("valid-key"),
        )
        self.assertEqual(resp.provider, "gemini")
        self.assertIsNone(resp.usage.budget_limit_tokens)
        self.assertIsNone(resp.usage.budget_remaining_tokens)
        self.assertEqual(resp.usage.budget_decision, "completed")

        # Non-rate-limit failure with unknown quota reports provider_managed without claiming budget was checked
        async def fail_handler(_request):
            raise httpx2.ConnectError("connection refused")

        fail_provider = GeminiProvider(GeminiProviderConfig(), sdk_client_factory=mock_sdk_factory(fail_handler, []))
        with self.assertRaises(ProviderFailure) as caught:
            await fail_provider.complete(
                InferenceRequest([Message("user", ["prompt"])], agent_name="scheme"),
                ProviderCredential("fail-key"),
            )
        self.assertEqual(caught.exception.diagnostic.budget_decision, "provider_managed")
        self.assertIsNone(caught.exception.diagnostic.budget_limit_tokens)
        self.assertIsNone(caught.exception.diagnostic.budget_remaining_tokens)

    async def test_thinking_level_and_request_compatibility(self):
        """Verify Gemini 3.8 request payload passes reasoning_effort and rejects unsupported parameters."""
        sent_payloads = []

        async def handler(request):
            sent_payloads.append(json.loads(request.content.decode("utf-8")))
            return httpx2.Response(200, json=completion('{"result": "ok"}'))

        provider = GeminiProvider(GeminiProviderConfig(), sdk_client_factory=mock_sdk_factory(handler, []))

        # Default thinking_level (None) omits reasoning_effort to avoid overhead
        req_default = InferenceRequest(
            [Message("user", ["hello"])],
            options={"max_tokens": 256, "temperature": 0.0},
            agent_name="intake",
        )
        await provider.complete(req_default, ProviderCredential("key-1"))
        self.assertEqual(len(sent_payloads), 1)
        self.assertNotIn("reasoning_effort", sent_payloads[0])
        self.assertEqual(sent_payloads[0].get("temperature"), 0.0)
        self.assertNotIn("top_k", sent_payloads[0])
        self.assertNotIn("candidate_count", sent_payloads[0])
        self.assertNotIn("thinking_budget", sent_payloads[0])

        # Explicit thinking_level mapped to reasoning_effort
        req_custom = InferenceRequest(
            [Message("user", ["hello again"])],
            options={"max_tokens": 400, "thinking_level": "medium"},
            agent_name="crop",
        )
        await provider.complete(req_custom, ProviderCredential("key-2"))
        self.assertEqual(len(sent_payloads), 2)
        self.assertEqual(sent_payloads[1].get("reasoning_effort"), "medium")

        # Unsupported parameters rejected with MALFORMED_RESPONSE before dispatch
        for bad_param in ("top_k", "candidate_count", "thinking_budget"):
            req_unsupported = InferenceRequest(
                [Message("user", ["bad"])],
                options={"max_tokens": 256, bad_param: 10},
                agent_name="intake",
            )
            with self.assertRaises(ProviderFailure) as caught:
                await provider.complete(req_unsupported, ProviderCredential("bad-key"))
            self.assertEqual(caught.exception.kind, ProviderFailureKind.MALFORMED_RESPONSE)

    async def test_rpm_limit_enforcement(self):
        """When rpm_limit is configured, requests exceeding the rolling 60s limit are rejected."""
        async def handler(_request):
            return httpx2.Response(200, json=completion('{"result": "ok"}'))

        config = GeminiProviderConfig(rpm_limit=2)
        provider = GeminiProvider(config, sdk_client_factory=mock_sdk_factory(handler, []))

        req1 = InferenceRequest([Message("user", ["call 1"])], agent_name="intake")
        req2 = InferenceRequest([Message("user", ["call 2"])], agent_name="crop")
        req3 = InferenceRequest([Message("user", ["call 3"])], agent_name="scheme")

        resp1 = await provider.complete(req1, ProviderCredential("rpm-key-1"))
        self.assertEqual(resp1.provider, "gemini")
        resp2 = await provider.complete(req2, ProviderCredential("rpm-key-2"))
        self.assertEqual(resp2.provider, "gemini")

        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(req3, ProviderCredential("rpm-key-3"))

        self.assertEqual(caught.exception.kind, ProviderFailureKind.RATE_LIMIT)
        self.assertEqual(caught.exception.diagnostic.code, InferenceDiagnosticCode.GEMINI_RATE_LIMITED)
        self.assertEqual(caught.exception.diagnostic.rate_limit_scope, RateLimitScope.REQUESTS_PER_MINUTE)
        self.assertEqual(caught.exception.diagnostic.budget_decision, "rejected")

    async def test_rpd_limit_enforcement(self):
        """When rpd_limit is configured, requests exceeding the rolling 24h limit are rejected."""
        async def handler(_request):
            return httpx2.Response(200, json=completion('{"result": "ok"}'))

        config = GeminiProviderConfig(rpd_limit=2)
        provider = GeminiProvider(config, sdk_client_factory=mock_sdk_factory(handler, []))

        resp1 = await provider.complete(InferenceRequest([Message("user", ["call 1"])], agent_name="intake"), ProviderCredential("rpd-key-1"))
        self.assertEqual(resp1.provider, "gemini")
        resp2 = await provider.complete(InferenceRequest([Message("user", ["call 2"])], agent_name="crop"), ProviderCredential("rpd-key-2"))
        self.assertEqual(resp2.provider, "gemini")

        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(InferenceRequest([Message("user", ["call 3"])], agent_name="scheme"), ProviderCredential("rpd-key-3"))

        self.assertEqual(caught.exception.kind, ProviderFailureKind.RATE_LIMIT)
        self.assertEqual(caught.exception.diagnostic.code, InferenceDiagnosticCode.GEMINI_QUOTA_EXHAUSTED)
        self.assertEqual(caught.exception.diagnostic.rate_limit_scope, RateLimitScope.REQUESTS_PER_DAY)
        self.assertEqual(caught.exception.diagnostic.budget_decision, "rejected")

    async def test_architectural_limit_validation(self):
        """Requests exceeding architectural max output or context window fail validation before dispatch."""
        dispatched = []

        async def handler(request):
            dispatched.append(request)
            return httpx2.Response(200, json=completion('{"result": "ok"}'))

        provider = GeminiProvider(GeminiProviderConfig(), sdk_client_factory=mock_sdk_factory(handler, []))
        cred = ProviderCredential("arch-key")

        # 1. Output limit exceeding architectural max (65,536)
        req_excessive_output = InferenceRequest(
            [Message("user", ["hi"])],
            options={"max_tokens": 70_000},
            agent_name="crop",
        )
        with self.assertRaises(ProviderFailure) as caught1:
            await provider.complete(req_excessive_output, cred)
        self.assertEqual(caught1.exception.kind, ProviderFailureKind.MALFORMED_RESPONSE)
        self.assertEqual(caught1.exception.diagnostic.code, InferenceDiagnosticCode.INVALID_REQUEST)
        self.assertEqual(len(dispatched), 0)

        # 2. Total tokens exceeding model context window (1,048,576)
        provider_small_ctx = GeminiProvider(
            GeminiProviderConfig(model_context_window=2048),
            sdk_client_factory=mock_sdk_factory(handler, []),
        )
        req_excessive_ctx = InferenceRequest(
            [Message("user", ["word " * 1500])],
            options={"max_tokens": 1024},
            agent_name="coordinator",
        )
        with self.assertRaises(ProviderFailure) as caught2:
            await provider_small_ctx.complete(req_excessive_ctx, cred)
        self.assertEqual(caught2.exception.kind, ProviderFailureKind.MALFORMED_RESPONSE)
        self.assertEqual(caught2.exception.diagnostic.code, InferenceDiagnosticCode.INVALID_REQUEST)
        self.assertEqual(len(dispatched), 0)

    async def test_concurrency_protection_under_budget_lock(self):
        """Concurrent requests atomically reserve quota without race conditions."""
        async def handler(_request):
            await asyncio.sleep(0.01)
            return httpx2.Response(200, json=completion('{"result": "ok"}'))

        config = GeminiProviderConfig(rpm_limit=3)
        provider = GeminiProvider(config, sdk_client_factory=mock_sdk_factory(handler, []))

        requests = [
            InferenceRequest([Message("user", [f"call {i}"])], agent_name="crop")
            for i in range(5)
        ]

        results = await asyncio.gather(
            *(provider.complete(req, ProviderCredential(f"concurrent-key-{i}")) for i, req in enumerate(requests)),
            return_exceptions=True,
        )

        succeeded = [r for r in results if not isinstance(r, Exception)]
        failed = [r for r in results if isinstance(r, Exception)]

        self.assertEqual(len(succeeded), 3)
        self.assertEqual(len(failed), 2)
        for exc in failed:
            self.assertIsInstance(exc, ProviderFailure)
            self.assertEqual(exc.kind, ProviderFailureKind.RATE_LIMIT)
            self.assertEqual(exc.diagnostic.rate_limit_scope, RateLimitScope.REQUESTS_PER_MINUTE)

    async def test_successful_complete_and_usage_scrubbing(self):
        captures = []
        clients = []
        sent_payloads = []

        async def handler(request):
            sent_payloads.append(request.content.decode("utf-8"))
            return httpx2.Response(200, json=completion('{"response": "test output"}'))

        factory = mock_sdk_factory(handler, captures, clients)
        provider = GeminiProvider(GeminiProviderConfig(), sdk_client_factory=factory)
        cred = ProviderCredential("test-gemini-secret-key")
        req = InferenceRequest(
            messages=[Message("user", ["Hello Gemini"])],
            agent_name="crop",
        )

        resp = await provider.complete(req, cred)
        self.assertEqual(resp.provider, "gemini")
        self.assertEqual(resp.model_id, "gemini-3.8-flash")
        self.assertIsNotNone(resp.usage)
        self.assertEqual(resp.usage.actual_input_tokens, 15)
        self.assertEqual(resp.usage.actual_output_tokens, 25)
        self.assertEqual(resp.usage.actual_total_tokens, 40)
        self.assertEqual(resp.usage.agent_name, "crop")
        self.assertEqual(captures[0]["base_url"], GEMINI_BASE_URL)
        self.assertNotIn("test-gemini-secret-key", sent_payloads[0])
        self.assertNotIn("test-gemini-secret-key", repr(resp))

    async def test_authentication_failure_diagnostic(self):
        captures = []

        async def handler(_request):
            return httpx2.Response(
                401,
                json={"error": {"code": 401, "message": "API key not valid", "status": "UNAUTHENTICATED"}},
            )

        factory = mock_sdk_factory(handler, captures)
        provider = GeminiProvider(GeminiProviderConfig(), sdk_client_factory=factory)
        cred = ProviderCredential("invalid-gemini-key")
        req = InferenceRequest(messages=[Message("user", ["Hello"])], agent_name="intake")

        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(req, cred)

        self.assertEqual(caught.exception.kind, ProviderFailureKind.AUTHENTICATION)
        self.assertEqual(
            caught.exception.diagnostic.code,
            InferenceDiagnosticCode.GEMINI_AUTHENTICATION_FAILED,
        )
        self.assertEqual(caught.exception.diagnostic.provider, "gemini")
        self.assertEqual(caught.exception.diagnostic.http_status, 401)
        self.assertNotIn("invalid-gemini-key", repr(caught.exception.diagnostic))

    async def test_rate_limit_and_quota_exhausted_diagnostics(self):
        async def quota_handler(_request):
            return httpx2.Response(
                429,
                json={"error": {"code": 429, "message": "Resource has been exhausted (e.g. check quota).", "status": "RESOURCE_EXHAUSTED"}},
            )

        provider = GeminiProvider(
            GeminiProviderConfig(), sdk_client_factory=mock_sdk_factory(quota_handler, [])
        )
        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(
                InferenceRequest([Message("user", ["Hi"])], agent_name="coordinator"),
                ProviderCredential("quota-key"),
            )

        self.assertEqual(caught.exception.kind, ProviderFailureKind.RATE_LIMIT)
        self.assertEqual(
            caught.exception.diagnostic.code,
            InferenceDiagnosticCode.GEMINI_QUOTA_EXHAUSTED,
        )
        self.assertEqual(caught.exception.diagnostic.provider, "gemini")
        self.assertEqual(caught.exception.diagnostic.rate_limit_scope, RateLimitScope.ACCOUNT_QUOTA)

    async def test_timeout_and_network_failure_diagnostics(self):
        async def read_timeout_handler(_request):
            raise httpx2.ReadTimeout("read timed out")

        provider = GeminiProvider(
            GeminiProviderConfig(), sdk_client_factory=mock_sdk_factory(read_timeout_handler, [])
        )
        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(
                InferenceRequest([Message("user", ["Hi"])], agent_name="scheme"),
                ProviderCredential("timeout-key"),
            )

        self.assertEqual(caught.exception.kind, ProviderFailureKind.TIMEOUT)
        self.assertEqual(
            caught.exception.diagnostic.code,
            InferenceDiagnosticCode.GEMINI_READ_TIMEOUT,
        )
        self.assertEqual(caught.exception.diagnostic.provider, "gemini")

        async def connect_timeout_handler(_request):
            raise httpx2.ConnectTimeout("connect timed out")

        provider_conn = GeminiProvider(
            GeminiProviderConfig(), sdk_client_factory=mock_sdk_factory(connect_timeout_handler, [])
        )
        with self.assertRaises(ProviderFailure) as caught_conn:
            await provider_conn.complete(
                InferenceRequest([Message("user", ["Hi"])], agent_name="crop"),
                ProviderCredential("connect-timeout-key"),
            )

        self.assertEqual(caught_conn.exception.kind, ProviderFailureKind.TIMEOUT)
        self.assertEqual(
            caught_conn.exception.diagnostic.code,
            InferenceDiagnosticCode.GEMINI_CONNECT_TIMEOUT,
        )

        async def generic_timeout_handler(_request):
            raise httpx2.TimeoutException("generic timeout")

        provider_gen = GeminiProvider(
            GeminiProviderConfig(), sdk_client_factory=mock_sdk_factory(generic_timeout_handler, [])
        )
        with self.assertRaises(ProviderFailure) as caught_gen:
            await provider_gen.complete(
                InferenceRequest([Message("user", ["Hi"])], agent_name="crop"),
                ProviderCredential("generic-timeout-key"),
            )

        self.assertEqual(caught_gen.exception.kind, ProviderFailureKind.TIMEOUT)
        self.assertEqual(
            caught_gen.exception.diagnostic.code,
            InferenceDiagnosticCode.GEMINI_TIMEOUT,
        )

        async def network_handler(_request):
            raise httpx2.ConnectError("connection refused")

        provider2 = GeminiProvider(
            GeminiProviderConfig(), sdk_client_factory=mock_sdk_factory(network_handler, [])
        )
        with self.assertRaises(ProviderFailure) as caught2:
            await provider2.complete(
                InferenceRequest([Message("user", ["Hi"])], agent_name="scheme"),
                ProviderCredential("network-key"),
            )

        self.assertEqual(caught2.exception.kind, ProviderFailureKind.NETWORK)
        self.assertEqual(
            caught2.exception.diagnostic.code,
            InferenceDiagnosticCode.GEMINI_NETWORK_FAILURE,
        )

    async def test_malformed_response_diagnostic(self):
        async def malformed_handler(_request):
            return httpx2.Response(
                200, content=b"not-json", headers={"content-type": "application/json"}
            )

        provider = GeminiProvider(
            GeminiProviderConfig(), sdk_client_factory=mock_sdk_factory(malformed_handler, [])
        )
        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(
                InferenceRequest([Message("user", ["Hi"])], agent_name="crop"),
                ProviderCredential("malformed-key"),
            )

        self.assertEqual(caught.exception.kind, ProviderFailureKind.MALFORMED_RESPONSE)
        self.assertEqual(
            caught.exception.diagnostic.code,
            InferenceDiagnosticCode.GEMINI_MALFORMED_RESPONSE,
        )

    async def test_tpm_budget_reservation_and_exhaustion(self):
        async def handler(_request):
            return httpx2.Response(200, json=completion('{"result": "ok"}'))

        config = GeminiProviderConfig(
            tpm_limit_tokens=1000,
            tpm_headroom_tokens=200,
            default_max_output_tokens=300,
        )
        provider = GeminiProvider(config, sdk_client_factory=mock_sdk_factory(handler, []))

        # First call reserves ~ (estimated_input + 300) <= 800
        req = InferenceRequest([Message("user", ["short message"])], agent_name="crop")
        resp1 = await provider.complete(req, ProviderCredential("budget-key-1"))
        self.assertEqual(resp1.provider, "gemini")

        # Second call with large request should exceed budget and fail with GEMINI_TPM_BUDGET_EXCEEDED
        large_req = InferenceRequest(
            [Message("user", ["word " * 800])],
            agent_name="coordinator",
        )
        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(large_req, ProviderCredential("budget-key-2"))

        self.assertEqual(caught.exception.kind, ProviderFailureKind.RATE_LIMIT)
        self.assertEqual(
            caught.exception.diagnostic.code,
            InferenceDiagnosticCode.GEMINI_TPM_BUDGET_EXCEEDED,
        )
        self.assertEqual(caught.exception.diagnostic.budget_decision, "rejected")

    async def test_unsafe_options_and_secret_in_prompt_rejected_before_dispatch(self):
        dispatched = []

        async def handler(request):
            dispatched.append(request)
            return httpx2.Response(200, json=completion('{}'))

        provider = GeminiProvider(
            GeminiProviderConfig(), sdk_client_factory=mock_sdk_factory(handler, [])
        )
        secret_key = "sensitive-gemini-secret-12345"

        # Secret in prompt
        req_with_secret = InferenceRequest(
            messages=[Message("user", [f"My secret is {secret_key}"])]
        )
        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(req_with_secret, ProviderCredential(secret_key))
        self.assertEqual(caught.exception.kind, ProviderFailureKind.SENSITIVE_INPUT)
        self.assertEqual(len(dispatched), 0)

        # Unsafe option: api_key
        req_unsafe_opt = InferenceRequest(
            messages=[Message("user", ["safe"])],
            options={"api_key": "some-key"},  # type: ignore[typeddict-unknown-key]
        )
        with self.assertRaises(ProviderFailure) as caught2:
            await provider.complete(req_unsafe_opt, ProviderCredential(secret_key))
        self.assertEqual(caught2.exception.kind, ProviderFailureKind.SENSITIVE_INPUT)
        self.assertEqual(len(dispatched), 0)

    async def test_structured_output_response_format_passed_to_endpoint(self):
        sent_requests = []

        async def handler(request):
            sent_requests.append(json.loads(request.content.decode("utf-8")))
            return httpx2.Response(200, json=completion('{"intents": ["weather"]}'))

        provider = GeminiProvider(
            GeminiProviderConfig(), sdk_client_factory=mock_sdk_factory(handler, [])
        )

        class IntentModel(BaseModel):
            intents: list[str]

        schema = IntentModel.model_json_schema()
        req = InferenceRequest(
            messages=[Message("user", ["What is the weather?"])],
            options={
                "temperature": 0.0,
                "max_tokens": 256,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "IntentModel",
                        "schema": schema,
                        "strict": True,
                    },
                },
            },
            agent_name="intake",
        )

        resp = await provider.complete(req, ProviderCredential("test-structured-key"))
        self.assertEqual(resp.provider, "gemini")
        self.assertEqual(len(sent_requests), 1)
        sent = sent_requests[0]
        self.assertIn("response_format", sent)
        self.assertEqual(sent["response_format"], {"type": "json_object"})

    async def test_http_503_service_unavailable_classification(self):
        """HTTP 503 maps to GEMINI_SERVICE_UNAVAILABLE, stage PROVIDER_RESPONSE, extracting Google error info and request id."""
        async def handler(_request):
            return httpx2.Response(
                503,
                headers={"x-goog-request-id": "goog-req-503-abc"},
                json={
                    "error": {
                        "code": 503,
                        "message": "The model is overloaded. Please try again later.",
                        "status": "UNAVAILABLE",
                    }
                },
            )

        provider = GeminiProvider(
            GeminiProviderConfig(), sdk_client_factory=mock_sdk_factory(handler, [])
        )
        req = InferenceRequest(
            [Message("user", ["hi"])],
            options={"tools": ["disabled_retry"]},  # unsafe option disables retry so we observe single attempt
            agent_name="crop",
        )
        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(req, ProviderCredential("key-503"))

        diag = caught.exception.diagnostic
        self.assertEqual(caught.exception.kind, ProviderFailureKind.PROVIDER_FAILURE)
        self.assertEqual(diag.code, InferenceDiagnosticCode.GEMINI_SERVICE_UNAVAILABLE)
        self.assertEqual(diag.stage, InferenceStage.PROVIDER_RESPONSE)
        self.assertEqual(diag.http_status, 503)
        self.assertEqual(diag.request_id, "goog-req-503-abc")
        self.assertEqual(diag.provider_error_status, "UNAVAILABLE")
        self.assertEqual(diag.provider_error_message, "The model is overloaded. Please try again later.")

    async def test_http_502_and_504_classification(self):
        """HTTP 502 maps to GEMINI_BAD_GATEWAY and 504 maps to GEMINI_GATEWAY_TIMEOUT under PROVIDER_RESPONSE."""
        async def handler_502(_request):
            return httpx2.Response(
                502,
                headers={"goog-request-id": "goog-req-502-xyz"},
                json={"error": {"code": 502, "message": "Bad Gateway", "status": "UNAVAILABLE"}},
            )

        provider_502 = GeminiProvider(
            GeminiProviderConfig(), sdk_client_factory=mock_sdk_factory(handler_502, [])
        )
        req_unsafe = InferenceRequest(
            [Message("user", ["hi"])],
            options={"tools": ["no_retry"]},
            agent_name="scheme",
        )
        with self.assertRaises(ProviderFailure) as caught_502:
            await provider_502.complete(req_unsafe, ProviderCredential("key-502"))

        diag_502 = caught_502.exception.diagnostic
        self.assertEqual(diag_502.code, InferenceDiagnosticCode.GEMINI_BAD_GATEWAY)
        self.assertEqual(diag_502.stage, InferenceStage.PROVIDER_RESPONSE)
        self.assertEqual(diag_502.request_id, "goog-req-502-xyz")

        async def handler_504(_request):
            return httpx2.Response(
                504,
                headers={"x-request-id": "req-504-ijk"},
                json={"error": {"code": 504, "message": "Gateway Timeout", "status": "DEADLINE_EXCEEDED"}},
            )

        provider_504 = GeminiProvider(
            GeminiProviderConfig(), sdk_client_factory=mock_sdk_factory(handler_504, [])
        )
        with self.assertRaises(ProviderFailure) as caught_504:
            await provider_504.complete(req_unsafe, ProviderCredential("key-504"))

        diag_504 = caught_504.exception.diagnostic
        self.assertEqual(diag_504.code, InferenceDiagnosticCode.GEMINI_GATEWAY_TIMEOUT)
        self.assertEqual(diag_504.stage, InferenceStage.PROVIDER_RESPONSE)
        self.assertEqual(diag_504.request_id, "req-504-ijk")

    async def test_one_retry_after_503_success(self):
        """A safe request encountering HTTP 503 performs at most one retry and succeeds if the retry returns 200."""
        calls = 0

        async def handler(_request):
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx2.Response(
                    503,
                    headers={"x-goog-request-id": "attempt-1"},
                    json={"error": {"code": 503, "message": "The model is overloaded.", "status": "UNAVAILABLE"}},
                )
            return httpx2.Response(
                200,
                headers={"x-goog-request-id": "attempt-2"},
                json=completion('{"response": "recovered"}'),
            )

        provider = GeminiProvider(
            GeminiProviderConfig(), sdk_client_factory=mock_sdk_factory(handler, [])
        )
        req = InferenceRequest([Message("user", ["safe prompt"])], agent_name="crop")

        with patch("asyncio.sleep", return_value=None):
            resp = await provider.complete(req, ProviderCredential("key-retry-success"))

        self.assertEqual(resp.provider, "gemini")
        self.assertEqual(calls, 2)
        self.assertEqual(resp.usage.retries_attempted, 1)
        self.assertEqual(resp.usage.retry_decision, "retried")

    async def test_no_retry_after_second_503(self):
        """A second 503 terminates immediately without entering an unbounded retry loop."""
        calls = 0

        async def handler(_request):
            nonlocal calls
            calls += 1
            return httpx2.Response(
                503,
                headers={"x-goog-request-id": f"attempt-{calls}"},
                json={"error": {"code": 503, "message": "The model is overloaded.", "status": "UNAVAILABLE"}},
            )

        provider = GeminiProvider(
            GeminiProviderConfig(), sdk_client_factory=mock_sdk_factory(handler, [])
        )
        req = InferenceRequest([Message("user", ["safe prompt"])], agent_name="scheme")

        with patch("asyncio.sleep", return_value=None):
            with self.assertRaises(ProviderFailure) as caught:
                await provider.complete(req, ProviderCredential("key-retry-fail"))

        self.assertEqual(calls, 2)
        self.assertEqual(caught.exception.diagnostic.retries_attempted, 1)
        self.assertEqual(caught.exception.diagnostic.retry_decision, "retried")
        self.assertEqual(caught.exception.diagnostic.code, InferenceDiagnosticCode.GEMINI_SERVICE_UNAVAILABLE)

    async def test_max_retries_zero_prevents_503_retry(self):
        """When max_retries is 0, a 503 response is not retried and fails immediately."""
        calls = 0

        async def handler(_request):
            nonlocal calls
            calls += 1
            return httpx2.Response(
                503,
                headers={"x-goog-request-id": f"attempt-{calls}"},
                json={"error": {"code": 503, "message": "The model is overloaded.", "status": "UNAVAILABLE"}},
            )

        provider = GeminiProvider(
            GeminiProviderConfig(max_retries=0), sdk_client_factory=mock_sdk_factory(handler, [])
        )
        req = InferenceRequest([Message("user", ["safe prompt"])], agent_name="scheme")

        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(req, ProviderCredential("key-no-retry"))

        self.assertEqual(calls, 1)
        self.assertEqual(caught.exception.diagnostic.retries_attempted, 0)
        self.assertEqual(caught.exception.diagnostic.retry_decision, "not_retried")
        self.assertEqual(caught.exception.diagnostic.code, InferenceDiagnosticCode.GEMINI_SERVICE_UNAVAILABLE)

    async def test_retry_after_handling_and_excessive_wait_rejection(self):
        """Retry-After is honored when within limits; excessive wait (e.g. 30s) aborts retry immediately."""
        # 1. Valid Retry-After: 0.5s is honored
        calls_valid = 0
        sleep_durations = []

        async def handler_valid(_request):
            nonlocal calls_valid
            calls_valid += 1
            if calls_valid == 1:
                return httpx2.Response(
                    503,
                    headers={"retry-after": "0.5"},
                    json={"error": {"code": 503, "message": "Overloaded"}},
                )
            return httpx2.Response(200, json=completion('{"result": "ok"}'))

        provider = GeminiProvider(
            GeminiProviderConfig(), sdk_client_factory=mock_sdk_factory(handler_valid, [])
        )
        req = InferenceRequest([Message("user", ["prompt"])], agent_name="crop")

        async def mock_sleep(d):
            sleep_durations.append(d)

        with patch("asyncio.sleep", side_effect=mock_sleep):
            resp = await provider.complete(req, ProviderCredential("key-retry-after"))

        self.assertEqual(resp.provider, "gemini")
        self.assertEqual(calls_valid, 2)
        self.assertEqual(len(sleep_durations), 1)
        self.assertGreaterEqual(sleep_durations[0], 0.5)
        self.assertLessEqual(sleep_durations[0], 1.5)

        # 2. Excessive Retry-After: 30s aborts retry immediately
        calls_excessive = 0

        async def handler_excessive(_request):
            nonlocal calls_excessive
            calls_excessive += 1
            return httpx2.Response(
                503,
                headers={"retry-after": "30"},
                json={"error": {"code": 503, "message": "Overloaded"}},
            )

        provider_excessive = GeminiProvider(
            GeminiProviderConfig(), sdk_client_factory=mock_sdk_factory(handler_excessive, [])
        )
        with self.assertRaises(ProviderFailure) as caught:
            await provider_excessive.complete(req, ProviderCredential("key-excessive"))

        self.assertEqual(calls_excessive, 1)
        self.assertEqual(caught.exception.diagnostic.retries_attempted, 0)
        self.assertEqual(caught.exception.diagnostic.retry_decision, "not_retried")

    async def test_jittered_retry_when_retry_after_absent(self):
        """When Retry-After is absent, initial backoff delay is jittered in the 1.0–2.0s range."""
        calls = 0
        sleep_durations = []

        async def handler(_request):
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx2.Response(503, json={"error": {"code": 503, "message": "Overloaded"}})
            return httpx2.Response(200, json=completion('{"result": "ok"}'))

        provider = GeminiProvider(
            GeminiProviderConfig(), sdk_client_factory=mock_sdk_factory(handler, [])
        )
        req = InferenceRequest([Message("user", ["prompt"])], agent_name="crop")

        async def mock_sleep(d):
            sleep_durations.append(d)

        with patch("asyncio.sleep", side_effect=mock_sleep):
            resp = await provider.complete(req, ProviderCredential("key-jitter"))

        self.assertEqual(resp.provider, "gemini")
        self.assertEqual(calls, 2)
        self.assertEqual(len(sleep_durations), 1)
        self.assertGreaterEqual(sleep_durations[0], 1.0)
        self.assertLessEqual(sleep_durations[0], 2.5)

    async def test_unsafe_request_rejection_from_retry(self):
        """Requests containing tool or function definitions are never retried."""
        calls = 0

        async def handler(_request):
            nonlocal calls
            calls += 1
            return httpx2.Response(503, json={"error": {"code": 503, "message": "Overloaded"}})

        provider = GeminiProvider(
            GeminiProviderConfig(), sdk_client_factory=mock_sdk_factory(handler, [])
        )
        req_tools = InferenceRequest(
            [Message("user", ["prompt"])],
            options={"tools": [{"type": "function", "function": {"name": "test"}}]},
            agent_name="crop",
        )
        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(req_tools, ProviderCredential("key-unsafe"))

        self.assertEqual(calls, 1)
        self.assertEqual(caught.exception.diagnostic.retries_attempted, 0)
        self.assertEqual(caught.exception.diagnostic.retry_decision, "not_retried")

    async def test_no_retry_for_authentication_or_invalid_request(self):
        """Authentication (401) and invalid request (400) failures are never retried."""
        calls = 0

        async def handler_401(_request):
            nonlocal calls
            calls += 1
            return httpx2.Response(401, json={"error": {"code": 401, "message": "Auth error"}})

        provider_401 = GeminiProvider(
            GeminiProviderConfig(), sdk_client_factory=mock_sdk_factory(handler_401, [])
        )
        req = InferenceRequest([Message("user", ["prompt"])], agent_name="crop")
        with self.assertRaises(ProviderFailure) as caught_401:
            await provider_401.complete(req, ProviderCredential("key-401"))

        self.assertEqual(calls, 1)
        self.assertEqual(caught_401.exception.diagnostic.retries_attempted, 0)
        self.assertEqual(caught_401.exception.diagnostic.retry_decision, "not_retried")

        calls_400 = 0

        async def handler_400(_request):
            nonlocal calls_400
            calls_400 += 1
            return httpx2.Response(400, json={"error": {"code": 400, "message": "Bad request"}})

        provider_400 = GeminiProvider(
            GeminiProviderConfig(), sdk_client_factory=mock_sdk_factory(handler_400, [])
        )
        with self.assertRaises(ProviderFailure) as caught_400:
            await provider_400.complete(req, ProviderCredential("key-400"))

        self.assertEqual(calls_400, 1)
        self.assertEqual(caught_400.exception.diagnostic.retries_attempted, 0)
        self.assertEqual(caught_400.exception.diagnostic.retry_decision, "not_retried")

    async def test_rpm_rpd_and_tpm_accounting_across_retries(self):
        """Retried requests create distinct RPM/RPD events; retry settlement applies only to its own reservation."""
        calls = 0

        async def handler(_request):
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx2.Response(503, json={"error": {"code": 503, "message": "Overloaded"}})
            return httpx2.Response(
                200,
                json=completion('{"response": "ok"}'),
            )

        config = GeminiProviderConfig(
            rpm_limit=10,
            rpd_limit=10,
            tpm_limit_tokens=3000,
        )
        provider = GeminiProvider(config, sdk_client_factory=mock_sdk_factory(handler, []))
        req = InferenceRequest([Message("user", ["prompt for accounting"])], agent_name="crop")

        with patch("asyncio.sleep", return_value=None):
            resp = await provider.complete(req, ProviderCredential("key-accounting"))

        self.assertEqual(resp.provider, "gemini")
        self.assertEqual(calls, 2)
        # 1. Two separate requests recorded in rolling minute and day events
        self.assertEqual(len(provider._minute_events), 2)
        self.assertEqual(len(provider._day_events), 2)
        # 2. Attempt 2 settled to actual 40 total tokens
        self.assertEqual(provider._minute_events[1][1], 40)
        # 3. Attempt 1 retained conservative estimated input + output token reservation
        self.assertGreater(provider._minute_events[0][1], 0)

    async def test_concurrent_retry_coordination(self):
        """Concurrent retries from parallel branches are staggered to prevent lockstep dispatch."""
        async def handler(_request):
            return httpx2.Response(503, json={"error": {"code": 503, "message": "Overloaded"}})

        provider = GeminiProvider(
            GeminiProviderConfig(), sdk_client_factory=mock_sdk_factory(handler, [])
        )
        req1 = InferenceRequest([Message("user", ["crop question"])], agent_name="crop")
        req2 = InferenceRequest([Message("user", ["scheme question"])], agent_name="scheme")

        sleeps = []

        async def tracking_sleep(d):
            sleeps.append(d)

        with patch("asyncio.sleep", side_effect=tracking_sleep):
            results = await asyncio.gather(
                provider.complete(req1, ProviderCredential("key-concurrent-1")),
                provider.complete(req2, ProviderCredential("key-concurrent-2")),
                return_exceptions=True,
            )

        self.assertEqual(len(results), 2)
        self.assertIsInstance(results[0], ProviderFailure)
        self.assertIsInstance(results[1], ProviderFailure)
        self.assertEqual(len(sleeps), 2)
        # Staggered coordination guarantees a minimum separation between concurrent retry wakeups
        self.assertGreaterEqual(abs(sleeps[1] - sleeps[0]), 0.20)

    async def test_deadline_expiration_during_retry_wait(self):
        """When computed retry wait exceeds remaining deadline, retry is aborted and fallback triggered."""
        calls = 0

        async def handler(_request):
            nonlocal calls
            calls += 1
            return httpx2.Response(503, json={"error": {"code": 503, "message": "Overloaded"}})

        provider = GeminiProvider(
            GeminiProviderConfig(), sdk_client_factory=mock_sdk_factory(handler, [])
        )
        # Tight deadline of 0.2s: computed retry wait (1.0-2.0s) exceeds deadline
        req = InferenceRequest(
            [Message("user", ["tight deadline"])],
            options={"deadline_seconds": 0.2},
            agent_name="crop",
        )
        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(req, ProviderCredential("key-deadline"))

        self.assertEqual(calls, 1)
        self.assertEqual(caught.exception.diagnostic.retries_attempted, 0)
        self.assertEqual(caught.exception.diagnostic.retry_decision, "not_retried")

    async def test_api_timeout_error_classified_as_timeout_not_cancelled(self):
        """APITimeoutError (even with suppressed CancelledError in context) maps to GEMINI_READ_TIMEOUT, not REQUEST_CANCELLED."""
        async def handler(_request):
            read_exc = httpx2.ReadTimeout("read timed out")
            cancel_exc = asyncio.CancelledError()
            read_exc.__context__ = cancel_exc
            raise read_exc

        provider = GeminiProvider(
            GeminiProviderConfig(), sdk_client_factory=mock_sdk_factory(handler, [])
        )
        req = InferenceRequest([Message("user", ["test timeout"])], agent_name="intake")
        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(req, ProviderCredential("timeout-key"))

        self.assertEqual(caught.exception.kind, ProviderFailureKind.TIMEOUT)
        self.assertEqual(
            caught.exception.diagnostic.code,
            InferenceDiagnosticCode.GEMINI_READ_TIMEOUT,
        )
        self.assertEqual(caught.exception.diagnostic.stage, InferenceStage.PROVIDER_TRANSPORT)
        self.assertIn("ReadTimeout", caught.exception.diagnostic.exception_type_chain or "")

    async def test_api_connection_error_classified_as_network(self):
        """APIConnectionError maps to GEMINI_NETWORK_FAILURE."""
        async def handler(_request):
            raise httpx2.ConnectError("connection refused")

        provider = GeminiProvider(
            GeminiProviderConfig(), sdk_client_factory=mock_sdk_factory(handler, [])
        )
        req = InferenceRequest([Message("user", ["test conn"])], agent_name="crop")
        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(req, ProviderCredential("conn-key"))

        self.assertEqual(caught.exception.kind, ProviderFailureKind.NETWORK)
        self.assertEqual(
            caught.exception.diagnostic.code,
            InferenceDiagnosticCode.GEMINI_NETWORK_FAILURE,
        )
        self.assertEqual(caught.exception.diagnostic.stage, InferenceStage.PROVIDER_TRANSPORT)

    async def test_asyncio_cancelled_error_propagates_without_swallowing(self):
        """Direct asyncio.CancelledError is never swallowed into ProviderFailure."""
        async def handler(_request):
            raise asyncio.CancelledError("task cancelled")

        provider = GeminiProvider(
            GeminiProviderConfig(), sdk_client_factory=mock_sdk_factory(handler, [])
        )
        req = InferenceRequest([Message("user", ["test cancel"])], agent_name="intake")
        with self.assertRaises(asyncio.CancelledError):
            await provider.complete(req, ProviderCredential("cancel-key"))

    async def test_finish_reason_length_with_empty_content_triggers_malformed_response(self):
        """finish_reason='length' with empty message content produces MALFORMED_RESPONSE with length_terminated_empty."""
        async def handler(_request):
            return httpx2.Response(
                200,
                json={
                    "id": "chatcmpl-empty-len",
                    "object": "chat.completion",
                    "created": 1234567890,
                    "model": "gemini-3.8-flash",
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": ""},
                        "finish_reason": "length",
                    }],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 0, "total_tokens": 10},
                },
            )

        provider = GeminiProvider(
            GeminiProviderConfig(), sdk_client_factory=mock_sdk_factory(handler, [])
        )
        req = InferenceRequest([Message("user", ["prompt"])], agent_name="intake")
        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(req, ProviderCredential("len-key"))

        self.assertEqual(caught.exception.kind, ProviderFailureKind.MALFORMED_RESPONSE)
        self.assertEqual(
            caught.exception.diagnostic.code,
            InferenceDiagnosticCode.GEMINI_MALFORMED_RESPONSE,
        )
        self.assertEqual(caught.exception.diagnostic.stage, InferenceStage.RESPONSE_VALIDATION)
        self.assertEqual(caught.exception.diagnostic.finish_reason, "length")
        self.assertEqual(caught.exception.diagnostic.parsing_outcome, "length_terminated_empty")
        self.assertEqual(caught.exception.diagnostic.response_character_count, 0)

    async def test_gemini_adapter_converts_json_schema_to_json_object(self):
        """Gemini adapter automatically converts response_format json_schema to json_object."""
        sent_requests = []

        async def handler(request):
            sent_requests.append(json.loads(request.read()))
            return httpx2.Response(200, json=completion('{"response": "ok"}'))

        provider = GeminiProvider(
            GeminiProviderConfig(), sdk_client_factory=mock_sdk_factory(handler, [])
        )
        req = InferenceRequest(
            [Message("user", ["test json schema"])],
            options={
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "TestModel", "strict": True, "schema": {"type": "object"}},
                },
            },
            agent_name="crop",
        )
        resp = await provider.complete(req, ProviderCredential("fmt-key"))
        self.assertEqual(resp.provider, "gemini")
        self.assertEqual(len(sent_requests), 1)
        self.assertEqual(sent_requests[0]["response_format"], {"type": "json_object"})

    async def test_http_503_error_body_full_extraction(self):
        """HTTP 503 extracts error.code, error.status, error.message, Retry-After, and trace identifiers."""
        async def handler(_request):
            return httpx2.Response(
                503,
                headers={
                    "x-goog-request-id": "goog-req-503-full",
                    "x-goog-trace-id": "trace-999-xyz",
                    "retry-after": "8.5",
                    "content-type": "application/json",
                },
                json={
                    "error": {
                        "code": 503,
                        "status": "UNAVAILABLE",
                        "message": "The service is temporarily unavailable due to high load.",
                    }
                },
            )

        provider = GeminiProvider(
            GeminiProviderConfig(max_retries=0), sdk_client_factory=mock_sdk_factory(handler, [])
        )
        req = InferenceRequest(
            [Message("user", ["hi"])],
            agent_name="crop",
        )
        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(req, ProviderCredential("k-503-full"))

        diag = caught.exception.diagnostic
        self.assertEqual(diag.code, InferenceDiagnosticCode.GEMINI_SERVICE_UNAVAILABLE)
        self.assertEqual(diag.stage, InferenceStage.PROVIDER_RESPONSE)
        self.assertEqual(diag.http_status, 503)
        self.assertEqual(diag.provider_error_code, 503)
        self.assertEqual(diag.provider_error_status, "UNAVAILABLE")
        self.assertEqual(diag.provider_error_message, "The service is temporarily unavailable due to high load.")
        self.assertEqual(diag.retry_after_seconds, 8.5)
        self.assertEqual(diag.request_id, "goog-req-503-full")
        self.assertEqual(diag.trace_id, "trace-999-xyz")
        hdrs = diag.response_headers_dict
        self.assertEqual(hdrs.get("content-type"), "application/json")
        self.assertEqual(hdrs.get("retry-after"), "8.5")

    async def test_http_503_missing_error_body(self):
        """HTTP 503 with an empty response body handles missing error structure cleanly."""
        async def handler(_request):
            return httpx2.Response(
                503,
                headers={"x-goog-request-id": "goog-req-503-empty"},
                content=b"",
            )

        provider = GeminiProvider(
            GeminiProviderConfig(max_retries=0), sdk_client_factory=mock_sdk_factory(handler, [])
        )
        req = InferenceRequest([Message("user", ["hi"])], agent_name="crop")
        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(req, ProviderCredential("k-503-empty"))

        diag = caught.exception.diagnostic
        self.assertEqual(diag.code, InferenceDiagnosticCode.GEMINI_SERVICE_UNAVAILABLE)
        self.assertEqual(diag.stage, InferenceStage.PROVIDER_RESPONSE)
        self.assertEqual(diag.http_status, 503)
        self.assertIsNone(diag.provider_error_code)
        self.assertIsNone(diag.provider_error_status)
        self.assertIsNone(diag.provider_error_message)
        self.assertEqual(diag.request_id, "goog-req-503-empty")

    async def test_http_503_malformed_html_error_body(self):
        """HTTP 503 with HTML error body from a reverse proxy or frontend does not crash."""
        async def handler(_request):
            return httpx2.Response(
                503,
                headers={"goog-request-id": "goog-req-503-html"},
                text="<html><head><title>503 Service Unavailable</title></head><body><h1>Frontend Error</h1></body></html>",
            )

        provider = GeminiProvider(
            GeminiProviderConfig(max_retries=0), sdk_client_factory=mock_sdk_factory(handler, [])
        )
        req = InferenceRequest([Message("user", ["hi"])], agent_name="crop")
        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(req, ProviderCredential("k-503-html"))

        diag = caught.exception.diagnostic
        self.assertEqual(diag.code, InferenceDiagnosticCode.GEMINI_SERVICE_UNAVAILABLE)
        self.assertEqual(diag.stage, InferenceStage.PROVIDER_RESPONSE)
        self.assertEqual(diag.http_status, 503)
        self.assertIsNone(diag.provider_error_code)
        self.assertIsNone(diag.provider_error_status)
        self.assertIsNone(diag.provider_error_message)
        self.assertEqual(diag.request_id, "goog-req-503-html")

    async def test_http_503_malformed_json_syntax(self):
        """HTTP 503 with corrupted/truncated JSON syntax handles error extraction cleanly."""
        async def handler(_request):
            return httpx2.Response(
                503,
                headers={"x-goog-request-id": "goog-req-503-trunc"},
                text='{"error": {"code": 503, "status": "UNAVAIL',
            )

        provider = GeminiProvider(
            GeminiProviderConfig(max_retries=0), sdk_client_factory=mock_sdk_factory(handler, [])
        )
        req = InferenceRequest([Message("user", ["hi"])], agent_name="crop")
        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(req, ProviderCredential("k-503-trunc"))

        diag = caught.exception.diagnostic
        self.assertEqual(diag.code, InferenceDiagnosticCode.GEMINI_SERVICE_UNAVAILABLE)
        self.assertEqual(diag.http_status, 503)
        self.assertIsNone(diag.provider_error_code)
        self.assertIsNone(diag.provider_error_status)
        self.assertIsNone(diag.provider_error_message)
        self.assertEqual(diag.request_id, "goog-req-503-trunc")

    async def test_response_headers_never_expose_secrets(self):
        """Sanitized response headers drop authorization, api-key, cookie, and secret tokens."""
        async def handler(_request):
            return httpx2.Response(
                503,
                headers={
                    "x-goog-request-id": "goog-req-safe-1",
                    "authorization": "Bearer AIzaSySecretKey999",
                    "x-api-key": "secret-key-12345",
                    "set-cookie": "session_token=xyzabc",
                    "content-type": "application/json",
                },
                json={"error": {"code": 503, "status": "UNAVAILABLE", "message": "Overloaded"}},
            )

        provider = GeminiProvider(
            GeminiProviderConfig(max_retries=0), sdk_client_factory=mock_sdk_factory(handler, [])
        )
        req = InferenceRequest([Message("user", ["hi"])], agent_name="crop")
        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(req, ProviderCredential("k-safe"))

        diag = caught.exception.diagnostic
        hdrs = diag.response_headers_dict
        self.assertNotIn("authorization", hdrs)
        self.assertNotIn("x-api-key", hdrs)
        self.assertNotIn("set-cookie", hdrs)
        for k, v in hdrs.items():
            self.assertNotIn("AIzaSy", v)
            self.assertNotIn("secret-key", v)
            self.assertNotIn("xyzabc", v)
        self.assertEqual(hdrs.get("x-goog-request-id"), "goog-req-safe-1")
        self.assertEqual(hdrs.get("content-type"), "application/json")


if __name__ == "__main__":
    unittest.main()
