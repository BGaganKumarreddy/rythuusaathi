"""Independent smoke test for Open-Meteo weather provider."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rythusaathi.agents.open_meteo import OpenMeteoWeatherProvider
from rythusaathi.domain.seed import LOCATION_REGISTRY


async def async_main(args: argparse.Namespace) -> int:
    provider = OpenMeteoWeatherProvider()
    location_query = args.location
    print("=" * 60)
    print("RythuSaathi — Open-Meteo Weather Provider Smoke Test")
    print("=" * 60)
    print(f"Location Query: {location_query}")

    # Check if registered location
    matching = LOCATION_REGISTRY.search(location_query)
    location_id = matching[0].location_id if matching else location_query
    print(f"Resolved Location ID: {location_id}")

    # Geocoding test
    geocoded = await provider.geocode(location_query)
    if geocoded:
        lat, lon, tz = geocoded
        print(f"Geocoding: SUCCESS ({lat:.4f} N, {lon:.4f} E, tz={tz})")
    else:
        print("Geocoding: FAILED or not found in Open-Meteo database")

    # Observation test
    req_id = f"smoke-w-{uuid.uuid4().hex[:8]}"
    print(f"\nDispatching live observation request (request_id={req_id})...")
    obs = await provider.get_observation(req_id, location_id)
    if obs is None:
        print("Observation outcome: UNAVAILABLE (no observation returned)")
        return 1

    print("Observation outcome: SUCCESS")
    print(f"  Observed At: {obs.observed_at.isoformat()}")
    print(f"  Retrieved At: {obs.retrieved_at.isoformat()}")
    if obs.temperature_c is not None:
        print(f"  Temperature: {obs.temperature_c:g} °C")
    if obs.rainfall_mm is not None:
        print(f"  Precipitation / Rain: {obs.rainfall_mm:g} mm")
    if obs.relative_humidity_pct is not None:
        print(f"  Relative Humidity: {obs.relative_humidity_pct:g}%")
    if obs.wind_speed_kmh is not None:
        print(f"  Wind Speed: {obs.wind_speed_kmh:g} km/h")
    if obs.weather_condition:
        print(f"  Weather Condition: {obs.weather_condition}")
    if obs.forecast_summary:
        print(f"  Forecast Summary: {obs.forecast_summary}")
    print(f"  Source Attribution: {obs.source.title} ({obs.source.publisher})")
    print(f"  Source URL: {obs.source.reference.value}")
    print("=" * 60)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Test Open-Meteo weather integration independently")
    parser.add_argument("--location", default="Warangal", help="Location name or ID (default: Warangal)")
    args = parser.parse_args()
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())

