"""data.gov.in Mandi Market Price Provider for RythuSaathi.

Integrates the official Government of India Open Government Data (data.gov.in)
Agmarknet daily market price API (resource 9ef84268-d588-465a-a308-a864a43d0070).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
import json
import logging
import math
import re
import time
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx

from ..credentials import CredentialService
from ..domain import (
    ContentReference,
    EvidenceMode,
    EvidenceSource,
    EvidenceSourceType,
    ReferenceKind,
)
from ..domain.registry import RegistryLevel
from ..domain.seed import CROP_REGISTRY, LOCATION_REGISTRY
from ..security import VerifiedIdentity
from .contracts import MarketQuote

logger = logging.getLogger(__name__)

DEFAULT_RESOURCE_ID = "9ef84268-d588-465a-a308-a864a43d0070"
DEFAULT_BASE_URL = "https://api.data.gov.in/resource/"
DEFAULT_TIMEOUT_SECONDS = 15.0
IST = timezone(timedelta(hours=5, minutes=30), name="IST")

DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RythuSaathi/1.0",
    "Accept": "application/json",
}

KNOWN_DISTRICT_STATES: dict[str, str] = {
    "warangal": "Telangana",
    "nalgonda": "Telangana",
    "khammam": "Telangana",
    "karimnagar": "Telangana",
    "adilabad": "Telangana",
    "nirmal": "Telangana",
    "siddipet": "Telangana",
    "peddapalli": "Telangana",
    "mancherial": "Telangana",
    "ranga reddy": "Telangana",
    "hyderabad": "Telangana",
    "guntur": "Andhra Pradesh",
    "ludhiana": "Punjab",
    "mysuru": "Karnataka",
    "mysore": "Karnataka",
}


class MandiDiagnosticCode(StrEnum):
    """Sanitized diagnostic codes covering Mandi provider operations."""

    CREDENTIAL_NOT_FOUND = "credential_not_found"
    CREDENTIAL_HANDOFF_FAILED = "credential_handoff_failed"
    DNS_CONNECTION_FAILURE = "dns_connection_failure"
    CONNECTION_TIMEOUT = "connection_timeout"
    HTTP_400_AUTHORIZATION_MISSING = "http_400_authorization_missing"
    HTTP_401_403_UNAUTHORIZED = "http_401_403_unauthorized"
    HTTP_404_NOT_FOUND = "http_404_not_found"
    HTTP_429_RATE_LIMITED = "http_429_rate_limited"
    HTTP_500_SERVER_ERROR = "http_500_server_error"
    INVALID_JSON = "invalid_json"
    UNEXPECTED_RESPONSE_SCHEMA = "unexpected_response_schema"
    ZERO_RECORDS = "zero_records"
    NO_USABLE_PRICE_FIELDS = "no_usable_price_fields"
    COMPLETED = "completed"


@dataclass
class MandiDiagnostic:
    """Sanitized diagnostic report for a Mandi market price request."""

    code: MandiDiagnosticCode
    http_status: int | None = None
    provider_error_message: str | None = None
    sanitized_response_body: str | None = None
    request_id: str | None = None
    retry_after_seconds: float | None = None
    sanitized_params: dict[str, str] = field(default_factory=dict)
    record_count: int = 0
    exception_type_chain: str | None = None
    latency_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "http_status": self.http_status,
            "provider_error_message": self.provider_error_message,
            "sanitized_response_body": self.sanitized_response_body,
            "request_id": self.request_id,
            "retry_after_seconds": self.retry_after_seconds,
            "sanitized_params": self.sanitized_params,
            "record_count": self.record_count,
            "exception_type_chain": self.exception_type_chain,
            "latency_seconds": self.latency_seconds,
        }


def sanitize_url(url: str) -> str:
    """Redact api-key from URL strings for safe logging."""
    try:
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if "api-key" in query:
            query["api-key"] = ["[REDACTED]"]
        sanitized_query = urlencode(query, doseq=True)
        return urlunparse(parsed._replace(query=sanitized_query))
    except Exception:
        return "[URL_SANITIZATION_FAILED]"


def sanitize_params(params: dict[str, Any]) -> dict[str, str]:
    """Redact api-key from request parameters for safe reporting."""
    sanitized: dict[str, str] = {}
    for k, v in params.items():
        if k == "api-key":
            sanitized[k] = "[REDACTED]"
        else:
            sanitized[k] = str(v)
    return sanitized


def sanitize_response_preview(text: str, max_chars: int = 200) -> str:
    """Safely truncate response preview without revealing credentials."""
    trimmed = text[:max_chars].strip()
    return re.sub(r'([a-zA-Z0-9_-]{20,})', '[REDACTED]', trimmed) if "key" in trimmed.lower() else trimmed


def _build_exception_chain(exc: BaseException) -> str:
    """Extract ordered chain of exception types."""
    types: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        types.append(type(current).__name__)
        current = current.__cause__ or current.__context__
    return " -> ".join(types)


def _parse_arrival_date(date_str: str | None) -> datetime:
    """Parse Indian Mandi arrival date (e.g. DD/MM/YYYY) into timezone-aware datetime."""
    now = datetime.now(IST)
    if not date_str or not date_str.strip():
        return now
    s = date_str.strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y"):
        try:
            parsed = datetime.strptime(s, fmt)
            return parsed.replace(hour=6, minute=0, second=0, tzinfo=IST)
        except ValueError:
            continue
    return now


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        val = float(value)
        if math.isfinite(val) and val > 0:
            return val
        return None
    except (ValueError, TypeError):
        return None


class DataGovInMarketProvider:
    """Asynchronous Agmarknet Mandi market price adapter using data.gov.in API."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        credential_service: CredentialService | None = None,
        identity: VerifiedIdentity | None = None,
        credential_id: str | None = None,
        api_key_resolver: Callable[[], str | None] | None = None,
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        resource_id: str = DEFAULT_RESOURCE_ID,
        base_url: str = DEFAULT_BASE_URL,
        locations: Any | None = None,
        crops: Any | None = None,
    ) -> None:
        self._api_key = api_key.strip() if api_key and api_key.strip() else None
        self._credential_service = credential_service
        self._identity = identity
        self._credential_id = credential_id
        self._api_key_resolver = api_key_resolver
        self._external_client = http_client
        self._timeout_seconds = timeout_seconds
        self._resource_id = resource_id
        self._base_url = base_url
        self._locations = locations or LOCATION_REGISTRY
        self._crops = crops or CROP_REGISTRY
        self.last_diagnostic: MandiDiagnostic | None = None

    def __repr__(self) -> str:
        has_key = bool(self._api_key or self._credential_id or self._api_key_resolver)
        return f"<DataGovInMarketProvider resource={self._resource_id!r} authenticated={has_key}>"

    def _resolve_api_key(self) -> tuple[str | None, MandiDiagnosticCode | None]:
        """Resolve API key securely without exposing it."""
        if self._api_key:
            return self._api_key, None
        if self._api_key_resolver is not None:
            try:
                resolved = self._api_key_resolver()
                if resolved and resolved.strip():
                    return resolved.strip(), None
                return None, MandiDiagnosticCode.CREDENTIAL_NOT_FOUND
            except Exception as exc:
                logger.warning("API key resolver failed: %s", type(exc).__name__)
                return None, MandiDiagnosticCode.CREDENTIAL_HANDOFF_FAILED
        if (
            self._credential_service is not None
            and self._identity is not None
            and self._credential_id is not None
        ):
            try:
                key = self._credential_service.dispatch_handoff(
                    self._identity,
                    self._credential_id,
                    lambda secret, _gen: secret,
                    expected_provider_id="data.gov.in",
                )
                if key and key.strip():
                    return key.strip(), None
                return None, MandiDiagnosticCode.CREDENTIAL_NOT_FOUND
            except Exception as exc:
                logger.warning("Credential handoff failed for data.gov.in: %s", type(exc).__name__)
                return None, MandiDiagnosticCode.CREDENTIAL_HANDOFF_FAILED
        return None, MandiDiagnosticCode.CREDENTIAL_NOT_FOUND

    async def _get_client(self) -> httpx.AsyncClient:
        if self._external_client is not None:
            return self._external_client
        return httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout_seconds, connect=5.0),
            headers=DEFAULT_HEADERS,
        )

    def _resolve_commodity(self, crop_id: str) -> str:
        """Resolve Agmarknet commodity name from crop identifier."""
        crop_record = self._crops.get(crop_id)
        if crop_record is not None and crop_record.common_names:
            first_name = crop_record.common_names[0].value.strip()
            # Map canonical English name to Agmarknet commodity name
            name_lower = first_name.lower()
            if "rice" in name_lower or "paddy" in name_lower:
                return "Paddy(Fine/Superfine/Grade A)"
            if "cotton" in name_lower:
                return "Cotton"
            if "wheat" in name_lower:
                return "Wheat"
            if "maize" in name_lower or "corn" in name_lower:
                return "Maize"
            if "chickpea" in name_lower or "gram" in name_lower:
                return "Bengal Gram"
            if "black gram" in name_lower or "urad" in name_lower:
                return "Black Gram Dal(Urd Dal)"
            return first_name.title()

        cid_lower = crop_id.lower()
        if "gossypium" in cid_lower or "cotton" in cid_lower:
            return "Cotton"
        if "oryza" in cid_lower or "rice" in cid_lower or "paddy" in cid_lower:
            return "Paddy(Fine/Superfine/Grade A)"
        if "triticum" in cid_lower or "wheat" in cid_lower:
            return "Wheat"
        if "zea" in cid_lower or "maize" in cid_lower:
            return "Maize"
        if "cicer" in cid_lower or "chickpea" in cid_lower:
            return "Bengal Gram"
        if "vigna" in cid_lower or "black" in cid_lower:
            return "Black Gram Dal(Urd Dal)"
        return crop_id.split(":")[-1].title()

    def _resolve_location(self, location_id: str) -> tuple[str | None, str | None]:
        """Resolve state and district name from location identifier."""
        state_name: str | None = None
        district_name: str | None = None

        location_record = self._locations.get(location_id)
        if location_record is None and hasattr(self._locations, "search"):
            matches = self._locations.search(location_id)
            if matches:
                location_record = matches[0]

        if location_record is not None:
            ancestors = self._locations.ancestors(location_record.location_id)
            for anc in ancestors:
                if anc.level is RegistryLevel.STATE:
                    state_name = anc.name.strip()
                elif anc.level is RegistryLevel.DISTRICT:
                    district_name = anc.name.strip()

            if location_record.level is RegistryLevel.DISTRICT and not district_name:
                district_name = location_record.name.strip()
            elif location_record.level is RegistryLevel.STATE and not state_name:
                state_name = location_record.name.strip()
        else:
            # Fallback parsing for canonical ID e.g. location:district:in-tg-warangal
            if location_id.startswith("location:"):
                segments = location_id.split(":")
                if len(segments) >= 3:
                    subparts = segments[2].split("-")
                    if len(subparts) >= 3 and subparts[0] == "in":
                        state_map = {
                            "tg": "Telangana",
                            "ap": "Andhra Pradesh",
                            "ka": "Karnataka",
                            "pb": "Punjab",
                        }
                        state_name = state_map.get(subparts[1])
                        district_name = subparts[2].replace("-", " ").title()
            else:
                loc_lower = location_id.strip().lower()
                if loc_lower in KNOWN_DISTRICT_STATES:
                    state_name = KNOWN_DISTRICT_STATES[loc_lower]
                    district_name = location_id.strip().title()
                elif "," in location_id:
                    parts = location_id.split(",")
                    district_name = parts[0].strip().title()
                    state_name = parts[1].strip().title()
                elif location_id.strip().title() in ("Telangana", "Andhra Pradesh", "Punjab", "Karnataka"):
                    state_name = location_id.strip().title()
                else:
                    district_name = location_id.strip().title()

        return state_name, district_name

    async def get_quote(
        self,
        request_id: str,
        crop_id: str,
        location_id: str,
    ) -> MarketQuote | None:
        """Fetch current Mandi price quote for a registered crop and location."""
        self.last_diagnostic = None
        if not request_id or not crop_id or not location_id:
            return None

        # 1. Resolve API key
        api_key, key_diag = self._resolve_api_key()
        if not api_key:
            self.last_diagnostic = MandiDiagnostic(
                code=key_diag or MandiDiagnosticCode.CREDENTIAL_NOT_FOUND,
                request_id=request_id,
            )
            logger.info("No data.gov.in API key configured; diagnostic=%s", self.last_diagnostic.code.value)
            return None

        # 2. Resolve commodity and geographic filters
        commodity = self._resolve_commodity(crop_id)
        state_name, district_name = self._resolve_location(location_id)

        # 3. Construct URL parameters conforming to official data.gov.in resource 9ef84268-d588-465a-a308-a864a43d0070
        endpoint = f"{self._base_url.rstrip('/')}/{self._resource_id}"
        params: dict[str, Any] = {
            "api-key": api_key,
            "format": "json",
            "offset": 0,
            "limit": 10,
            "filters[commodity]": commodity,
        }
        if state_name:
            params["filters[state]"] = state_name
        if district_name:
            params["filters[district]"] = district_name

        sanitized_p = sanitize_params(params)
        client = await self._get_client()
        should_close = self._external_client is None
        start_time = time.monotonic()

        try:
            resp = await client.get(endpoint, params=params, headers=DEFAULT_HEADERS)
            latency = max(0.0, time.monotonic() - start_time)
            status_code = resp.status_code
            raw_text = resp.text
            preview = sanitize_response_preview(raw_text)

            # Parse JSON error body if present
            err_msg: str | None = None
            data: dict[str, Any] | None = None
            try:
                parsed = resp.json()
                if isinstance(parsed, dict):
                    data = parsed
                    err_val = parsed.get("error") or parsed.get("message")
                    if isinstance(err_val, str) and err_val.strip():
                        err_msg = err_val.strip()
            except Exception:
                pass

            # Extract Retry-After header if present
            retry_after: float | None = None
            if "retry-after" in resp.headers:
                try:
                    retry_after = float(resp.headers["retry-after"])
                except (ValueError, TypeError):
                    pass

            # 4. Handle HTTP error status codes
            if status_code == 400:
                self.last_diagnostic = MandiDiagnostic(
                    code=MandiDiagnosticCode.HTTP_400_AUTHORIZATION_MISSING,
                    http_status=400,
                    provider_error_message=err_msg or "Authorization field missing",
                    sanitized_response_body=preview,
                    request_id=request_id,
                    retry_after_seconds=retry_after,
                    sanitized_params=sanitized_p,
                    latency_seconds=latency,
                )
                logger.warning(
                    "data.gov.in authorization missing (HTTP 400): %s",
                    self.last_diagnostic.provider_error_message,
                )
                return None

            if status_code in {401, 403}:
                self.last_diagnostic = MandiDiagnostic(
                    code=MandiDiagnosticCode.HTTP_401_403_UNAUTHORIZED,
                    http_status=status_code,
                    provider_error_message=err_msg or "Key not authorised",
                    sanitized_response_body=preview,
                    request_id=request_id,
                    retry_after_seconds=retry_after,
                    sanitized_params=sanitized_p,
                    latency_seconds=latency,
                )
                logger.warning(
                    "data.gov.in authentication failed (HTTP %d): %s",
                    status_code,
                    self.last_diagnostic.provider_error_message,
                )
                return None

            if status_code == 404:
                self.last_diagnostic = MandiDiagnostic(
                    code=MandiDiagnosticCode.HTTP_404_NOT_FOUND,
                    http_status=404,
                    provider_error_message=err_msg or "Resource not found",
                    sanitized_response_body=preview,
                    request_id=request_id,
                    retry_after_seconds=retry_after,
                    sanitized_params=sanitized_p,
                    latency_seconds=latency,
                )
                logger.warning("data.gov.in resource not found (HTTP 404)")
                return None

            if status_code == 429:
                self.last_diagnostic = MandiDiagnostic(
                    code=MandiDiagnosticCode.HTTP_429_RATE_LIMITED,
                    http_status=429,
                    provider_error_message=err_msg or "Rate limit exceeded",
                    sanitized_response_body=preview,
                    request_id=request_id,
                    retry_after_seconds=retry_after,
                    sanitized_params=sanitized_p,
                    latency_seconds=latency,
                )
                logger.warning("data.gov.in rate limited (HTTP 429)")
                return None

            if status_code >= 500:
                self.last_diagnostic = MandiDiagnostic(
                    code=MandiDiagnosticCode.HTTP_500_SERVER_ERROR,
                    http_status=status_code,
                    provider_error_message=err_msg or f"Server error (HTTP {status_code})",
                    sanitized_response_body=preview,
                    request_id=request_id,
                    retry_after_seconds=retry_after,
                    sanitized_params=sanitized_p,
                    latency_seconds=latency,
                )
                logger.warning("data.gov.in server error (HTTP %d)", status_code)
                return None

            if status_code != 200:
                self.last_diagnostic = MandiDiagnostic(
                    code=MandiDiagnosticCode.UNEXPECTED_RESPONSE_SCHEMA,
                    http_status=status_code,
                    provider_error_message=err_msg or f"Unexpected HTTP {status_code}",
                    sanitized_response_body=preview,
                    request_id=request_id,
                    retry_after_seconds=retry_after,
                    sanitized_params=sanitized_p,
                    latency_seconds=latency,
                )
                logger.warning("data.gov.in returned unexpected HTTP %d", status_code)
                return None

            # 5. Handle HTTP 200 responses
            if data is None:
                self.last_diagnostic = MandiDiagnostic(
                    code=MandiDiagnosticCode.INVALID_JSON,
                    http_status=200,
                    provider_error_message="Response body could not be parsed as JSON",
                    sanitized_response_body=preview,
                    request_id=request_id,
                    sanitized_params=sanitized_p,
                    latency_seconds=latency,
                )
                logger.warning("data.gov.in returned invalid JSON")
                return None

            records = data.get("records")
            if not isinstance(records, list):
                self.last_diagnostic = MandiDiagnostic(
                    code=MandiDiagnosticCode.UNEXPECTED_RESPONSE_SCHEMA,
                    http_status=200,
                    provider_error_message="Response missing list 'records' field",
                    sanitized_response_body=preview,
                    request_id=request_id,
                    sanitized_params=sanitized_p,
                    record_count=0,
                    latency_seconds=latency,
                )
                logger.warning("data.gov.in response schema missing list records")
                return None

            record_count = len(records)
            if record_count == 0:
                self.last_diagnostic = MandiDiagnostic(
                    code=MandiDiagnosticCode.ZERO_RECORDS,
                    http_status=200,
                    provider_error_message=None,
                    sanitized_response_body=preview,
                    request_id=request_id,
                    sanitized_params=sanitized_p,
                    record_count=0,
                    latency_seconds=latency,
                )
                logger.info(
                    "No Mandi records found for commodity=%s, state=%s, district=%s",
                    commodity,
                    state_name,
                    district_name,
                )
                return None

            # 6. Extract usable price fields
            selected_record: dict[str, Any] | None = None
            modal_price: float | None = None

            for rec in records:
                if not isinstance(rec, dict):
                    continue
                p = (
                    _safe_float(rec.get("modal_price"))
                    or _safe_float(rec.get("max_price"))
                    or _safe_float(rec.get("min_price"))
                )
                if p is not None:
                    selected_record = rec
                    modal_price = p
                    break

            if selected_record is None or modal_price is None:
                self.last_diagnostic = MandiDiagnostic(
                    code=MandiDiagnosticCode.NO_USABLE_PRICE_FIELDS,
                    http_status=200,
                    provider_error_message="No record contained usable numeric modal, min, or max price",
                    sanitized_response_body=preview,
                    request_id=request_id,
                    sanitized_params=sanitized_p,
                    record_count=record_count,
                    latency_seconds=latency,
                )
                logger.info("Mandi records returned but none had usable numeric prices")
                return None

            # 7. Construct validated MarketQuote
            market_name = (
                selected_record.get("market")
                or district_name
                or "Mandi Market"
            ).strip()

            min_p = _safe_float(selected_record.get("min_price"))
            max_p = _safe_float(selected_record.get("max_price"))
            variety = selected_record.get("variety")
            grade = selected_record.get("grade")
            arrival_date_str = selected_record.get("arrival_date")

            observed_at = _parse_arrival_date(arrival_date_str)
            retrieved_at = datetime.now(timezone.utc)
            if observed_at > retrieved_at:
                observed_at = retrieved_at

            ref_url = f"https://api.data.gov.in/resource/{self._resource_id}"
            source = EvidenceSource(
                source_id="external:data-gov-in",
                source_identity="data.gov.in Mandi Daily Prices",
                source_type=EvidenceSourceType.PRIMARY_GOVERNMENT,
                title=f"Agmarknet Daily Market Price — {market_name} ({commodity})",
                reference=ContentReference(kind=ReferenceKind.URL, value=ref_url),
                publisher="Ministry of Agriculture and Farmers Welfare, Government of India",
            )

            self.last_diagnostic = MandiDiagnostic(
                code=MandiDiagnosticCode.COMPLETED,
                http_status=200,
                provider_error_message=None,
                sanitized_response_body=preview,
                request_id=request_id,
                sanitized_params=sanitized_p,
                record_count=record_count,
                latency_seconds=latency,
            )

            return MarketQuote(
                request_id=request_id,
                crop_id=crop_id,
                location_id=location_id,
                market_name=market_name,
                price=modal_price,
                unit="Rs/Quintal",
                observed_at=observed_at,
                retrieved_at=retrieved_at,
                source=source,
                mode=EvidenceMode.LIVE,
                min_price=min_p,
                max_price=max_p,
                modal_price=modal_price,
                commodity=commodity,
                variety=str(variety).strip() if variety else None,
                grade=str(grade).strip() if grade else None,
                state=state_name,
                district=district_name,
                arrival_date=str(arrival_date_str).strip() if arrival_date_str else None,
            )

        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout, httpx.TimeoutException) as exc:
            chain = _build_exception_chain(exc)
            latency = max(0.0, time.monotonic() - start_time)
            self.last_diagnostic = MandiDiagnostic(
                code=MandiDiagnosticCode.CONNECTION_TIMEOUT,
                request_id=request_id,
                sanitized_params=sanitized_p,
                exception_type_chain=chain,
                latency_seconds=latency,
                provider_error_message=f"{type(exc).__name__}: {exc}" if str(exc) else f"{type(exc).__name__} during request",
            )
            logger.warning("data.gov.in connection timeout: %s", chain)
            return None

        except (httpx.ConnectError, httpx.NetworkError, httpx.TransportError) as exc:
            chain = _build_exception_chain(exc)
            latency = max(0.0, time.monotonic() - start_time)
            self.last_diagnostic = MandiDiagnostic(
                code=MandiDiagnosticCode.DNS_CONNECTION_FAILURE,
                request_id=request_id,
                sanitized_params=sanitized_p,
                exception_type_chain=chain,
                latency_seconds=latency,
                provider_error_message=f"{type(exc).__name__}: {exc}" if str(exc) else f"{type(exc).__name__} during transport",
            )
            logger.warning("data.gov.in network/transport error: %s", chain)
            return None

        except Exception as exc:
            chain = _build_exception_chain(exc)
            latency = max(0.0, time.monotonic() - start_time)
            self.last_diagnostic = MandiDiagnostic(
                code=MandiDiagnosticCode.UNEXPECTED_RESPONSE_SCHEMA,
                request_id=request_id,
                sanitized_params=sanitized_p,
                exception_type_chain=chain,
                latency_seconds=latency,
                provider_error_message=f"{type(exc).__name__}: {exc}",
            )
            logger.warning("data.gov.in request failed: %s (%s)", type(exc).__name__, exc)
            return None

        finally:
            api_key = ""
            if should_close:
                await client.aclose()
