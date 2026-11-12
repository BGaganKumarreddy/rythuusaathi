from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import io
import json
import logging
import os
import unittest
from unittest.mock import patch

import httpx2
from agent_framework import ChatResponse, Content, Message
from openai import AsyncOpenAI
from pydantic import BaseModel

from rythusaathi.credentials import CredentialService, WindowsCredentialStore
from rythusaathi.inference import (
    InferenceDiagnostic,
    InferenceDiagnosticCode,
    InferenceRequest,
    InferenceStage,
    ProviderCredential,
    ProviderFailure,
    ProviderFailureKind,
    RateLimitScope,
)
from rythusaathi.inference.contracts import contains_provider_secret, sanitized_chat_response
from rythusaathi.inference.providers.cerebras import (
    CEREBRAS_BASE_URL,
    DEFAULT_MODEL_ID,
    CerebrasProvider,
    CerebrasProviderConfig,
    estimate_cerebras_input_tokens,
    extract_cerebras_usage_counts,
)
from rythusaathi.inference.service import (
    AuthenticatedCerebrasService,
    AuthenticatedInferenceService,
)
from rythusaathi.agents.llm import (
    AuthenticatedCerebrasInference,
    CoordinatorDraft,
    GroundedStatement,
    call_structured,
    make_json_request,
)
from rythusaathi.local_auth import LocalAuthentication


CANARY = "synthetic-" + "cerebras-" + "credential-canary"


def completion(content: str | None, *, tool_calls=None):
    message = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-cerebras-fixture",
        "object": "chat.completion",
        "created": 1,
        "model": DEFAULT_MODEL_ID,
        "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if tool_calls else "stop"}],
        "usage": {"prompt_tokens": 15, "completion_tokens": 8, "total_tokens": 23},
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


