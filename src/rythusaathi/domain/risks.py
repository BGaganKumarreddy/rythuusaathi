"""Typed risk assessment models and deterministic evaluation rules for RythuSaathi."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
import re
from typing import TYPE_CHECKING, Sequence
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .evidence import EvidenceMode, EvidenceRecord, EvidenceSource, EvidenceSourceType, EvidenceStatus

if TYPE_CHECKING:
    from ..agents.contracts import WeatherObservation


class RiskCategory(StrEnum):
    WEATHER = "weather"
    DISEASE = "disease"
    PEST = "pest"


class RiskSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RiskEvidenceSufficiency(StrEnum):
    SUFFICIENT = "sufficient"
    LIMITED = "limited"
    INSUFFICIENT = "insufficient"


_BANNED_OR_HIGH_HAZARD_PESTICIDES = (
    r"monocrotophos", r"chlorpyrifos", r"endosulfan", r"paraquat",
    r"carbofuran", r"phorate", r"methyl\s+parathion", r"dichlorvos",
    r"triazophos", r"methomyl", r"phosphamidon",
)

_BANNED_PESTICIDE_PATTERNS = tuple(
    re.compile(r"\b" + name + r"\b", re.I) for name in _BANNED_OR_HIGH_HAZARD_PESTICIDES
)

_UNSAFE_ACTION_PATTERNS = (
    re.compile(r"\b(?:spray|apply|drench)\s+\d+(?:\.\d+)?\s*(?:ml|l|g|kg|ppm|liter|litre|gm)\b", re.I),
    re.compile(r"\b\d+(?:\.\d+)?\s*(?:ml|l|g|kg|ppm|liter|litre|gm)\s*(?:/|\s*per\s*|\s*in\s*)\s*(?:acre|ha|hectare|liter|litre|l|tank|plant)\b", re.I),
    re.compile(r"\b(?:apply|spray|use)\s+(?:chemical\s+)?pesticides?\s+immediately\b", re.I),
    re.compile(r"\b(?:ignore|neglect|disregard)\s+(?:waterlogging|drainage|heat|frost|stagnation|pest|disease)\b", re.I),
    re.compile(r"\bchemical\s+spray\s+without\s+(?:consultation|testing|inspection|advisory)\b", re.I),
    re.compile(r"\bspray\s+(?:during|at)\s+(?:midday|noon|high\s+winds?|rain)\b", re.I),
)

_SAFE_ACTION_REQUIRED_PATTERNS = (
    re.compile(r"\b(?:drainage|drain|channel|bund|runoff|stagnation|water\s+accumulation)\b", re.I),
    re.compile(r"\b(?:irrigation|irrigate|water|mulch|moisture|soil\s+moisture)\b", re.I),
    re.compile(r"\b(?:monitor(?:ing)?|scout(?:ing)?|inspect(?:ion|ing)?|trap(?:s|ping)?|pheromone|check(?:ing)?|watch(?:ing)?|field\s+inspection)\b", re.I),
    re.compile(r"\b(?:consult(?:ation|ing)?|confirm|contact|verify|extension|officer|kvk|authority|expert|specialist)\b", re.I),
    re.compile(r"\b(?:rogue|remove|prune|clean|sanitation|isolate|hand[- ]pick)\b", re.I),
    re.compile(r"\b(?:avoid|pause|delay)\s+(?:chemical|spray(?:ing)?|fertilizer|operations)\b", re.I),
)


def validate_safe_action(action: str) -> tuple[bool, str | None]:
    """Verify that a recommended action is safe, non-hazardous, and hedged.
    
    Treating keyword presence alone as proof of safety is rejected: if any
    hazardous chemical or dosage pattern is present, the action is rejected
    regardless of whether safe keywords appear elsewhere.
    """
    trimmed = action.strip()
    if not trimmed:
        return False, "action cannot be empty"

    for pattern in _BANNED_PESTICIDE_PATTERNS:
        match = pattern.search(trimmed)
        if match:
            return False, f"action contains banned or restricted pesticide: {match.group(0)}"

    for pattern in _UNSAFE_ACTION_PATTERNS:
        match = pattern.search(trimmed)
        if match:
            return False, f"action contains hazardous or unhedged recommendation: {match.group(0)}"

    if not any(pattern.search(trimmed) for pattern in _SAFE_ACTION_REQUIRED_PATTERNS):
        return False, "action must recommend cultural control, drainage, monitoring, or official extension consultation"

    return True, None


class RiskAlertItem(BaseModel):
    """Structured, evidence-grounded risk finding published to the AdvisoryBoard."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    finding_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    category: RiskCategory
    title: str = Field(min_length=1)
    severity: RiskSeverity
    explanation: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    source_provenance: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    sufficiency: RiskEvidenceSufficiency = RiskEvidenceSufficiency.SUFFICIENT
    crop_id: str | None = None
    location_id: str | None = None
    safe_action: str = Field(min_length=1)
    detected_at: datetime
    is_active: bool = True

    @field_validator("title", "explanation", "safe_action", "source_provenance")
    @classmethod
    def validate_trimmed_strings(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("text fields must not be blank")
        if trimmed != value:
            raise ValueError("text fields must be trimmed")
        return trimmed

    @model_validator(mode="after")
    def validate_risk_contract(self):
        if self.detected_at.tzinfo is None or self.detected_at.utcoffset() is None:
            raise ValueError("risk alert timestamp must be timezone-aware")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("risk alert evidence IDs must be unique")
        if not self.evidence_ids:
            raise ValueError("risk alert must cite at least one evidence ID")

        valid, reason = validate_safe_action(self.safe_action)
        if not valid:
            raise ValueError(f"invalid safe recommended action: {reason}")

        return self


# --- Deterministic Evaluation Rules ---


def evaluate_weather_risks(
    *,
    request_id: str,
    weather_obs: WeatherObservation,
    location_id: str | None,
    evidence_id: str,
    detected_at: datetime | None = None,
) -> list[RiskAlertItem]:
    """Evaluate objective meteorological risk thresholds from observed weather evidence.
    
    Validates temporal measurement window (fresh within 48 hours; non-future).
    Explicitly states 24-hour rainfall accumulation and ambient temperature measurement windows.
    """
    now = detected_at or datetime.now(timezone.utc)

    # Validate measurement window
    if weather_obs.observed_at > now:
        return []
    age_seconds = (now - weather_obs.observed_at).total_seconds()
    if age_seconds > 48 * 3600:
        return []

    obs_time = weather_obs.observed_at.strftime("%Y-%m-%d %H:%M UTC")
    provenance = f"Weather observation at {weather_obs.observed_at.isoformat()} via {weather_obs.source.title}"
    alerts: list[RiskAlertItem] = []

    rain = weather_obs.rainfall_mm
    temp = weather_obs.temperature_c
    humidity = weather_obs.relative_humidity_pct
    forecast = weather_obs.forecast_summary or ""
    forecast_lower = forecast.casefold()

    # 1. Excessive rainfall / waterlogging (24-hour measurement window)
    if rain is not None and rain >= 100.0:
        alerts.append(
            RiskAlertItem(
                finding_id=f"risk:weather:flood:{uuid.uuid4().hex[:12]}",
                request_id=request_id,
                category=RiskCategory.WEATHER,
                title="Severe Waterlogging and Flood Risk (24h Rainfall >= 100mm)",
                severity=RiskSeverity.CRITICAL,
                explanation=(
                    f"Observed 24-hour rainfall of {rain:.1f} mm recorded by {weather_obs.source.title} at {obs_time} "
                    f"exceeds critical drainage threshold (100.0 mm in 24h), risking severe root zone saturation and field flooding."
                ),
                evidence_ids=(evidence_id,),
                source_provenance=provenance,
                confidence=0.95,
                sufficiency=RiskEvidenceSufficiency.SUFFICIENT,
                location_id=location_id,
                safe_action="Ensure clear drainage channels immediately; clear field runoff outlets and avoid fertilizer/chemical applications until water recedes.",
                detected_at=now,
                is_active=True,
            )
        )
    elif rain is not None and (rain >= 50.0 or (rain >= 35.0 and humidity is not None and humidity >= 85.0)):
        alerts.append(
            RiskAlertItem(
                finding_id=f"risk:weather:rain_high:{uuid.uuid4().hex[:12]}",
                request_id=request_id,
                category=RiskCategory.WEATHER,
                title="Heavy Rainfall and Waterlogging Risk (24h Rainfall >= 50mm)",
                severity=RiskSeverity.HIGH,
                explanation=(
                    f"Observed 24-hour rainfall of {rain:.1f} mm recorded by {weather_obs.source.title} at {obs_time} "
                    f"creates elevated risk of standing water and water stagnation in low-lying field sections."
                ),
                evidence_ids=(evidence_id,),
                source_provenance=provenance,
                confidence=0.90,
                sufficiency=RiskEvidenceSufficiency.SUFFICIENT,
                location_id=location_id,
                safe_action="Inspect field drainage bunds and clear excess standing water from crop root zones.",
                detected_at=now,
                is_active=True,
            )
        )
    elif rain is not None and rain >= 25.0:
        alerts.append(
            RiskAlertItem(
                finding_id=f"risk:weather:rain_med:{uuid.uuid4().hex[:12]}",
                request_id=request_id,
                category=RiskCategory.WEATHER,
                title="Moderate Rainfall Excess (24h Rainfall >= 25mm)",
                severity=RiskSeverity.MEDIUM,
                explanation=(
                    f"Observed 24-hour rainfall of {rain:.1f} mm recorded by {weather_obs.source.title} at {obs_time} "
                    f"may cause localized moisture accumulation in poorly drained soil."
                ),
                evidence_ids=(evidence_id,),
                source_provenance=provenance,
                confidence=0.85,
                sufficiency=RiskEvidenceSufficiency.SUFFICIENT,
                location_id=location_id,
                safe_action="Monitor soil moisture and pause planned irrigation until drainage is confirmed.",
                detected_at=now,
                is_active=True,
            )
        )

    # 2. Temperature extremes (Heat / Frost)
    if temp is not None and temp >= 42.0:
        alerts.append(
            RiskAlertItem(
                finding_id=f"risk:weather:heat_crit:{uuid.uuid4().hex[:12]}",
                request_id=request_id,
                category=RiskCategory.WEATHER,
                title="Extreme Heatwave Risk (Ambient Temp >= 42°C)",
                severity=RiskSeverity.CRITICAL,
                explanation=(
                    f"Ambient temperature of {temp:.1f} °C recorded by {weather_obs.source.title} at {obs_time} "
                    f"exceeds critical physiological threshold (42.0 °C), risking severe heat scorch and pollen desiccation."
                ),
                evidence_ids=(evidence_id,),
                source_provenance=provenance,
                confidence=0.95,
                sufficiency=RiskEvidenceSufficiency.SUFFICIENT,
                location_id=location_id,
                safe_action="Provide light frequent irrigation during early morning or evening hours; avoid all midday chemical spraying.",
                detected_at=now,
                is_active=True,
            )
        )
    elif temp is not None and temp >= 36.0:
        alerts.append(
            RiskAlertItem(
                finding_id=f"risk:weather:heat_high:{uuid.uuid4().hex[:12]}",
                request_id=request_id,
                category=RiskCategory.WEATHER,
                title="High Heat Stress Risk (Ambient Temp >= 36°C)",
                severity=RiskSeverity.HIGH,
                explanation=(
                    f"High ambient temperature of {temp:.1f} °C recorded by {weather_obs.source.title} at {obs_time} "
                    f"exceeds thermal comfort threshold (36.0 °C) and can accelerate evapotranspiration during sensitive growth stages."
                ),
                evidence_ids=(evidence_id,),
                source_provenance=provenance,
                confidence=0.90,
                sufficiency=RiskEvidenceSufficiency.SUFFICIENT,
                location_id=location_id,
                safe_action="Ensure adequate soil moisture to buffer root temperatures; consult local KVK before applying any foliar sprays.",
                detected_at=now,
                is_active=True,
            )
        )
    elif temp is not None and temp <= 5.0:
        alerts.append(
            RiskAlertItem(
                finding_id=f"risk:weather:frost_crit:{uuid.uuid4().hex[:12]}",
                request_id=request_id,
                category=RiskCategory.WEATHER,
                title="Frost and Extreme Cold Risk (Ambient Temp <= 5°C)",
                severity=RiskSeverity.CRITICAL,
                explanation=(
                    f"Low ambient temperature of {temp:.1f} °C recorded by {weather_obs.source.title} at {obs_time} "
                    f"poses immediate frost injury hazard (threshold <= 5.0 °C) to tender vegetative shoots and flowers."
                ),
                evidence_ids=(evidence_id,),
                source_provenance=provenance,
                confidence=0.95,
                sufficiency=RiskEvidenceSufficiency.SUFFICIENT,
                location_id=location_id,
                safe_action="Provide light night irrigation or create smoke mulch on field boundaries to protect crops from frost injury.",
                detected_at=now,
                is_active=True,
            )
        )
    elif temp is not None and temp <= 10.0:
        alerts.append(
            RiskAlertItem(
                finding_id=f"risk:weather:cold_high:{uuid.uuid4().hex[:12]}",
                request_id=request_id,
                category=RiskCategory.WEATHER,
                title="Chilling Injury Risk (Ambient Temp <= 10°C)",
                severity=RiskSeverity.HIGH,
                explanation=(
                    f"Ambient temperature of {temp:.1f} °C recorded by {weather_obs.source.title} at {obs_time} "
                    f"exposes crops to chilling stress (threshold <= 10.0 °C) slowing vegetative growth and nutrient uptake."
                ),
                evidence_ids=(evidence_id,),
                source_provenance=provenance,
                confidence=0.88,
                sufficiency=RiskEvidenceSufficiency.SUFFICIENT,
                location_id=location_id,
                safe_action="Maintain protective soil moisture buffers; delay non-essential pruning or fertilization operations.",
                detected_at=now,
                is_active=True,
            )
        )

    # 3. Dry spell / moisture depletion (24-hour rainfall is 0.0 mm)
    if rain == 0.0:
        if temp is not None and temp >= 38.0:
            alerts.append(
                RiskAlertItem(
                    finding_id=f"risk:weather:dry_high:{uuid.uuid4().hex[:12]}",
                    request_id=request_id,
                    category=RiskCategory.WEATHER,
                    title="Severe Dry Spell and Moisture Stress Risk",
                    severity=RiskSeverity.HIGH,
                    explanation=(
                        f"Zero recorded 24-hour rainfall (0.0 mm) combined with elevated temperature of {temp:.1f} °C "
                        f"recorded by {weather_obs.source.title} at {obs_time} indicates rapid soil moisture depletion and crop moisture stress."
                    ),
                    evidence_ids=(evidence_id,),
                    source_provenance=provenance,
                    confidence=0.90,
                    sufficiency=RiskEvidenceSufficiency.SUFFICIENT,
                    location_id=location_id,
                    safe_action="Prioritize critical-stage protective irrigation where available; apply light mulching to conserve root zone moisture.",
                    detected_at=now,
                    is_active=True,
                )
            )
        elif (temp is not None and temp >= 32.0) and ("dry" in forecast_lower or "no rain" in forecast_lower):
            alerts.append(
                RiskAlertItem(
                    finding_id=f"risk:weather:dry_med:{uuid.uuid4().hex[:12]}",
                    request_id=request_id,
                    category=RiskCategory.WEATHER,
                    title="Dry Spell Moisture Alert",
                    severity=RiskSeverity.MEDIUM,
                    explanation=(
                        f"Dry conditions with zero recorded 24-hour rainfall (0.0 mm) and {temp:.1f} °C temperature "
                        f"recorded by {weather_obs.source.title} at {obs_time} indicate progressive moisture deficit under ongoing dry forecast."
                    ),
                    evidence_ids=(evidence_id,),
                    source_provenance=provenance,
                    confidence=0.80,
                    sufficiency=RiskEvidenceSufficiency.SUFFICIENT,
                    location_id=location_id,
                    safe_action="Schedule irrigation according to crop stage requirements and avoid daytime water spraying to reduce evaporation.",
                    detected_at=now,
                    is_active=True,
                )
            )

    return alerts


def evaluate_disease_risks(
    *,
    request_id: str,
    crop_id: str | None,
    location_id: str | None,
    growth_stage: str | None,
    evidence_records: Sequence[EvidenceRecord],
    weather_obs: WeatherObservation | None = None,
    detected_at: datetime | None = None,
    active_only: bool = True,
    farmer_query: str | None = None,
) -> list[RiskAlertItem]:
    """Evaluate evidence-backed disease threats requiring relevant crop, geography and supporting indicators."""
    if not crop_id or not evidence_records:
        return []

    now = detected_at or datetime.now(timezone.utc)
    alerts: list[RiskAlertItem] = []
    seen_titles: set[str] = set()
    query_lower = (farmer_query or "").casefold()

    for ev in evidence_records:
        text = f"{ev.source.title} {ev.excerpt or ''}".casefold()
        provenance = ev.provenance or f"Source: {ev.source.title}"

        # 1. Rice Blast (Magnaporthe oryzae)
        if crop_id == "crop:oryza-sativa" and ("blast" in text or "magnaporthe" in text):
            # Check supporting risk indicators:
            # - Environmental: verified relative humidity >= 85% or rainfall >= 10mm
            # - Symptom/field: reported blast lesions in query
            has_environmental_trigger = (
                weather_obs is not None
                and (
                    (weather_obs.relative_humidity_pct is not None and weather_obs.relative_humidity_pct >= 85.0)
                    or (weather_obs.rainfall_mm is not None and weather_obs.rainfall_mm >= 10.0)
                )
            )
            has_symptom_trigger = any(
                term in query_lower for term in ("blast", "spindle", "neck blast", "leaf blast", "blast lesion")
            )
            is_active_risk = has_environmental_trigger or has_symptom_trigger

            if is_active_risk:
                title = "Rice Blast Disease Threat"
                if title not in seen_titles:
                    seen_titles.add(title)
                    trigger_detail = (
                        f"elevated relative humidity ({weather_obs.relative_humidity_pct:.0f}%)"
                        if has_environmental_trigger and weather_obs and weather_obs.relative_humidity_pct
                        else ("rainfall accumulation" if has_environmental_trigger else "reported leaf blast symptoms")
                    )
                    alerts.append(
                        RiskAlertItem(
                            finding_id=f"risk:disease:blast:{uuid.uuid4().hex[:12]}",
                            request_id=request_id,
                            category=RiskCategory.DISEASE,
                            title=title,
                            severity=RiskSeverity.HIGH,
                            explanation=(
                                f"ICAR advisory documents blast vulnerability in rice, and current {trigger_detail} "
                                "creates favorable microclimate conditions for fungal sporulation."
                            ),
                            evidence_ids=(ev.evidence_id,),
                            source_provenance=provenance,
                            confidence=0.88,
                            sufficiency=RiskEvidenceSufficiency.SUFFICIENT,
                            crop_id=crop_id,
                            location_id=location_id,
                            safe_action="Inspect fields for spindle-shaped blast lesions on leaves; avoid excessive nitrogen fertilizer; consult local agriculture officer or KVK before applying recommended fungicides.",
                            detected_at=now,
                            is_active=True,
                        )
                    )
            elif not active_only:
                title = "Background Rice Blast Susceptibility"
                if title not in seen_titles:
                    seen_titles.add(title)
                    alerts.append(
                        RiskAlertItem(
                            finding_id=f"risk:disease:blast_bg:{uuid.uuid4().hex[:12]}",
                            request_id=request_id,
                            category=RiskCategory.DISEASE,
                            title=title,
                            severity=RiskSeverity.LOW,
                            explanation=(
                                "ICAR advisory documents general rice crop susceptibility to blast disease; "
                                "current observations show no elevated microclimate or symptom indicators."
                            ),
                            evidence_ids=(ev.evidence_id,),
                            source_provenance=provenance,
                            confidence=0.75,
                            sufficiency=RiskEvidenceSufficiency.LIMITED,
                            crop_id=crop_id,
                            location_id=location_id,
                            safe_action="Maintain routine crop scouting during tillering; consult local agriculture officer or KVK before considering any preventive fungicide applications.",
                            detected_at=now,
                            is_active=False,
                        )
                    )

        # 2. Rice Dwarfing Disease (SRBSDV)
        if crop_id == "crop:oryza-sativa" and ("dwarf" in text or "dwarfing" in text):
            # Check geographic relevance: PAU package applies to Punjab
            if ev.geographic_applicability and location_id and not any(loc in ev.geographic_applicability for loc in ("location:state:in-pb", "location:country:in")):
                continue

            has_vector_trigger = any(term in query_lower for term in ("planthopper", "wbph", "vector", "hopper"))
            has_symptom_trigger = any(term in query_lower for term in ("dwarf", "dwarfing", "stunt", "stunted"))
            is_nursery_stage = bool(growth_stage and any(term in growth_stage.casefold() for term in ("nursery", "transplant", "seedling")))
            is_active_risk = (has_vector_trigger or has_symptom_trigger) and is_nursery_stage

            if is_active_risk:
                title = "Rice Dwarfing Viral Disease Threat"
                if title not in seen_titles:
                    seen_titles.add(title)
                    alerts.append(
                        RiskAlertItem(
                            finding_id=f"risk:disease:dwarf:{uuid.uuid4().hex[:12]}",
                            request_id=request_id,
                            category=RiskCategory.DISEASE,
                            title=title,
                            severity=RiskSeverity.HIGH,
                            explanation=(
                                "PAU advisory guidance warns of Southern rice black-streaked dwarf viral transmission during early crop establishment stages; "
                                "reported indicators match active transmission risk."
                            ),
                            evidence_ids=(ev.evidence_id,),
                            source_provenance=provenance,
                            confidence=0.85,
                            sufficiency=RiskEvidenceSufficiency.SUFFICIENT,
                            crop_id=crop_id,
                            location_id=location_id,
                            safe_action="Monitor for vector white-backed planthoppers and rogue out severely stunted plants; confirm symptoms with university or KVK extension specialists.",
                            detected_at=now,
                            is_active=True,
                        )
                    )
            elif not active_only:
                title = "Background Rice Dwarfing Disease Vulnerability"
                if title not in seen_titles:
                    seen_titles.add(title)
                    alerts.append(
                        RiskAlertItem(
                            finding_id=f"risk:disease:dwarf_bg:{uuid.uuid4().hex[:12]}",
                            request_id=request_id,
                            category=RiskCategory.DISEASE,
                            title=title,
                            severity=RiskSeverity.LOW,
                            explanation=(
                                "PAU guidance notes historical rice dwarfing viral vulnerability in Punjab paddy; "
                                "no vector planthoppers or stunting indicators reported."
                            ),
                            evidence_ids=(ev.evidence_id,),
                            source_provenance=provenance,
                            confidence=0.70,
                            sufficiency=RiskEvidenceSufficiency.LIMITED,
                            crop_id=crop_id,
                            location_id=location_id,
                            safe_action="Inspect field bunds and monitor for planthoppers during nursery and transplanting; no chemical intervention needed.",
                            detected_at=now,
                            is_active=False,
                        )
                    )

    return alerts


def evaluate_pest_risks(
    *,
    request_id: str,
    crop_id: str | None,
    location_id: str | None,
    growth_stage: str | None,
    evidence_records: Sequence[EvidenceRecord],
    detected_at: datetime | None = None,
    active_only: bool = True,
    farmer_query: str | None = None,
) -> list[RiskAlertItem]:
    """Evaluate evidence-backed insect pest threats requiring relevant crop, geography and supporting indicators."""
    if not crop_id or not evidence_records:
        return []

    now = detected_at or datetime.now(timezone.utc)
    alerts: list[RiskAlertItem] = []
    seen_titles: set[str] = set()
    query_lower = (farmer_query or "").casefold()

    for ev in evidence_records:
        text = f"{ev.source.title} {ev.excerpt or ''}".casefold()
        provenance = ev.provenance or f"Source: {ev.source.title}"

        # 1. Cotton Pink Bollworm (Pectinophora gossypiella)
        if crop_id == "crop:gossypium-hirsutum" and ("pink bollworm" in text or "bollworm" in text):
            has_trap_or_pest_trigger = any(
                term in query_lower for term in ("trap", "bollworm", "moth", "rosette", "bore", "larva")
            )
            is_active_risk = has_trap_or_pest_trigger

            if is_active_risk:
                title = "Pink Bollworm Pest Threat"
                if title not in seen_titles:
                    seen_titles.add(title)
                    alerts.append(
                        RiskAlertItem(
                            finding_id=f"risk:pest:pbw:{uuid.uuid4().hex[:12]}",
                            request_id=request_id,
                            category=RiskCategory.PEST,
                            title=title,
                            severity=RiskSeverity.HIGH,
                            explanation="ICAR advisory identifies pink bollworm as a critical pest threat in cotton requiring early IPM measures and monitoring.",
                            evidence_ids=(ev.evidence_id,),
                            source_provenance=provenance,
                            confidence=0.90,
                            sufficiency=RiskEvidenceSufficiency.SUFFICIENT,
                            crop_id=crop_id,
                            location_id=location_id,
                            safe_action="Install pheromone traps (5 per acre) for early monitoring; avoid unverified chemical sprays during initial 90 days; consult local KVK.",
                            detected_at=now,
                            is_active=True,
                        )
                    )
            elif not active_only:
                title = "Background Cotton Pink Bollworm Vulnerability"
                if title not in seen_titles:
                    seen_titles.add(title)
                    alerts.append(
                        RiskAlertItem(
                            finding_id=f"risk:pest:pbw_bg:{uuid.uuid4().hex[:12]}",
                            request_id=request_id,
                            category=RiskCategory.PEST,
                            title=title,
                            severity=RiskSeverity.LOW,
                            explanation="ICAR advisory outlines seasonal pink bollworm IPM management for cotton; current field reports show no elevated moth catches or rosette flower signs.",
                            evidence_ids=(ev.evidence_id,),
                            source_provenance=provenance,
                            confidence=0.75,
                            sufficiency=RiskEvidenceSufficiency.LIMITED,
                            crop_id=crop_id,
                            location_id=location_id,
                            safe_action="Deploy 5 pheromone traps per acre as routine seasonal monitoring; hold insecticide applications until economic threshold levels are reached.",
                            detected_at=now,
                            is_active=False,
                        )
                    )

        # 2. Maize Fall Armyworm (Spodoptera frugiperda)
        if crop_id == "crop:zea-mays" and ("fall armyworm" in text or "armyworm" in text):
            has_scouting_trigger = any(
                term in query_lower for term in ("fall armyworm", "faw", "whorl", "frass", "pin-hole", "larva", "caterpillar", "pellet", "armyworm")
            )
            if has_scouting_trigger:
                title = "Fall Armyworm Pest Threat"
                if title not in seen_titles:
                    seen_titles.add(title)
                    alerts.append(
                        RiskAlertItem(
                            finding_id=f"risk:pest:faw:{uuid.uuid4().hex[:12]}",
                            request_id=request_id,
                            category=RiskCategory.PEST,
                            title=title,
                            severity=RiskSeverity.HIGH,
                            explanation="ICAR advisory highlights Fall Armyworm as an invasive pest threat across maize crops; reported whorl feeding signs confirm active field pressure.",
                            evidence_ids=(ev.evidence_id,),
                            source_provenance=provenance,
                            confidence=0.90,
                            sufficiency=RiskEvidenceSufficiency.SUFFICIENT,
                            crop_id=crop_id,
                            location_id=location_id,
                            safe_action="Scout central whorls for early pin-hole leaf damage and fecal pellets; apply hand-picking or bio-pesticides as approved by local KVK.",
                            detected_at=now,
                            is_active=True,
                        )
                    )
            elif not active_only:
                title = "Background Maize Fall Armyworm Vulnerability"
                if title not in seen_titles:
                    seen_titles.add(title)
                    alerts.append(
                        RiskAlertItem(
                            finding_id=f"risk:pest:faw_bg:{uuid.uuid4().hex[:12]}",
                            request_id=request_id,
                            category=RiskCategory.PEST,
                            title=title,
                            severity=RiskSeverity.LOW,
                            explanation="ICAR advisory notes general Fall Armyworm susceptibility in maize; no active whorl feeding or caterpillar damage reported.",
                            evidence_ids=(ev.evidence_id,),
                            source_provenance=provenance,
                            confidence=0.75,
                            sufficiency=RiskEvidenceSufficiency.LIMITED,
                            crop_id=crop_id,
                            location_id=location_id,
                            safe_action="Conduct weekly central whorl scouting for early pin-hole damage; do not apply calendar-based pesticide sprays.",
                            detected_at=now,
                            is_active=False,
                        )
                    )

        # 3. Rice Yellow Stem Borer (Scirpophaga incertulas)
        if crop_id == "crop:oryza-sativa" and ("stem borer" in text or "borer" in text):
            has_field_trigger = any(
                term in query_lower for term in ("stem borer", "borer", "dead heart", "white ear", "egg mass", "clipping")
            )
            if has_field_trigger:
                title = "Yellow Stem Borer Pest Threat"
                if title not in seen_titles:
                    seen_titles.add(title)
                    alerts.append(
                        RiskAlertItem(
                            finding_id=f"risk:pest:ysb:{uuid.uuid4().hex[:12]}",
                            request_id=request_id,
                            category=RiskCategory.PEST,
                            title=title,
                            severity=RiskSeverity.MEDIUM,
                            explanation="ICAR advisory documents yellow stem borer vulnerability in rice, and reported field indicators confirm active borer pressure.",
                            evidence_ids=(ev.evidence_id,),
                            source_provenance=provenance,
                            confidence=0.85,
                            sufficiency=RiskEvidenceSufficiency.SUFFICIENT,
                            crop_id=crop_id,
                            location_id=location_id,
                            safe_action="Monitor for egg masses and clipping of seedling leaf tips; consult local extension officer before any insecticide application.",
                            detected_at=now,
                            is_active=True,
                        )
                    )
            elif not active_only:
                title = "Background Rice Stem Borer Vulnerability"
                if title not in seen_titles:
                    seen_titles.add(title)
                    alerts.append(
                        RiskAlertItem(
                            finding_id=f"risk:pest:ysb_bg:{uuid.uuid4().hex[:12]}",
                            request_id=request_id,
                            category=RiskCategory.PEST,
                            title=title,
                            severity=RiskSeverity.LOW,
                            explanation="ICAR advisory documents seasonal susceptibility of rice to yellow stem borer; current field reports show no egg mass or dead heart signs.",
                            evidence_ids=(ev.evidence_id,),
                            source_provenance=provenance,
                            confidence=0.75,
                            sufficiency=RiskEvidenceSufficiency.LIMITED,
                            crop_id=crop_id,
                            location_id=location_id,
                            safe_action="Monitor for egg masses during seedling and tillering stages; avoid unhedged prophylactic chemical sprays.",
                            detected_at=now,
                            is_active=False,
                        )
                    )

    return alerts
