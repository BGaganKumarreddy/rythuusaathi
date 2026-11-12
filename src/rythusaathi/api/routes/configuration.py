"""Configuration routes exposing crops, locations, and supported inference providers."""

from __future__ import annotations

from fastapi import APIRouter

from ...domain.seed import CROP_REGISTRY, LOCATION_REGISTRY
from ..schemas.configuration import (
    CropSchema,
    GrowthStageItem,
    LocationSchema,
    ProviderSchema,
    VarietyItem,
)

router = APIRouter(prefix="/configuration", tags=["Configuration"])


@router.get(
    "/crops",
    response_model=list[CropSchema],
    summary="List all registered crops, growth stages, and varieties",
)
async def list_crops() -> list[CropSchema]:
    crops: list[CropSchema] = []
    for crop in CROP_REGISTRY.records:
        common_names = [n.value for n in crop.common_names]
        growth_stages = [
            GrowthStageItem(
                stage_id=s.stage_id,
                names=[n.value for n in s.names],
                order=s.order,
            )
            for s in crop.growth_stages
        ]
        varieties = [
            VarietyItem(
                variety_id=v.variety_id,
                names=[n.value for n in v.names],
            )
            for v in crop.varieties
        ]
        crops.append(
            CropSchema(
                crop_id=crop.crop_id,
                common_names=common_names,
                scientific_name=crop.scientific_name,
                category=crop.category.value if hasattr(crop.category, "value") else str(crop.category),
                growth_stages=growth_stages,
                varieties=varieties,
            )
        )
    return crops


@router.get(
    "/locations",
    response_model=list[LocationSchema],
    summary="List all registered administrative geographic scopes",
)
async def list_locations() -> list[LocationSchema]:
    return [
        LocationSchema(
            location_id=loc.location_id,
            display_name=loc.name,
            level=loc.level.value if hasattr(loc.level, "value") else str(loc.level),
            parent_id=loc.parent_id,
        )
        for loc in LOCATION_REGISTRY.records
    ]


@router.get(
    "/providers",
    response_model=list[ProviderSchema],
    summary="List all available inference engines and external data services",
)
async def list_providers() -> list[ProviderSchema]:
    return [
        ProviderSchema(
            provider_id="deterministic",
            name="Deterministic Baseline",
            kind="inference",
            requires_credential=False,
            supported_models=[],
            description="Local deterministic fallback and rule-based agronomic synthesis without external LLM calls.",
        ),
        ProviderSchema(
            provider_id="cerebras",
            name="Cerebras Cloud Inference",
            kind="inference",
            requires_credential=True,
            supported_models=["gpt-oss-120b", "llama-3.3-70b", "llama3.1-8b"],
            description="Ultra-fast wafer-scale engine inference with 30,000 TPM limit for full seven-agent workflow.",
        ),
        ProviderSchema(
            provider_id="gemini",
            name="Google Gemini API",
            kind="inference",
            requires_credential=True,
            supported_models=["gemini-2.5-flash", "gemini-2.5-pro"],
            description="Google DeepMind multimodal reasoning models via Google AI Studio BYOK.",
        ),
        ProviderSchema(
            provider_id="groq",
            name="Groq Cloud LPU",
            kind="inference",
            requires_credential=True,
            supported_models=["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
            description="Groq LPU hardware inference engine with 7,200 TPM reservation limits.",
        ),
        ProviderSchema(
            provider_id="open-meteo",
            name="Open-Meteo Weather API",
            kind="weather",
            requires_credential=False,
            supported_models=[],
            description="Free live weather observations, precipitation accumulation, and 3-day agricultural forecast.",
        ),
        ProviderSchema(
            provider_id="data.gov.in",
            name="data.gov.in Agmarknet Mandi Prices",
            kind="market",
            requires_credential=True,
            supported_models=[],
            description="Official Ministry of Agriculture daily APMC mandi market commodity prices.",
        ),
    ]
