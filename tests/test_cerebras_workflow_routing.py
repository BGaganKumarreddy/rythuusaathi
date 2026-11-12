"""Regression tests for Phase 5 full-workflow Cerebras routing and context propagation."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock

from agent_framework import ChatResponse, Message
from pydantic import BaseModel

from rythusaathi.agents.contracts import (
    AdvisoryResponse,
    AdvisoryStatus,
    AgentId,
    AgentResult,
    AgentStatus,
    FarmerRequest,
    MarketQuote,
    ProviderPayload,
    RetrievalPayload,
    WeatherObservation,
    WorkflowMode,
)
from rythusaathi.agents.llm import (
    AuthenticatedCerebrasInference,
    AuthenticatedWorkflowInference,
    CoordinatorDraft,
    GroundedStatement,
    IntakeInterpretation,
    QuotedMention,
    QuotedObservation,
    SpecialistInterpretation,
)
from rythusaathi.agents.workflow import (
    RythuSaathiWorkflow,
    default_coverage_resolver,
    empty_coverage_resolver,
)
from rythusaathi.credentials import (
    CredentialDecryptionFailed,
    CredentialRecoveryRequired,
    CredentialUnavailable,
)
from rythusaathi.domain import (
    ContentReference,
    ContextFact,
    ContextResolutionRequest,
    EvidenceMode,
    EvidenceRecord,
    EvidenceSource,
    EvidenceSourceType,
    EvidenceStatus,
    FactState,
    ReferenceKind,
    ResolutionStatus,
    WorkflowMetadata,
)
from rythusaathi.domain.seed import (
    COTTON,
    CROP_REGISTRY,
    LOCATION_REGISTRY,
    SEEDED_COVERAGE_REGISTRY,
)
from rythusaathi.inference.contracts import (
    InferenceDiagnostic,
    InferenceDiagnosticCode,
    InferenceRequest,
    InferenceStage,
    InferenceTokenUsage,
    ProviderFailure,
    ProviderFailureKind,
    ProviderResponse,
)
from rythusaathi.inference.service import AuthenticatedIdentityMismatch
from rythusaathi.knowledge import (
    HashEmbeddingProvider,
    PersistentKnowledgeBase,
)
from rythusaathi.security import AuthorizationDenied


def _message_text(message: Message) -> str:
    return "".join(item if isinstance(item, str) else getattr(item, "text", "")
                   for item in message.contents)


class MockCerebrasService:
    provider_id = "cerebras"
    model_id = "gpt-oss-120b"

    def __init__(self, *, remaining_budget: int = 25000):
        self.dispatched_requests: list[InferenceRequest] = []
        self.reservations: list[int] = []
        self.released: list[list[float | int]] = []
        self._remaining_budget = remaining_budget

    @property
    def remaining_budget_tokens(self) -> int:
        return self._remaining_budget

    def reserve_budget(self, token_count: int) -> list[float | int]:
        self.reservations.append(token_count)
        return [float(token_count), 1]

    def release_reservation(self, reservation: list[float | int] | None) -> None:
        if reservation is not None:
            self.released.append(reservation)

    async def complete(self, session_token: str, credential_id: str,
                        request: InferenceRequest, *,
                        expected_user_id: str, expected_tenant_id: str) -> ProviderResponse:
        self.dispatched_requests.append(request)
        system = _message_text(request.messages[0])
        user_json = _message_text(request.messages[1])
        agent_name = request.agent_name

        if agent_name == "intake" or "Extract only candidate mentions" in system:
            draft = IntakeInterpretation(
                crops=[QuotedMention(value="cotton", quote="cotton")],
                locations=[QuotedMention(value="Adilabad", quote="Adilabad")],
                growth_stages=[QuotedMention(value="flowering", quote="flowering")],
                intents=["disease_or_pest"],
                observations=[QuotedObservation(label="leaf spots", quote="leaf spots")],
                farming_problem=QuotedObservation(label="leaf spots", quote="leaf spots"),
            )
            content = draft.model_dump_json()
        elif agent_name in {"weather", "crop", "market", "scheme"} or "You are a specialist agricultural domain agent" in system:
            user_data = json.loads(user_json)["task_input"]
            ev_list = user_data.get("evidence", [])
            ev_ids = [ev["evidence_id"] for ev in ev_list] if ev_list else ["ev:mock:01"]
            draft = SpecialistInterpretation(
                summary=f"Specialist assessment for {agent_name}.",
                statements=[
                    GroundedStatement(
                        text=f"{agent_name.title()} context observed from evidence.",
                        kind="interpretation",
                        evidence_ids=ev_ids[:1],
                    )
                ],
            )
            content = draft.model_dump_json()
        elif agent_name == "coordinator" or "Synthesize the supplied typed agent results" in system:
            user_data = json.loads(user_json)["task_input"]
            cat = user_data.get("evidence_catalog", [])
            cited_id = cat[0]["evidence_id"] if cat else "ev:mock:01"
            draft = CoordinatorDraft(
                summary="Unified advisory for cotton in Adilabad during flowering stage.",
                statements=[
                    GroundedStatement(
                        text="Cotton crop in Adilabad is at flowering stage.",
                        kind="evidence",
                        evidence_ids=[cited_id],
                    )
                ],
                follow_up_questions=[],
            )
            content = draft.model_dump_json()
        else:
            content = "{}"

        msg = Message("assistant", [content])
        chat_resp = ChatResponse(messages=[msg])
        usage = InferenceTokenUsage(
            agent_name=agent_name,
            model_id=self.model_id,
            estimated_input_tokens=100,
            output_token_limit=1024,
            actual_input_tokens=100,
            actual_output_tokens=150,
            actual_total_tokens=250,
            budget_limit_tokens=30000,
            budget_remaining_tokens=25000,
            elapsed_seconds=0.1,
            http_status=200,
        )
        return ProviderResponse(self.provider_id, self.model_id, chat_resp, usage=usage)


class MockFailingService:
    provider_id = "cerebras"
    model_id = "gpt-oss-120b"

    def __init__(self, exc: Exception):
        self._exc = exc

    async def complete(self, *args, **kwargs):
        raise self._exc


class TestCerebrasWorkflowRouting(unittest.IsolatedAsyncioTestCase):
    """Verify provider routing, credential scoping, and evidence provenance under Cerebras mode."""

    async def test_diagnostic_provider_is_cerebras_on_credential_exceptions(self):
        """Inference diagnostics must report provider='cerebras' instead of leaking 'groq'."""
        exceptions_to_test = [
            CredentialUnavailable(),
            AuthorizationDenied(),
            PermissionError("session invalid"),
            AuthenticatedIdentityMismatch("identity mismatch"),
            CredentialDecryptionFailed(),
            CredentialRecoveryRequired(),
            RuntimeError("unexpected network failure"),
        ]

        for exc in exceptions_to_test:
            with self.subTest(exc=type(exc).__name__):
                service = MockFailingService(exc)
                inference = AuthenticatedCerebrasInference(service, "valid-session", "valid-cred")
                request = InferenceRequest(
                    messages=[Message("user", ["test"])],
                    agent_name="intake",
                )
                with self.assertRaises(ProviderFailure) as caught:
                    await inference.complete(
                        request,
                        expected_user_id="user-01",
                        expected_tenant_id="tenant-01",
                    )
                diag = caught.exception.diagnostic
                self.assertEqual(diag.provider, "cerebras")
                self.assertEqual(diag.model_id, "gpt-oss-120b")
                self.assertNotEqual(diag.provider, "groq")

    def test_cotton_growth_stage_flowering_resolution(self):
        """Cotton crop record in seed.py must support flowering and other stages."""
        cotton = CROP_REGISTRY.get("crop:gossypium-hirsutum")
        self.assertIsNotNone(cotton)
        stage_ids = {s.stage_id for s in cotton.growth_stages}
        self.assertIn("flowering", stage_ids)
        self.assertIn("vegetative", stage_ids)
        self.assertIn("squaring", stage_ids)
        self.assertIn("boll-development", stage_ids)
        self.assertIn("maturity", stage_ids)

        resolver = default_coverage_resolver()
        res_req = ContextResolutionRequest(
            request_id="req-test-cotton",
            user_id="user-01",
            tenant_id="tenant-01",
            crop_query="cotton",
            location_query="Adilabad",
            growth_stage_query="flowering",
            workflow=WorkflowMetadata(trace_id="req-test-cotton", stage="test"),
            as_of=date(2026, 6, 1),
        )
        resolved = resolver.resolve(res_req)
        self.assertEqual(resolved.crop.status, ResolutionStatus.MATCHED)
        self.assertEqual(resolved.location.status, ResolutionStatus.MATCHED)
        self.assertEqual(resolved.growth_stage.status, ResolutionStatus.MATCHED)
        self.assertEqual(resolved.coverage.status, ResolutionStatus.MATCHED)

        self.assertEqual(resolved.context.crop.state, FactState.SUPPLIED)
        self.assertEqual(resolved.context.location.state, FactState.SUPPLIED)
        self.assertEqual(resolved.context.growth_stage.state, FactState.SUPPLIED)
        self.assertEqual(resolved.context.growth_stage.value, "flowering")

    def test_seeded_coverage_registry(self):
        """Seeded coverage registry must match registered Cotton / Adilabad."""
        match_result = SEEDED_COVERAGE_REGISTRY.assess(
            "crop:gossypium-hirsutum", "location:district:in-tg-adilabad", on_date=date(2026, 6, 1)
        )
        self.assertEqual(match_result.status, ResolutionStatus.MATCHED)

        unsupported_result = SEEDED_COVERAGE_REGISTRY.assess(
            "crop:gossypium-hirsutum", "location:district:in-ka-mysuru", on_date=date(2026, 6, 1)
        )
        self.assertEqual(unsupported_result.status, ResolutionStatus.UNSUPPORTED)

    async def test_full_workflow_routes_all_agents_to_cerebras(self):
        """Selecting WorkflowMode.CEREBRAS must route all agents to Cerebras without Groq."""
        service = MockCerebrasService()
        inference = AuthenticatedCerebrasInference(service, "test-session", "test-cred")

        class MockWeather:
            async def get_observation(self, req_id, loc_id):
                return WeatherObservation(
                    request_id=req_id,
                    location_id=loc_id,
                    observed_at=datetime.now(timezone.utc),
                    retrieved_at=datetime.now(timezone.utc),
                    source=EvidenceSource(
                        source_id="src:weather:open-meteo",
                        source_identity="Open-Meteo Weather API",
                        title="Open-Meteo",
                        source_type=EvidenceSourceType.EXTERNAL_DATA,
                        reference=ContentReference(kind=ReferenceKind.URL, value="https://api.open-meteo.com"),
                    ),
                    mode=EvidenceMode.LIVE,
                    temperature_c=28.5,
                    rainfall_mm=0.0,
                    relative_humidity_pct=72.0,
                )

        class MockMarket:
            async def get_quote(self, req_id, crop_id, loc_id):
                return MarketQuote(
                    request_id=req_id,
                    crop_id=crop_id,
                    location_id=loc_id,
                    market_name="Adilabad APMC",
                    price=7100.0,
                    unit="INR/quintal",
                    observed_at=datetime.now(timezone.utc),
                    retrieved_at=datetime.now(timezone.utc),
                    source=EvidenceSource(
                        source_id="src:market:data-gov-in",
                        source_identity="data.gov.in Mandi Daily Prices",
                        title="Agmarknet",
                        source_type=EvidenceSourceType.PRIMARY_GOVERNMENT,
                        reference=ContentReference(kind=ReferenceKind.URL, value="https://api.data.gov.in"),
                    ),
                    mode=EvidenceMode.LIVE,
                    modal_price=7100.0,
                )

        tmp_db = Path("tmp/test-kb-routing.sqlite3")
        tmp_db.parent.mkdir(parents=True, exist_ok=True)
        kb = PersistentKnowledgeBase(tmp_db, HashEmbeddingProvider())

        workflow = RythuSaathiWorkflow(
            resolver=default_coverage_resolver(),
            knowledge=kb,
            weather_provider=MockWeather(),
            market_provider=MockMarket(),
        )

        request = FarmerRequest(
            request_id="req-cerebras-routing-01",
            user_id="user-01",
            tenant_id="tenant-01",
            query="My cotton crop has leaf spots in Adilabad at flowering stage.",
            crop_query="cotton",
            location_query="Adilabad",
            growth_stage_query="flowering",
        )

        response = await workflow.run(
            request,
            mode=WorkflowMode.CEREBRAS,
            inference=inference,
        )

        self.assertEqual(response.execution_mode, WorkflowMode.CEREBRAS)
        self.assertEqual(response.provider_id, "cerebras")
        self.assertEqual(response.model_id, "gpt-oss-120b")

        # Verify Coordinator reservation used 3,500 tokens for Cerebras
        self.assertIn(3500, service.reservations)

        # Dispatched requests should only have been to Cerebras
        dispatched_agents = [req.agent_name for req in service.dispatched_requests]
        self.assertIn("intake", dispatched_agents)
        self.assertIn("weather", dispatched_agents)
        self.assertIn("market", dispatched_agents)
        self.assertIn("coordinator", dispatched_agents)

        # Verify each agent result contains provider_id and model_id
        for res in response.agent_results:
            if res.agent_id != AgentId.CROP:
                self.assertEqual(res.provider_id, "cerebras")
                self.assertEqual(res.model_id, "gpt-oss-120b")

        # Verify Crop agent was honestly UNAVAILABLE with 0 evidence rather than inventing evidence
        crop_res = next(r for r in response.agent_results if r.agent_id is AgentId.CROP)
        self.assertEqual(crop_res.status, AgentStatus.UNAVAILABLE)
        self.assertEqual(len(crop_res.evidence_ids), 0)

        # Clean up
        kb.close()
        if tmp_db.is_file():
            try:
                tmp_db.unlink()
            except OSError:
                pass

    async def test_live_evidence_provenance_not_labelled_synthetic(self):
        """Live weather/market data must not be labelled as synthetic fixtures when specialist fails."""
        # Service fails during specialist inference (weather/market)
        class FailingSpecialistService(MockCerebrasService):
            async def complete(self, session_token, credential_id, request, **kwargs):
                if request.agent_name in {"weather", "market"}:
                    raise ProviderFailure(ProviderFailureKind.TIMEOUT)
                return await super().complete(session_token, credential_id, request, **kwargs)

        service = FailingSpecialistService()
        inference = AuthenticatedCerebrasInference(service, "test-session", "test-cred")

        class MockWeather:
            async def get_observation(self, req_id, loc_id):
                return WeatherObservation(
                    request_id=req_id,
                    location_id=loc_id,
                    observed_at=datetime.now(timezone.utc),
                    retrieved_at=datetime.now(timezone.utc),
                    source=EvidenceSource(
                        source_id="src:weather:open-meteo",
                        source_identity="Open-Meteo Weather API",
                        title="Open-Meteo",
                        source_type=EvidenceSourceType.EXTERNAL_DATA,
                        reference=ContentReference(kind=ReferenceKind.URL, value="https://api.open-meteo.com"),
                    ),
                    mode=EvidenceMode.LIVE,
                    temperature_c=29.0,
                    rainfall_mm=0.0,
                )

        tmp_db = Path("tmp/test-kb-provenance.sqlite3")
        tmp_db.parent.mkdir(parents=True, exist_ok=True)
        kb = PersistentKnowledgeBase(tmp_db, HashEmbeddingProvider())

        workflow = RythuSaathiWorkflow(
            resolver=default_coverage_resolver(),
            knowledge=kb,
            weather_provider=MockWeather(),
        )

        request = FarmerRequest(
            request_id="req-provenance-01",
            user_id="user-01",
            tenant_id="tenant-01",
            query="Cotton in Adilabad flowering",
            crop_query="cotton",
            location_query="Adilabad",
            growth_stage_query="flowering",
        )

        response = await workflow.run(
            request,
            mode=WorkflowMode.CEREBRAS,
            inference=inference,
        )

        # Weather result should be PARTIAL because provider succeeded but LLM interpretation timed out
        weather_res = next(r for r in response.agent_results if r.agent_id is AgentId.WEATHER)
        self.assertEqual(weather_res.status, AgentStatus.PARTIAL)

        # The final advisory text MUST NOT claim live provider data is synthetic fixture!
        self.assertNotIn("The provider returned fixture data; it is synthetic", response.response_text)

        kb.close()
        if tmp_db.is_file():
            try:
                tmp_db.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
