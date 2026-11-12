"""Configuration schemas for crops, locations, and inference providers."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class GrowthStageItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    stage_id: str
    names: list[str]
    order: int


class VarietyItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    variety_id: str
    names: list[str]


class CropSchema(BaseModel):
    """Registered crop metadata from the agricultural seed catalog."""

    model_config = ConfigDict(frozen=True)

    crop_id: str
    common_names: list[str]
    scientific_name: str
    category: str
    growth_stages: list[GrowthStageItem] = Field(default_factory=list)
    varieties: list[VarietyItem] = Field(default_factory=list)


class LocationSchema(BaseModel):
    """Registered geographic scope from the location hierarchy catalog."""

    model_config = ConfigDict(frozen=True)

    location_id: str
    display_name: str
    level: str
    parent_id: str | None = None


class ProviderSchema(BaseModel):
    """Available inference or external source provider definition."""

    model_config = ConfigDict(frozen=True)

    provider_id: str
    name: str
    kind: str = Field(description="Provider kind: 'inference', 'weather', or 'market'")
    requires_credential: bool
    supported_models: list[str] = Field(default_factory=list)
    description: str

