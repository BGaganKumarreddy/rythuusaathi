from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import io
import json
import logging
import os
import pickle
import time
import unittest
from enum import Enum
from unittest.mock import AsyncMock, patch

import httpx2
from agent_framework import ChatResponse, Content, FunctionTool, Message
from openai import AsyncOpenAI
from pydantic import BaseModel

from rythusaathi.inference import (
    InferenceDiagnosticCode,
    InferenceRequest,
    ProviderCredential,
    ProviderFailure,
    ProviderFailureKind,
    RateLimitScope,
)
from rythusaathi.inference.contracts import contains_provider_secret, sanitized_chat_response
from rythusaathi.inference.providers import groq as groq_module
from rythusaathi.inference.providers.groq import (
    DEFAULT_MODEL_ID,
    GROQ_BASE_URL,
    GroqProvider,
    GroqProviderConfig,
    _parse_duration_seconds,
    _parse_retry_after_seconds,
    _rate_limit_scope,
    estimate_input_tokens,
)


CANARY = "synthetic-" + "groq-" + "credential-canary"
_GLOBAL_TOOL_MAPPING = {"nested": {"credential": CANARY}}
_GLOBAL_TOOL_LIST = ["prefix-" + CANARY]


def _tool_referencing_global_mapping() -> str:
    return _GLOBAL_TOOL_MAPPING["nested"]["credential"]


def _tool_referencing_global_list() -> str:
    return _GLOBAL_TOOL_LIST[0]


def _tool_with_empty_closure_cell():
    value = "temporary"

    def tool():
        return value

    del value
    return tool


def completion(content: str | None, *, tool_calls=None):
    message = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-production-fixture",
        "object": "chat.completion",
        "created": 1,
        "model": DEFAULT_MODEL_ID,
        "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if tool_calls else "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
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


class GroqProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_configuration_explicit_credential_and_sanitized_response(self):
        seen = []
        model_id = "fixture/custom-groq-model"

        async def handle(request):
            body = json.loads(request.content)
            seen.append((request, body))
            return httpx2.Response(200, json=completion(CANARY))

        captures = []
        clients = []
        provider = GroqProvider(
            GroqProviderConfig(model_id=model_id),
            sdk_client_factory=mock_sdk_factory(handle, captures, clients),
        )
        credential = ProviderCredential(CANARY)
        result = await provider.complete(
            InferenceRequest(
                [Message("user", ["safe prompt"])],
                agent_name="crop",
                correlation_id="a" * 32,
            ), credential
        )
        request, body = seen[0]
        self.assertEqual(str(request.url), f"{GROQ_BASE_URL}/chat/completions")
        self.assertEqual(request.headers.get("authorization"), f"Bearer {CANARY}")
        self.assertEqual(body["model"], model_id)
        self.assertNotIn(CANARY, json.dumps(body))
        self.assertEqual(captures[0]["base_url"], GROQ_BASE_URL)
        self.assertEqual(result.model_id, model_id)
        self.assertEqual(result.usage.agent_name, "crop")
        self.assertEqual(result.usage.correlation_id, "a" * 32)
        self.assertGreater(result.usage.estimated_input_tokens, 0)
        self.assertEqual(result.usage.actual_input_tokens, 1)
        self.assertEqual(result.usage.actual_output_tokens, 1)
        self.assertEqual(result.usage.actual_total_tokens, 2)
        self.assertTrue(clients[0].is_closed())
        serialized_response = result.response.messages[0].contents[0].text
        self.assertNotIn(CANARY, serialized_response)
        self.assertEqual(serialized_response, "[REDACTED]")
        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(
                InferenceRequest([Message("user", ["safe prompt"])]), credential
            )
        self.assertEqual(caught.exception.kind, ProviderFailureKind.AUTHENTICATION)

    async def test_tool_call_content_is_preserved_for_framework_tool_handling(self):
        seen = []
        calls = [{
            "id": "call-weather-fixture",
            "type": "function",
            "function": {"name": "get_weather", "arguments": '{"location":"Mysuru"}'},
        }]

        async def handle(request):
            body = json.loads(request.content)
            seen.append(body)
            if len(seen) == 1:
                return httpx2.Response(200, json=completion(None, tool_calls=calls))
            return httpx2.Response(200, json=completion("Fixture weather is 20 C."))

        def get_weather(location: str) -> str:
            """Look up deterministic fixture weather for a location."""
            return f"Fixture weather for {location}: 20 C."

        provider = GroqProvider(
            GroqProviderConfig(), sdk_client_factory=mock_sdk_factory(handle, [])
        )
        result = await provider.complete(
            InferenceRequest(
                [Message("user", ["Get local weather."])],
                options={"tools": [get_weather]},
            ),
            ProviderCredential(CANARY),
        )
        self.assertEqual(result.response.messages[-1].contents[0].text, "Fixture weather is 20 C.")
        self.assertIn("get_weather", json.dumps(seen[0]["tools"]))
        tool_results = [m for m in seen[1]["messages"] if m.get("role") == "tool"]
        self.assertEqual(len(tool_results), 1)
        self.assertIn("Fixture weather for Mysuru: 20 C.", tool_results[0]["content"])

    async def test_tool_execution_failure_is_returned_without_detailed_exception_text(self):
        seen = []
        tool_calls = [{
            "id": "call-failing-tool",
            "type": "function",
            "function": {"name": "fixture_lookup", "arguments": "{}"},
        }]

        async def handle(request):
            body = json.loads(request.content)
            seen.append(body)
            if len(seen) == 1:
                return httpx2.Response(200, json=completion(None, tool_calls=tool_calls))
            return httpx2.Response(200, json=completion("I could not complete the lookup."))

        def fixture_lookup() -> str:
            """A deterministic tool that raises a fixture failure."""
            raise RuntimeError("fixture tool failure with private detail")

        provider = GroqProvider(sdk_client_factory=mock_sdk_factory(handle, []))
        log_stream = io.StringIO()
        log_handler = logging.StreamHandler(log_stream)
        root_logger = logging.getLogger()
        root_logger.addHandler(log_handler)
        try:
            result = await provider.complete(
                InferenceRequest(
                    [Message("user", ["Use the lookup tool."])],
                    options={"tools": [fixture_lookup]},
                ),
                ProviderCredential(CANARY),
            )
        finally:
            root_logger.removeHandler(log_handler)
            log_handler.close()
        self.assertEqual(
            result.response.messages[-1].contents[0].text,
            "I could not complete the lookup.",
        )
        tool_results = [message for message in seen[1]["messages"] if message.get("role") == "tool"]
        self.assertEqual(len(tool_results), 1)
        self.assertNotIn("private detail", tool_results[0]["content"])
        self.assertNotIn("private detail", log_stream.getvalue())
        self.assertNotIn(CANARY, log_stream.getvalue())

    async def test_provider_errors_are_classified_and_sanitized(self):
        cases = [
            (401, {"error": {"message": CANARY, "type": "auth_error"}},
             ProviderFailureKind.AUTHENTICATION, InferenceDiagnosticCode.GROQ_AUTHENTICATION_FAILED),
            (403, {"error": {"message": "forbidden", "type": "auth_error"}},
             ProviderFailureKind.AUTHENTICATION, InferenceDiagnosticCode.GROQ_AUTHENTICATION_FAILED),
            (400, {"error": {"message": f"model {CANARY} does not exist", "code": "model_not_found"}},
             ProviderFailureKind.INVALID_MODEL, InferenceDiagnosticCode.GROQ_INVALID_MODEL),
            (429, {"error": {"message": "rate limited", "type": "rate_limit_error"}},
             ProviderFailureKind.RATE_LIMIT, InferenceDiagnosticCode.GROQ_RATE_LIMITED),
        ]
        for status, body, kind, diagnostic_code in cases:
            with self.subTest(kind=kind):
                async def handle(_request, status=status, body=body):
                    return httpx2.Response(status, json=body)
                clients = []
                provider = GroqProvider(
                    sdk_client_factory=mock_sdk_factory(handle, [], clients)
                )
                log_stream = io.StringIO()
                log_handler = logging.StreamHandler(log_stream)
                root_logger = logging.getLogger()
                root_logger.addHandler(log_handler)
                try:
                    with self.assertRaises(ProviderFailure) as caught:
                        await provider.complete(
                            InferenceRequest([Message("user", ["safe"])]),
                            ProviderCredential(CANARY),
                        )
                finally:
                    root_logger.removeHandler(log_handler)
                    log_handler.close()
                self.assertEqual(caught.exception.kind, kind)
                self.assertEqual(caught.exception.diagnostic.code, diagnostic_code)
                self.assertEqual(caught.exception.diagnostic.http_status, status)
                self.assertEqual(caught.exception.diagnostic.model_id, DEFAULT_MODEL_ID)
                self.assertTrue(caught.exception.diagnostic.correlation_id)
                self.assertIsNotNone(caught.exception.diagnostic.timestamp.tzinfo)
                self.assertNotIn(CANARY, str(caught.exception))
                self.assertNotIn(CANARY, repr(caught.exception.diagnostic))
                self.assertNotIn("Bearer", str(caught.exception))
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)
                self.assertTrue(clients[0].is_closed())
                self.assertNotIn(CANARY, log_stream.getvalue())
                self.assertNotIn("Bearer", log_stream.getvalue())

    async def test_timeout_and_malformed_response(self):
        async def timed_out(_request):
            raise httpx2.ReadTimeout("fixture timeout")
        clients = []
        provider = GroqProvider(sdk_client_factory=mock_sdk_factory(timed_out, [], clients))
        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(InferenceRequest([Message("user", ["x"])]), ProviderCredential(CANARY))
        self.assertEqual(caught.exception.kind, ProviderFailureKind.TIMEOUT)
        self.assertEqual(caught.exception.diagnostic.code, InferenceDiagnosticCode.GROQ_TIMEOUT)
        self.assertTrue(clients[0].is_closed())

        async def malformed(_request):
            return httpx2.Response(200, content=b"not-json", headers={"content-type": "application/json"})
        clients = []
        provider = GroqProvider(sdk_client_factory=mock_sdk_factory(malformed, [], clients))
        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(InferenceRequest([Message("user", ["x"])]), ProviderCredential(CANARY))
        self.assertEqual(caught.exception.kind, ProviderFailureKind.MALFORMED_RESPONSE)
        self.assertEqual(caught.exception.diagnostic.code,
                         InferenceDiagnosticCode.GROQ_MALFORMED_RESPONSE)
        self.assertTrue(clients[0].is_closed())

    def test_retry_after_and_reset_header_parsing(self):
        self.assertEqual(
            _parse_retry_after_seconds({"retry-after-ms": "1250", "retry-after": "4"}),
            1.25,
        )
        self.assertEqual(_parse_retry_after_seconds({"retry-after": "1.75"}), 1.75)
        http_date = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=30))
        date_delay = _parse_retry_after_seconds({"retry-after": http_date})
        self.assertIsNotNone(date_delay)
        self.assertGreaterEqual(date_delay, 0)
        self.assertLessEqual(date_delay, 31)
        self.assertIsNone(_parse_retry_after_seconds({"retry-after": "not-a-delay"}))
        self.assertIsNone(_parse_retry_after_seconds({}))
        self.assertAlmostEqual(_parse_duration_seconds("2m59.56s"), 179.56)
        self.assertEqual(_parse_duration_seconds("7.66s"), 7.66)
        self.assertIsNone(_parse_duration_seconds("invalid-reset"))

    def test_rate_limit_scope_uses_explicit_safe_provider_metadata(self):
        self.assertEqual(
            _rate_limit_scope({"error": {"code": "requests_per_minute"}},
                              remaining_requests=4, remaining_tokens=8),
            RateLimitScope.REQUESTS_PER_MINUTE,
        )
        self.assertEqual(
            _rate_limit_scope({"error": {"message": "tokens per minute limit reached"}},
                              remaining_requests=4, remaining_tokens=8),
            RateLimitScope.TOKENS_PER_MINUTE,
        )
        self.assertEqual(
            _rate_limit_scope({"error": {
                "message": "Token quota exceeded for tokens per minute (TPM)."
            }}, remaining_requests=997, remaining_tokens=361),
            RateLimitScope.TOKENS_PER_MINUTE,
        )
        self.assertEqual(
            _rate_limit_scope({"error": {"code": "quota_exceeded"}},
                              remaining_requests=4, remaining_tokens=8),
            RateLimitScope.ACCOUNT_QUOTA,
        )
        self.assertEqual(
            _rate_limit_scope({"error": {"type": "tokens_per_day"}},
                              remaining_requests=4, remaining_tokens=8),
            RateLimitScope.TOKENS_PER_DAY,
        )
        self.assertEqual(
            _rate_limit_scope({
                "error": {"message": "You exceeded your current quota; check billing details."}
            }, remaining_requests=4, remaining_tokens=8),
            RateLimitScope.ACCOUNT_QUOTA,
        )
        self.assertEqual(
            _rate_limit_scope({}, remaining_requests=0, remaining_tokens=8),
            RateLimitScope.REQUESTS_PER_DAY,
        )
        self.assertEqual(
            _rate_limit_scope({}, remaining_requests=4, remaining_tokens=0),
            RateLimitScope.TOKENS_PER_MINUTE,
        )
        self.assertEqual(
            _rate_limit_scope({}, remaining_requests=None, remaining_tokens=None),
            RateLimitScope.UNKNOWN,
        )

    async def test_short_retry_after_retries_once_and_returns_success(self):
        seen = []

        async def handle(request):
            seen.append(request)
            if len(seen) == 1:
                return httpx2.Response(
                    429,
                    json={"error": {"type": "rate_limit_exceeded", "message": "busy"}},
                    headers={
                        "retry-after": "0.05",
                        "x-ratelimit-remaining-requests": "12",
                        "x-ratelimit-remaining-tokens": "1500",
                        "x-ratelimit-reset-requests": "2m59.56s",
                        "x-ratelimit-reset-tokens": "1.2s",
                    },
                )
            return httpx2.Response(200, json=completion("valid model response"))

        provider = GroqProvider(sdk_client_factory=mock_sdk_factory(handle, []))
        with patch("rythusaathi.inference.providers.groq.asyncio.sleep",
                   new=AsyncMock()) as sleep, patch(
                       "rythusaathi.inference.providers.groq.random.uniform",
                       return_value=0.05,
                   ):
            result = await provider.complete(
                InferenceRequest([Message("user", ["safe prompt"])]),
                ProviderCredential(CANARY),
            )
        self.assertEqual(len(seen), 2)
        self.assertEqual(sleep.await_count, 1)
        delay = sleep.await_args.args[0]
        self.assertGreaterEqual(delay, 0.05)
        self.assertLessEqual(delay, 2.0)
        self.assertEqual(result.response.messages[-1].contents[0].text,
                         "valid model response")

    async def test_tpm_quota_wording_keeps_scope_and_long_wait_is_not_retried(self):
        seen = []

        async def handle(request):
            seen.append(request)
            return httpx2.Response(
                429,
                json={"error": {
                    "type": "rate_limit_exceeded",
                    "message": "Token quota exceeded for tokens per minute (TPM).",
                }},
                headers={
                    "retry-after": "42",
                    "x-ratelimit-remaining-requests": "997",
                    "x-ratelimit-remaining-tokens": "361",
                    "x-ratelimit-reset-requests": "4m19.2s",
                    "x-ratelimit-reset-tokens": "57.292s",
                },
            )

        provider = GroqProvider(sdk_client_factory=mock_sdk_factory(handle, []))
        with patch("rythusaathi.inference.providers.groq.asyncio.sleep",
                   new=AsyncMock()) as sleep:
            with self.assertRaises(ProviderFailure) as caught:
                await provider.complete(
                    InferenceRequest([Message("user", ["safe prompt"])]),
                    ProviderCredential(CANARY),
                )

        diagnostic = caught.exception.diagnostic
        self.assertEqual(len(seen), 1)
        self.assertEqual(sleep.await_count, 0)
        self.assertEqual(caught.exception.kind, ProviderFailureKind.RATE_LIMIT)
        self.assertEqual(diagnostic.code, InferenceDiagnosticCode.GROQ_RATE_LIMITED)
        self.assertEqual(diagnostic.rate_limit_scope, RateLimitScope.TOKENS_PER_MINUTE)
        self.assertEqual(diagnostic.retry_after_seconds, 42.0)
        self.assertEqual(diagnostic.rate_limit_remaining_requests, 997)
        self.assertEqual(diagnostic.rate_limit_remaining_tokens, 361)
        self.assertEqual(diagnostic.rate_limit_reset_requests_seconds, 259.2)
        self.assertEqual(diagnostic.rate_limit_reset_tokens_seconds, 57.292)
        self.assertEqual(diagnostic.retries_attempted, 0)
        with self.assertRaises(ProviderFailure) as blocked:
            await provider.complete(
                InferenceRequest(
                    [Message("user", ["safe prompt"])], agent_name="coordinator"
                ),
                ProviderCredential(CANARY),
            )
        self.assertEqual(len(seen), 1)
        self.assertEqual(blocked.exception.diagnostic.stage.value, "request_validation")
        self.assertEqual(blocked.exception.diagnostic.rate_limit_scope,
                         RateLimitScope.TOKENS_PER_MINUTE)
        self.assertGreater(blocked.exception.diagnostic.retry_after_seconds, 0)

    async def test_tpm_budget_rejects_oversized_input_before_provider_dispatch(self):
        created = []
        provider = GroqProvider(
            GroqProviderConfig(
                tpm_limit_tokens=300,
                tpm_headroom_tokens=100,
                default_max_output_tokens=128,
            ),
            sdk_client_factory=lambda *_args: created.append(True),
        )
        request = InferenceRequest(
            [Message("user", ["x" * 1000])],
            options={"max_tokens": 128},
            agent_name="crop",
            correlation_id="b" * 32,
        )
        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(request, ProviderCredential(CANARY))
        diagnostic = caught.exception.diagnostic
        self.assertEqual(created, [])
        self.assertEqual(diagnostic.code, InferenceDiagnosticCode.GROQ_TPM_BUDGET_EXCEEDED)
        self.assertEqual(diagnostic.agent_name, "crop")
        self.assertEqual(diagnostic.correlation_id, "b" * 32)
        self.assertGreater(diagnostic.estimated_input_tokens, 0)
        self.assertEqual(diagnostic.output_token_limit, 128)
        self.assertEqual(diagnostic.budget_limit_tokens, 200)
        self.assertEqual(diagnostic.budget_decision, "rejected")
        self.assertNotIn(CANARY, str(caught.exception))

    async def test_output_reservation_and_missing_usage_keep_estimate_in_rolling_budget(self):
        requests = []

        async def handle(request):
            requests.append(json.loads(request.content))
            reply = completion("ok")
            reply.pop("usage", None)
            return httpx2.Response(200, json=reply)

        provider = GroqProvider(
            GroqProviderConfig(
                tpm_limit_tokens=1500,
                tpm_headroom_tokens=500,
                default_max_output_tokens=600,
            ),
            sdk_client_factory=mock_sdk_factory(handle, []),
        )
        request = InferenceRequest(
            [Message("user", ["ordinary request"])],
            options={"max_tokens": 600},
            agent_name="intake",
        )
        result = await provider.complete(request, ProviderCredential(CANARY))
        output_key = ("max_tokens" if "max_tokens" in requests[0]
                      else "max_completion_tokens")
        self.assertEqual(requests[0][output_key], 600)
        self.assertIsNone(result.usage.actual_total_tokens)
        self.assertEqual(result.usage.budget_decision, "estimate_only")
        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(request, ProviderCredential(CANARY))
        self.assertEqual(len(requests), 1)
        self.assertEqual(caught.exception.diagnostic.code,
                         InferenceDiagnosticCode.GROQ_TPM_BUDGET_EXCEEDED)
        self.assertEqual(caught.exception.diagnostic.output_token_limit, 600)

    def test_conservative_token_estimator_counts_utf8_content(self):
        english = estimate_input_tokens([Message("user", ["rice advice"])])
        regional = estimate_input_tokens([Message("user", ["పంట సలహా"])])
        longer = estimate_input_tokens([Message("user", ["x" * 200])])
        self.assertGreater(english, 0)
        self.assertGreater(regional, 0)
        self.assertGreater(longer, english)

    async def test_rate_limit_retry_exhaustion_preserves_first_metadata_and_correlation(self):
        seen = []

        async def handle(request):
            seen.append(request)
            retry_after = "0.2" if len(seen) == 1 else "0.4"
            return httpx2.Response(
                429,
                json={"error": {"type": "rate_limit_exceeded", "message": CANARY}},
                headers={
                    "retry-after": retry_after,
                    "x-ratelimit-remaining-requests": "9",
                    "x-ratelimit-remaining-tokens": "999",
                    "x-ratelimit-reset-requests": "1h",
                    "x-ratelimit-reset-tokens": "3s",
                },
            )

        provider = GroqProvider(sdk_client_factory=mock_sdk_factory(handle, []))
        original_factory = groq_module._diagnostic_for_exception
        with patch("rythusaathi.inference.providers.groq.asyncio.sleep",
                   new=AsyncMock()) as sleep, patch(
                       "rythusaathi.inference.providers.groq.random.uniform",
                       return_value=0.0,
                   ), patch.object(
                       groq_module, "_diagnostic_for_exception",
                       wraps=original_factory,
                   ) as diagnostic_factory:
            with self.assertRaises(ProviderFailure) as caught:
                await provider.complete(
                    InferenceRequest([Message("user", ["safe prompt"])]),
                    ProviderCredential(CANARY),
                )
        diagnostic = caught.exception.diagnostic
        self.assertEqual(len(seen), 2)
        self.assertEqual(sleep.await_count, 1)
        self.assertEqual(caught.exception.kind, ProviderFailureKind.RATE_LIMIT)
        self.assertEqual(diagnostic.code, InferenceDiagnosticCode.GROQ_RATE_LIMITED)
        self.assertEqual(diagnostic.retries_attempted, 1)
        self.assertEqual(diagnostic.retry_after_seconds, 0.4)
        self.assertEqual(len(diagnostic.prior_rate_limits), 1)
        self.assertEqual(diagnostic.prior_rate_limits[0].retry_after_seconds, 0.2)
        correlation_ids = [call.kwargs["correlation_id"]
                           for call in diagnostic_factory.call_args_list]
        self.assertEqual(len(correlation_ids), 2)
        self.assertEqual(correlation_ids[0], correlation_ids[1])
        self.assertEqual(diagnostic.correlation_id, correlation_ids[0])
        self.assertNotIn(CANARY, repr(diagnostic))
        self.assertNotIn(CANARY, str(caught.exception))

    async def test_missing_or_malformed_rate_headers_are_unknown_and_not_retried(self):
        cases = (
            {},
            {
                "retry-after": "tomorrow",
                "x-ratelimit-remaining-requests": "NaN",
                "x-ratelimit-remaining-tokens": "-2",
                "x-ratelimit-reset-requests": "soon",
                "x-ratelimit-reset-tokens": "not-a-duration",
            },
        )
        for headers in cases:
            with self.subTest(headers=headers):
                seen = []

                async def handle(request, headers=headers):
                    seen.append(request)
                    return httpx2.Response(
                        429,
                        json={"error": {"type": "rate_limit_exceeded", "message": CANARY}},
                        headers=headers,
                    )

                provider = GroqProvider(sdk_client_factory=mock_sdk_factory(handle, []))
                with patch("rythusaathi.inference.providers.groq.asyncio.sleep",
                           new=AsyncMock()) as sleep:
                    with self.assertRaises(ProviderFailure) as caught:
                        await provider.complete(
                            InferenceRequest([Message("user", ["safe prompt"])]),
                            ProviderCredential(CANARY),
                        )
                diagnostic = caught.exception.diagnostic
                self.assertEqual(len(seen), 1)
                self.assertEqual(sleep.await_count, 0)
                self.assertEqual(diagnostic.code, InferenceDiagnosticCode.GROQ_RATE_LIMITED)
                self.assertEqual(diagnostic.rate_limit_scope, RateLimitScope.UNKNOWN)
                self.assertIsNone(diagnostic.retry_after_seconds)
                self.assertIsNone(diagnostic.rate_limit_remaining_requests)
                self.assertIsNone(diagnostic.rate_limit_remaining_tokens)
                self.assertIsNone(diagnostic.rate_limit_reset_requests_seconds)
                self.assertIsNone(diagnostic.rate_limit_reset_tokens_seconds)
                self.assertEqual(diagnostic.retries_attempted, 0)

    async def test_quota_exhaustion_and_permanent_failures_are_not_retried(self):
        for status, headers, expected_code in (
            (429, {
                "retry-after": "0.1",
                "x-ratelimit-remaining-requests": "0",
                "x-ratelimit-remaining-tokens": "500",
                "x-ratelimit-reset-requests": "22h",
                "x-ratelimit-reset-tokens": "1s",
            }, InferenceDiagnosticCode.GROQ_QUOTA_EXHAUSTED),
            (401, {"retry-after": "0.1"},
             InferenceDiagnosticCode.GROQ_AUTHENTICATION_FAILED),
        ):
            with self.subTest(status=status):
                seen = []

                async def handle(request, status=status, headers=headers):
                    seen.append(request)
                    body = {"error": {"type": "auth_error", "message": CANARY}}
                    if status == 429:
                        body = {"error": {"type": "rate_limit_exceeded", "message": "limit"}}
                    return httpx2.Response(status, json=body, headers=headers)

                provider = GroqProvider(sdk_client_factory=mock_sdk_factory(handle, []))
                with patch("rythusaathi.inference.providers.groq.asyncio.sleep",
                           new=AsyncMock()) as sleep:
                    with self.assertRaises(ProviderFailure) as caught:
                        await provider.complete(
                            InferenceRequest([Message("user", ["safe prompt"])]),
                            ProviderCredential(CANARY),
                        )
                self.assertEqual(len(seen), 1)
                self.assertEqual(sleep.await_count, 0)
                self.assertEqual(caught.exception.diagnostic.code, expected_code)
                self.assertNotIn(CANARY, repr(caught.exception.diagnostic))

    async def test_rate_limit_retry_is_disabled_when_request_has_tools(self):
        seen = []

        async def handle(request):
            seen.append(request)
            return httpx2.Response(
                429,
                json={"error": {"type": "rate_limit_exceeded", "message": "busy"}},
                headers={"retry-after": "0.01"},
            )

        def local_tool() -> str:
            """A tool that must not be replayed by transport retry."""
            return "safe"

        provider = GroqProvider(sdk_client_factory=mock_sdk_factory(handle, []))
        with patch("rythusaathi.inference.providers.groq.asyncio.sleep",
                   new=AsyncMock()) as sleep:
            with self.assertRaises(ProviderFailure):
                await provider.complete(
                    InferenceRequest(
                        [Message("user", ["safe prompt"])],
                        options={"tools": [local_tool]},
                    ),
                    ProviderCredential(CANARY),
                )
        self.assertEqual(len(seen), 1)
        self.assertEqual(sleep.await_count, 0)

    async def test_network_failure_has_safe_distinct_diagnostic(self):
        async def disconnected(request):
            raise httpx2.ConnectError(CANARY, request=request)

        clients = []
        provider = GroqProvider(sdk_client_factory=mock_sdk_factory(disconnected, [], clients))
        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(
                InferenceRequest([Message("user", ["safe request"])]) ,
                ProviderCredential(CANARY),
            )
        self.assertEqual(caught.exception.kind, ProviderFailureKind.NETWORK)
        self.assertEqual(caught.exception.diagnostic.code,
                         InferenceDiagnosticCode.GROQ_NETWORK_FAILURE)
        self.assertIsNone(caught.exception.diagnostic.http_status)
        self.assertNotIn(CANARY, repr(caught.exception.diagnostic))
        self.assertNotIn(CANARY, str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertTrue(clients[0].is_closed())

    async def test_config_reads_model_but_never_provider_key(self):
        with patch.dict(os.environ, {"GROQ_MODEL_ID": "configured-model", "GROQ_API_KEY": CANARY}, clear=True):
            config = GroqProviderConfig.from_environment()
        self.assertEqual(config.model_id, "configured-model")
        self.assertFalse(hasattr(config, "api_key"))
        self.assertNotIn(CANARY, repr(config))

    async def test_missing_user_credential_cannot_fall_back_to_environment(self):
        provider = GroqProvider()
        with patch.dict(os.environ, {"GROQ_API_KEY": CANARY}, clear=True):
            with self.assertRaises(TypeError):
                await provider.complete(InferenceRequest([Message("user", ["x"])]))

    async def test_none_credential_is_rejected_without_dispatch(self):
        dispatched = []

        async def handle(request):
            dispatched.append(request)
            return httpx2.Response(200, json=completion("unexpected"))

        provider = GroqProvider(sdk_client_factory=mock_sdk_factory(handle, []))
        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(
                InferenceRequest([Message("user", ["safe"])]),  # type: ignore[arg-type]
                None,  # type: ignore[arg-type]
            )
        self.assertEqual(caught.exception.kind, ProviderFailureKind.AUTHENTICATION)
        self.assertEqual(dispatched, [])

    async def test_sdk_client_construction_failure_is_sanitized(self):
        def factory(api_key, _base_url, _timeout):
            self.assertEqual(api_key, CANARY)
            raise RuntimeError(f"construction leaked {api_key}")

        provider = GroqProvider(sdk_client_factory=factory)
        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(
                InferenceRequest([Message("user", ["safe"])]), ProviderCredential(CANARY)
            )
        self.assertEqual(caught.exception.kind, ProviderFailureKind.PROVIDER_FAILURE)
        self.assertNotIn(CANARY, repr(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)

    async def test_malformed_chat_options_rejected_before_consuming_credential(self):
        from types import MappingProxyType

        invalid_options = (
            [],
            "temperature=0.2",
            MappingProxyType({"temperature": 0.2}),
            {1: "not a ChatOptions key"},
            {"made_up_option": True},
        )
        dispatched = []

        async def handle(request):
            dispatched.append(request)
            return httpx2.Response(200, json=completion("ok"))

        provider = GroqProvider(sdk_client_factory=mock_sdk_factory(handle, []))
        for invalid in invalid_options:
            with self.subTest(options_type=type(invalid).__name__, options=repr(invalid)):
                credential = ProviderCredential(CANARY)
                request = InferenceRequest(
                    [Message("user", ["safe"])], options=invalid  # type: ignore[arg-type]
                )
                with self.assertRaises(ProviderFailure) as caught:
                    await provider.complete(request, credential)
                self.assertEqual(caught.exception.kind, ProviderFailureKind.MALFORMED_RESPONSE)
                # Invalid ChatOptions do not consume the one-use credential.
                await provider.complete(InferenceRequest([Message("user", ["safe"])]), credential)
        self.assertEqual(len(dispatched), len(invalid_options))

    async def test_supported_function_tool_metadata_is_scanned_for_secret(self):
        def safe_lookup() -> str:
            """Return a fixture value."""
            return "fixture"

        tool = FunctionTool(
            name="safe_lookup",
            func=safe_lookup,
            additional_properties={"description": {"internal_note": CANARY}},
        )
        dispatched = []

        async def handle(request):
            dispatched.append(request)
            return httpx2.Response(200, json=completion("unexpected"))

        provider = GroqProvider(sdk_client_factory=mock_sdk_factory(handle, []))
        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(
                InferenceRequest([Message("user", ["safe"])], options={"tools": [tool]}),
                ProviderCredential(CANARY),
            )
        self.assertEqual(caught.exception.kind, ProviderFailureKind.SENSITIVE_INPUT)
        self.assertEqual(dispatched, [])

    async def test_tool_globals_are_recursively_scanned(self):
        for tool in (_tool_referencing_global_mapping, _tool_referencing_global_list):
            with self.subTest(tool=tool.__name__):
                self.assertTrue(contains_provider_secret(tool, CANARY))

                def factory(_api_key, _base_url, _timeout):
                    self.fail("provider client must not be constructed for a secret-bearing tool")

                provider = GroqProvider(sdk_client_factory=factory)
                with self.assertRaises(ProviderFailure) as caught:
                    await provider.complete(
                        InferenceRequest([Message("user", ["safe"])], options={"tools": [tool]}),
                        ProviderCredential(CANARY),
                    )
                self.assertEqual(caught.exception.kind, ProviderFailureKind.SENSITIVE_INPUT)

    async def test_empty_callable_closure_cell_does_not_raise(self):
        tool = _tool_with_empty_closure_cell()
        self.assertFalse(contains_provider_secret(tool, CANARY))

    async def test_supported_tool_metadata_inspection_failure_fails_closed(self):
        class BrokenMetadata(dict):
            def items(self):
                raise RuntimeError("fixture metadata inspection failure")

        def safe_lookup() -> str:
            return "fixture"

        tool = FunctionTool(name="safe_lookup", func=safe_lookup)
        tool.additional_properties = BrokenMetadata({"description": "safe"})
        self.assertTrue(contains_provider_secret(tool, CANARY))

    async def test_secret_in_prompt_options_or_tool_closure_is_blocked_before_dispatch(self):
        requests = []

        async def handle(request):
            requests.append(request)
            return httpx2.Response(200, json=completion("should not run"))

        provider = GroqProvider(sdk_client_factory=mock_sdk_factory(handle, []))

        def secret_tool_factory():
            captured = CANARY
            def tool() -> str:
                """Return a fixture value."""
                return captured
            return tool

        invalid_requests = [
            InferenceRequest([Message("user", [f"Question includes {CANARY}"]) ]),
            InferenceRequest([Message("user", ["safe"])], options={"metadata": {"note": CANARY}}),
            InferenceRequest([Message("user", ["safe"])], options={"headers": {"Authorization": CANARY}}),
            InferenceRequest([Message("user", ["safe"])], options={"tools": [secret_tool_factory()]}),
            InferenceRequest([Message("user", ["safe"])], options={"presence_penalty": 0}),
        ]
        for request in invalid_requests:
            with self.subTest(options=bool(request.options)):
                credential = ProviderCredential(CANARY)
                with self.assertRaises(ProviderFailure) as caught:
                    await provider.complete(request, credential)
                if request.options and request.options.get("presence_penalty") is not None:
                    self.assertEqual(caught.exception.kind, ProviderFailureKind.MALFORMED_RESPONSE)
                else:
                    self.assertEqual(caught.exception.kind, ProviderFailureKind.SENSITIVE_INPUT)
                self.assertNotIn(CANARY, str(caught.exception))
                # Caller validation occurs before the one-use credential is consumed.
                await provider.complete(InferenceRequest([Message("user", ["safe"])]), credential)
        self.assertEqual(len(requests), len(invalid_requests))

    async def test_config_rejects_nonfinite_and_invalid_values(self):
        for timeout in (0, -1, float("nan"), float("inf"), True, "30"):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                GroqProviderConfig(timeout_seconds=timeout)
        for model in (None, 1, "", "  "):
            with self.subTest(model=model), self.assertRaises(ValueError):
                GroqProviderConfig(model_id=model)
        for field in ("max_tool_iterations", "max_tool_calls"):
            for limit in (0, -1, 1.5, True):
                with self.subTest(field=field, limit=limit), self.assertRaises(ValueError):
                    GroqProviderConfig(**{field: limit})
        for field, value in (
            ("tpm_limit_tokens", 0),
            ("tpm_limit_tokens", True),
            ("tpm_headroom_tokens", -1),
            ("tpm_headroom_tokens", 8000),
            ("default_max_output_tokens", 0),
        ):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                GroqProviderConfig(**{field: value})

    async def test_nonsecret_tpm_budget_settings_load_from_environment(self):
        with patch.dict(os.environ, {
            "GROQ_TPM_LIMIT_TOKENS": "12000",
            "GROQ_TPM_HEADROOM_TOKENS": "1500",
            "GROQ_MAX_OUTPUT_TOKENS": "2048",
        }, clear=True):
            config = GroqProviderConfig.from_environment()
        self.assertEqual(config.tpm_limit_tokens, 12000)
        self.assertEqual(config.tpm_headroom_tokens, 1500)
        self.assertEqual(config.default_max_output_tokens, 2048)

    async def test_configurable_request_model_and_provider_neutral_options(self):
        observed = []

        async def handle(request):
            observed.append(json.loads(request.content))
            return httpx2.Response(200, json=completion("ok"))

        provider = GroqProvider(sdk_client_factory=mock_sdk_factory(handle, []))
        result = await provider.complete(
            InferenceRequest(
                [Message("user", ["safe"])],
                options={"model": "fixture/alternate-model", "temperature": 0.2, "max_tokens": 17},
            ),
            ProviderCredential(CANARY),
        )
        self.assertEqual(observed[0]["model"], "fixture/alternate-model")
        self.assertEqual(observed[0]["temperature"], 0.2)
        self.assertEqual(result.model_id, "fixture/alternate-model")

    async def test_invalid_request_is_rejected_before_transport_and_credential_consumption(self):
        dispatched = []

        async def handle(request):
            dispatched.append(request)
            return httpx2.Response(200, json=completion("ok"))

        provider = GroqProvider(sdk_client_factory=mock_sdk_factory(handle, []))
        credential = ProviderCredential(CANARY)
        invalid = InferenceRequest([Message("user", ["safe"])], options={"not_a_framework_option": 1})
        with self.assertRaises(ProviderFailure):
            await provider.complete(invalid, credential)
        self.assertEqual(dispatched, [])
        await provider.complete(InferenceRequest([Message("user", ["safe"])]), credential)
        self.assertEqual(len(dispatched), 1)

    async def test_generic_status_and_non_model_400_are_not_misclassified(self):
        for status, body in (
            (400, {"error": {"message": "The word model appears here, but not a code"}}),
            (500, {"error": {"message": CANARY, "type": "server_error"}}),
        ):
            async def handle(_request, status=status, body=body):
                return httpx2.Response(status, json=body)
            provider = GroqProvider(sdk_client_factory=mock_sdk_factory(handle, []))
            with self.assertRaises(ProviderFailure) as caught:
                await provider.complete(
                    InferenceRequest([Message("user", ["safe"])]), ProviderCredential(CANARY)
                )
            self.assertEqual(caught.exception.kind, ProviderFailureKind.PROVIDER_FAILURE)
            self.assertIsNone(caught.exception.__context__)
            self.assertIsNone(caught.exception.__cause__)

    async def test_cleanup_failure_is_safe_and_does_not_expose_chain(self):
        class CleanupFailingClient(AsyncOpenAI):
            async def close(self):
                await super().close()
                raise RuntimeError(CANARY)

        async def handle(request):
            return httpx2.Response(200, json=completion("ok"))

        def factory(api_key, base_url, timeout):
            return CleanupFailingClient(
                api_key=api_key,
                base_url=base_url,
                timeout=timeout,
                max_retries=0,
                http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle)),
            )

        provider = GroqProvider(sdk_client_factory=factory)
        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(
                InferenceRequest([Message("user", ["safe"])]), ProviderCredential(CANARY)
            )
        self.assertEqual(caught.exception.kind, ProviderFailureKind.PROVIDER_FAILURE)
        self.assertNotIn(CANARY, str(caught.exception))
        self.assertIsNone(caught.exception.__context__)
        self.assertIsNone(caught.exception.__cause__)

    async def test_credential_is_not_repr_or_serializable(self):
        credential = ProviderCredential(CANARY)
        self.assertNotIn(CANARY, repr(credential))
        self.assertNotIn(CANARY, str(credential))
        with self.assertRaises(TypeError):
            pickle.dumps(credential)

    async def test_credential_consumption_is_one_use_under_concurrency(self):
        credential = ProviderCredential(CANARY)

        def consume():
            try:
                return credential._consume_for_provider()
            except ProviderFailure:
                return None

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _index: consume(), range(8)))
        self.assertEqual(results.count(CANARY), 1)
        self.assertEqual(results.count(None), 7)

    async def test_unsupported_framework_content_fails_closed(self):
        class UnsupportedContent:
            pass

        message = Message("assistant", ["safe"])
        message.contents = [UnsupportedContent()]
        response = ChatResponse(messages=[message])
        with self.assertRaises(ProviderFailure) as caught:
            sanitized_chat_response(response, CANARY)
        self.assertEqual(caught.exception.kind, ProviderFailureKind.MALFORMED_RESPONSE)

        nested = ChatResponse(
            messages=[Message("assistant", [Content(type="function_call", arguments=UnsupportedContent())])]
        )
        with self.assertRaises(ProviderFailure) as caught:
            sanitized_chat_response(nested, CANARY)
        self.assertEqual(caught.exception.kind, ProviderFailureKind.MALFORMED_RESPONSE)

    async def test_enum_values_are_scrubbed(self):
        class SensitiveFinish(str, Enum):
            VALUE = CANARY

        safe = sanitized_chat_response(
            ChatResponse(messages=[Message("assistant", ["safe"])], finish_reason=SensitiveFinish.VALUE),
            CANARY,
        )
        self.assertEqual(safe.finish_reason, "[REDACTED]")

    async def test_pydantic_structured_response_uses_public_dump_and_validate(self):
        class StructuredAnswer(BaseModel):
            summary: str

        response = ChatResponse(
            messages=[Message("assistant", ["safe"])],
            value=StructuredAnswer(summary=CANARY),
        )
        safe = sanitized_chat_response(response, CANARY)
        self.assertIsInstance(safe.value, StructuredAnswer)
        self.assertEqual(safe.value.summary, "[REDACTED]")

    async def test_cancellation_propagates(self):
        started = asyncio.Event()
        forever = asyncio.Event()

        async def blocking(_request):
            started.set()
            await forever.wait()
            return httpx2.Response(200, json=completion("unreachable"))

        clients = []
        provider = GroqProvider(sdk_client_factory=mock_sdk_factory(blocking, [], clients))
        task = asyncio.create_task(
            provider.complete(InferenceRequest([Message("user", ["x"])]), ProviderCredential(CANARY))
        )
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(clients[0].is_closed())

    def test_concurrent_reservations_are_thread_safe(self):
        """Concurrent reservations from multiple threads must be thread-safe and adhere to TPM limit."""
        provider = GroqProvider(GroqProviderConfig(tpm_limit_tokens=2000, tpm_headroom_tokens=500))
        # Usable limit is 1500 tokens
        tokens_per_call = 100
        num_threads = 25

        def reserve():
            return provider.reserve_budget(tokens_per_call)

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            results = list(executor.map(lambda _: reserve(), range(num_threads)))

        successful = [r for r in results if r is not None]
        rejected = [r for r in results if r is None]

        # Max successful reservations is limit // tokens_per_call = 15
        self.assertEqual(len(successful), 15)
        self.assertEqual(len(rejected), 10)
        self.assertEqual(provider._remaining_budget(), 0)

    async def test_settlement_on_failure_removes_reservation(self):
        """When an inference request fails, its preflight reservation must be removed, not left lingering."""
        async def fail_call(_request):
            raise httpx2.ConnectError("connection failed")

        provider = GroqProvider(
            GroqProviderConfig(tpm_limit_tokens=3000, tpm_headroom_tokens=500),
            sdk_client_factory=mock_sdk_factory(fail_call, []),
        )
        initial_remaining = provider._remaining_budget()
        self.assertEqual(initial_remaining, 2500)

        with self.assertRaises(ProviderFailure):
            await provider.complete(
                InferenceRequest([Message("user", ["test prompt"])]),
                ProviderCredential(CANARY),
            )

        # After failure, reservation must be completely removed from rolling budget events
        self.assertEqual(provider._remaining_budget(), initial_remaining)
        self.assertEqual(len(provider._budget_events), 0)

    def test_reserve_and_release_budget_lifecycle(self):
        """Explicit reservation and release must accurately reflect in remaining budget."""
        provider = GroqProvider(GroqProviderConfig(tpm_limit_tokens=5000, tpm_headroom_tokens=1000))
        usable = 4000
        self.assertEqual(provider._remaining_budget(), usable)

        res1 = provider.reserve_budget(1200)
        self.assertIsNotNone(res1)
        self.assertEqual(provider._remaining_budget(), usable - 1200)

        res2 = provider.reserve_budget(2000)
        self.assertIsNotNone(res2)
        self.assertEqual(provider._remaining_budget(), usable - 3200)

        # Releasing res1 restores 1200 tokens immediately
        provider.release_reservation(res1)
        self.assertEqual(provider._remaining_budget(), usable - 2000)

        # Releasing res2 restores the rest
        provider.release_reservation(res2)
        self.assertEqual(provider._remaining_budget(), usable)

        # Invalid reservation requests return None
        self.assertIsNone(provider.reserve_budget(0))
        self.assertIsNone(provider.reserve_budget(-50))
        self.assertIsNone(provider.reserve_budget(usable + 1))
        # Releasing None is safe no-op
        provider.release_reservation(None)
        self.assertEqual(provider._remaining_budget(), usable)

    async def test_insufficient_budget_local_rejection_without_network_dispatch(self):
        """When TPM budget is exhausted, request is rejected locally before dispatching to provider."""
        calls = []

        async def handler(request):
            calls.append(request)
            return httpx2.Response(200, json=completion("should never happen"))

        provider = GroqProvider(
            GroqProviderConfig(tpm_limit_tokens=2000, tpm_headroom_tokens=500),
            sdk_client_factory=mock_sdk_factory(handler, []),
        )
        # Usable budget is 1500 tokens. Reserve 1400 tokens.
        provider.reserve_budget(1400)
        self.assertEqual(provider._remaining_budget(), 100)

        # A request needing > 100 tokens must fail locally
        request = InferenceRequest(
            [Message("user", ["sufficiently long prompt to exceed 100 budget cost"])],
            options={"max_tokens": 128},
        )
        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(request, ProviderCredential(CANARY))

        self.assertEqual(caught.exception.diagnostic.code, InferenceDiagnosticCode.GROQ_TPM_BUDGET_EXCEEDED)
        self.assertEqual(len(calls), 0)  # Zero network calls made

    def test_budget_boundary_conditions_and_expiration(self):
        """Test boundary conditions: exact budget match, zero remaining, and 60s expiration."""
        provider = GroqProvider(GroqProviderConfig(tpm_limit_tokens=2000, tpm_headroom_tokens=500))
        usable = 1500

        # Exact budget match: reserve exactly usable budget
        res_exact = provider.reserve_budget(usable)
        self.assertIsNotNone(res_exact)
        self.assertEqual(provider._remaining_budget(), 0)

        # Zero remaining rejects any further reservation
        self.assertIsNone(provider.reserve_budget(1))

        # Test expiration: mock time advancing past 60.1 seconds
        current_time = time.monotonic()
        with patch("time.monotonic", return_value=current_time + 61.0):
            # All past events expired, budget is fully restored
            self.assertEqual(provider._remaining_budget(), usable)
            res_new = provider.reserve_budget(500)
            self.assertIsNotNone(res_new)
            self.assertEqual(provider._remaining_budget(), usable - 500)


if __name__ == "__main__":
    unittest.main(verbosity=2)
