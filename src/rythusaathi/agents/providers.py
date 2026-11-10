"""Local-first weather and market provider interfaces and safe test fixtures."""

from __future__ import annotations

from datetime import datetime, timezone

from ..domain import ContentReference, EvidenceMode, EvidenceSource, EvidenceSourceType, ReferenceKind
from .contracts import MarketQuote, WeatherObservation


class UnavailableWeatherProvider:
    """Honest default until a verified weather integration is configured."""

    async def get_observation(self, request_id: str, location_id: str) -> None:
        return None


class UnavailableMarketProvider:
    """Honest default until a verified market integration is configured."""

    async def get_quote(self, request_id: str, crop_id: str, location_id: str) -> None:
        return None


class DeterministicWeatherFixture:
    """Explicit fixture for tests and demonstrations; never represents live data."""

    async def get_observation(self, request_id: str, location_id: str) -> WeatherObservation:
        observed_at = datetime(2026, 1, 15, 6, tzinfo=timezone.utc)
        return WeatherObservation(
            request_id=request_id,
            location_id=location_id,
            observed_at=observed_at,
            retrieved_at=observed_at,
            source=EvidenceSource(
                source_id="fixture:weather",
                source_identity="RythuSaathi deterministic test fixture",
                source_type=EvidenceSourceType.EXTERNAL_DATA,
                title="Deterministic weather fixture; not a real observation",
                reference=ContentReference(kind=ReferenceKind.DOCUMENT_ID,
                                           value="fixture:weather:2026-01-15"),
                publisher="RythuSaathi test fixture",
            ),
            mode=EvidenceMode.FIXTURE,
            temperature_c=23.0,
            rainfall_mm=0.0,
        )


class DeterministicMarketFixture:
    """Explicit fixture for tests and demonstrations; never represents a live price."""

    async def get_quote(self, request_id: str, crop_id: str, location_id: str) -> MarketQuote:
        observed_at = datetime(2026, 1, 15, 6, tzinfo=timezone.utc)
        return MarketQuote(
            request_id=request_id,
            crop_id=crop_id,
            location_id=location_id,
            market_name="Fixture market; not a real market",
            price=1.0,
            unit="fixture-unit",
            observed_at=observed_at,
            retrieved_at=observed_at,
            source=EvidenceSource(
                source_id="fixture:market",
                source_identity="RythuSaathi deterministic test fixture",
                source_type=EvidenceSourceType.EXTERNAL_DATA,
                title="Deterministic market fixture; not a real price",
                reference=ContentReference(kind=ReferenceKind.DOCUMENT_ID,
                                           value="fixture:market:2026-01-15"),
                publisher="RythuSaathi test fixture",
            ),
            mode=EvidenceMode.FIXTURE,
        )
