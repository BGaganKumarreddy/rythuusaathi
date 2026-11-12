"""Unit and regression tests for DataGovInMarketProvider using mocked HTTP transport."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import unittest

import httpx

from rythusaathi.agents.data_gov_in import (
    DEFAULT_HEADERS,
    DEFAULT_RESOURCE_ID,
    DataGovInMarketProvider,
    MandiDiagnosticCode,
    sanitize_params,
    sanitize_response_preview,
    sanitize_url,
)
from rythusaathi.domain import EvidenceMode


class MandiProviderTests(unittest.IsolatedAsyncioTestCase):
    """Exhaustive test suite for data.gov.in Mandi Market Price adapter."""

    # 1. Successful market-price retrieval (COMPLETED)
    async def test_successful_market_price_retrieval(self):
        mock_api_response = {
            "status": "ok",
            "total": 1,
            "count": 1,
            "records": [
                {
                    "state": "Telangana",
                    "district": "Warangal",
                    "market": "Warangal",
                    "commodity": "Cotton",
                    "variety": "Other",
                    "grade": "FAQ",
                    "arrival_date": "10/11/2026",
                    "min_price": "6800",
                    "max_price": "7400",
                    "modal_price": "7100",
                }
            ],
        }

        async def handler(request: httpx.Request) -> httpx.Response:
            url_str = str(request.url)
            self.assertIn("api-key=test-key-123", url_str)
            self.assertIn("filters%5Bcommodity%5D=Cotton", url_str)
            self.assertIn("filters%5Bdistrict%5D=Warangal", url_str)
            self.assertIn("filters%5Bstate%5D=Telangana", url_str)
            self.assertEqual(request.headers.get("User-Agent"), DEFAULT_HEADERS["User-Agent"])
            self.assertEqual(request.headers.get("Accept"), "application/json")
            return httpx.Response(200, json=mock_api_response)

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        try:
            provider = DataGovInMarketProvider(api_key="test-key-123", http_client=client)
            quote = await provider.get_quote("req-m1", "crop:gossypium-hirsutum", "location:district:in-tg-warangal")
            self.assertIsNotNone(quote)
            self.assertEqual(quote.request_id, "req-m1")
            self.assertEqual(quote.crop_id, "crop:gossypium-hirsutum")
            self.assertEqual(quote.location_id, "location:district:in-tg-warangal")
            self.assertEqual(quote.market_name, "Warangal")
            self.assertEqual(quote.price, 7100.0)
            self.assertEqual(quote.unit, "Rs/Quintal")
            self.assertEqual(quote.min_price, 6800.0)
            self.assertEqual(quote.max_price, 7400.0)
            self.assertEqual(quote.modal_price, 7100.0)
            self.assertEqual(quote.commodity, "Cotton")
            self.assertEqual(quote.state, "Telangana")
            self.assertEqual(quote.district, "Warangal")
            self.assertEqual(quote.mode, EvidenceMode.LIVE)
            self.assertIsNotNone(quote.source)
            self.assertIn("Agmarknet Daily Market Price", quote.source.title)
            self.assertIsNotNone(quote.observed_at)
            self.assertIsNotNone(quote.retrieved_at)
            self.assertLessEqual(quote.observed_at, quote.retrieved_at)

            # Check diagnostic
            diag = provider.last_diagnostic
            self.assertIsNotNone(diag)
            self.assertEqual(diag.code, MandiDiagnosticCode.COMPLETED)
            self.assertEqual(diag.http_status, 200)
            self.assertEqual(diag.record_count, 1)
        finally:
            await client.aclose()

    # 2. Diagnostic: Credential Not Found
    async def test_diagnostic_credential_not_found(self):
        provider = DataGovInMarketProvider(api_key=None)
        quote = await provider.get_quote("req-no-key", "crop:gossypium-hirsutum", "location:district:in-tg-warangal")
        self.assertIsNone(quote)
        self.assertIsNotNone(provider.last_diagnostic)
        self.assertEqual(provider.last_diagnostic.code, MandiDiagnosticCode.CREDENTIAL_NOT_FOUND)
        self.assertIsNone(provider.last_diagnostic.http_status)

    # 3. Diagnostic: Credential Handoff Failed
    async def test_diagnostic_credential_handoff_failed(self):
        def failing_resolver():
            raise RuntimeError("Secret decryption failed")

        provider = DataGovInMarketProvider(api_key_resolver=failing_resolver)
        quote = await provider.get_quote("req-handoff-fail", "crop:gossypium-hirsutum", "location:district:in-tg-warangal")
        self.assertIsNone(quote)
        self.assertIsNotNone(provider.last_diagnostic)
        self.assertEqual(provider.last_diagnostic.code, MandiDiagnosticCode.CREDENTIAL_HANDOFF_FAILED)

    # 4. Diagnostic: DNS / Network Connection Failure
    async def test_diagnostic_dns_connection_failure(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Failed to resolve 'api.data.gov.in'", request=request)

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        try:
            provider = DataGovInMarketProvider(api_key="secret-key", http_client=client)
            quote = await provider.get_quote("req-dns", "crop:gossypium-hirsutum", "location:district:in-tg-warangal")
            self.assertIsNone(quote)
            diag = provider.last_diagnostic
            self.assertIsNotNone(diag)
            self.assertEqual(diag.code, MandiDiagnosticCode.DNS_CONNECTION_FAILURE)
            self.assertIn("ConnectError", diag.exception_type_chain)
        finally:
            await client.aclose()

    # 5. Diagnostic: Connection Timeout (including empty-string ReadTimeout)
    async def test_diagnostic_connection_timeout_empty_string(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            # Raise ReadTimeout with empty string message, exactly as default HTTPX raises
            raise httpx.ReadTimeout("", request=request)

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        try:
            provider = DataGovInMarketProvider(api_key="secret-key", http_client=client)
            quote = await provider.get_quote("req-timeout", "crop:gossypium-hirsutum", "location:district:in-tg-warangal")
            self.assertIsNone(quote)
            diag = provider.last_diagnostic
            self.assertIsNotNone(diag)
            self.assertEqual(diag.code, MandiDiagnosticCode.CONNECTION_TIMEOUT)
            self.assertIn("ReadTimeout", diag.exception_type_chain)
            # Never blank error message
            self.assertIsNotNone(diag.provider_error_message)
            self.assertIn("ReadTimeout", diag.provider_error_message)
        finally:
            await client.aclose()

    # 6. Diagnostic: HTTP 400 Authorization field missing
    async def test_diagnostic_http_400_authorization_missing(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": "Authorization field missing"})

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        try:
            provider = DataGovInMarketProvider(api_key="test-key", http_client=client)
            quote = await provider.get_quote("req-400", "crop:gossypium-hirsutum", "location:district:in-tg-warangal")
            self.assertIsNone(quote)
            diag = provider.last_diagnostic
            self.assertIsNotNone(diag)
            self.assertEqual(diag.code, MandiDiagnosticCode.HTTP_400_AUTHORIZATION_MISSING)
            self.assertEqual(diag.http_status, 400)
            self.assertEqual(diag.provider_error_message, "Authorization field missing")
        finally:
            await client.aclose()

    # 7. Diagnostic: HTTP 401 & 403 Key not authorised
    async def test_diagnostic_http_401_403_unauthorized(self):
        for status_code in (401, 403):
            async def handler(request: httpx.Request, code=status_code) -> httpx.Response:
                return httpx.Response(code, json={"error": "Key not authorised"})

            transport = httpx.MockTransport(handler)
            client = httpx.AsyncClient(transport=transport)
            try:
                provider = DataGovInMarketProvider(api_key="bad-key", http_client=client)
                quote = await provider.get_quote("req-auth", "crop:gossypium-hirsutum", "location:district:in-tg-warangal")
                self.assertIsNone(quote)
                diag = provider.last_diagnostic
                self.assertIsNotNone(diag)
                self.assertEqual(diag.code, MandiDiagnosticCode.HTTP_401_403_UNAUTHORIZED)
                self.assertEqual(diag.http_status, status_code)
                self.assertEqual(diag.provider_error_message, "Key not authorised")
            finally:
                await client.aclose()

    # 8. Diagnostic: HTTP 404 Resource Not Found
    async def test_diagnostic_http_404_not_found(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"error": "Resource not found"})

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        try:
            provider = DataGovInMarketProvider(api_key="test-key", http_client=client)
            quote = await provider.get_quote("req-404", "crop:gossypium-hirsutum", "location:district:in-tg-warangal")
            self.assertIsNone(quote)
            diag = provider.last_diagnostic
            self.assertIsNotNone(diag)
            self.assertEqual(diag.code, MandiDiagnosticCode.HTTP_404_NOT_FOUND)
            self.assertEqual(diag.http_status, 404)
        finally:
            await client.aclose()

    # 9. Diagnostic: HTTP 429 Rate Limited with Retry-After header
    async def test_diagnostic_http_429_rate_limited(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429,
                json={"error": "Rate limit exceeded"},
                headers={"Retry-After": "30"},
            )

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        try:
            provider = DataGovInMarketProvider(api_key="test-key", http_client=client)
            quote = await provider.get_quote("req-429", "crop:gossypium-hirsutum", "location:district:in-tg-warangal")
            self.assertIsNone(quote)
            diag = provider.last_diagnostic
            self.assertIsNotNone(diag)
            self.assertEqual(diag.code, MandiDiagnosticCode.HTTP_429_RATE_LIMITED)
            self.assertEqual(diag.http_status, 429)
            self.assertEqual(diag.retry_after_seconds, 30.0)
            self.assertEqual(diag.provider_error_message, "Rate limit exceeded")
        finally:
            await client.aclose()

    # 10. Diagnostic: HTTP 500+ Server Error
    async def test_diagnostic_http_500_server_error(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(502, text="Bad Gateway")

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        try:
            provider = DataGovInMarketProvider(api_key="test-key", http_client=client)
            quote = await provider.get_quote("req-500", "crop:gossypium-hirsutum", "location:district:in-tg-warangal")
            self.assertIsNone(quote)
            diag = provider.last_diagnostic
            self.assertIsNotNone(diag)
            self.assertEqual(diag.code, MandiDiagnosticCode.HTTP_500_SERVER_ERROR)
            self.assertEqual(diag.http_status, 502)
        finally:
            await client.aclose()

    # 11. Diagnostic: Invalid JSON Response
    async def test_diagnostic_invalid_json(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html><body>Non-JSON Error Page</body></html>")

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        try:
            provider = DataGovInMarketProvider(api_key="test-key", http_client=client)
            quote = await provider.get_quote("req-json", "crop:gossypium-hirsutum", "location:district:in-tg-warangal")
            self.assertIsNone(quote)
            diag = provider.last_diagnostic
            self.assertIsNotNone(diag)
            self.assertEqual(diag.code, MandiDiagnosticCode.INVALID_JSON)
            self.assertEqual(diag.http_status, 200)
            self.assertIn("Non-JSON", diag.sanitized_response_body)
        finally:
            await client.aclose()

    # 12. Diagnostic: Unexpected Response Schema (missing 'records' field)
    async def test_diagnostic_unexpected_response_schema(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "ok", "unexpected_field": 123})

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        try:
            provider = DataGovInMarketProvider(api_key="test-key", http_client=client)
            quote = await provider.get_quote("req-schema", "crop:gossypium-hirsutum", "location:district:in-tg-warangal")
            self.assertIsNone(quote)
            diag = provider.last_diagnostic
            self.assertIsNotNone(diag)
            self.assertEqual(diag.code, MandiDiagnosticCode.UNEXPECTED_RESPONSE_SCHEMA)
            self.assertEqual(diag.http_status, 200)
        finally:
            await client.aclose()

    # 13. Diagnostic: Zero Records
    async def test_diagnostic_zero_records(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "ok", "total": 0, "count": 0, "records": []})

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        try:
            provider = DataGovInMarketProvider(api_key="test-key", http_client=client)
            quote = await provider.get_quote("req-zero", "crop:gossypium-hirsutum", "location:district:in-tg-warangal")
            self.assertIsNone(quote)
            diag = provider.last_diagnostic
            self.assertIsNotNone(diag)
            self.assertEqual(diag.code, MandiDiagnosticCode.ZERO_RECORDS)
            self.assertEqual(diag.http_status, 200)
            self.assertEqual(diag.record_count, 0)
        finally:
            await client.aclose()

    # 14. Diagnostic: No Usable Price Fields
    async def test_diagnostic_no_usable_price_fields(self):
        mock_response = {
            "status": "ok",
            "total": 1,
            "count": 1,
            "records": [
                {
                    "state": "Telangana",
                    "district": "Warangal",
                    "market": "Warangal",
                    "commodity": "Cotton",
                    "modal_price": "N/A",
                    "min_price": None,
                    "max_price": "0",
                }
            ],
        }

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=mock_response)

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        try:
            provider = DataGovInMarketProvider(api_key="test-key", http_client=client)
            quote = await provider.get_quote("req-noprice", "crop:gossypium-hirsutum", "location:district:in-tg-warangal")
            self.assertIsNone(quote)
            diag = provider.last_diagnostic
            self.assertIsNotNone(diag)
            self.assertEqual(diag.code, MandiDiagnosticCode.NO_USABLE_PRICE_FIELDS)
            self.assertEqual(diag.http_status, 200)
            self.assertEqual(diag.record_count, 1)
        finally:
            await client.aclose()

    # 15. Location resolution for plain location names (Warangal -> Warangal, Telangana)
    async def test_location_resolution_plain_name(self):
        recorded_params = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            for k, v in request.url.params.items():
                recorded_params[k] = v
            return httpx.Response(200, json={"status": "ok", "records": []})

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        try:
            provider = DataGovInMarketProvider(api_key="test-key", http_client=client)
            await provider.get_quote("req-plain-loc", "crop:gossypium-hirsutum", "Warangal")
            self.assertEqual(recorded_params.get("filters[district]"), "Warangal")
            self.assertEqual(recorded_params.get("filters[state]"), "Telangana")
            self.assertEqual(recorded_params.get("filters[commodity]"), "Cotton")
        finally:
            await client.aclose()

    # 16. Sanitize URL, parameters and repr never expose keys
    def test_credential_sanitization(self):
        raw_url = "https://api.data.gov.in/resource/9ef84268?api-key=my_super_secret_12345&format=json&filters[state]=Telangana"
        sanitized = sanitize_url(raw_url)
        self.assertNotIn("my_super_secret_12345", sanitized)
        self.assertIn("api-key=%5BREDACTED%5D", sanitized)

        params = {"api-key": "my_super_secret_12345", "format": "json"}
        sanitized_p = sanitize_params(params)
        self.assertEqual(sanitized_p["api-key"], "[REDACTED]")

        preview = sanitize_response_preview('{"error": "Key secret_token_12345678901234567890 invalid"}')
        self.assertNotIn("secret_token_12345678901234567890", preview)

        provider = DataGovInMarketProvider(api_key="my_super_secret_12345")
        self.assertNotIn("my_super_secret_12345", repr(provider))
        self.assertIn("authenticated=True", repr(provider))


if __name__ == "__main__":
    unittest.main()
