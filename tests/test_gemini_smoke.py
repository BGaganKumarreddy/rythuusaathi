"""Task 3 local verification tests for isolated Gemini smoke test."""

from __future__ import annotations

import asyncio
import io
import json
import os
from pathlib import Path
import socket
import ssl
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import httpx2
from openai import AsyncOpenAI

from scripts.gemini_smoke import (
    ExperimentResult,
    TransportProbeResult,
    print_experiment_report,
    print_smoke_report,
    print_transport_probe_report,
    probe_gemini_transport,
    run_gemini_minimal_probe,
    run_gemini_smoke,
    run_gemini_structured_experiment,
    setup_authenticated_inference,
)
from rythusaathi.agents.llm import AuthenticatedGeminiInference, IntakeInterpretation
from rythusaathi.credentials import CredentialService, CredentialUnavailable
from rythusaathi.inference.contracts import (
    InferenceDiagnostic,
    InferenceDiagnosticCode,
    InferenceStage,
    InferenceTokenUsage,
    ProviderFailure,
)
from rythusaathi.inference.providers.gemini import (
    DEFAULT_MODEL_ID,
    GeminiProvider,
    GeminiProviderConfig,
)
from rythusaathi.inference.service import AuthenticatedGeminiService
from rythusaathi.local_auth import LocalAuthentication


class MemoryKeys:
    """In-memory key store for unit tests without DPAPI dependency."""

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


def _completion(content: str | None, *, model: str = DEFAULT_MODEL_ID):
    return {
        "id": "chatcmpl-smoke-test-fixture",
        "object": "chat.completion",
        "created": 1,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 28, "total_tokens": 40},
    }


def _mock_provider(handler, *, config: GeminiProviderConfig | None = None):
    def factory(api_key: str, base_url: str, timeout: float) -> AsyncOpenAI:
        transport = httpx2.MockTransport(handler)
        http_client = httpx2.AsyncClient(transport=transport, timeout=timeout)
        return AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=0,
            http_client=http_client,
        )

    return GeminiProvider(config or GeminiProviderConfig(), sdk_client_factory=factory)


