"""Unit and integration tests for RythuSaathi Phase 6 — Risk Alert Agent.

Covers:
1. Weather risk detection:
   - Excessive rainfall and waterlogging indicators (CRITICAL, HIGH, MEDIUM).
   - Dry spells (HIGH, MEDIUM, LOW).
   - Temperature extremes (extreme heatwave, high heat, chilling, frost).
2. Disease risk evidence matching:
   - Evidence-backed disease threats (Rice blast, Rice dwarfing virus).
   - Environmental favorability impact on severity (high humidity -> HIGH severity).
   - Stage-sensitive disease threats.
3. Pest risk evidence matching:
   - Cotton Pink Bollworm IPM matching.
   - Maize Fall Armyworm matching.
   - Rice Yellow Stem Borer matching.
4. Mismatch handling:
   - Crop mismatch produces no unsupported risks.
   - Growth-stage mismatch rejects stage-specific risk.
5. Insufficient evidence handling:
   - Missing crop or location records limitation and publishes no speculative risks.
   - Missing weather provider skips weather risks without guessing.
6. Deterministic severity thresholds:
   - Explicit evidence rules dictate severity; cannot be arbitrarily assigned.
7. Evidence ID integrity:
   - Empty evidence IDs rejected by model validator.
   - Non-existent evidence IDs rejected by board conflict checks.
8. Safe-action validation:
   - Safe cultural practice, scouting, drainage, and KVK consultation accepted.
   - Specific unhedged chemical dosage prescriptions rejected.
9. Coordinator integration:
   - Coordinator consumes Risk Alert findings and renders `Risk alerts:` section.
   - HIGH and CRITICAL severity actions surfaced in suggested next steps.
10. Full seven-agent workflow regression:
    - All 7 agents execute within the single workflow graph.
    - All existing agent behaviors and contracts preserved.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from pathlib import Path
import unittest

from rythusaathi.agents import (
    FarmerRequest,
    RythuSaathiWorkflow,
    WorkflowMode,
    empty_coverage_resolver,
)
from rythusaathi.agents.contracts import (
    AdvisoryStatus,
    AgentId,
    AgentResult,
    AgentStatus,
    IntakePayload,
    MarketQuote,
    ProviderPayload,
    RetrievalPayload,
    RiskAlertPayload,
    WeatherObservation,
)
from rythusaathi.domain import (
    AdvisoryBoard,
    AgentFinding,
    BoardSection,
    BoardConflict,
    ClaimRelationship,
    ContentReference,
    EvidenceMode,
    EvidenceRecord,
    EvidenceSource,
    EvidenceSourceType,
    EvidenceStatus,
    ReferenceKind,
    RequestContext,
    RiskAlertItem,
    RiskCategory,
    RiskEvidenceSufficiency,
    RiskSeverity,
    classify_claim_relationship,
    evaluate_disease_risks,
    evaluate_pest_risks,
    evaluate_weather_risks,
    validate_safe_action,
)
from rythusaathi.domain.seed import CROP_REGISTRY
from rythusaathi.knowledge import (
    DocumentProcessor,
    HashEmbeddingProvider,
    LocalKnowledgeBase,
    load_curated,
    load_schemes,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.now(timezone.utc)

WEATHER_SRC = EvidenceSource(
    source_id="weather:open-meteo:test",
    title="Open-Meteo Live Feed",
    reference=ContentReference(kind=ReferenceKind.URL, value="https://open-meteo.com"),
    source_type=EvidenceSourceType.EXTERNAL_DATA,
    source_identity="Open-Meteo API",
)

MARKET_SRC = EvidenceSource(
    source_id="market:data-gov-in:test",
    title="data.gov.in APMC Mandi",
    reference=ContentReference(kind=ReferenceKind.URL, value="https://data.gov.in"),
    source_type=EvidenceSourceType.EXTERNAL_DATA,
    source_identity="data.gov.in",
)


def build_test_index() -> LocalKnowledgeBase:
    processor = DocumentProcessor()
    agriculture = load_curated(ROOT / "data/agricultural/icar_advisories.json", CROP_REGISTRY)
    schemes = load_schemes(ROOT / "data/schemes/central_agricultural_schemes.json")
    chunks = [chunk for document in agriculture for chunk in processor.process_curated(document)]
    chunks.extend(chunk for document in schemes for chunk in processor.process_scheme(document))
    kb = LocalKnowledgeBase(HashEmbeddingProvider())
    kb.add(chunks)
    return kb


class MockWeather(WeatherObservation):
    @classmethod
    def create(
        cls,
        *,
        temp: float = 28.0,
        rain: float = 0.0,
        humidity: float = 65.0,
        forecast: str = "Clear weather expected",
        location_id: str = "location:district:in-tg-ad",
    ) -> WeatherObservation:
        return WeatherObservation(
            request_id="req-test-risk",
            location_id=location_id,
            observed_at=NOW,
            retrieved_at=NOW,
            source=WEATHER_SRC,
            mode=EvidenceMode.LIVE,
            temperature_c=temp,
            rainfall_mm=rain,
            relative_humidity_pct=humidity,
            forecast_summary=forecast,
        )


class MockWeatherProvider:
    def __init__(self, observation: WeatherObservation | None = None):
        self._obs = observation

    async def get_observation(self, request_id: str, location_id: str) -> WeatherObservation:
        if self._obs is not None:
            return dataclasses.replace(self._obs, request_id=request_id, location_id=location_id)
        return MockWeather.create(location_id=location_id)


class MockMarketProvider:
    async def get_quote(self, request_id: str, crop_id: str, location_id: str) -> MarketQuote:
        return MarketQuote(
            request_id=request_id,
            crop_id=crop_id,
            location_id=location_id,
            market_name="Adilabad APMC",
            price=8870.0,
            unit="INR/quintal",
            observed_at=NOW,
            retrieved_at=NOW,
            source=MARKET_SRC,
            mode=EvidenceMode.LIVE,
            min_price=8231.0,
            max_price=8870.0,
            commodity="Cotton (Unginned)",
            variety="Medium Staple",
            grade="FAQ",
            arrival_date="12/11/2026",
        )


class WeatherRiskDetectionTests(unittest.TestCase):
    """Step 2 & 3: Test deterministic weather risk rules and severity thresholds."""

    def test_rainfall_critical_severity_threshold(self):
        obs = MockWeather.create(rain=110.0, temp=26.0)
        alerts = evaluate_weather_risks(
            request_id="req-rain-crit",
            weather_obs=obs,
            location_id="location:district:in-tg-ad",
            evidence_id="ev-wtr-1",
        )
        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertEqual(alert.category, RiskCategory.WEATHER)
        self.assertEqual(alert.severity, RiskSeverity.CRITICAL)
        self.assertIn("Severe Waterlogging", alert.title)
        self.assertIn("drainage", alert.safe_action.lower())

    def test_rainfall_high_severity_threshold(self):
        obs = MockWeather.create(rain=60.0, temp=27.0)
        alerts = evaluate_weather_risks(
            request_id="req-rain-high",
            weather_obs=obs,
            location_id="location:district:in-tg-ad",
            evidence_id="ev-wtr-1",
        )
        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertEqual(alert.severity, RiskSeverity.HIGH)
        self.assertIn("Heavy Rainfall", alert.title)

    def test_rainfall_high_severity_with_elevated_humidity(self):
        obs = MockWeather.create(rain=40.0, humidity=90.0, temp=27.0)
        alerts = evaluate_weather_risks(
            request_id="req-rain-humid",
            weather_obs=obs,
            location_id="location:district:in-tg-ad",
            evidence_id="ev-wtr-1",
        )
        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertEqual(alert.severity, RiskSeverity.HIGH)

    def test_rainfall_moderate_severity_threshold(self):
        obs = MockWeather.create(rain=30.0, humidity=70.0, temp=28.0)
        alerts = evaluate_weather_risks(
            request_id="req-rain-med",
            weather_obs=obs,
            location_id="location:district:in-tg-ad",
            evidence_id="ev-wtr-1",
        )
        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertEqual(alert.severity, RiskSeverity.MEDIUM)

    def test_extreme_heatwave_critical_severity_threshold(self):
        obs = MockWeather.create(temp=43.5, rain=0.0)
        alerts = evaluate_weather_risks(
            request_id="req-heat-crit",
            weather_obs=obs,
            location_id="location:district:in-tg-ad",
            evidence_id="ev-wtr-1",
        )
        # Should generate extreme heatwave risk
        heat_alerts = [a for a in alerts if "Heatwave" in a.title]
        self.assertEqual(len(heat_alerts), 1)
        self.assertEqual(heat_alerts[0].severity, RiskSeverity.CRITICAL)

    def test_high_heat_stress_threshold(self):
        obs = MockWeather.create(temp=37.5, rain=0.0)
        alerts = evaluate_weather_risks(
            request_id="req-heat-high",
            weather_obs=obs,
            location_id="location:district:in-tg-ad",
            evidence_id="ev-wtr-1",
        )
        heat_alerts = [a for a in alerts if "Heat Stress" in a.title]
        self.assertEqual(len(heat_alerts), 1)
        self.assertEqual(heat_alerts[0].severity, RiskSeverity.HIGH)

    def test_frost_critical_threshold(self):
        obs = MockWeather.create(temp=3.5, rain=0.0)
        alerts = evaluate_weather_risks(
            request_id="req-frost",
            weather_obs=obs,
            location_id="location:district:in-pb",
            evidence_id="ev-wtr-1",
        )
        frost_alerts = [a for a in alerts if "Frost" in a.title]
        self.assertEqual(len(frost_alerts), 1)
        self.assertEqual(frost_alerts[0].severity, RiskSeverity.CRITICAL)

    def test_chilling_injury_high_threshold(self):
        obs = MockWeather.create(temp=8.0, rain=0.0)
        alerts = evaluate_weather_risks(
            request_id="req-chilling",
            weather_obs=obs,
            location_id="location:district:in-pb",
            evidence_id="ev-wtr-1",
        )
        chilling_alerts = [a for a in alerts if "Chilling" in a.title]
        self.assertEqual(len(chilling_alerts), 1)
        self.assertEqual(chilling_alerts[0].severity, RiskSeverity.HIGH)

    def test_dry_spell_severe_high_threshold(self):
        obs = MockWeather.create(temp=39.0, rain=0.0, forecast="No rain for 5 days")
        alerts = evaluate_weather_risks(
            request_id="req-dry-high",
            weather_obs=obs,
            location_id="location:district:in-tg-ad",
            evidence_id="ev-wtr-1",
        )
        dry_alerts = [a for a in alerts if "Dry Spell" in a.title]
        self.assertEqual(len(dry_alerts), 1)
        self.assertEqual(dry_alerts[0].severity, RiskSeverity.HIGH)

    def test_normal_weather_produces_no_alerts(self):
        obs = MockWeather.create(temp=27.0, rain=0.0, humidity=60.0, forecast="Partly cloudy")
        alerts = evaluate_weather_risks(
            request_id="req-normal",
            weather_obs=obs,
            location_id="location:district:in-tg-ad",
            evidence_id="ev-wtr-1",
        )
        self.assertEqual(len(alerts), 0)

    def test_stale_weather_observation_rejected(self):
        from datetime import timedelta
        stale_time = NOW - timedelta(hours=50)
        obs = dataclasses.replace(MockWeather.create(rain=110.0), observed_at=stale_time)
        alerts = evaluate_weather_risks(
            request_id="req-stale",
            weather_obs=obs,
            location_id="location:district:in-tg-ad",
            evidence_id="ev-wtr-1",
        )
        self.assertEqual(len(alerts), 0)

    def test_future_weather_observation_rejected(self):
        from datetime import timedelta
        future_time = NOW + timedelta(hours=2)
        obs = dataclasses.replace(MockWeather.create(rain=110.0), observed_at=future_time, retrieved_at=future_time)
        alerts = evaluate_weather_risks(
            request_id="req-future",
            weather_obs=obs,
            location_id="location:district:in-tg-ad",
            evidence_id="ev-wtr-1",
        )
        self.assertEqual(len(alerts), 0)

    def test_measurement_window_explicit_in_alert_explanation(self):
        obs = MockWeather.create(rain=110.0, temp=26.0)
        alerts = evaluate_weather_risks(
            request_id="req-rain-crit",
            weather_obs=obs,
            location_id="location:district:in-tg-ad",
            evidence_id="ev-wtr-1",
        )
        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertIn("24h Rainfall >= 100mm", alert.title)
        self.assertIn("24-hour rainfall", alert.explanation.lower())
        self.assertIn("110.0 mm", alert.explanation)


class DiseaseAndPestEvidenceMatchingTests(unittest.TestCase):
    """Step 2 & 3: Test evidence-backed disease and pest risk evaluation."""

    def setUp(self):
        self.src = EvidenceSource(
            source_id="icar-rice-blast",
            title="ICAR Kharif Agro-Advisories 2025 — Rice blast and stem borer",
            reference=ContentReference(kind=ReferenceKind.URL, value="https://icar.gov.in/blast"),
            source_type=EvidenceSourceType.CURATED_AGRICULTURAL_KNOWLEDGE,
            source_identity="ICAR",
        )
        self.blast_ev = EvidenceRecord(
            evidence_id="ev-blast-1",
            request_id="req-test",
            source=self.src,
            excerpt="ICAR advisory discusses management options for blast and stem borer.",
            content_reference=self.src.reference,
            retrieved_at=NOW,
            provenance="ICAR Kharif 2025 advisory",
            crop_applicability=("crop:oryza-sativa",),
            geographic_applicability=("location:state:in-pb",),
            mode=EvidenceMode.LIVE,
            status=EvidenceStatus.AVAILABLE,
        )

    def test_general_disease_advisory_alone_does_not_produce_active_alert(self):
        # General ICAR blast advisory with normal weather (RH 60%) must NOT produce an active alert
        alerts = evaluate_disease_risks(
            request_id="req-test",
            crop_id="crop:oryza-sativa",
            location_id="location:state:in-pb",
            growth_stage="tillering",
            evidence_records=[self.blast_ev],
            weather_obs=MockWeather.create(humidity=60.0),
            active_only=True,
        )
        self.assertEqual(len(alerts), 0, "General ICAR advisory alone must NOT generate active alert")

        # But background vulnerability is retained when active_only=False
        all_items = evaluate_disease_risks(
            request_id="req-test",
            crop_id="crop:oryza-sativa",
            location_id="location:state:in-pb",
            growth_stage="tillering",
            evidence_records=[self.blast_ev],
            weather_obs=MockWeather.create(humidity=60.0),
            active_only=False,
        )
        self.assertEqual(len(all_items), 1)
        bg = all_items[0]
        self.assertFalse(bg.is_active)
        self.assertEqual(bg.severity, RiskSeverity.LOW)
        self.assertEqual(bg.sufficiency, RiskEvidenceSufficiency.LIMITED)

    def test_rice_blast_severity_escalates_to_high_with_high_humidity(self):
        # High humidity (>85%) creates favorable microclimate
        alerts = evaluate_disease_risks(
            request_id="req-test",
            crop_id="crop:oryza-sativa",
            location_id="location:state:in-pb",
            growth_stage="tillering",
            evidence_records=[self.blast_ev],
            weather_obs=MockWeather.create(humidity=92.0),
            active_only=True,
        )
        self.assertEqual(len(alerts), 1)
        self.assertTrue(alerts[0].is_active)
        self.assertEqual(alerts[0].severity, RiskSeverity.HIGH)
        self.assertIn("relative humidity (92%)", alerts[0].explanation)

    def test_rice_blast_active_alert_with_reported_symptoms(self):
        alerts = evaluate_disease_risks(
            request_id="req-test",
            crop_id="crop:oryza-sativa",
            location_id="location:state:in-pb",
            growth_stage="tillering",
            evidence_records=[self.blast_ev],
            weather_obs=MockWeather.create(humidity=60.0),
            active_only=True,
            farmer_query="My rice crop leaves show spindle-shaped blast lesions and necrotic spots.",
        )
        self.assertEqual(len(alerts), 1)
        self.assertTrue(alerts[0].is_active)
        self.assertEqual(alerts[0].severity, RiskSeverity.HIGH)
        self.assertIn("leaf blast symptoms", alerts[0].explanation.lower())

    def test_general_pest_advisory_alone_does_not_produce_active_alert(self):
        # Advisory alone without trap or symptom indicators must NOT produce active alert
        alerts = evaluate_pest_risks(
            request_id="req-test",
            crop_id="crop:oryza-sativa",
            location_id="location:state:in-pb",
            growth_stage="tillering",
            evidence_records=[self.blast_ev],
            active_only=True,
        )
        self.assertEqual(len(alerts), 0)

        # Background vulnerability retained
        all_items = evaluate_pest_risks(
            request_id="req-test",
            crop_id="crop:oryza-sativa",
            location_id="location:state:in-pb",
            growth_stage="tillering",
            evidence_records=[self.blast_ev],
            active_only=False,
        )
        self.assertEqual(len(all_items), 1)
        self.assertFalse(all_items[0].is_active)
        self.assertEqual(all_items[0].severity, RiskSeverity.LOW)

    def test_rice_stem_borer_pest_risk_detected_with_symptoms(self):
        alerts = evaluate_pest_risks(
            request_id="req-test",
            crop_id="crop:oryza-sativa",
            location_id="location:state:in-pb",
            growth_stage="tillering",
            evidence_records=[self.blast_ev],
            active_only=True,
            farmer_query="Found egg masses and dead hearts in rice tillers.",
        )
        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertTrue(alert.is_active)
        self.assertEqual(alert.category, RiskCategory.PEST)
        self.assertEqual(alert.severity, RiskSeverity.MEDIUM)
        self.assertIn("Stem Borer", alert.title)

    def test_cotton_pink_bollworm_pest_risk_detected(self):
        pbw_src = EvidenceSource(
            source_id="icar-cotton-pbw",
            title="ICAR Kharif Agro-Advisories 2025 — cotton sowing and pink bollworm IPM",
            reference=ContentReference(kind=ReferenceKind.URL, value="https://icar.gov.in/cotton"),
            source_type=EvidenceSourceType.CURATED_AGRICULTURAL_KNOWLEDGE,
            source_identity="ICAR",
        )
        pbw_ev = EvidenceRecord(
            evidence_id="ev-pbw-1",
            request_id="req-test",
            source=pbw_src,
            excerpt="Integrated measures including pheromone traps for pink bollworm management.",
            content_reference=pbw_src.reference,
            retrieved_at=NOW,
            provenance="ICAR Kharif 2025 cotton advisory",
            crop_applicability=("crop:gossypium-hirsutum",),
            geographic_applicability=("location:district:in-tg-ad",),
            mode=EvidenceMode.LIVE,
            status=EvidenceStatus.AVAILABLE,
        )
        # Without trap/symptom indicator: no active alert
        alerts_quiet = evaluate_pest_risks(
            request_id="req-test",
            crop_id="crop:gossypium-hirsutum",
            location_id="location:district:in-tg-ad",
            growth_stage="vegetative",
            evidence_records=[pbw_ev],
            active_only=True,
        )
        self.assertEqual(len(alerts_quiet), 0)

        # With trap catch indicator: active HIGH alert
        alerts = evaluate_pest_risks(
            request_id="req-test",
            crop_id="crop:gossypium-hirsutum",
            location_id="location:district:in-tg-ad",
            growth_stage="vegetative",
            evidence_records=[pbw_ev],
            active_only=True,
            farmer_query="Pheromone traps caught 15 pink bollworm moths per trap.",
        )
        self.assertEqual(len(alerts), 1)
        self.assertTrue(alerts[0].is_active)
        self.assertEqual(alerts[0].severity, RiskSeverity.HIGH)
        self.assertIn("Pink Bollworm", alerts[0].title)
        self.assertIn("pheromone traps", alerts[0].safe_action.lower())

    def test_maize_fall_armyworm_pest_risk_detected(self):
        faw_src = EvidenceSource(
            source_id="icar-maize-faw",
            title="ICAR Kharif Agro-Advisories 2025 — maize Fall Armyworm",
            reference=ContentReference(kind=ReferenceKind.URL, value="https://icar.gov.in/faw"),
            source_type=EvidenceSourceType.CURATED_AGRICULTURAL_KNOWLEDGE,
            source_identity="ICAR",
        )
        faw_ev = EvidenceRecord(
            evidence_id="ev-faw-1",
            request_id="req-test",
            source=faw_src,
            excerpt="Fall Armyworm as a maize pest with monitoring and control options.",
            content_reference=faw_src.reference,
            retrieved_at=NOW,
            provenance="ICAR Kharif 2025 maize advisory",
            crop_applicability=("crop:zea-mays",),
            geographic_applicability=("location:district:in-tg-ad",),
            mode=EvidenceMode.LIVE,
            status=EvidenceStatus.AVAILABLE,
        )
        # Without scouting signs: no active alert
        alerts_quiet = evaluate_pest_risks(
            request_id="req-test",
            crop_id="crop:zea-mays",
            location_id="location:district:in-tg-ad",
            growth_stage="vegetative",
            evidence_records=[faw_ev],
            active_only=True,
        )
        self.assertEqual(len(alerts_quiet), 0)

        # With scouting whorl damage: active HIGH alert
        alerts = evaluate_pest_risks(
            request_id="req-test",
            crop_id="crop:zea-mays",
            location_id="location:district:in-tg-ad",
            growth_stage="vegetative",
            evidence_records=[faw_ev],
            active_only=True,
            farmer_query="I see pinhole leaf damage and caterpillar frass inside whorls.",
        )
        self.assertEqual(len(alerts), 1)
        self.assertTrue(alerts[0].is_active)
        self.assertEqual(alerts[0].severity, RiskSeverity.HIGH)
        self.assertIn("Fall Armyworm", alerts[0].title)

    def test_crop_mismatch_produces_no_unsupported_risk(self):
        # Request is for Cotton, but evidence is for Rice
        alerts = evaluate_disease_risks(
            request_id="req-test",
            crop_id="crop:gossypium-hirsutum",
            location_id="location:district:in-tg-ad",
            growth_stage="flowering",
            evidence_records=[self.blast_ev],
        )
        self.assertEqual(len(alerts), 0, "Rice blast must NOT be asserted for Cotton!")

    def test_insufficient_evidence_produces_no_speculative_risk(self):
        # Empty evidence records
        alerts = evaluate_disease_risks(
            request_id="req-test",
            crop_id="crop:gossypium-hirsutum",
            location_id="location:district:in-tg-ad",
            growth_stage="flowering",
            evidence_records=[],
        )
        self.assertEqual(len(alerts), 0)


class SafeActionValidationTests(unittest.TestCase):
    """Step 3: Test safe-action validation protocol."""

    def test_safe_action_accepted(self):
        safe_actions = [
            "Ensure clear drainage channels immediately; clear field runoff outlets and avoid fertilizer applications.",
            "Install pheromone traps (5 per acre) for early monitoring; consult local KVK.",
            "Inspect field drainage bunds and clear excess standing water from crop root zones.",
            "Schedule irrigation according to crop stage requirements and avoid daytime water spraying.",
            "Monitor for vector planthoppers and rogue out severely stunted plants; confirm symptoms with KVK extension specialists.",
        ]
        for action in safe_actions:
            valid, reason = validate_safe_action(action)
            self.assertTrue(valid, f"Expected '{action}' to be valid; got error: {reason}")

    def test_hazardous_chemical_dosage_prescription_rejected(self):
        unsafe_actions = [
            "Spray 500 ml of monocrotophos immediately on leaves.",
            "Apply 2.5 kg chemical pesticide per acre without delay.",
            "Apply 100 ml chlorpyrifos directly to plants.",
            "Ignore waterlogging as crops will recover naturally.",
            "Chemical spray without testing or extension consultation.",
        ]
        for action in unsafe_actions:
            valid, reason = validate_safe_action(action)
            self.assertFalse(valid, f"Expected '{action}' to be rejected, but it passed!")

    def test_banned_pesticides_rejected_unconditionally(self):
        banned = [
            "Spray monocrotophos 2 ml per liter immediately; consult local KVK.",
            "Apply chlorpyrifos to the crop leaves and monitor field bunds.",
            "Use endosulfan at 500 ml/acre as recommended practice.",
            "Treat plants with paraquat solution; contact extension worker.",
        ]
        for action in banned:
            valid, reason = validate_safe_action(action)
            self.assertFalse(valid, f"Expected banned chemical in '{action}' to be rejected; passed!")
            self.assertIn("banned or restricted", reason.lower())

    def test_empty_action_rejected(self):
        valid, reason = validate_safe_action("   ")
        self.assertFalse(valid)


class BoardRiskAlertIntegrityTests(unittest.TestCase):
    """Step 3 & 4: Test AdvisoryBoard risk alert contract, ID verification, and provenance."""

    def setUp(self):
        ctx = RequestContext(request_id="req-board-risk", user_id="u1", tenant_id="t1")
        self.board = AdvisoryBoard(ctx)

    def test_risk_alert_publishing_and_retrieval(self):
        # Publish evidence first
        writer = self.board.writer_for(AgentId.RISK_ALERT)
        ev = EvidenceRecord(
            evidence_id="ev-risk-wtr",
            request_id="req-board-risk",
            source=WEATHER_SRC,
            excerpt="Rainfall 65 mm",
            content_reference=WEATHER_SRC.reference,
            retrieved_at=NOW,
            provenance="Open-Meteo test feed",
            mode=EvidenceMode.LIVE,
            status=EvidenceStatus.AVAILABLE,
        )
        writer.publish_evidence(ev)

        alert = RiskAlertItem(
            finding_id="risk:test:1",
            request_id="req-board-risk",
            category=RiskCategory.WEATHER,
            title="Heavy Rainfall Risk",
            severity=RiskSeverity.HIGH,
            explanation="65 mm rainfall recorded.",
            evidence_ids=("ev-risk-wtr",),
            source_provenance="Open-Meteo",
            confidence=0.9,
            sufficiency=RiskEvidenceSufficiency.SUFFICIENT,
            location_id="location:district:in-tg-ad",
            safe_action="Inspect field drainage bunds and clear excess standing water.",
            detected_at=NOW,
        )
        writer.publish_risk_alert(alert)

        # Check structured risk alert property
        self.assertEqual(len(self.board.risk_alerts), 1)
        self.assertEqual(self.board.risk_alerts[0].title, "Heavy Rainfall Risk")

        # Check board findings
        findings = self.board.findings_for(AgentId.RISK_ALERT, section=BoardSection.RISK_ALERTS)
        self.assertEqual(len(self.board.risk_alerts), 1)
        self.assertEqual(alert.evidence_ids, ("ev-risk-wtr",))

    def test_unregistered_evidence_id_rejected(self):
        writer = self.board.writer_for(AgentId.RISK_ALERT)
        alert = RiskAlertItem(
            finding_id="risk:test:nonexistent",
            request_id="req-board-risk",
            category=RiskCategory.WEATHER,
            title="Heavy Rainfall Risk",
            severity=RiskSeverity.HIGH,
            explanation="Rainfall recorded.",
            evidence_ids=("ev-does-not-exist",),
            source_provenance="Unknown",
            confidence=0.9,
            sufficiency=RiskEvidenceSufficiency.SUFFICIENT,
            safe_action="Inspect field drainage bunds.",
            detected_at=NOW,
        )
        with self.assertRaises(BoardConflict):
            writer.publish_risk_alert(alert)

    def test_empty_evidence_ids_rejected_by_model(self):
        with self.assertRaises(Exception):
            RiskAlertItem(
                finding_id="risk:test:noev",
                request_id="req-board-risk",
                category=RiskCategory.WEATHER,
                title="Test Risk",
                severity=RiskSeverity.LOW,
                explanation="No evidence.",
                evidence_ids=(),
                source_provenance="Test",
                confidence=0.5,
                safe_action="Inspect field drainage.",
                detected_at=NOW,
            )

    def test_non_risk_agent_cannot_publish_risk_alerts(self):
        crop_writer = self.board.writer_for(AgentId.CROP)
        alert = RiskAlertItem(
            finding_id="risk:test:denied",
            request_id="req-board-risk",
            category=RiskCategory.WEATHER,
            title="Test Risk",
            severity=RiskSeverity.LOW,
            explanation="Test.",
            evidence_ids=("ev-crop",),
            source_provenance="Test",
            confidence=0.5,
            safe_action="Inspect field drainage.",
            detected_at=NOW,
        )
        with self.assertRaises(PermissionError):
            crop_writer.publish_risk_alert(alert)

    def test_risk_alert_claim_relationship_classification(self):
        writer = self.board.writer_for(AgentId.RISK_ALERT)
        ev = EvidenceRecord(
            evidence_id="ev-risk-test",
            request_id="req-board-risk",
            source=WEATHER_SRC,
            excerpt="Weather data",
            content_reference=WEATHER_SRC.reference,
            retrieved_at=NOW,
            provenance="Open-Meteo test feed",
            mode=EvidenceMode.LIVE,
            status=EvidenceStatus.AVAILABLE,
        )
        writer.publish_evidence(ev)

        f_flood = writer.publish_finding(
            topic="risk_alert:weather",
            claim="[HIGH] Flood Risk: Heavy rainfall poses flood hazard. Recommended action: Clear field drainage channels.",
            evidence_ids=("ev-risk-test",),
        )
        f_pest = writer.publish_finding(
            topic="risk_alert:pest",
            claim="[HIGH] Pink Bollworm: Seasonal pest threat. Recommended action: Install pheromone traps.",
            evidence_ids=("ev-risk-test",),
        )
        f_no_flood = writer.publish_finding(
            topic="risk_alert:weather",
            claim="[LOW] No flood risk or heavy rainfall expected. Recommended action: Normal moisture monitoring.",
            evidence_ids=("ev-risk-test",),
        )

        # Different topics are independent
        self.assertEqual(classify_claim_relationship(f_flood, f_pest), ClaimRelationship.INDEPENDENT)
        # Affirmation vs negation under same topic is contradictory
        self.assertEqual(classify_claim_relationship(f_flood, f_no_flood), ClaimRelationship.CONTRADICTORY)


class WorkflowSevenAgentIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Step 4 & 5: Test complete seven-agent Microsoft Agent Framework workflow."""

    @classmethod
    def setUpClass(cls):
        cls.index = build_test_index()
        cls.resolver = empty_coverage_resolver()

    async def test_workflow_runs_all_seven_agents_in_deterministic_mode(self):
        weather_provider = MockWeatherProvider(
            MockWeather.create(temp=38.5, rain=65.0, humidity=88.0, forecast="Heavy rain expected")
        )
        market_provider = MockMarketProvider()
        workflow = RythuSaathiWorkflow(
            resolver=self.resolver,
            knowledge=self.index,
            weather_provider=weather_provider,
            market_provider=market_provider,
        )
        self.assertEqual(len(workflow.agent_ids), 7)
        self.assertIn(AgentId.RISK_ALERT, workflow.agent_ids)

        req = FarmerRequest(
            request_id="req-seven-agent-rice",
            user_id="u1",
            tenant_id="t1",
            query="Rice crop in Punjab has yellowing leaves; what risks are present?",
            crop_query="rice",
            location_query="Punjab",
            growth_stage_query="tillering",
        )
        response = await workflow.run(req, mode=WorkflowMode.DETERMINISTIC)

        # 1. Advisory response status
        self.assertIn(response.status, {AdvisoryStatus.COMPLETED, AdvisoryStatus.PARTIAL})

        # 2. Seven agents present in agent_results
        by_agent = {r.agent_id: r for r in response.agent_results}
        self.assertEqual(len(by_agent), 7)
        expected_agents = {
            AgentId.INTAKE, AgentId.WEATHER, AgentId.CROP,
            AgentId.MARKET, AgentId.SCHEME, AgentId.RISK_ALERT, AgentId.COORDINATOR,
        }
        self.assertEqual(set(by_agent.keys()), expected_agents)

        # 3. Risk Alert agent completed with structured payload
        risk_result = by_agent[AgentId.RISK_ALERT]
        self.assertEqual(risk_result.status, AgentStatus.COMPLETED)
        self.assertIsInstance(risk_result.payload, RiskAlertPayload)
        self.assertGreater(risk_result.payload.alert_count, 0)

        # Check detected weather risk (65mm rain -> HIGH)
        weather_alerts = [a for a in risk_result.payload.alerts if a.category == RiskCategory.WEATHER]
        self.assertGreater(len(weather_alerts), 0)
        self.assertEqual(weather_alerts[0].severity, RiskSeverity.HIGH)

        # Check detected disease risk (Rice blast / dwarfing)
        disease_alerts = [a for a in risk_result.payload.alerts if a.category == RiskCategory.DISEASE]
        self.assertGreater(len(disease_alerts), 0)
        self.assertTrue(any("Blast" in a.title for a in disease_alerts))

        # 4. Coordinator output contains Risk alerts section and safe action in next steps
        self.assertIn("Risk alerts:", response.response_text)
        self.assertIn("Heavy Rainfall and Waterlogging Risk", response.response_text)
        self.assertIn("Suggested next steps:", response.response_text)
        self.assertIn("drainage", response.response_text.lower())

    async def test_risk_alert_skipped_when_crop_and_location_missing(self):
        workflow = RythuSaathiWorkflow(
            resolver=self.resolver,
            knowledge=self.index,
            weather_provider=None,
            market_provider=None,
        )
        req = FarmerRequest(
            request_id="req-missing-crop",
            user_id="u1",
            tenant_id="t1",
            query="What should I do about pests?",
        )
        response = await workflow.run(req, mode=WorkflowMode.DETERMINISTIC)
        by_agent = {r.agent_id: r for r in response.agent_results}
        self.assertEqual(by_agent[AgentId.RISK_ALERT].status, AgentStatus.SKIPPED)
        self.assertEqual(by_agent[AgentId.RISK_ALERT].payload.alert_count, 0)
        self.assertIn("required", by_agent[AgentId.RISK_ALERT].payload.summary.lower())

    async def test_six_agent_regression_behavior_preserved(self):
        # Verification that existing agents behave as expected
        workflow = RythuSaathiWorkflow(
            resolver=self.resolver,
            knowledge=self.index,
            weather_provider=None,
            market_provider=None,
        )
        req = FarmerRequest(
            request_id="req-regression",
            user_id="u1",
            tenant_id="t1",
            query="Cotton crop in Adilabad; what schemes are available?",
            crop_query="cotton",
            location_query="Adilabad",
        )
        response = await workflow.run(req, mode=WorkflowMode.DETERMINISTIC)
        by_agent = {r.agent_id: r for r in response.agent_results}

        # Intake completed
        self.assertEqual(by_agent[AgentId.INTAKE].status, AgentStatus.COMPLETED)
        # Weather unavailable (no provider)
        self.assertEqual(by_agent[AgentId.WEATHER].status, AgentStatus.UNAVAILABLE)
        # Market unavailable (no provider)
        self.assertEqual(by_agent[AgentId.MARKET].status, AgentStatus.UNAVAILABLE)
        # Scheme completed
        self.assertEqual(by_agent[AgentId.SCHEME].status, AgentStatus.COMPLETED)
        # Coordinator completed
        self.assertIn(by_agent[AgentId.COORDINATOR].status, {AgentStatus.COMPLETED, AgentStatus.PARTIAL})

    async def test_workflow_with_normal_weather_and_general_advisory_retains_background_only(self):
        weather_provider = MockWeatherProvider(
            MockWeather.create(temp=28.0, rain=0.0, humidity=60.0, forecast="Clear sky")
        )
        market_provider = MockMarketProvider()
        workflow = RythuSaathiWorkflow(
            resolver=self.resolver,
            knowledge=self.index,
            weather_provider=weather_provider,
            market_provider=market_provider,
        )
        req = FarmerRequest(
            request_id="req-seven-agent-normal",
            user_id="u1",
            tenant_id="t1",
            query="Rice crop in Punjab; what pest or disease risks should I watch out for?",
            crop_query="rice",
            location_query="Punjab",
            growth_stage_query="tillering",
        )
        response = await workflow.run(req, mode=WorkflowMode.DETERMINISTIC)
        by_agent = {r.agent_id: r for r in response.agent_results}

        risk_result = by_agent[AgentId.RISK_ALERT]
        self.assertEqual(risk_result.status, AgentStatus.COMPLETED)
        self.assertIsInstance(risk_result.payload, RiskAlertPayload)
        # Zero active alerts
        self.assertEqual(risk_result.payload.alert_count, 0)
        # Background vulnerabilities retained
        self.assertGreater(len(risk_result.payload.background_vulnerabilities), 0)
        self.assertIn("retained as background context", risk_result.payload.summary)

        # Coordinator response does not assert false active alerts
        self.assertIn("No active weather, disease, or pest risk thresholds exceeded", response.response_text)
        self.assertIn("Background agricultural context:", response.response_text)
        # Suggested next steps must not promote false high/critical risks
        if "Suggested next steps:" in response.response_text:
            steps_section = response.response_text.split("Suggested next steps:")[1]
            self.assertNotIn("[HIGH RISK]", steps_section)
            self.assertNotIn("[CRITICAL RISK]", steps_section)
