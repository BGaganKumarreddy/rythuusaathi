"""Unit and regression tests for OpenMeteoWeatherProvider using mocked HTTP transport."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import unittest

import httpx

from rythusaathi.agents.open_meteo import (
    DEFAULT_FORECAST_URL,
    DEFAULT_GEOCODING_URL,
    OpenMeteoWeatherProvider,
)
from rythusaathi.domain import EvidenceMode, EvidenceStatus
from rythusaathi.domain.seed import LOCATION_REGISTRY


class WeatherProviderTests(unittest.IsolatedAsyncioTestCase):
    """Exhaustive test suite for OpenMeteoWeatherProvider."""

    # 1. Successful geocoding
    async def test_successful_geocoding(self):
        mock_geocoding_response = {
            "results": [
                {
                    "id": 1253184,
                    "name": "Warangal",
                    "latitude": 17.97014,
                    "longitude": 79.59405,
                    "country_code": "IN",
                    "country": "India",
                    "admin1": "Telangana",
                    "timezone": "Asia/Kolkata",
                }
            ]
        }

        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertIn("name=Warangal", str(request.url))
            return httpx.Response(200, json=mock_geocoding_response)

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        try:
            provider = OpenMeteoWeatherProvider(http_client=client)
            res = await provider.geocode("Warangal", state_hint="Telangana")
            self.assertIsNotNone(res)
            lat, lon, tz = res
            self.assertAlmostEqual(lat, 17.97014, places=4)
            self.assertAlmostEqual(lon, 79.59405, places=4)
            self.assertEqual(tz, "Asia/Kolkata")
        finally:
            await client.aclose()

    # 2. Successful current weather retrieval
    async def test_successful_current_weather_retrieval(self):
        mock_forecast_response = {
            "latitude": 17.97,
            "longitude": 79.59,
            "timezone": "Asia/Kolkata",
            "current": {
                "time": "2026-11-10T12:00",
                "temperature_2m": 28.5,
                "relative_humidity_2m": 58,
                "precipitation": 0.0,
                "weather_code": 0,
                "wind_speed_10m": 12.4,
            },
            "daily": {
                "time": ["2026-11-10", "2026-11-11", "2026-11-12"],
                "weather_code": [0, 1, 2],
                "temperature_2m_max": [31.0, 30.5, 29.8],
                "temperature_2m_min": [19.0, 18.5, 18.0],
                "precipitation_sum": [0.0, 0.0, 0.5],
            },
        }

        async def handler(request: httpx.Request) -> httpx.Response:
            if "search" in str(request.url):
                return httpx.Response(200, json={
                    "results": [{
                        "name": "Warangal",
                        "latitude": 17.97014,
                        "longitude": 79.59405,
                        "country_code": "IN",
                        "admin1": "Telangana",
                        "timezone": "Asia/Kolkata",
                    }]
                })
            return httpx.Response(200, json=mock_forecast_response)

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        try:
            provider = OpenMeteoWeatherProvider(http_client=client)
            obs = await provider.get_observation("req-1", "location:district:in-tg-warangal")
            self.assertIsNotNone(obs)
            self.assertEqual(obs.request_id, "req-1")
            self.assertEqual(obs.location_id, "location:district:in-tg-warangal")
            self.assertEqual(obs.temperature_c, 28.5)
            self.assertEqual(obs.rainfall_mm, 0.0)
            self.assertEqual(obs.relative_humidity_pct, 58.0)
            self.assertEqual(obs.wind_speed_kmh, 12.4)
            self.assertEqual(obs.weather_condition, "Clear sky")
            self.assertEqual(obs.mode, EvidenceMode.LIVE)
            self.assertIsNotNone(obs.source)
            self.assertIn("Open-Meteo", obs.source.title)
            self.assertIsNotNone(obs.observed_at)
            self.assertIsNotNone(obs.retrieved_at)
            self.assertLessEqual(obs.observed_at, obs.retrieved_at)
        finally:
            await client.aclose()

    # 3. Successful forecast retrieval summary
    async def test_successful_forecast_summary(self):
        mock_forecast_response = {
            "latitude": 17.97,
            "longitude": 79.59,
            "timezone": "Asia/Kolkata",
            "current": {
                "time": "2026-11-10T12:00",
                "temperature_2m": 27.0,
                "relative_humidity_2m": 60,
                "precipitation": 2.5,
                "weather_code": 61,
                "wind_speed_10m": 15.0,
            },
            "daily": {
                "time": ["2026-11-10", "2026-11-11", "2026-11-12"],
                "weather_code": [61, 63, 0],
                "temperature_2m_max": [29.0, 28.0, 31.0],
                "temperature_2m_min": [20.0, 19.5, 18.0],
                "precipitation_sum": [5.2, 8.4, 0.0],
            },
        }

        async def handler(request: httpx.Request) -> httpx.Response:
            if "search" in str(request.url):
                return httpx.Response(200, json={
                    "results": [{
                        "name": "Warangal",
                        "latitude": 17.97014,
                        "longitude": 79.59405,
                        "country_code": "IN",
                        "admin1": "Telangana",
                        "timezone": "Asia/Kolkata",
                    }]
                })
            return httpx.Response(200, json=mock_forecast_response)

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        try:
            provider = OpenMeteoWeatherProvider(http_client=client)
            obs = await provider.get_observation("req-2", "location:district:in-tg-warangal")
            self.assertIsNotNone(obs)
            self.assertEqual(obs.weather_condition, "Slight rain")
            self.assertIsNotNone(obs.forecast_summary)
            self.assertIn("2026-11-10", obs.forecast_summary)
            self.assertIn("5.2mm rain", obs.forecast_summary)
        finally:
            await client.aclose()

    # 4. Invalid location / unresolvable geocoding
    async def test_invalid_location_returns_none(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"results": []})

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        try:
            provider = OpenMeteoWeatherProvider(http_client=client)
            obs = await provider.get_observation("req-3", "location:nonexistent:place")
            self.assertIsNone(obs)
        finally:
            await client.aclose()

    # 5. Empty or incomplete response (missing current block)
    async def test_incomplete_response_returns_none(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"latitude": 17.97, "longitude": 79.59})

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        try:
            provider = OpenMeteoWeatherProvider(http_client=client)
            obs = await provider.get_observation("req-4", "location:district:in-tg-warangal")
            self.assertIsNone(obs)
        finally:
            await client.aclose()

    # 6. Response missing measured temperature and rainfall
    async def test_missing_measurements_returns_none(self):
        mock_response = {
            "latitude": 17.97,
            "longitude": 79.59,
            "current": {
                "time": "2026-11-10T12:00",
                "temperature_2m": None,
                "precipitation": None,
            },
        }

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=mock_response)

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        try:
            provider = OpenMeteoWeatherProvider(http_client=client)
            obs = await provider.get_observation("req-5", "location:district:in-tg-warangal")
            self.assertIsNone(obs)
        finally:
            await client.aclose()

    # 7. Timeout handling
    async def test_timeout_returns_none(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("Connection timed out", request=request)

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        try:
            provider = OpenMeteoWeatherProvider(http_client=client)
            obs = await provider.get_observation("req-6", "location:district:in-tg-warangal")
            self.assertIsNone(obs)
        finally:
            await client.aclose()

    # 8. HTTP 4xx and 5xx handling
    async def test_http_errors_return_none(self):
        for status_code in (400, 404, 500, 503):
            async def handler(request: httpx.Request, code=status_code) -> httpx.Response:
                return httpx.Response(code, text="Error")

            transport = httpx.MockTransport(handler)
            client = httpx.AsyncClient(transport=transport)
            try:
                provider = OpenMeteoWeatherProvider(http_client=client)
                obs = await provider.get_observation("req-7", "location:district:in-tg-warangal")
                self.assertIsNone(obs, f"Expected None for HTTP {status_code}")
            finally:
                await client.aclose()

    # 9. Invalid response structure / malformed JSON
    async def test_malformed_json_returns_none(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="Not JSON at all")

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        try:
            provider = OpenMeteoWeatherProvider(http_client=client)
            obs = await provider.get_observation("req-8", "location:district:in-tg-warangal")
            self.assertIsNone(obs)
        finally:
            await client.aclose()


if __name__ == "__main__":
    unittest.main()