class GeminiSmokeLocalVerificationTests(unittest.IsolatedAsyncioTestCase):
    """Local verification tests covering all scenarios specified in Tasks 3 and 8."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "smoke_auth.sqlite3"
        self.auth = LocalAuthentication(self.database)
        self.auth.create_tenant("test-tenant", "Test Tenant")
        self.owner_id = self.auth.register("farmer_gagan", "farmer-password-123", "test-tenant")
        self.session_token = self.auth.login("farmer_gagan", "farmer-password-123", "test-tenant")
        self.identity = self.auth.authenticate(self.session_token)

        self.keys = MemoryKeys()
        self.credentials = CredentialService(self.database, self.keys)
        status = self.credentials.create(self.identity, "gemini", "synthetic-gemini-smoke-key")
        self.credential_id = status.credential_id

    def tearDown(self):
        self.temp.cleanup()

    def _build_inference(
        self, handler, *, config: GeminiProviderConfig | None = None
    ) -> AuthenticatedGeminiInference:
        provider = _mock_provider(handler, config=config)
        service = AuthenticatedGeminiService(self.auth, self.credentials, provider)
        return AuthenticatedGeminiInference(service, self.session_token, self.credential_id)

    # 1. Successful valid JSON response
    async def test_successful_valid_json_response(self):
        valid_intake = {
            "crops": [{"value": "cotton", "quote": "cotton"}],
            "locations": [{"value": "Warangal", "quote": "Warangal"}],
            "varieties": [],
            "growth_stages": [],
            "intents": ["crop_advice"],
            "observations": [{"label": "leaf spot", "quote": "leaf spot"}],
            "farming_problem": None,
        }

        async def handler(request):
            return httpx2.Response(200, json=_completion(json.dumps(valid_intake)))

        inference = self._build_inference(handler)
        success, interp, diag, usage, elapsed = await run_gemini_smoke(
            inference,
            expected_user_id=self.owner_id,
            expected_tenant_id="test-tenant",
            query="My cotton crop in Warangal has leaf spot",
        )

        self.assertTrue(success)
        self.assertIsNotNone(interp)
        self.assertIsInstance(interp, IntakeInterpretation)
        self.assertEqual(interp.crops[0].value, "cotton")
        self.assertEqual(interp.locations[0].value, "Warangal")
        self.assertIsNone(diag)
        self.assertIsNotNone(usage)
        self.assertEqual(usage.actual_total_tokens, 40)
        self.assertEqual(usage.http_status, 200)

    # 2. HTTP 503
    async def test_http_503_service_unavailable(self):
        async def handler(request):
            body = {
                "error": {
                    "code": 503,
                    "message": "The model is overloaded. Please try again later.",
                    "status": "UNAVAILABLE",
                }
            }
            return httpx2.Response(503, json=body, headers={"retry-after": "5"})

        inference = self._build_inference(handler)
        success, interp, diag, usage, elapsed = await run_gemini_smoke(
            inference,
            expected_user_id=self.owner_id,
            expected_tenant_id="test-tenant",
        )

        self.assertFalse(success)
        self.assertIsNone(interp)
        self.assertIsNotNone(diag)
        self.assertEqual(diag.code, InferenceDiagnosticCode.GEMINI_SERVICE_UNAVAILABLE)
        self.assertEqual(diag.http_status, 503)
        self.assertEqual(diag.stage, InferenceStage.PROVIDER_RESPONSE)
        self.assertIn("UNAVAILABLE", diag.provider_error_status or "")

    # 3. HTTP 429
    async def test_http_429_rate_limit(self):
        async def handler(request):
            body = {
                "error": {
                    "code": 429,
                    "message": "Resource has been exhausted (e.g. check quota).",
                    "status": "RESOURCE_EXHAUSTED",
                }
            }
            return httpx2.Response(429, json=body, headers={"retry-after": "15"})

        inference = self._build_inference(handler)
        success, interp, diag, usage, elapsed = await run_gemini_smoke(
            inference,
            expected_user_id=self.owner_id,
            expected_tenant_id="test-tenant",
        )

        self.assertFalse(success)
        self.assertIsNone(interp)
        self.assertIsNotNone(diag)
        self.assertIn(
            diag.code,
            {InferenceDiagnosticCode.GEMINI_RATE_LIMITED, InferenceDiagnosticCode.GEMINI_QUOTA_EXHAUSTED},
        )
        self.assertEqual(diag.http_status, 429)
        self.assertEqual(diag.stage, InferenceStage.PROVIDER_RESPONSE)

    # 4. Read timeout
    async def test_read_timeout(self):
        async def handler(request):
            raise httpx2.ReadTimeout("The read operation timed out after 60.0 seconds")

        inference = self._build_inference(handler)
        success, interp, diag, usage, elapsed = await run_gemini_smoke(
            inference,
            expected_user_id=self.owner_id,
            expected_tenant_id="test-tenant",
        )

        self.assertFalse(success)
        self.assertIsNone(interp)
        self.assertIsNotNone(diag)
        self.assertEqual(diag.code, InferenceDiagnosticCode.GEMINI_READ_TIMEOUT)
        self.assertEqual(diag.stage, InferenceStage.PROVIDER_TRANSPORT)
        self.assertIn("ReadTimeout", diag.exception_type_chain or "")

    # 5. Invalid JSON
    async def test_invalid_json_response(self):
        async def handler(request):
            return httpx2.Response(200, json=_completion("This is plain text and definitely not JSON."))

        inference = self._build_inference(handler)
        success, interp, diag, usage, elapsed = await run_gemini_smoke(
            inference,
            expected_user_id=self.owner_id,
            expected_tenant_id="test-tenant",
        )

        self.assertFalse(success)
        self.assertIsNone(interp)
        self.assertIsNotNone(diag)
        self.assertEqual(diag.code, InferenceDiagnosticCode.GEMINI_MALFORMED_RESPONSE)
        self.assertEqual(diag.parsing_outcome, "json_decode_failed")
        self.assertEqual(diag.stage, InferenceStage.RESPONSE_VALIDATION)

    # 6. Pydantic validation failure
    async def test_pydantic_validation_failure(self):
        async def handler(request):
            # Valid JSON but completely invalid shape for IntakeInterpretation
            invalid_shape = {"crops": "cotton", "not_allowed_extra": True}
            return httpx2.Response(200, json=_completion(json.dumps(invalid_shape)))

        inference = self._build_inference(handler)
        success, interp, diag, usage, elapsed = await run_gemini_smoke(
            inference,
            expected_user_id=self.owner_id,
            expected_tenant_id="test-tenant",
        )

        self.assertFalse(success)
        self.assertIsNone(interp)
        self.assertIsNotNone(diag)
        self.assertEqual(diag.code, InferenceDiagnosticCode.GEMINI_MALFORMED_RESPONSE)
        self.assertEqual(diag.parsing_outcome, "pydantic_validation_failed")
        self.assertEqual(diag.stage, InferenceStage.RESPONSE_VALIDATION)

    # 7. Credential unavailable
    async def test_credential_unavailable(self):
        # Case A: Requesting a non-existent credential ID
        service = AuthenticatedGeminiService(
            self.auth, self.credentials, _mock_provider(lambda r: None)
        )
        inference = AuthenticatedGeminiInference(service, self.session_token, "non-existent-cred-id")

        success, interp, diag, usage, elapsed = await run_gemini_smoke(
            inference,
            expected_user_id=self.owner_id,
            expected_tenant_id="test-tenant",
        )

        self.assertFalse(success)
        self.assertIsNone(interp)
        self.assertIsNotNone(diag)
        self.assertEqual(diag.code, InferenceDiagnosticCode.CREDENTIAL_UNAVAILABLE)
        self.assertEqual(diag.stage, InferenceStage.CREDENTIAL_LOOKUP)

        # Case B: setup_authenticated_inference with user having no active credentials
        other_user = self.auth.register("other_farmer", "farmer-password-456", "test-tenant")
        with self.assertRaises(CredentialUnavailable):
            setup_authenticated_inference(
                self.database,
                tenant_id="test-tenant",
                username="other_farmer",
                password="farmer-password-456",
                key_store=self.keys,
            )

    # 8. Single network attempt when max_retries=0 on HTTP 503
    async def test_http_503_exactly_one_attempt_when_max_retries_zero(self):
        calls = 0

        async def handler(request):
            nonlocal calls
            calls += 1
            body = {
                "error": {
                    "code": 503,
                    "message": "The model is overloaded.",
                    "status": "UNAVAILABLE",
                }
            }
            return httpx2.Response(503, json=body, headers={"retry-after": "2"})

        config = GeminiProviderConfig(max_retries=0, timeout_seconds=15.0, read_timeout_seconds=15.0)
        inference = self._build_inference(handler, config=config)

        success, interp, diag, usage, elapsed = await run_gemini_smoke(
            inference,
            expected_user_id=self.owner_id,
            expected_tenant_id="test-tenant",
        )

        self.assertFalse(success)
        self.assertEqual(calls, 1, "Exactly one network attempt must be made when max_retries=0")
        self.assertIsNotNone(diag)
        self.assertEqual(diag.retries_attempted, 0)
        self.assertEqual(diag.retry_decision, "not_retried")
        self.assertEqual(diag.code, InferenceDiagnosticCode.GEMINI_SERVICE_UNAVAILABLE)
        self.assertEqual(diag.http_status, 503)
        self.assertEqual(diag.stage, InferenceStage.PROVIDER_RESPONSE)

    # 9. HTTP 503 captures request-id, retry-after, and Google body safely
    async def test_http_503_captures_headers_and_body_safely(self):
        async def handler(request):
            headers = {
                "x-goog-request-id": "req-live-smoke-503-goog",
                "retry-after": "3.5",
            }
            body = {
                "error": {
                    "code": 503,
                    "message": "The model is currently overloaded. Please try again later.",
                    "status": "UNAVAILABLE",
                }
            }
            return httpx2.Response(503, json=body, headers=headers)

        config = GeminiProviderConfig(max_retries=0, timeout_seconds=15.0, read_timeout_seconds=15.0)
        inference = self._build_inference(handler, config=config)

        success, interp, diag, usage, elapsed = await run_gemini_smoke(
            inference,
            expected_user_id=self.owner_id,
            expected_tenant_id="test-tenant",
        )

        self.assertFalse(success)
        self.assertIsNotNone(diag)
        self.assertEqual(diag.request_id, "req-live-smoke-503-goog")
        self.assertAlmostEqual(diag.retry_after_seconds or 0.0, 3.5, delta=0.1)
        self.assertEqual(diag.provider_error_status, "UNAVAILABLE")
        self.assertEqual(diag.provider_error_message, "The model is currently overloaded. Please try again later.")
        self.assertNotIn("synthetic-gemini-smoke-key", str(diag))

    # 10. setup_authenticated_inference sets bounded timeout and max_retries
    def test_setup_authenticated_inference_respects_bounded_timeout_and_retries(self):
        inference, user_id, tenant_id, token, auth = setup_authenticated_inference(
            self.database,
            tenant_id="test-tenant",
            username="farmer_gagan",
            password="farmer-password-123",
            key_store=self.keys,
            timeout_seconds=15.0,
            max_retries=0,
            model_id="gemini-3.8-flash",
        )
        try:
            svc = inference._AuthenticatedWorkflowInference__service
            provider = svc._provider
            self.assertEqual(provider.config.timeout_seconds, 15.0)
            self.assertEqual(provider.config.read_timeout_seconds, 15.0)
            self.assertEqual(provider.config.max_retries, 0)
            self.assertEqual(provider.config.model_id, "gemini-3.8-flash")
        finally:
            auth.logout(token)

    # 11. Regression test: setup_authenticated_inference does not fail due to missing os or imports
    def test_setup_authenticated_inference_default_model_and_imports_regression(self):
        """Verify that default model resolution using os.environ succeeds without NameError."""
        inference, user_id, tenant_id, token, auth = setup_authenticated_inference(
            self.database,
            tenant_id="test-tenant",
            username="farmer_gagan",
            password="farmer-password-123",
            key_store=self.keys,
            # Omitting provider and model_id to exercise os.environ resolution path
        )
        try:
            svc = inference._AuthenticatedWorkflowInference__service
            provider = svc._provider
            self.assertEqual(provider.config.model_id, "gemini-3.8-flash")
            self.assertEqual(provider.config.timeout_seconds, 15.0)
            self.assertEqual(provider.config.max_retries, 0)
        finally:
            auth.logout(token)

    # 12. Read timeout with SSLWantReadError in exception chain
    async def test_read_timeout_with_ssl_want_read_error_chain(self):
        async def handler(request):
            ssl_err = ssl.SSLWantReadError("The operation did not complete (read) (_ssl.c:2426)")
            cancelled = asyncio.CancelledError()
            cancelled.__cause__ = ssl_err
            py_timeout = TimeoutError("The read operation timed out")
            py_timeout.__cause__ = cancelled
            core_timeout = httpx2.ReadTimeout("timed out")
            core_timeout.__cause__ = py_timeout
            raise core_timeout

        inference = self._build_inference(handler)
        success, interp, diag, usage, elapsed = await run_gemini_smoke(
            inference,
            expected_user_id=self.owner_id,
            expected_tenant_id="test-tenant",
        )

        self.assertFalse(success)
        self.assertIsNone(interp)
        self.assertIsNotNone(diag)
        self.assertEqual(diag.code, InferenceDiagnosticCode.GEMINI_READ_TIMEOUT)
        self.assertEqual(diag.stage, InferenceStage.PROVIDER_TRANSPORT)
        self.assertEqual(diag.retry_decision, "not_retried")
        self.assertIn("SSLWantReadError", diag.exception_type_chain or "")

    # 13. Timeout propagation with different values
    def test_setup_authenticated_inference_custom_timeouts(self):
        # Case A: 25.0s timeout
        inference, _, _, token, auth = setup_authenticated_inference(
            self.database,
            tenant_id="test-tenant",
            username="farmer_gagan",
            password="farmer-password-123",
            key_store=self.keys,
            timeout_seconds=25.0,
            max_retries=0,
        )
        try:
            prov = inference._AuthenticatedWorkflowInference__service._provider
            self.assertEqual(prov.config.timeout_seconds, 25.0)
            self.assertEqual(prov.config.read_timeout_seconds, 25.0)
            self.assertEqual(prov.config.connect_timeout_seconds, 10.0)
            self.assertEqual(prov.config.write_timeout_seconds, 10.0)
            self.assertEqual(prov.config.pool_timeout_seconds, 10.0)
        finally:
            auth.logout(token)

        # Case B: 5.0s timeout (bounded below 10.0s)
        inference, _, _, token, auth = setup_authenticated_inference(
            self.database,
            tenant_id="test-tenant",
            username="farmer_gagan",
            password="farmer-password-123",
            key_store=self.keys,
            timeout_seconds=5.0,
            max_retries=0,
        )
        try:
            prov = inference._AuthenticatedWorkflowInference__service._provider
            self.assertEqual(prov.config.timeout_seconds, 5.0)
            self.assertEqual(prov.config.read_timeout_seconds, 5.0)
            self.assertEqual(prov.config.connect_timeout_seconds, 5.0)
            self.assertEqual(prov.config.write_timeout_seconds, 5.0)
            self.assertEqual(prov.config.pool_timeout_seconds, 5.0)
        finally:
            auth.logout(token)

    # 14. Non-inference transport probe success
    @patch("socket.getaddrinfo")
    @patch("socket.socket")
    @patch("ssl.create_default_context")
    def test_probe_gemini_transport_success(self, mock_ssl_ctx, mock_socket_cls, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.217.113.4", 443)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:4860:4847:400::", 443)),
        ]
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock

        mock_ssock = MagicMock()
        mock_ssock.version.return_value = "TLSv1.3"
        mock_ssock.cipher.return_value = ("TLS_AES_256_GCM_SHA384", "TLSv1.3", 256)
        mock_ssock.recv.side_effect = [b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n", b""]
        mock_ssl_ctx.return_value.wrap_socket.return_value = mock_ssock

        result = probe_gemini_transport(host="generativelanguage.googleapis.com", port=443, timeout=5.0)

        self.assertTrue(result.overall_transport_ok)
        self.assertEqual(result.tcp_connected_ip, "172.217.113.4")
        self.assertEqual(result.tls_negotiated_version, "TLSv1.3")
        self.assertEqual(result.tls_cipher_suite, "TLS_AES_256_GCM_SHA384")
        self.assertEqual(result.http_status_line, "HTTP/1.1 200 OK")
        self.assertIn("172.217.113.4", result.dns_ipv4_ips)
        self.assertIsNone(result.dns_error)
        self.assertIsNone(result.tcp_error)
        self.assertIsNone(result.tls_error)
        self.assertIsNone(result.http_error)

    # 15. Transport probe DNS failure
    @patch("socket.getaddrinfo")
    def test_probe_gemini_transport_dns_failure(self, mock_getaddrinfo):
        mock_getaddrinfo.side_effect = socket.gaierror(8, "nodename nor servname provided, or not known")

        result = probe_gemini_transport(host="generativelanguage.googleapis.com", port=443, timeout=5.0)

        self.assertFalse(result.overall_transport_ok)
        self.assertIn("gaierror", result.dns_error or "")
        self.assertIsNone(result.tcp_connected_ip)

    # 16. Transport probe TCP connect timeout
    @patch("socket.getaddrinfo")
    @patch("socket.socket")
    def test_probe_gemini_transport_tcp_timeout(self, mock_socket_cls, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.217.113.4", 443))
        ]
        mock_sock = MagicMock()
        mock_sock.connect.side_effect = socket.timeout("timed out connecting to server")
        mock_socket_cls.return_value = mock_sock

        result = probe_gemini_transport(host="generativelanguage.googleapis.com", port=443, timeout=5.0)

        self.assertFalse(result.overall_transport_ok)
        self.assertIn("time", (result.tcp_error or "").lower())
        self.assertIsNone(result.tls_negotiated_version)

    # 17. Transport probe TLS failure
    @patch("socket.getaddrinfo")
    @patch("socket.socket")
    @patch("ssl.create_default_context")
    def test_probe_gemini_transport_tls_failure(self, mock_ssl_ctx, mock_socket_cls, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.217.113.4", 443))
        ]
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock
        mock_ssl_ctx.return_value.wrap_socket.side_effect = ssl.SSLError("CERTIFICATE_VERIFY_FAILED")

        result = probe_gemini_transport(host="generativelanguage.googleapis.com", port=443, timeout=5.0)

        self.assertFalse(result.overall_transport_ok)
        self.assertIn("SSLError", result.tls_error or "")
        self.assertIsNone(result.http_status_line)

    # 18. Transport probe HTTP header timeout
    @patch("socket.getaddrinfo")
    @patch("socket.socket")
    @patch("ssl.create_default_context")
    def test_probe_gemini_transport_http_header_timeout(self, mock_ssl_ctx, mock_socket_cls, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.217.113.4", 443))
        ]
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock

        mock_ssock = MagicMock()
        mock_ssock.version.return_value = "TLSv1.3"
        mock_ssock.cipher.return_value = ("TLS_AES_256_GCM_SHA384", "TLSv1.3", 256)
        mock_ssock.recv.side_effect = socket.timeout("timed out waiting for HTTP response headers")
        mock_ssl_ctx.return_value.wrap_socket.return_value = mock_ssock

        result = probe_gemini_transport(host="generativelanguage.googleapis.com", port=443, timeout=5.0)

        self.assertFalse(result.overall_transport_ok)
        self.assertIn("time", (result.http_error or "").lower())

    # 19. Transport probe proxy password sanitization
    @patch("socket.getaddrinfo")
    def test_probe_gemini_transport_sanitizes_proxy_password(self, mock_getaddrinfo):
        mock_getaddrinfo.side_effect = socket.gaierror(8, "test stop")
        old_val = os.environ.get("HTTPS_PROXY")
        try:
            os.environ["HTTPS_PROXY"] = "http://admin:super_secret_password_123@proxy.corp.internal:8080"
            result = probe_gemini_transport()
            self.assertIn("HTTPS_PROXY", result.proxy_settings)
            self.assertNotIn("super_secret_password_123", result.proxy_settings["HTTPS_PROXY"])
            self.assertIn("[REDACTED]", result.proxy_settings["HTTPS_PROXY"])
        finally:
            if old_val is not None:
                os.environ["HTTPS_PROXY"] = old_val
            else:
                os.environ.pop("HTTPS_PROXY", None)

    # 20. CLI --probe-transport flag handling in main()
    @patch("scripts.gemini_smoke.probe_gemini_transport")
    @patch("scripts.gemini_smoke.print_transport_probe_report")
    def test_main_probe_transport_cli_flag(self, mock_print, mock_probe):
        from scripts.gemini_smoke import TransportProbeResult, main
        mock_probe.return_value = TransportProbeResult(
            host="generativelanguage.googleapis.com",
            port=443,
            overall_transport_ok=True,
        )
        with patch("sys.argv", ["gemini_smoke.py", "--probe-transport"]):
            exit_code = main()
            self.assertEqual(exit_code, 0)
            mock_probe.assert_called_once()
            mock_print.assert_called_once()

    # 21. Exact HTTP payload inspection (URL, headers, body structure)
    async def test_exact_http_request_payload_structure(self):
        captured_request = None

        async def handler(request):
            nonlocal captured_request
            captured_request = request
            valid_intake = {
                "crops": [{"value": "cotton", "quote": "cotton"}],
                "locations": [{"value": "Warangal", "quote": "Warangal"}],
                "varieties": [],
                "growth_stages": [],
                "intents": ["crop_advice"],
                "observations": [{"label": "leaf spot", "quote": "leaf spot"}],
                "farming_problem": None,
            }
            return httpx2.Response(200, json=_completion(json.dumps(valid_intake)))

        inference = self._build_inference(handler)
        success, interp, diag, usage, elapsed = await run_gemini_smoke(
            inference,
            expected_user_id=self.owner_id,
            expected_tenant_id="test-tenant",
            query="My cotton crop in Warangal has leaf spot",
        )

        self.assertTrue(success)
        self.assertIsNotNone(captured_request)
        self.assertEqual(captured_request.method, "POST")
        self.assertEqual(
            str(captured_request.url),
            "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        )
        self.assertEqual(captured_request.headers.get("content-type"), "application/json")
        auth_header = captured_request.headers.get("authorization", "")
        self.assertTrue(auth_header.startswith("Bearer "))
        self.assertIn("synthetic-gemini-smoke-key", auth_header)

        body = json.loads(captured_request.content)
        self.assertEqual(body.get("model"), "gemini-3.8-flash")
        self.assertIs(body.get("stream"), False)
        self.assertEqual(body.get("response_format"), {"type": "json_object"})
        self.assertEqual(body.get("temperature"), 0)
        self.assertEqual(body.get("max_completion_tokens"), 1024)
        self.assertIn("messages", body)
        self.assertEqual(len(body["messages"]), 2)
        self.assertEqual(body["messages"][0]["role"], "system")
        self.assertEqual(body["messages"][1]["role"], "user")
        user_content = json.loads(body["messages"][1]["content"])
        self.assertEqual(user_content["task_input"]["query"], "My cotton crop in Warangal has leaf spot")
        self.assertIn("required_json_schema", user_content)

    # 22. Thinking level / reasoning effort propagation
    async def test_thinking_level_propagation_to_reasoning_effort(self):
        captured_requests = []

        async def handler(request):
            captured_requests.append(request)
            valid_intake = {
                "crops": [],
                "locations": [],
                "varieties": [],
                "growth_stages": [],
                "intents": ["general"],
                "observations": [],
                "farming_problem": None,
            }
            return httpx2.Response(200, json=_completion(json.dumps(valid_intake)))

        # Case A: thinking_level="low"
        config_low = GeminiProviderConfig(thinking_level="low", max_retries=0)
        inference_low = self._build_inference(handler, config=config_low)
        success, _, _, _, _ = await run_gemini_smoke(
            inference_low,
            expected_user_id=self.owner_id,
            expected_tenant_id="test-tenant",
        )
        self.assertTrue(success)
        body_low = json.loads(captured_requests[-1].content)
        self.assertEqual(body_low.get("reasoning_effort"), "low")

        # Case B: thinking_level=None
        config_none = GeminiProviderConfig(thinking_level=None, max_retries=0)
        inference_none = self._build_inference(handler, config=config_none)
        success, _, _, _, _ = await run_gemini_smoke(
            inference_none,
            expected_user_id=self.owner_id,
            expected_tenant_id="test-tenant",
        )
        self.assertTrue(success)
        body_none = json.loads(captured_requests[-1].content)
        self.assertNotIn("reasoning_effort", body_none)

    # 23. setup_authenticated_inference supports thinking_level and env fallback
    def test_setup_authenticated_inference_thinking_level(self):
        # Case A: explicit thinking_level parameter
        inference, _, _, token, auth = setup_authenticated_inference(
            self.database,
            tenant_id="test-tenant",
            username="farmer_gagan",
            password="farmer-password-123",
            key_store=self.keys,
            thinking_level="low",
        )
        try:
            prov = inference._AuthenticatedWorkflowInference__service._provider
            self.assertEqual(prov.config.thinking_level, "low")
        finally:
            auth.logout(token)

        # Case B: GEMINI_THINKING_LEVEL env var fallback
        old_env = os.environ.get("GEMINI_THINKING_LEVEL")
        try:
            os.environ["GEMINI_THINKING_LEVEL"] = "minimal"
            inference, _, _, token, auth = setup_authenticated_inference(
                self.database,
                tenant_id="test-tenant",
                username="farmer_gagan",
                password="farmer-password-123",
                key_store=self.keys,
            )
            try:
                prov = inference._AuthenticatedWorkflowInference__service._provider
                self.assertEqual(prov.config.thinking_level, "minimal")
            finally:
                auth.logout(token)
        finally:
            if old_env is not None:
                os.environ["GEMINI_THINKING_LEVEL"] = old_env
            else:
                os.environ.pop("GEMINI_THINKING_LEVEL", None)

    # 24. Regression test: print_smoke_report handles GEMINI_READ_TIMEOUT without NameError
    def test_print_smoke_report_gemini_read_timeout_regression(self):
        diag = InferenceDiagnostic(
            code=InferenceDiagnosticCode.GEMINI_READ_TIMEOUT,
            stage=InferenceStage.PROVIDER_TRANSPORT,
            provider="gemini",
            model_id="gemini-3.8-flash",
            elapsed_seconds=35.587,
            retry_decision="not_retried",
            budget_decision="provider_managed",
            exception_type_chain="ChatClientException -> APITimeoutError -> ReadTimeout -> ReadTimeout -> TimeoutError -> CancelledError -> SSLWantReadError",
        )
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            print_smoke_report(
                success=False,
                interpretation=None,
                diagnostic=diag,
                usage=None,
                elapsed=35.587,
            )
        output = buf.getvalue()
        self.assertIn("Outcome: FAILURE", output)
        self.assertIn("Diagnostic Code: gemini_read_timeout", output)
        self.assertIn("Diagnostic Stage: provider_transport", output)
        self.assertIn("SSLWantReadError", output)
        self.assertIn("Transport Diagnostic Tip:", output)
        self.assertIn("--probe-transport", output)

    # 25. Regression test: print_smoke_report handles all provider diagnostic codes without crashing
    def test_print_smoke_report_all_diagnostic_codes_regression(self):
        test_codes = [
            (InferenceDiagnosticCode.GEMINI_CONNECT_TIMEOUT, InferenceStage.PROVIDER_TRANSPORT),
            (InferenceDiagnosticCode.GEMINI_TIMEOUT, InferenceStage.PROVIDER_TRANSPORT),
            (InferenceDiagnosticCode.GEMINI_NETWORK_FAILURE, InferenceStage.PROVIDER_TRANSPORT),
            (InferenceDiagnosticCode.GEMINI_SERVICE_UNAVAILABLE, InferenceStage.PROVIDER_RESPONSE),
            (InferenceDiagnosticCode.GEMINI_BAD_GATEWAY, InferenceStage.PROVIDER_RESPONSE),
            (InferenceDiagnosticCode.GEMINI_GATEWAY_TIMEOUT, InferenceStage.PROVIDER_RESPONSE),
            (InferenceDiagnosticCode.GEMINI_RATE_LIMITED, InferenceStage.PROVIDER_RESPONSE),
            (InferenceDiagnosticCode.GEMINI_QUOTA_EXHAUSTED, InferenceStage.PROVIDER_RESPONSE),
            (InferenceDiagnosticCode.GEMINI_AUTHENTICATION_FAILED, InferenceStage.PROVIDER_RESPONSE),
            (InferenceDiagnosticCode.GEMINI_INVALID_MODEL, InferenceStage.PROVIDER_RESPONSE),
            (InferenceDiagnosticCode.GEMINI_REQUEST_REJECTED, InferenceStage.PROVIDER_RESPONSE),
            (InferenceDiagnosticCode.GEMINI_MALFORMED_RESPONSE, InferenceStage.RESPONSE_VALIDATION),
            (InferenceDiagnosticCode.REQUEST_CANCELLED, InferenceStage.PROVIDER_TRANSPORT),
            (InferenceDiagnosticCode.CREDENTIAL_UNAVAILABLE, InferenceStage.CREDENTIAL_LOOKUP),
        ]
        for code, stage in test_codes:
            diag = InferenceDiagnostic(
                code=code,
                stage=stage,
                provider="gemini",
                model_id="gemini-3.8-flash",
                http_status=503 if code == InferenceDiagnosticCode.GEMINI_SERVICE_UNAVAILABLE else None,
                retry_after_seconds=5.0,
                retry_decision="not_retried",
                budget_decision="provider_managed",
            )
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                print_smoke_report(
                    success=False,
                    interpretation=None,
                    diagnostic=diag,
                    usage=None,
                    elapsed=1.23,
                )
            output = buf.getvalue()
            self.assertIn("Outcome: FAILURE", output)
            self.assertIn(f"Diagnostic Code: {code.value}", output)

    # 26. Regression test: print_smoke_report with success and with None diagnostic
    def test_print_smoke_report_success_and_none_diagnostic(self):
        # Case A: Success
        valid_intake = IntakeInterpretation(
            crops=[{"value": "cotton", "quote": "cotton"}],
            locations=[{"value": "Warangal", "quote": "Warangal"}],
        )
        usage = InferenceTokenUsage(
            agent_name="intake",
            model_id="gemini-3.8-flash",
            correlation_id="0123456789abcdef0123456789abcdef",
            estimated_input_tokens=10,
            actual_input_tokens=15,
            actual_output_tokens=25,
            actual_total_tokens=40,
            output_token_limit=1024,
            budget_limit_tokens=1000,
            budget_remaining_tokens=960,
            budget_decision="completed",
            retries_attempted=0,
            retry_decision="not_retried",
            http_status=200,
            finish_reason="stop",
        )
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            print_smoke_report(
                success=True,
                interpretation=valid_intake,
                diagnostic=None,
                usage=usage,
                elapsed=2.5,
            )
        output = buf.getvalue()
        self.assertIn("Outcome: SUCCESS", output)
        self.assertIn("HTTP Status: 200", output)
        self.assertIn("cotton", output)

        # Case B: Failure with diagnostic=None
        buf_none = io.StringIO()
        with patch("sys.stdout", buf_none):
            print_smoke_report(
                success=False,
                interpretation=None,
                diagnostic=None,
                usage=None,
                elapsed=1.0,
            )
        output_none = buf_none.getvalue()
        self.assertIn("Outcome: FAILURE", output_none)
        self.assertIn("Diagnostic Code: none", output_none)

    # 27. Minimal probe success path
    async def test_run_gemini_minimal_probe_success(self):
        async def handler(request):
            return httpx2.Response(200, json=_completion("OK"))

        inference = self._build_inference(handler)
        result = await run_gemini_minimal_probe(
            inference,
            expected_user_id=self.owner_id,
            expected_tenant_id="test-tenant",
            prompt="Respond with OK.",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.http_status, 200)
        self.assertTrue(result.headers_received)
        self.assertTrue(result.body_bytes_received)
        self.assertEqual(result.response_text, "OK")
        self.assertEqual(result.model_id, "gemini-3.8-flash")
        self.assertEqual(result.endpoint_path, "/v1beta/openai/chat/completions")
        self.assertIsNone(result.diagnostic)

    # 28. Minimal probe read timeout failure path
    async def test_run_gemini_minimal_probe_read_timeout(self):
        async def handler(request):
            raise httpx2.ReadTimeout("read timed out after 15.0 seconds")

        inference = self._build_inference(handler)
        result = await run_gemini_minimal_probe(
            inference,
            expected_user_id=self.owner_id,
            expected_tenant_id="test-tenant",
        )

        self.assertFalse(result.success)
        self.assertIsNone(result.http_status)
        self.assertFalse(result.headers_received)
        self.assertFalse(result.body_bytes_received)
        self.assertIsNotNone(result.diagnostic)
        self.assertEqual(result.diagnostic.code, InferenceDiagnosticCode.GEMINI_READ_TIMEOUT)
        self.assertEqual(result.diagnostic.stage, InferenceStage.PROVIDER_TRANSPORT)

    # 29. Minimal probe HTTP 503 failure path
    async def test_run_gemini_minimal_probe_http_503(self):
        async def handler(request):
            body = {
                "error": {
                    "code": 503,
                    "message": "The model is overloaded. Please try again later.",
                    "status": "UNAVAILABLE",
                }
            }
            return httpx2.Response(503, json=body, headers={"retry-after": "5"})

        inference = self._build_inference(handler)
        result = await run_gemini_minimal_probe(
            inference,
            expected_user_id=self.owner_id,
            expected_tenant_id="test-tenant",
        )

        self.assertFalse(result.success)
        self.assertEqual(result.http_status, 503)
        self.assertTrue(result.headers_received)
        self.assertTrue(result.body_bytes_received)
        self.assertIsNotNone(result.diagnostic)
        self.assertEqual(result.diagnostic.code, InferenceDiagnosticCode.GEMINI_SERVICE_UNAVAILABLE)
        self.assertEqual(result.diagnostic.stage, InferenceStage.PROVIDER_RESPONSE)
        self.assertEqual(result.provider_error_status, "UNAVAILABLE")

    # 30. Structured experiment wrapper returns valid ExperimentResult
    async def test_run_gemini_structured_experiment_wrapper(self):
        valid_intake = {
            "crops": [{"value": "cotton", "quote": "cotton"}],
            "locations": [{"value": "Warangal", "quote": "Warangal"}],
            "varieties": [],
            "growth_stages": [],
            "intents": ["crop_advice"],
            "observations": [],
            "farming_problem": None,
        }

        async def handler(request):
            return httpx2.Response(200, json=_completion(json.dumps(valid_intake)))

        inference = self._build_inference(handler)
        result = await run_gemini_structured_experiment(
            inference,
            expected_user_id=self.owner_id,
            expected_tenant_id="test-tenant",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.http_status, 200)
        self.assertTrue(result.headers_received)
        self.assertTrue(result.body_bytes_received)
        self.assertIsNotNone(result.interpretation)
        self.assertEqual(result.interpretation.crops[0].value, "cotton")

    # 31. print_experiment_report renders all fields without leaking secrets
    def test_print_experiment_report_rendering(self):
        res = ExperimentResult(
            experiment_name="1. Minimal Chat Completion Probe",
            model_id="gemini-3.8-flash",
            endpoint_path="/v1beta/openai/chat/completions",
            success=False,
            http_status=None,
            elapsed_seconds=15.585,
            headers_received=False,
            body_bytes_received=False,
            diagnostic=InferenceDiagnostic(
                code=InferenceDiagnosticCode.GEMINI_READ_TIMEOUT,
                stage=InferenceStage.PROVIDER_TRANSPORT,
                provider="gemini",
                model_id="gemini-3.8-flash",
                retry_decision="not_retried",
                budget_decision="provider_managed",
            ),
        )
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            print_experiment_report(res)
        out = buf.getvalue()
        self.assertIn("1. Minimal Chat Completion Probe", out)
        self.assertIn("gemini-3.8-flash", out)
        self.assertIn("/v1beta/openai/chat/completions", out)
        self.assertIn("Outcome: FAILURE", out)
        self.assertIn("Response Headers Received: False", out)
        self.assertIn("Response Body Bytes Received: False", out)
        self.assertIn("gemini_read_timeout", out)

    # 32. CLI --mode flag handling in main()
    @patch("scripts.gemini_smoke.setup_authenticated_inference")
    @patch("scripts.gemini_smoke.run_gemini_minimal_probe")
    @patch("scripts.gemini_smoke.run_gemini_structured_experiment")
    def test_main_mode_cli_flags(self, mock_struct, mock_min, mock_setup):
        from scripts.gemini_smoke import main
        mock_auth = MagicMock()
        mock_setup.return_value = (MagicMock(), "user", "tenant", "token", mock_auth)
        mock_min.return_value = ExperimentResult(
            experiment_name="minimal",
            model_id="gemini-3.8-flash",
            endpoint_path="/v1beta/openai/chat/completions",
            success=True,
            http_status=200,
            elapsed_seconds=0.5,
            headers_received=True,
            body_bytes_received=True,
        )
        mock_struct.return_value = ExperimentResult(
            experiment_name="structured",
            model_id="gemini-3.8-flash",
            endpoint_path="/v1beta/openai/chat/completions",
            success=True,
            http_status=200,
            elapsed_seconds=1.0,
            headers_received=True,
            body_bytes_received=True,
        )

        with patch("sys.argv", ["gemini_smoke.py", "--database", str(self.database), "--mode", "minimal"]):
            code = main()
            self.assertEqual(code, 0)
            mock_min.assert_called_once()
            mock_struct.assert_not_called()

    # 33. print_experiment_report renders HTTP 503 error details and headers
    def test_print_experiment_report_renders_http_503_details(self):
        diag = InferenceDiagnostic(
            code=InferenceDiagnosticCode.GEMINI_SERVICE_UNAVAILABLE,
            stage=InferenceStage.PROVIDER_RESPONSE,
            provider="gemini",
            model_id="gemini-3.8-flash",
            http_status=503,
            retry_after_seconds=6.5,
            request_id="goog-req-503-xyz",
            trace_id="trace-503-xyz",
            provider_error_code=503,
            provider_error_status="UNAVAILABLE",
            provider_error_message="The model is overloaded.",
            retry_decision="not_retried",
            budget_decision="provider_managed",
            sanitized_response_headers=(
                ("content-type", "application/json"),
                ("retry-after", "6.5"),
            ),
        )
        res = ExperimentResult(
            experiment_name="1. Minimal Chat Completion Probe",
            model_id="gemini-3.8-flash",
            endpoint_path="/v1beta/openai/chat/completions",
            success=False,
            http_status=503,
            elapsed_seconds=0.625,
            headers_received=True,
            body_bytes_received=True,
            diagnostic=diag,
            provider_error_code=503,
            provider_error_status="UNAVAILABLE",
            provider_error_message="The model is overloaded.",
            retry_after_seconds=6.5,
            request_id="goog-req-503-xyz",
            trace_id="trace-503-xyz",
            sanitized_headers={"content-type": "application/json", "retry-after": "6.5"},
        )
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            print_experiment_report(res)
        out = buf.getvalue()
        self.assertIn("HTTP Status: 503", out)
        self.assertIn("gemini_service_unavailable", out)
        self.assertIn("code=503", out)
        self.assertIn("status=UNAVAILABLE", out)
        self.assertIn("message=The model is overloaded.", out)
        self.assertIn("Request ID: goog-req-503-xyz", out)
        self.assertIn("Trace ID: trace-503-xyz", out)
        self.assertIn("Retry-After: 6.5s", out)
        self.assertIn("Sanitized Response Headers:", out)

    # 34. print_smoke_report renders HTTP 503 error details and headers
    def test_print_smoke_report_renders_http_503_details(self):
        diag = InferenceDiagnostic(
            code=InferenceDiagnosticCode.GEMINI_SERVICE_UNAVAILABLE,
            stage=InferenceStage.PROVIDER_RESPONSE,
            provider="gemini",
            model_id="gemini-3.8-flash",
            http_status=503,
            retry_after_seconds=9.0,
            request_id="goog-req-smoke-503",
            trace_id="trace-smoke-503",
            provider_error_code=503,
            provider_error_status="UNAVAILABLE",
            provider_error_message="High load backend issue.",
            retry_decision="not_retried",
            budget_decision="provider_managed",
            sanitized_response_headers=(
                ("content-type", "application/json"),
                ("server", "scaffolding"),
            ),
        )
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            print_smoke_report(
                success=False,
                interpretation=None,
                diagnostic=diag,
                usage=None,
                elapsed=0.88,
            )
        out = buf.getvalue()
        self.assertIn("HTTP Status: 503", out)
        self.assertIn("gemini_service_unavailable", out)
        self.assertIn("code=503", out)
        self.assertIn("status=UNAVAILABLE", out)
        self.assertIn("message=High load backend issue.", out)
        self.assertIn("Request ID: goog-req-smoke-503", out)
        self.assertIn("Trace ID: trace-smoke-503", out)
        self.assertIn("Retry-After: 9.0s", out)
        self.assertIn("Sanitized Response Headers:", out)


if __name__ == "__main__":
    unittest.main()