class CerebrasProviderTests(unittest.IsolatedAsyncioTestCase):
    def test_configuration_and_defaults(self):
        config = CerebrasProviderConfig()
        self.assertEqual(config.model_id, "gpt-oss-120b")
        self.assertEqual(config.timeout_seconds, 30.0)
        self.assertEqual(config.tpm_limit_tokens, 30_000)
        self.assertEqual(config.tpm_headroom_tokens, 3_000)
        self.assertEqual(config.default_max_output_tokens, 1024)

        # Fail closed on invalid configurations
        with self.assertRaises(ValueError):
            CerebrasProviderConfig(model_id="")
        with self.assertRaises(ValueError):
            CerebrasProviderConfig(timeout_seconds=-1.0)
        with self.assertRaises(ValueError):
            CerebrasProviderConfig(tpm_limit_tokens=0)
        with self.assertRaises(ValueError):
            CerebrasProviderConfig(tpm_limit_tokens=1000, tpm_headroom_tokens=1000)
        with self.assertRaises(ValueError):
            CerebrasProviderConfig(default_max_output_tokens=0)

    def test_configuration_from_environment(self):
        env = {
            "CEREBRAS_MODEL_ID": "custom-cerebras-model",
            "CEREBRAS_TIMEOUT_SECONDS": "45.5",
            "CEREBRAS_TPM_LIMIT_TOKENS": "50000",
            "CEREBRAS_TPM_HEADROOM_TOKENS": "5000",
            "CEREBRAS_MAX_OUTPUT_TOKENS": "2048",
        }
        with patch.dict(os.environ, env):
            config = CerebrasProviderConfig.from_environment()
            self.assertEqual(config.model_id, "custom-cerebras-model")
            self.assertEqual(config.timeout_seconds, 45.5)
            self.assertEqual(config.tpm_limit_tokens, 50000)
            self.assertEqual(config.tpm_headroom_tokens, 5000)
            self.assertEqual(config.default_max_output_tokens, 2048)

    async def test_successful_cerebras_inference_and_usage_extraction(self):
        seen = []

        async def handle(request):
            body = json.loads(request.content)
            seen.append(body)
            return httpx2.Response(200, json=completion("Cerebras synthesis response."))

        captures = []
        clients = []
        provider = CerebrasProvider(
            CerebrasProviderConfig(model_id="gpt-oss-120b"),
            sdk_client_factory=mock_sdk_factory(handle, captures, clients),
        )
        credential = ProviderCredential(CANARY)
        result = await provider.complete(
            InferenceRequest(
                [Message("user", ["Recommend pest management for cotton."])],
                agent_name="crop",
                correlation_id="a" * 32,
            ),
            credential,
        )

        self.assertEqual(result.provider, "cerebras")
        self.assertEqual(result.model_id, "gpt-oss-120b")
        self.assertEqual(result.response.messages[-1].contents[0].text, "Cerebras synthesis response.")
        self.assertIsNotNone(result.usage)
        self.assertEqual(result.usage.actual_input_tokens, 15)
        self.assertEqual(result.usage.actual_output_tokens, 8)
        self.assertEqual(result.usage.actual_total_tokens, 23)
        self.assertEqual(result.usage.retries_attempted, 0)
        self.assertEqual(result.usage.retry_decision, "not_retried")

        # Captured URL is Cerebras base URL
        self.assertEqual(captures[0]["base_url"], CEREBRAS_BASE_URL)
        self.assertTrue(captures[0]["auth_seen"])

    async def test_authentication_failure_http_401(self):
        async def handle(_request):
            return httpx2.Response(
                401,
                json={"error": {"message": f"Invalid API key: {CANARY}", "type": "authentication_error"}},
            )

        provider = CerebrasProvider(sdk_client_factory=mock_sdk_factory(handle, []))
        credential = ProviderCredential(CANARY)

        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(
                InferenceRequest([Message("user", ["Hello"])]),
                credential,
            )

        self.assertEqual(caught.exception.kind, ProviderFailureKind.AUTHENTICATION)
        self.assertEqual(caught.exception.diagnostic.code, InferenceDiagnosticCode.CEREBRAS_AUTHENTICATION_FAILED)
        self.assertEqual(caught.exception.diagnostic.http_status, 401)
        self.assertEqual(caught.exception.diagnostic.provider, "cerebras")
        # Ensure secret canary is scrubbed and never present in exception or diagnostic
        self.assertNotIn(CANARY, str(caught.exception))
        self.assertNotIn(CANARY, caught.exception.diagnostic.provider_error_message or "")

    async def test_payment_billing_error_http_402(self):
        async def handle(_request):
            return httpx2.Response(
                402,
                json={"error": {"message": "Payment required: account credits exhausted", "code": "insufficient_quota"}},
            )

        provider = CerebrasProvider(sdk_client_factory=mock_sdk_factory(handle, []))
        credential = ProviderCredential(CANARY)

        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(
                InferenceRequest([Message("user", ["Hello"])]),
                credential,
            )

        self.assertEqual(caught.exception.kind, ProviderFailureKind.PAYMENT_REQUIRED)
        self.assertEqual(caught.exception.diagnostic.code, InferenceDiagnosticCode.CEREBRAS_PAYMENT_REQUIRED)
        self.assertEqual(caught.exception.diagnostic.http_status, 402)
        self.assertEqual(caught.exception.diagnostic.stage, InferenceStage.PROVIDER_RESPONSE)
        self.assertEqual(caught.exception.diagnostic.provider, "cerebras")

    async def test_rate_limit_response_http_429(self):
        async def handle(_request):
            return httpx2.Response(
                429,
                headers={
                    "retry-after": "5",
                    "x-ratelimit-remaining-tokens": "0",
                    "x-ratelimit-reset-tokens": "5s",
                },
                json={"error": {"message": "Rate limit reached for TPM", "type": "tokens_per_minute"}},
            )

        provider = CerebrasProvider(sdk_client_factory=mock_sdk_factory(handle, []))
        credential = ProviderCredential(CANARY)

        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(
                InferenceRequest([Message("user", ["Hello"])]),
                credential,
            )

        self.assertEqual(caught.exception.kind, ProviderFailureKind.RATE_LIMIT)
        self.assertEqual(caught.exception.diagnostic.code, InferenceDiagnosticCode.CEREBRAS_RATE_LIMITED)
        self.assertEqual(caught.exception.diagnostic.http_status, 429)
        self.assertEqual(caught.exception.diagnostic.retry_after_seconds, 5.0)
        self.assertEqual(caught.exception.diagnostic.retries_attempted, 0)
        self.assertEqual(caught.exception.diagnostic.retry_decision, "not_retried")

    async def test_server_errors_and_gateway_failures(self):
        cases = [
            (500, InferenceDiagnosticCode.CEREBRAS_PROVIDER_FAILURE),
            (502, InferenceDiagnosticCode.CEREBRAS_BAD_GATEWAY),
            (503, InferenceDiagnosticCode.CEREBRAS_SERVICE_UNAVAILABLE),
            (504, InferenceDiagnosticCode.CEREBRAS_GATEWAY_TIMEOUT),
        ]
        for status, expected_code in cases:
            with self.subTest(status=status):
                async def handle(_request, st=status):
                    return httpx2.Response(st, json={"error": {"message": "Server error"}})

                provider = CerebrasProvider(sdk_client_factory=mock_sdk_factory(handle, []))
                credential = ProviderCredential(CANARY)

                with self.assertRaises(ProviderFailure) as caught:
                    await provider.complete(
                        InferenceRequest([Message("user", ["Hello"])]),
                        credential,
                    )

                self.assertEqual(caught.exception.diagnostic.code, expected_code)
                self.assertEqual(caught.exception.diagnostic.http_status, status)

    async def test_timeout_classification(self):
        async def handle(_request):
            raise httpx2.ReadTimeout("Read timed out after 30s")

        provider = CerebrasProvider(sdk_client_factory=mock_sdk_factory(handle, []))
        credential = ProviderCredential(CANARY)

        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(
                InferenceRequest([Message("user", ["Hello"])]),
                credential,
            )

        self.assertEqual(caught.exception.kind, ProviderFailureKind.TIMEOUT)
        self.assertIn(
            caught.exception.diagnostic.code,
            {InferenceDiagnosticCode.CEREBRAS_TIMEOUT, InferenceDiagnosticCode.CEREBRAS_READ_TIMEOUT},
        )

    async def test_invalid_structured_response_malformed(self):
        async def handle(_request):
            return httpx2.Response(200, text="not valid json {{{", headers={"content-type": "application/json"})

        provider = CerebrasProvider(sdk_client_factory=mock_sdk_factory(handle, []))
        credential = ProviderCredential(CANARY)

        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(
                InferenceRequest([Message("user", ["Hello"])]),
                credential,
            )

        self.assertEqual(caught.exception.kind, ProviderFailureKind.MALFORMED_RESPONSE)
        self.assertEqual(caught.exception.diagnostic.code, InferenceDiagnosticCode.CEREBRAS_MALFORMED_RESPONSE)

    async def test_credential_isolation_and_no_leakage(self):
        credential = ProviderCredential(CANARY)
        provider = CerebrasProvider()

        # Reject prompt containing sensitive canary
        bad_request = InferenceRequest([Message("user", [f"Leak: {CANARY}"] )])
        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(bad_request, credential)
        self.assertEqual(caught.exception.kind, ProviderFailureKind.SENSITIVE_INPUT)

        # Consumed credential cannot be reused
        self.assertEqual(credential._consume_for_provider(), CANARY)
        with self.assertRaises(ProviderFailure):
            credential._consume_for_provider()

    def test_independent_budget_accounting_and_reservation(self):
        config = CerebrasProviderConfig(tpm_limit_tokens=30_000, tpm_headroom_tokens=3_000)
        provider = CerebrasProvider(config)

        # Usable budget is 27,000
        self.assertEqual(provider._remaining_budget(), 27_000)

        # Reserve 10,000
        res = provider.reserve_budget(10_000)
        self.assertIsNotNone(res)
        self.assertEqual(provider._remaining_budget(), 17_000)

        # Settle with actual 5,000
        provider._settle_budget(res, 5_000)
        self.assertEqual(provider._remaining_budget(), 22_000)

        # Release reservation completely
        provider.release_reservation(res)
        self.assertEqual(provider._remaining_budget(), 27_000)

    async def test_budget_exceeded_rejects_locally(self):
        # Configure small budget: 2000 total, 500 headroom -> 1500 usable
        config = CerebrasProviderConfig(
            tpm_limit_tokens=2000,
            tpm_headroom_tokens=500,
            default_max_output_tokens=1000,
        )
        provider = CerebrasProvider(config)
        credential = ProviderCredential(CANARY)

        # Message with 800+ estimated input + 1000 output = 1800 > 1500 usable
        long_text = "This is a long repetitive prompt. " * 100
        req = InferenceRequest([Message("user", [long_text])], options={"max_tokens": 1000})

        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(req, credential)

        self.assertEqual(caught.exception.kind, ProviderFailureKind.RATE_LIMIT)
        self.assertEqual(caught.exception.diagnostic.code, InferenceDiagnosticCode.CEREBRAS_TPM_BUDGET_EXCEEDED)
        self.assertEqual(caught.exception.diagnostic.budget_decision, "rejected")

    def test_concurrent_budget_reservations(self):
        config = CerebrasProviderConfig(tpm_limit_tokens=30_000, tpm_headroom_tokens=3_000)
        provider = CerebrasProvider(config)

        def make_reservations():
            res = provider.reserve_budget(100)
            if res is not None:
                provider.release_reservation(res)
                return True
            return False

        with ThreadPoolExecutor(max_workers=8) as ex:
            results = list(ex.map(lambda _: make_reservations(), range(100)))

        self.assertTrue(all(results))
        self.assertEqual(provider._remaining_budget(), 27_000)

    async def test_provider_selection_and_service_integration(self):
        class MockAuth:
            def authenticate(self, _token):
                class Id:
                    subject = "u1"
                    tenant_id = "t1"
                return Id()

        class MockCredService:
            def dispatch_handoff(self, _identity, _cid, callback, expected_provider_id=None):
                if expected_provider_id != "cerebras":
                    raise ValueError(f"Wrong provider: {expected_provider_id}")
                return callback(CANARY, 1)

        async def handle(_request):
            return httpx2.Response(200, json=completion("Cerebras service ok."))

        provider = CerebrasProvider(sdk_client_factory=mock_sdk_factory(handle, []))
        service = AuthenticatedCerebrasService(MockAuth(), MockCredService(), provider)
        self.assertEqual(service.provider_id, "cerebras")
        self.assertEqual(service.model_id, "gpt-oss-120b")

        # Wrong provider rejected by AuthenticatedCerebrasService
        class FakeGroqProvider:
            provider_id = "groq"
        with self.assertRaises(ValueError):
            AuthenticatedCerebrasService(MockAuth(), MockCredService(), FakeGroqProvider())

        # Complete request through AuthenticatedCerebrasInference
        inference = AuthenticatedCerebrasInference(service, "session-token", "cred-id")
        self.assertEqual(inference.provider_id, "cerebras")
        self.assertEqual(inference.model_id, "gpt-oss-120b")

        req = InferenceRequest([Message("user", ["Synthesize"] )])
        resp = await inference.complete(req, expected_user_id="u1", expected_tenant_id="t1")
        self.assertEqual(resp.provider, "cerebras")
        self.assertEqual(inference.dispatched_count, 1)
        self.assertEqual(len(inference.usage_records), 1)
        self.assertEqual(inference.usage_records[0].actual_total_tokens, 23)

    async def test_invalid_model_error_400_and_404(self):
        cases = [
            (400, {"error": {"message": "Model not found", "code": "model_not_found"}}),
            (404, {"error": {"message": "Invalid model", "type": "model_not_found"}}),
        ]
        for status, body in cases:
            with self.subTest(status=status):
                async def handle(_request, st=status, b=body):
                    return httpx2.Response(st, json=b)

                provider = CerebrasProvider(sdk_client_factory=mock_sdk_factory(handle, []))
                credential = ProviderCredential(CANARY)

                with self.assertRaises(ProviderFailure) as caught:
                    await provider.complete(
                        InferenceRequest([Message("user", ["Hello"])]),
                        credential,
                    )

                self.assertEqual(caught.exception.kind, ProviderFailureKind.INVALID_MODEL)
                self.assertEqual(caught.exception.diagnostic.code, InferenceDiagnosticCode.CEREBRAS_INVALID_MODEL)

    async def test_no_automatic_retries_on_rate_limit(self):
        call_count = 0

        async def handle(_request):
            nonlocal call_count
            call_count += 1
            return httpx2.Response(429, json={"error": {"message": "rate limit"}})

        provider = CerebrasProvider(sdk_client_factory=mock_sdk_factory(handle, []))
        credential = ProviderCredential(CANARY)

        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(
                InferenceRequest([Message("user", ["Hello"])]),
                credential,
            )

        self.assertEqual(call_count, 1, "Cerebras provider must make zero retries")
        self.assertEqual(caught.exception.kind, ProviderFailureKind.RATE_LIMIT)
        self.assertEqual(caught.exception.diagnostic.retries_attempted, 0)

    async def test_unsafe_and_unsupported_options_rejected(self):
        provider = CerebrasProvider()
        credential = ProviderCredential(CANARY)

        # Unsafe option (api_key)
        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(
                InferenceRequest([Message("user", ["Hello"])], options={"api_key": "injected"}),
                credential,
            )
        self.assertEqual(caught.exception.kind, ProviderFailureKind.SENSITIVE_INPUT)

        # Unsupported option (presence_penalty)
        with self.assertRaises(ProviderFailure) as caught:
            await provider.complete(
                InferenceRequest([Message("user", ["Hello"])], options={"presence_penalty": 0.5}),
                credential,
            )
        self.assertEqual(caught.exception.kind, ProviderFailureKind.MALFORMED_RESPONSE)

    async def test_structured_output_format_mapping(self):
        captured_options = []

        async def handle(request):
            body = json.loads(request.content)
            captured_options.append(body.get("response_format"))
            return httpx2.Response(200, json=completion('{"result":"ok"}'))

        provider = CerebrasProvider(sdk_client_factory=mock_sdk_factory(handle, []))
        credential = ProviderCredential(CANARY)

        class OutputSchema(BaseModel):
            result: str

        req = InferenceRequest(
            [Message("user", ["Give JSON"])],
            options={"response_format": {"type": "json_schema", "json_schema": {"name": "test"}}},
        )
        await provider.complete(req, credential)
        self.assertEqual(captured_options[0], {"type": "json_object"})

    async def test_groq_diagnostic_codes_translated_to_cerebras(self):
        groq_codes_to_test = [
            (InferenceDiagnosticCode.GROQ_MALFORMED_RESPONSE, InferenceDiagnosticCode.CEREBRAS_MALFORMED_RESPONSE),
            (InferenceDiagnosticCode.GROQ_TIMEOUT, InferenceDiagnosticCode.CEREBRAS_TIMEOUT),
            (InferenceDiagnosticCode.GROQ_NETWORK_FAILURE, InferenceDiagnosticCode.CEREBRAS_NETWORK_FAILURE),
            (InferenceDiagnosticCode.GROQ_RATE_LIMITED, InferenceDiagnosticCode.CEREBRAS_RATE_LIMITED),
            (InferenceDiagnosticCode.GROQ_PROVIDER_FAILURE, InferenceDiagnosticCode.CEREBRAS_PROVIDER_FAILURE),
        ]
        for groq_code, expected_cerebras_code in groq_codes_to_test:
            with self.subTest(code=groq_code.value):
                async def handle(_request):
                    return httpx2.Response(200, json=completion("ok"))

                provider = CerebrasProvider(sdk_client_factory=mock_sdk_factory(handle, []))
                credential = ProviderCredential(CANARY)

                with patch(
                    "rythusaathi.inference.providers.cerebras.sanitized_chat_response",
                    side_effect=ProviderFailure(
                        ProviderFailureKind.MALFORMED_RESPONSE,
                        InferenceDiagnostic(code=groq_code, stage=InferenceStage.RESPONSE_VALIDATION),
                    ),
                ):
                    with self.assertRaises(ProviderFailure) as caught:
                        await provider.complete(
                            InferenceRequest([Message("user", ["hi"])]),
                            credential,
                        )
                    self.assertEqual(caught.exception.diagnostic.code, expected_cerebras_code)
                    self.assertEqual(caught.exception.diagnostic.provider, "cerebras")

    async def test_usage_metadata_survives_response_validation_failures(self):
        async def handle(_request):
            return httpx2.Response(200, json={
                "id": "chatcmpl-test-usage",
                "object": "chat.completion",
                "created": 1,
                "model": DEFAULT_MODEL_ID,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "bad"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 25, "total_tokens": 75},
            })

        provider = CerebrasProvider(sdk_client_factory=mock_sdk_factory(handle, []))
        credential = ProviderCredential(CANARY)

        with patch(
            "rythusaathi.inference.providers.cerebras.sanitized_chat_response",
            side_effect=ProviderFailure(ProviderFailureKind.MALFORMED_RESPONSE),
        ):
            with self.assertRaises(ProviderFailure) as caught:
                await provider.complete(
                    InferenceRequest([Message("user", ["hi"])]),
                    credential,
                )
            diag = caught.exception.diagnostic
            self.assertEqual(diag.code, InferenceDiagnosticCode.CEREBRAS_MALFORMED_RESPONSE)
            self.assertEqual(diag.provider, "cerebras")
            self.assertEqual(diag.http_status, 200)
            self.assertEqual(diag.request_id, "chatcmpl-test-usage")
            self.assertEqual(diag.finish_reason, "stop")

    async def test_call_structured_with_fenced_json(self):
        fenced_json = (
            "```json\n"
            "{\n"
            '  "statements": [\n'
            '    {"text": "Observed leaf spot.", "kind": "evidence", "evidence_ids": ["ev1"], "uncertainty": null}\n'
            "  ],\n"
            '  "follow_up_questions": []\n'
            "}\n"
            "```"
        )
        async def handle(_request):
            return httpx2.Response(200, json={
                "id": "chatcmpl-fenced",
                "object": "chat.completion",
                "created": 1,
                "model": DEFAULT_MODEL_ID,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": fenced_json}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 40, "completion_tokens": 30, "total_tokens": 70},
            })

        provider = CerebrasProvider(sdk_client_factory=mock_sdk_factory(handle, []))
        
        class MockInference:
            model_id = DEFAULT_MODEL_ID
            usage_records = []
            async def complete(self, req, expected_user_id, expected_tenant_id):
                resp = await provider.complete(req, ProviderCredential(CANARY))
                self.usage_records.append(resp.usage)
                return resp

        inf = MockInference()
        req = make_json_request(
            system_prompt="Test prompt",
            payload={"test": "payload"},
            response_model=CoordinatorDraft,
            agent_name="coordinator",
            max_output_tokens=1024,
        )
        draft = await call_structured(
            inf,
            req,
            expected_user_id="user1",
            expected_tenant_id="tenant1",
            response_model=CoordinatorDraft,
        )
        self.assertIsInstance(draft, CoordinatorDraft)
        self.assertEqual(len(draft.statements), 1)
        self.assertEqual(draft.statements[0].text, "Observed leaf spot.")
        self.assertEqual(draft.statements[0].evidence_ids, ["ev1"])

    async def test_call_structured_with_invalid_coordinator_json(self):
        async def handle(_request):
            return httpx2.Response(200, json={
                "id": "chatcmpl-invalid-json",
                "object": "chat.completion",
                "created": 1,
                "model": DEFAULT_MODEL_ID,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "not json at all"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
            })

        provider = CerebrasProvider(sdk_client_factory=mock_sdk_factory(handle, []))
        
        class MockInference:
            model_id = DEFAULT_MODEL_ID
            usage_records = []
            async def complete(self, req, expected_user_id, expected_tenant_id):
                resp = await provider.complete(req, ProviderCredential(CANARY))
                self.usage_records.append(resp.usage)
                return resp

        inf = MockInference()
        req = make_json_request(
            system_prompt="Test prompt",
            payload={"test": "payload"},
            response_model=CoordinatorDraft,
            agent_name="coordinator",
            max_output_tokens=1024,
        )
        with self.assertRaises(ProviderFailure) as caught:
            await call_structured(
                inf,
                req,
                expected_user_id="user1",
                expected_tenant_id="tenant1",
                response_model=CoordinatorDraft,
            )
        diag = caught.exception.diagnostic
        self.assertEqual(diag.code, InferenceDiagnosticCode.CEREBRAS_MALFORMED_RESPONSE)
        self.assertEqual(diag.stage, InferenceStage.RESPONSE_VALIDATION)
        self.assertEqual(diag.parsing_outcome, "json_decode_failed")
        self.assertEqual(diag.http_status, 200)

    async def test_call_structured_with_schema_invalid_coordinator_json(self):
        async def handle(_request):
            return httpx2.Response(200, json={
                "id": "chatcmpl-invalid-schema",
                "object": "chat.completion",
                "created": 1,
                "model": DEFAULT_MODEL_ID,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": '{"invalid": "schema"}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
            })

        provider = CerebrasProvider(sdk_client_factory=mock_sdk_factory(handle, []))
        
        class MockInference:
            model_id = DEFAULT_MODEL_ID
            usage_records = []
            async def complete(self, req, expected_user_id, expected_tenant_id):
                resp = await provider.complete(req, ProviderCredential(CANARY))
                self.usage_records.append(resp.usage)
                return resp

        inf = MockInference()
        req = make_json_request(
            system_prompt="Test prompt",
            payload={"test": "payload"},
            response_model=CoordinatorDraft,
            agent_name="coordinator",
            max_output_tokens=1024,
        )
        with self.assertRaises(ProviderFailure) as caught:
            await call_structured(
                inf,
                req,
                expected_user_id="user1",
                expected_tenant_id="tenant1",
                response_model=CoordinatorDraft,
            )
        diag = caught.exception.diagnostic
        self.assertEqual(diag.code, InferenceDiagnosticCode.CEREBRAS_MALFORMED_RESPONSE)
        self.assertEqual(diag.stage, InferenceStage.RESPONSE_VALIDATION)
        self.assertEqual(diag.parsing_outcome, "pydantic_validation_failed")
        self.assertEqual(diag.http_status, 200)



if __name__ == "__main__":
    unittest.main()
