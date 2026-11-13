"""Open-Meteo weather adapter with geocoding and forecast support for RythuSaathi."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
from typing import Any
from urllib.parse import urlparse

import httpx

from ..domain import (
    ContentReference,
    EvidenceMode,
    EvidenceSource,
    EvidenceSourceType,
    ReferenceKind,
)
from ..domain.seed import LOCATION_REGISTRY
from .contracts import WeatherObservation

logger = logging.getLogger(__name__)

DEFAULT_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
DEFAULT_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
DEFAULT_TIMEOUT_SECONDS = 10.0

WMO_WEATHER_CODES: dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snow fall",
    73: "Moderate snow fall",
    75: "Heavy snow fall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


def _parse_time(time_str: str, tz_name: str) -> datetime:
    """Parse ISO timestamp with timezone awareness."""
    if tz_name in {"Asia/Kolkata", "Asia/Calcutta", "IST"}:
        tz = timezone(timedelta(hours=5, minutes=30), name="IST")
    else:
        tz = timezone.utc
    try:
        # Time string may be "2026-11-10T18:30" (naive local) or full ISO with offset
        dt = datetime.fromisoformat(time_str)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=tz)
        return dt
    except Exception:
        return datetime.now(timezone.utc)


class OpenMeteoWeatherProvider:
    """Production-ready Open-Meteo weather adapter with geocoding, current weather, and forecasts."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        geocoding_url: str = DEFAULT_GEOCODING_URL,
        forecast_url: str = DEFAULT_FORECAST_URL,
        locations: Any | None = None,
    ) -> None:
        self._external_client = http_client
        self._timeout_seconds = timeout_seconds
        self._geocoding_url = geocoding_url
        self._forecast_url = forecast_url
        self._locations = locations or LOCATION_REGISTRY
        self._geocoding_cache: dict[str, tuple[float, float, str]] = {}

    async def _get_client(self) -> httpx.AsyncClient:
        if self._external_client is not None:
            return self._external_client
        return httpx.AsyncClient(timeout=self._timeout_seconds)

    async def geocode(
        self,
        location_name: str,
        *,
        state_hint: str | None = None,
    ) -> tuple[float, float, str] | None:
        """Geocode a location query to (latitude, longitude, timezone)."""
        cache_key = f"{location_name.strip().casefold()}:{state_hint.strip().casefold() if state_hint else ''}"
        if cache_key in self._geocoding_cache:
            return self._geocoding_cache[cache_key]

        client = await self._get_client()
        should_close = self._external_client is None
        try:
            params = {
                "name": location_name.strip(),
                "count": 5,
                "language": "en",
                "format": "json",
            }
            resp = await client.get(self._geocoding_url, params=params)
            if resp.status_code != 200:
                logger.warning("Open-Meteo geocoding returned HTTP %d for %s", resp.status_code, location_name)
                return None
            data = resp.json()
            results = data.get("results")
            if not isinstance(results, list) or not results:
                return None

            # Filter for Indian locations first
            in_results = [
                r for r in results
                if r.get("country_code", "").upper() == "IN" or r.get("country", "").casefold() == "india"
            ]
            candidates = in_results if in_results else results

            # Prefer result matching state hint
            selected = candidates[0]
            if state_hint:
                hint_lower = state_hint.strip().casefold()
                for c in candidates:
                    admin1 = (c.get("admin1") or "").casefold()
                    if hint_lower in admin1 or admin1 in hint_lower:
                        selected = c
                        break

            lat = float(selected["latitude"])
            lon = float(selected["longitude"])
            tz = selected.get("timezone") or "Asia/Kolkata"
            coord = (lat, lon, tz)
            self._geocoding_cache[cache_key] = coord
            return coord
        except (httpx.TimeoutException, httpx.HTTPError, asyncio.TimeoutError, KeyError, ValueError, json.JSONDecodeError, Exception) as exc:
            logger.warning("Geocoding failed for %s (%s): %s", location_name, type(exc).__name__, exc)
            return None
        finally:
            if should_close:
                await client.aclose()

    async def get_observation(
        self,
        request_id: str,
        location_id: str,
    ) -> WeatherObservation | None:
        """Retrieve real-time and forecast weather data for a canonical location."""
        if not request_id or not location_id:
            return None

        # 1. Resolve coordinates from registry or geocoding
        lat: float | None = None
        lon: float | None = None
        tz: str = "Asia/Kolkata"

        record = self._locations.get(location_id)
        if record is not None and record.latitude is not None and record.longitude is not None:
            lat = record.latitude
            lon = record.longitude
            tz = record.timezone or "Asia/Kolkata"
        elif record is not None:
            # Registry record exists but coordinates are None; geocode using its name and state ancestor
            ancestors = self._locations.ancestors(location_id)
            state_name = None
            for anc in ancestors:
                if getattr(anc, "level", None) and anc.level.value == "state":
                    state_name = anc.name
                    break
            geocoded = await self.geocode(record.name, state_hint=state_name)
            if geocoded is not None:
                lat, lon, tz = geocoded
        else:
            # Fallback parsing for canonical ID e.g. location:district:in-tg-warangal
            name_query = location_id
            state_hint = None
            if location_id.startswith("location:"):
                segments = location_id.split(":")
                if len(segments) >= 3:
                    subparts = segments[2].split("-")
                    if len(subparts) >= 2:
                        name_query = subparts[-1].capitalize()
                        if subparts[0] == "in" and len(subparts) >= 3:
                            state_map = {"tg": "Telangana", "ap": "Andhra Pradesh", "ka": "Karnataka", "pb": "Punjab"}
                            state_hint = state_map.get(subparts[1])
            geocoded = await self.geocode(name_query, state_hint=state_hint)
            if geocoded is not None:
                lat, lon, tz = geocoded

        if lat is None or lon is None:
            logger.info("Unable to resolve coordinates for location %s", location_id)
            return None

        # 2. Fetch current weather and forecast
        client = await self._get_client()
        should_close = self._external_client is None
        try:
            params = {
                "latitude": f"{lat:.4f}",
                "longitude": f"{lon:.4f}",
                "current": "temperature_2m,relative_humidity_2m,precipitation,weather_code,wind_speed_10m",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum",
                "timezone": tz,
                "forecast_days": 3,
            }
            resp = None
            for attempt in range(2):
                try:
                    resp = await client.get(self._forecast_url, params=params)
                    if resp.status_code == 200:
                        break
                    elif resp.status_code >= 500 and attempt == 0:
                        await asyncio.sleep(0.5)
                        continue
                    else:
                        logger.warning("Open-Meteo forecast returned HTTP %d for lat=%f lon=%f", resp.status_code, lat, lon)
                        return None
                except (httpx.TimeoutException, httpx.NetworkError):
                    if attempt == 0:
                        await asyncio.sleep(0.5)
                        continue
                    raise

            if resp is None or resp.status_code != 200:
                return None
            data = resp.json()
            current = data.get("current")
            if not isinstance(current, dict):
                return None

            raw_temp = current.get("temperature_2m")
            raw_rain = current.get("precipitation")
            raw_humidity = current.get("relative_humidity_2m")
            raw_wind = current.get("wind_speed_10m")
            weather_code = current.get("weather_code")

            temp_c = float(raw_temp) if raw_temp is not None else None
            rain_mm = float(raw_rain) if raw_rain is not None else None
            humidity_pct = float(raw_humidity) if raw_humidity is not None else None
            wind_kmh = float(raw_wind) if raw_wind is not None else None

            # Weather observation requires at least temperature or rainfall
            if temp_c is None and rain_mm is None:
                return None

            weather_condition = (
                WMO_WEATHER_CODES.get(weather_code, f"WMO code {weather_code}")
                if weather_code is not None
                else None
            )

            # Build forecast summary
            daily = data.get("daily", {})
            forecast_summary = None
            if isinstance(daily, dict):
                dates = daily.get("time", [])
                max_temps = daily.get("temperature_2m_max", [])
                min_temps = daily.get("temperature_2m_min", [])
                rain_sums = daily.get("precipitation_sum", [])
                forecast_parts = []
                for idx in range(min(len(dates), 3)):
                    day_str = dates[idx]
                    t_max = f"{max_temps[idx]:g}°C" if idx < len(max_temps) and max_temps[idx] is not None else ""
                    t_min = f"{min_temps[idx]:g}°C" if idx < len(min_temps) and min_temps[idx] is not None else ""
                    r_sum = f"{rain_sums[idx]:g}mm rain" if idx < len(rain_sums) and rain_sums[idx] is not None else ""
                    part = f"{day_str}: {t_min}-{t_max}"
                    if r_sum:
                        part += f" ({r_sum})"
                    forecast_parts.append(part)
                if forecast_parts:
                    forecast_summary = "; ".join(forecast_parts)

            time_str = current.get("time") or datetime.now(timezone.utc).isoformat()
            observed_at = _parse_time(time_str, tz)
            retrieved_at = datetime.now(timezone.utc)

            # Ensure observation timestamp is <= retrieval timestamp
            if observed_at > retrieved_at:
                observed_at = retrieved_at

            ref_url = f"https://open-meteo.com/en/docs#latitude={lat:.4f}&longitude={lon:.4f}"
            source = EvidenceSource(
                source_id="external:open-meteo",
                source_identity="Open-Meteo Weather API",
                source_type=EvidenceSourceType.EXTERNAL_DATA,
                title=f"Open-Meteo Current Weather & Forecast ({lat:.2f}N, {lon:.2f}E)",
                reference=ContentReference(kind=ReferenceKind.URL, value=ref_url),
                publisher="Open-Meteo (open-meteo.com)",
            )

            return WeatherObservation(
                request_id=request_id,
                location_id=location_id,
                observed_at=observed_at,
                retrieved_at=retrieved_at,
                source=source,
                mode=EvidenceMode.LIVE,
                temperature_c=temp_c,
                rainfall_mm=rain_mm,
                relative_humidity_pct=humidity_pct,
                wind_speed_kmh=wind_kmh,
                weather_condition=weather_condition,
                forecast_summary=forecast_summary,
            )
        except (httpx.TimeoutException, httpx.HTTPError, asyncio.TimeoutError, KeyError, ValueError, json.JSONDecodeError, Exception) as exc:
            logger.warning("Open-Meteo observation failed for location %s: %s", location_id, exc)
            return None
        finally:
            if should_close:
                await client.aclose()
