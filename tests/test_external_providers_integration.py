"""Integration tests for OpenMeteoWeatherProvider and DataGovInMarketProvider with RythuSaathi workflow agents."""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
import unittest

import httpx

from rythusaathi.agents import (
    DataGovInMarketProvider,
    FarmerRequest,
    OpenMeteoWeatherProvider,
    RythuSaathiWorkflow,
    WorkflowMode,
    empty_coverage_resolver,
)
from rythusaathi.agents.contracts import (
    AdvisoryResponse,
    AdvisoryStatus,
    AgentExecutionInput,
    AgentId,
    AgentResult,
    AgentStatus,
    FarmerIntent,
    IntakePayload,
    MarketQuote,
    WeatherObservation,
)
from rythusaathi.agents.workflow import WeatherAgent, MarketAgent
from rythusaathi.domain import (
    AdvisoryBoard,
    ContextFact,
    ContextResolutionRequest,
    CoverageRegistry,
    EvidenceMode,
    EvidenceStatus,
    FactState,
    LocationRecord,
    LocationRegistry,
    RegistryLevel,
    RegistryMetadata,
    WorkflowMetadata,
)
from rythusaathi.domain.resolution import ContextResolver
from rythusaathi.domain.seed import CROP_REGISTRY, LOCATION_RECORDS, LOCATION_REGISTRY_METADATA
from rythusaathi.knowledge import PersistentKnowledgeBase, HashEmbeddingProvider


class MockKnowledge:
    """Mock search for isolated agent testing without disk database."""
    def search(self, query: str, top_k: int = 4, filters=None):
        return []


class ExternalProvidersIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Integration verification for external weather and market data adapters."""

    def setUp(self):
        metadata = RegistryMetadata(
            registry_id="rythusaathi:test-coverage",
            version="v1",
            effective_from=date(2026, 1, 1),
            source_reference="test",
            scope_note="test",
        )
        self.locations = LocationRegistry(
            LOCATION_REGISTRY_METADATA,
            LOCATION_RECORDS + (
                LocationRecord(
                    location_id="location:district:in-tg-warangal",
                    name="Warangal",
                    level=RegistryLevel.DISTRICT,
                    parent_id="location:state:in-tg",
                    latitude=17.97014,
                    longitude=79.59405,
                    timezone="Asia/Kolkata",
                    source_reference="https://warangal.telangana.gov.in/",
                ),
            ),
        )
        self.resolver = ContextResolver(CROP_REGISTRY, self.locations, CoverageRegistry(metadata))

    # 1. Weather Agent with real adapter publishes evidence to board
    async def test_weather_agent_with_real_adapter_publishes_evidence_to_board(self):
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
            return httpx.Response(200, json=mock_forecast_response)

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        try:
            weather_provider = OpenMeteoWeatherProvider(http_client=client, locations=self.locations)
            agent = WeatherAgent(weather_provider)

            res_req = ContextResolutionRequest(
                request_id="test-req-w",
                user_id="u1",
                tenant_id="t1",
                crop_query="cotton",
                location_query="Warangal",
            )
            resolution = self.resolver.resolve(res_req)
            board = AdvisoryBoard(resolution.context)
            case = AgentExecutionInput(
                request=FarmerRequest(
                    request_id="test-req-w",
                    user_id="u1",
                    tenant_id="t1",
                    query="cotton in Warangal",
                ),
                resolution=resolution,
                board=board,
                intake_result=AgentResult(
                    agent_id=AgentId.INTAKE,
                    status=AgentStatus.COMPLETED,
                    started_at=datetime.now(timezone.utc),
                    completed_at=datetime.now(timezone.utc),
                ),
            )

            work = await agent.perform(case)
            self.assertEqual(work.status, AgentStatus.COMPLETED)
            self.assertIn("temperature 28.5 °C", work.summary)
            self.assertIn("humidity 58%", work.summary)
            self.assertIn("conditions Clear sky", work.summary)

            # Check evidence published to blackboard
            evidence = board.evidence
            self.assertEqual(len(evidence), 1)
            self.assertEqual(evidence[0].mode, EvidenceMode.LIVE)
            self.assertEqual(evidence[0].status, EvidenceStatus.AVAILABLE)
            self.assertIn("Open-Meteo", evidence[0].source.title)

            # Check finding published to blackboard
            findings = board.findings
            self.assertGreaterEqual(len(findings), 1)
            self.assertEqual(findings[0].agent_id, AgentId.WEATHER)
        finally:
            await client.aclose()

    # 2. Market Agent with real adapter publishes evidence to board
    async def test_market_agent_with_real_adapter_publishes_evidence_to_board(self):
        mock_mandi_response = {
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
            return httpx.Response(200, json=mock_mandi_response)

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        try:
            market_provider = DataGovInMarketProvider(api_key="mock-api-key", http_client=client, locations=self.locations)
            agent = MarketAgent(market_provider)

            res_req = ContextResolutionRequest(
                request_id="test-req-m",
                user_id="u1",
                tenant_id="t1",
                crop_query="cotton",
                location_query="Warangal",
            )
            resolution = self.resolver.resolve(res_req)
            board = AdvisoryBoard(resolution.context)
            case = AgentExecutionInput(
                request=FarmerRequest(
                    request_id="test-req-m",
                    user_id="u1",
                    tenant_id="t1",
                    query="cotton in Warangal",
                ),
                resolution=resolution,
                board=board,
                intake_result=AgentResult(
                    agent_id=AgentId.INTAKE,
                    status=AgentStatus.COMPLETED,
                    started_at=datetime.now(timezone.utc),
                    completed_at=datetime.now(timezone.utc),
                ),
            )

            work = await agent.perform(case)
            self.assertEqual(work.status, AgentStatus.COMPLETED)
            self.assertIn("Warangal: modal price 7100 Rs/Quintal", work.summary)
            self.assertIn("range 6800-7400 Rs/Quintal", work.summary)

            # Check evidence published to blackboard
            evidence = board.evidence
            self.assertEqual(len(evidence), 1)
            self.assertEqual(evidence[0].mode, EvidenceMode.LIVE)
            self.assertEqual(evidence[0].status, EvidenceStatus.AVAILABLE)
            self.assertIn("data.gov.in", evidence[0].source.source_identity)

            # Check finding published to blackboard
            findings = board.findings
            self.assertGreaterEqual(len(findings), 1)
            self.assertEqual(findings[0].agent_id, AgentId.MARKET)
        finally:
            await client.aclose()

    # 3. Acceptance Scenario: Cotton in Warangal, Telangana with both Weather & Mandi
    async def test_acceptance_scenario_cotton_warangal_telangana(self):
        """End-to-end acceptance scenario testing location resolution, weather, mandi, and coordinator."""
        weather_data = {
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

        mandi_data = {
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

        async def weather_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=weather_data)

        async def mandi_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=mandi_data)

        weather_client = httpx.AsyncClient(transport=httpx.MockTransport(weather_handler))
        mandi_client = httpx.AsyncClient(transport=httpx.MockTransport(mandi_handler))

        try:
            weather_provider = OpenMeteoWeatherProvider(http_client=weather_client, locations=self.locations)
            market_provider = DataGovInMarketProvider(api_key="acceptance-key", http_client=mandi_client, locations=self.locations)

            workflow = RythuSaathiWorkflow(
                resolver=self.resolver,
                knowledge=MockKnowledge(),
                weather_provider=weather_provider,
                market_provider=market_provider,
            )

            request = FarmerRequest(
                request_id="acceptance-scenario-1",
                user_id="farmer-1",
                tenant_id="tenant-1",
                query="My cotton crop in Warangal",
                crop_query="cotton",
                location_query="Warangal",
            )

            response = await workflow.run(request, mode=WorkflowMode.DETERMINISTIC)
            self.assertIsInstance(response, AdvisoryResponse)

            # 1. Location resolution succeeds
            self.assertEqual(response.context.crop.state, FactState.SUPPLIED)
            self.assertEqual(response.context.crop.value.display_name, "cotton")
            self.assertEqual(response.context.location.state, FactState.SUPPLIED)
            self.assertEqual(response.context.location.value.display_name, "Warangal")

            # 2. Weather & Mandi evidence published
            evidence_sources = {ev.source.source_id for ev in response.evidence}
            self.assertIn("external:open-meteo", evidence_sources)
            self.assertIn("external:data-gov-in", evidence_sources)

            # 3. Agent results completed
            agent_statuses = {ar.agent_id: ar.status for ar in response.agent_results}
            self.assertEqual(agent_statuses[AgentId.WEATHER], AgentStatus.COMPLETED)
            self.assertEqual(agent_statuses[AgentId.MARKET], AgentStatus.COMPLETED)

            # 4. Coordinator incorporates evidence into response text
            self.assertIn("Weather:", response.response_text)
            self.assertIn("temperature 28.5 °C", response.response_text)
            self.assertIn("Market:", response.response_text)
            self.assertIn("Warangal: modal price 7100 Rs/Quintal", response.response_text)

        finally:
            await weather_client.aclose()
            await mandi_client.aclose()

    # 4. Coordinator handles unavailable weather and market without fabricating data
    async def test_coordinator_handles_unavailable_providers(self):
        """When providers return None, Coordinator reports unavailable without fabricating values."""
        workflow = RythuSaathiWorkflow(
            resolver=self.resolver,
            knowledge=MockKnowledge(),
            weather_provider=None,  # Defaults to UnavailableWeatherProvider
            market_provider=None,   # Defaults to UnavailableMarketProvider
        )

        request = FarmerRequest(
            request_id="unavailable-test-1",
            user_id="farmer-1",
            tenant_id="tenant-1",
            query="cotton in Warangal",
            crop_query="cotton",
            location_query="Warangal",
        )

        response = await workflow.run(request, mode=WorkflowMode.DETERMINISTIC)
        self.assertIsInstance(response, AdvisoryResponse)

        # Agent results indicate unavailable
        agent_statuses = {ar.agent_id: ar.status for ar in response.agent_results}
        self.assertEqual(agent_statuses[AgentId.WEATHER], AgentStatus.UNAVAILABLE)
        self.assertEqual(agent_statuses[AgentId.MARKET], AgentStatus.UNAVAILABLE)

        # Response text states honest provider unavailability; no fabricated data
        self.assertIn("No weather provider observation is available", response.response_text)
        self.assertIn("No market provider quote is available", response.response_text)


if __name__ == "__main__":
    unittest.main()
