from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import httpx2
from agent_framework import Message
from openai import AsyncOpenAI

from rythusaathi.agents import (
    AuthenticatedGroqInference,
    FarmerRequest,
    RythuSaathiWorkflow,
    empty_coverage_resolver,
)

from rythusaathi.credentials import CredentialService, CredentialUnavailable
from rythusaathi.inference import (InferenceDiagnosticCode, InferenceRequest,
                                   ProviderFailure, ProviderFailureKind)
from rythusaathi.inference.providers.groq import (DEFAULT_MODEL_ID, GROQ_BASE_URL,
                                                  GroqProvider, GroqProviderConfig)
from rythusaathi.inference.service import AuthenticatedGroqService
from rythusaathi.local_auth import LocalAuthentication
from rythusaathi.security import IdentityRole


class MemoryKeys:
    def __init__(self):
        self.value = None

    def get_or_create_key(self, version):
        if self.value is None:
            import secrets
            self.value = secrets.token_bytes(32)
        return self.value

    def get_key(self, version):
        if self.value is None:
            raise RuntimeError("missing test KEK")
        return self.value


def _completion(text: str):
    return {
        "id": "chatcmpl-integration-fixture", "object": "chat.completion",
        "created": 1, "model": DEFAULT_MODEL_ID,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


class AuthenticatedGroqIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "integration.sqlite3"
        self.auth = LocalAuthentication(self.database)
        self.auth.create_tenant("tenant-one", "Tenant One")
        self.auth.create_tenant("tenant-two", "Tenant Two")
        self.owner_id = self.auth.register("owner", "owner-password-long", "tenant-one")
        self.peer_id = self.auth.register("peer", "peer-password-long", "tenant-one")
        self.other_id = self.auth.register("other", "other-password-long", "tenant-two")
        self.owner_token = self.auth.login("owner", "owner-password-long", "tenant-one")
        self.peer_token = self.auth.login("peer", "peer-password-long", "tenant-one")
        self.other_token = self.auth.login("other", "other-password-long", "tenant-two")
        self.identity = self.auth.authenticate(self.owner_token)
        self.keys = MemoryKeys()
        self.credentials = CredentialService(self.database, self.keys)
        self.seen_authorization = []
        self.seen_payloads = []
        self.service = AuthenticatedGroqService(
            self.auth, self.credentials, self._provider()
        )

    def tearDown(self):
        self.temp.cleanup()

    def _provider(self):
        async def handle(request):
            self.seen_authorization.append(request.headers.get("authorization"))
            self.seen_payloads.append(request.content.decode("utf-8"))
            return httpx2.Response(200, json=_completion("safe response"))

        def factory(api_key, base_url, timeout):
            client = httpx2.AsyncClient(transport=httpx2.MockTransport(handle), timeout=timeout)
            return AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout,
                               max_retries=0, http_client=client)

        return GroqProvider(GroqProviderConfig(), sdk_client_factory=factory)

    def _provider_with_response(self, response_text: str):
        async def handle(request):
            self.seen_authorization.append(request.headers.get("authorization"))
            self.seen_payloads.append(request.content.decode("utf-8"))
            return httpx2.Response(200, json=_completion(response_text))

        def factory(api_key, base_url, timeout):
            client = httpx2.AsyncClient(transport=httpx2.MockTransport(handle), timeout=timeout)
            return AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout,
                               max_retries=0, http_client=client)

        return GroqProvider(GroqProviderConfig(), sdk_client_factory=factory)

    def _request(self):
        return InferenceRequest([Message("user", ["safe prompt"])])

    async def test_authenticated_owner_uses_own_stored_groq_credential(self):
        key = "test-only-groq-key-owner-one"
        status = self.credentials.create(self.identity, "groq", key)
        result = await self.service.complete(self.owner_token, status.credential_id, self._request())
        self.assertEqual(result.provider, "groq")
        self.assertEqual(self.seen_authorization, [f"Bearer {key}"])
        self.assertNotIn(key, json.dumps(result.response.to_dict(exclude_none=False)))
        self.assertNotIn(key, repr(vars(self.service)))

    async def test_request_scoped_inference_records_only_sanitized_token_usage(self):
        secret = "test-only-usage-key-canary"
        status = self.credentials.create(self.identity, "groq", secret)
        inference = AuthenticatedGroqInference(
            self.service, self.owner_token, status.credential_id
        )
        request = InferenceRequest(
            [Message("user", ["safe prompt"])], agent_name="crop"
        )
        result = await inference.complete(
            request,
            expected_user_id=self.owner_id,
            expected_tenant_id="tenant-one",
        )
        self.assertEqual(result.usage.actual_total_tokens, 2)
        self.assertEqual(inference.usage_records, (result.usage,))
        self.assertEqual(result.usage.agent_name, "crop")
        self.assertNotIn(secret, repr(result.usage))
        self.assertNotIn(self.owner_token, repr(result.usage))
        self.assertNotIn(status.credential_id, repr(result.usage))

    async def test_workflow_identity_must_match_authenticated_session(self):
        key = "test-only-workflow-identity-key"
        status = self.credentials.create(self.identity, "groq", key)
        with self.assertRaises(PermissionError):
            await self.service.complete(
                self.owner_token,
                status.credential_id,
                self._request(),
                expected_user_id="different-user",
                expected_tenant_id="tenant-one",
            )
        self.assertEqual(self.seen_authorization, [])

    async def test_identity_mismatch_has_distinct_safe_diagnostic(self):
        status = self.credentials.create(self.identity, "groq", "test-only-identity-key")
        inference = AuthenticatedGroqInference(
            self.service, self.owner_token, status.credential_id
        )
        with self.assertRaises(ProviderFailure) as caught:
            await inference.complete(
                self._request(), expected_user_id="different-user",
                expected_tenant_id="tenant-one",
            )
        self.assertEqual(caught.exception.kind, ProviderFailureKind.AUTHENTICATION)
        self.assertEqual(caught.exception.diagnostic.code,
                         InferenceDiagnosticCode.IDENTITY_MISMATCH)
        self.assertEqual(inference.diagnostics, (caught.exception.diagnostic,))
        self.assertIsNone(caught.exception.__context__)
        self.assertEqual(self.seen_authorization, [])

    async def test_missing_inactive_and_wrong_provider_credentials_are_not_disclosed(self):
        missing_id = "missing-credential-id-canary"
        missing = AuthenticatedGroqInference(self.service, self.owner_token, missing_id)
        with self.assertRaises(ProviderFailure) as missing_error:
            await missing.complete(
                self._request(), expected_user_id=self.owner_id,
                expected_tenant_id="tenant-one",
            )
        self.assertEqual(missing_error.exception.diagnostic.code,
                         InferenceDiagnosticCode.CREDENTIAL_UNAVAILABLE)
        self.assertNotIn(missing_id, repr(missing_error.exception.diagnostic))
        self.assertIsNone(missing_error.exception.__context__)

        inactive_status = self.credentials.create(
            self.identity, "groq", "test-only-inactive-key"
        )
        self.credentials.revoke(self.identity, inactive_status.credential_id)
        inactive = AuthenticatedGroqInference(
            self.service, self.owner_token, inactive_status.credential_id
        )
        with self.assertRaises(ProviderFailure) as inactive_error:
            await inactive.complete(
                self._request(), expected_user_id=self.owner_id,
                expected_tenant_id="tenant-one",
            )
        self.assertEqual(inactive_error.exception.diagnostic.code,
                         InferenceDiagnosticCode.CREDENTIAL_UNAVAILABLE)

        wrong_provider_status = self.credentials.create(
            self.identity, "another-provider", "test-only-other-provider-key"
        )
        wrong_provider = AuthenticatedGroqInference(
            self.service, self.owner_token, wrong_provider_status.credential_id
        )
        with self.assertRaises(ProviderFailure) as wrong_provider_error:
            await wrong_provider.complete(
                self._request(), expected_user_id=self.owner_id,
                expected_tenant_id="tenant-one",
            )
        self.assertEqual(wrong_provider_error.exception.diagnostic.code,
                         InferenceDiagnosticCode.CREDENTIAL_UNAVAILABLE)
        self.assertEqual(self.seen_authorization, [])

    async def test_credential_decryption_failure_has_separate_safe_diagnostic(self):
        status = self.credentials.create(self.identity, "groq", "test-only-decryption-key")

        def key_unavailable(_version):
            raise RuntimeError("decryption failure secret canary")

        self.keys.get_key = key_unavailable
        inference = AuthenticatedGroqInference(
            self.service, self.owner_token, status.credential_id
        )
        with self.assertRaises(ProviderFailure) as caught:
            await inference.complete(
                self._request(), expected_user_id=self.owner_id,
                expected_tenant_id="tenant-one",
            )
        self.assertEqual(caught.exception.kind, ProviderFailureKind.AUTHENTICATION)
        self.assertEqual(caught.exception.diagnostic.code,
                         InferenceDiagnosticCode.CREDENTIAL_DECRYPTION_FAILED)
        self.assertEqual(caught.exception.diagnostic.stage.value, "credential_decryption")
        self.assertIsNone(caught.exception.__context__)
        self.assertNotIn("decryption failure secret canary", str(caught.exception))
        self.assertNotIn("decryption failure secret canary", repr(inference.diagnostics))
        self.assertEqual(self.seen_authorization, [])

    async def test_workflow_adapter_keeps_key_session_and_credential_id_out_of_prompt(self):
        key = "test-only-workflow-key-canary"
        status = self.credentials.create(self.identity, "groq", key)
        inference = AuthenticatedGroqInference(self.service, self.owner_token, status.credential_id)
        await inference.complete(
            self._request(),
            expected_user_id=self.owner_id,
            expected_tenant_id="tenant-one",
        )
        sent = self.seen_payloads[0]
        self.assertNotIn(key, sent)
        self.assertNotIn(self.owner_token, sent)
        self.assertNotIn(status.credential_id, sent)
        self.assertIn(f"Bearer {key}", self.seen_authorization[0])
        self.assertNotIn(self.owner_token, repr(inference))
        self.assertNotIn(status.credential_id, repr(inference))

    async def test_authenticated_groq_provider_runs_inside_the_existing_workflow(self):
        class EmptyKnowledge:
            def search(self, *_args, **_kwargs):
                return ()

        key = "test-only-graph-workflow-key"
        status = self.credentials.create(self.identity, "groq", key)
        service = AuthenticatedGroqService(
            self.auth,
            self.credentials,
            self._provider_with_response('{"intents":["weather"]}'),
        )
        inference = AuthenticatedGroqInference(service, self.owner_token, status.credential_id)
        workflow = RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(), knowledge=EmptyKnowledge()
        )
        response = await workflow.run(
            FarmerRequest(
                "phase5-authenticated-graph",
                self.owner_id,
                "tenant-one",
                "Will it rain tomorrow?",
            ),
            mode="groq",
            inference=inference,
        )
        intake = next(item for item in response.agent_results if item.agent_id.value == "Intake")
        self.assertEqual(response.provider_id, "groq")
        self.assertEqual(response.execution_mode.value, "groq")
        self.assertEqual(intake.payload.interpretation_mode, "groq")
        self.assertEqual(intake.payload.inference_status, "completed")
        self.assertEqual(self.seen_authorization, [f"Bearer {key}"])
        sent = self.seen_payloads[0]
        self.assertNotIn(key, sent)
        self.assertNotIn(self.owner_token, sent)
        self.assertNotIn(status.credential_id, sent)
        self.assertNotIn(key, response.response_text)
        self.assertNotIn(self.owner_token, repr(response))

    async def test_rate_limit_diagnostic_survives_workflow_fallback_without_secret_leakage(self):
        key = "test-only-workflow-auth-canary"
        status = self.credentials.create(self.identity, "groq", key)

        async def reject(_request):
            return httpx2.Response(
                429,
                json={"error": {"type": "rate_limit_exceeded", "message": key}},
                headers={
                    "retry-after": "10",
                    "x-ratelimit-remaining-requests": "8",
                    "x-ratelimit-remaining-tokens": "900",
                    "x-ratelimit-reset-requests": "2h",
                    "x-ratelimit-reset-tokens": "3s",
                },
            )

        def factory(api_key, base_url, timeout):
            client = httpx2.AsyncClient(
                transport=httpx2.MockTransport(reject), timeout=timeout
            )
            return AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout,
                               max_retries=0, http_client=client)

        service = AuthenticatedGroqService(
            self.auth, self.credentials,
            GroqProvider(GroqProviderConfig(), sdk_client_factory=factory),
        )
        inference = AuthenticatedGroqInference(
            service, self.owner_token, status.credential_id
        )

        class EmptyKnowledge:
            def search(self, *_args, **_kwargs):
                return ()

        workflow = RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(), knowledge=EmptyKnowledge()
        )
        response = await workflow.run(
            FarmerRequest(
                "phase5-diagnostic-fallback", self.owner_id, "tenant-one",
                "Will it rain tomorrow?",
            ),
            mode="groq",
            inference=inference,
        )
        self.assertEqual(response.execution_mode.value, "groq")
        intake = next(item for item in response.agent_results
                      if item.agent_id.value == "Intake")
        self.assertEqual(intake.payload.inference_status, "rate_limit")
        self.assertTrue(response.response_text)
        self.assertGreaterEqual(len(inference.diagnostics), 1)
        self.assertTrue(all(
            item.code is InferenceDiagnosticCode.GROQ_RATE_LIMITED
            and item.http_status == 429
            and item.rate_limit_scope.value == "unknown"
            and item.retry_after_seconds == 10.0
            and item.retries_attempted == 0
            for item in inference.diagnostics
        ))
        self.assertNotIn(key, response.response_text)
        self.assertNotIn(key, repr(inference.diagnostics))
        self.assertNotIn(key, repr(response))

    async def test_local_tpm_budget_rejection_uses_fallback_without_provider_dispatch(self):
        key = "test-only-local-budget-key"
        status = self.credentials.create(self.identity, "groq", key)
        dispatches = []

        async def handle(request):
            dispatches.append(request)
            return httpx2.Response(200, json=_completion("unexpected provider call"))

        def factory(api_key, base_url, timeout):
            client = httpx2.AsyncClient(
                transport=httpx2.MockTransport(handle), timeout=timeout
            )
            return AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout,
                               max_retries=0, http_client=client)

        service = AuthenticatedGroqService(
            self.auth,
            self.credentials,
            GroqProvider(
                GroqProviderConfig(
                    tpm_limit_tokens=300,
                    tpm_headroom_tokens=100,
                    default_max_output_tokens=128,
                ),
                sdk_client_factory=factory,
            ),
        )
        inference = AuthenticatedGroqInference(
            service, self.owner_token, status.credential_id
        )

        class EmptyKnowledge:
            def search(self, *_args, **_kwargs):
                return ()

        workflow = RythuSaathiWorkflow(
            resolver=empty_coverage_resolver(), knowledge=EmptyKnowledge()
        )
        response = await workflow.run(
            FarmerRequest(
                "phase5-budget-fallback", self.owner_id, "tenant-one",
                "x" * 2000,
            ),
            mode="groq",
            inference=inference,
        )
        self.assertEqual(dispatches, [])
        self.assertEqual(response.execution_mode.value, "groq")
        intake = next(item for item in response.agent_results
                      if item.agent_id.value == "Intake")
        self.assertEqual(intake.payload.inference_status, "rate_limit")
        self.assertEqual(len(inference.diagnostics), 1)
        diagnostic = inference.diagnostics[0]
        self.assertEqual(diagnostic.code, InferenceDiagnosticCode.GROQ_TPM_BUDGET_EXCEEDED)
        self.assertEqual(diagnostic.agent_name, "intake")
        self.assertEqual(diagnostic.budget_decision, "rejected")
        self.assertNotIn(key, repr(inference.diagnostics))
        self.assertNotIn(self.owner_token, repr(inference.diagnostics))

    async def test_same_tenant_peer_cannot_retrieve_or_use_owner_credential(self):
        status = self.credentials.create(self.identity, "groq", "test-only-peer-denial-key")
        with self.assertRaises(CredentialUnavailable):
            await self.service.complete(self.peer_token, status.credential_id, self._request())
        self.assertEqual(self.seen_authorization, [])

    async def test_cross_tenant_access_is_denied(self):
        status = self.credentials.create(self.identity, "groq", "test-only-cross-tenant-key")
        with self.assertRaises(CredentialUnavailable):
            await self.service.complete(self.other_token, status.credential_id, self._request())
        self.assertEqual(self.seen_authorization, [])

    async def test_revoked_credential_is_not_dispatched(self):
        status = self.credentials.create(self.identity, "groq", "test-only-revoked-key")
        self.credentials.revoke(self.identity, status.credential_id)
        with self.assertRaises(CredentialUnavailable):
            await self.service.complete(self.owner_token, status.credential_id, self._request())
        self.assertEqual(self.seen_authorization, [])

    async def test_rotated_credential_supplies_only_new_value(self):
        first = self.credentials.create(self.identity, "groq", "test-only-old-key")
        self.credentials.rotate(self.identity, first.credential_id, "test-only-new-key")
        await self.service.complete(self.owner_token, first.credential_id, self._request())
        self.assertEqual(self.seen_authorization, ["Bearer test-only-new-key"])

    async def test_provider_failure_does_not_expose_transient_credential(self):
        key = "test-only-provider-failure-key"

        class FailingGroq:
            provider_id = "groq"

            async def complete(self, request, credential):
                secret = credential._consume_for_provider()
                raise RuntimeError(f"simulated transport failure included {secret}")

        failure_service = AuthenticatedGroqService(self.auth, self.credentials, FailingGroq())
        status = self.credentials.create(self.identity, "groq", key)
        with self.assertRaises(ProviderFailure) as caught:
            await failure_service.complete(self.owner_token, status.credential_id, self._request())
        self.assertEqual(caught.exception.kind, ProviderFailureKind.PROVIDER_FAILURE)
        self.assertNotIn(key, str(caught.exception))
        self.assertIsNone(caught.exception.__context__)

    async def test_non_groq_credential_cannot_be_sent_to_groq(self):
        status = self.credentials.create(self.identity, "another-provider", "test-only-other-key")
        with self.assertRaises(CredentialUnavailable):
            await self.service.complete(self.owner_token, status.credential_id, self._request())
        self.assertEqual(self.seen_authorization, [])


if __name__ == "__main__":
    unittest.main()
