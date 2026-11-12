"""Small, source-attributed starter catalogs; these are not complete registries."""

from datetime import date

from .registry import (CoverageRegistry, CropCategory, CropRecord, CropRegistry, GrowthStageRecord,
                       LocationRecord, LocationRegistry, NameVariant, RegistryLevel,
                       RegistryMetadata, VarietyRecord)

SEED_VERSION = "seed-2026.11-v1"
_CROP_SEED_VERSION = "seed-2026.11-v2"
_EFFECTIVE_FROM = date(2026, 1, 1)

CROP_REGISTRY_METADATA = RegistryMetadata(
    registry_id="rythusaathi:crop-seed", version=_CROP_SEED_VERSION,
    effective_from=_EFFECTIVE_FROM, source_reference="https://powo.science.kew.org/",
    scope_note="Small cited starter catalog; absence does not mean a crop is unsupported in agriculture.",
)

RICE = CropRecord(
    crop_id="crop:oryza-sativa",
    common_names=(NameVariant(value="rice"), NameVariant(value="paddy")),
    regional_names=(NameVariant(value="వరి", language="te", region_id="location:state:in-tg"),),
    scientific_name="Oryza sativa L.", category=CropCategory.CEREAL,
    varieties=(VarietyRecord(
        variety_id="variety:oryza-sativa:pusa-basmati-1121",
        names=(NameVariant(value="Pusa Basmati 1121"),),
        source_reference="https://icar.org.in/en/crop-science/basmati-rice-varieties",
    ),),
    growth_stages=(
        GrowthStageRecord(stage_id="seedling", names=(NameVariant(value="seedling"),), order=10,
                          source_reference="https://www.icar.org.in/sites/default/files/Circulars/ICAR-En-Kharif-Agro-Advisories-for-Farmers-2025.pdf"),
        GrowthStageRecord(stage_id="tillering", names=(NameVariant(value="tillering"),), order=20,
                          source_reference="https://www.icar.org.in/sites/default/files/Circulars/ICAR-En-Kharif-Agro-Advisories-for-Farmers-2025.pdf"),
        GrowthStageRecord(stage_id="panicle-initiation", names=(NameVariant(value="panicle initiation"),), order=30,
                          source_reference="https://www.icar.org.in/sites/default/files/Circulars/ICAR-En-Kharif-Agro-Advisories-for-Farmers-2025.pdf"),
        GrowthStageRecord(stage_id="booting", names=(NameVariant(value="booting"),), order=40,
                          source_reference="https://www.icar.org.in/sites/default/files/Circulars/ICAR-En-Kharif-Agro-Advisories-for-Farmers-2025.pdf"),
        GrowthStageRecord(stage_id="heading", names=(NameVariant(value="heading"),), order=50,
                          source_reference="https://www.icar.org.in/sites/default/files/Circulars/ICAR-En-Kharif-Agro-Advisories-for-Farmers-2025.pdf"),
        GrowthStageRecord(stage_id="grain-filling", names=(NameVariant(value="grain filling"),), order=60,
                          source_reference="https://www.icar.org.in/sites/default/files/Circulars/ICAR-En-Kharif-Agro-Advisories-for-Farmers-2025.pdf"),
        GrowthStageRecord(stage_id="maturity", names=(NameVariant(value="maturity"),), order=70,
                          source_reference="https://epubs.icar.org.in/index.php/JISAS/article/view/171327"),
    ),
    source_references=("https://powo.science.kew.org/taxon/urn:lsid:ipni.org:names:316812-2",
                       "https://icar.org.in/en/crop-science/basmati-rice-varieties",
                       "https://www.icar.gov.in/sites/default/files/Circulars/ICAR%20Kharif%20Agro-Advisories%20for%20Farmers%202025__multi%20language%2B%2BNew.pdf"),
    effective_from=_EFFECTIVE_FROM,
)

WHEAT = CropRecord(
    crop_id="crop:triticum-aestivum",
    common_names=(NameVariant(value="wheat"), NameVariant(value="bread wheat")),
    scientific_name="Triticum aestivum L.", category=CropCategory.CEREAL,
    varieties=(VarietyRecord(
        variety_id="variety:triticum-aestivum:hd-3226",
        names=(NameVariant(value="HD 3226"),), source_reference="https://icar.org.in/en/node/12081",
    ),),
    source_references=("https://powo.science.kew.org/taxon/332110-2",),
    effective_from=_EFFECTIVE_FROM,
)

COTTON = CropRecord(
    crop_id="crop:gossypium-hirsutum",
    common_names=(NameVariant(value="cotton"), NameVariant(value="American cotton")),
    scientific_name="Gossypium hirsutum L.", category=CropCategory.FIBRE,
    varieties=(VarietyRecord(
        variety_id="variety:gossypium-hirsutum:f-2228",
        names=(NameVariant(value="F 2228"), NameVariant(value="F2228")),
        source_reference="https://www.icar.org.in/sites/default/files/Circulars/ICAR-En-Kharif-Agro-Advisories-for-Farmers-2025.pdf",
        applicable_region_ids=("location:state:in-pb",),
    ),),
    growth_stages=(
        GrowthStageRecord(
            stage_id="seedling",
            names=(NameVariant(value="seedling"), NameVariant(value="germination")),
            order=10,
            source_reference="https://www.icar.org.in/sites/default/files/Circulars/ICAR-En-Kharif-Agro-Advisories-for-Farmers-2025.pdf",
        ),
        GrowthStageRecord(
            stage_id="vegetative",
            names=(NameVariant(value="vegetative"), NameVariant(value="vegetative growth")),
            order=20,
            source_reference="https://www.icar.org.in/sites/default/files/Circulars/ICAR-En-Kharif-Agro-Advisories-for-Farmers-2025.pdf",
        ),
        GrowthStageRecord(
            stage_id="squaring",
            names=(NameVariant(value="squaring"), NameVariant(value="square formation"), NameVariant(value="square")),
            order=30,
            source_reference="https://www.icar.org.in/sites/default/files/Circulars/ICAR-En-Kharif-Agro-Advisories-for-Farmers-2025.pdf",
        ),
        GrowthStageRecord(
            stage_id="flowering",
            names=(NameVariant(value="flowering"), NameVariant(value="bloom"), NameVariant(value="flower")),
            order=40,
            source_reference="https://www.icar.org.in/sites/default/files/Circulars/ICAR-En-Kharif-Agro-Advisories-for-Farmers-2025.pdf",
        ),
        GrowthStageRecord(
            stage_id="boll-development",
            names=(NameVariant(value="boll development"), NameVariant(value="boll formation"), NameVariant(value="boll")),
            order=50,
            source_reference="https://www.icar.org.in/sites/default/files/Circulars/ICAR-En-Kharif-Agro-Advisories-for-Farmers-2025.pdf",
        ),
        GrowthStageRecord(
            stage_id="maturity",
            names=(NameVariant(value="maturity"), NameVariant(value="boll opening"), NameVariant(value="boll bursting")),
            order=60,
            source_reference="https://www.icar.org.in/sites/default/files/Circulars/ICAR-En-Kharif-Agro-Advisories-for-Farmers-2025.pdf",
        ),
    ),
    source_references=("https://powo.science.kew.org/taxon/urn:lsid:ipni.org:names:30042843-2",),
    effective_from=_EFFECTIVE_FROM,
)

MAIZE = CropRecord(
    crop_id="crop:zea-mays",
    common_names=(NameVariant(value="maize"), NameVariant(value="corn")),
    scientific_name="Zea mays L.", category=CropCategory.CEREAL,
    source_references=("https://powo.science.kew.org/taxon/urn:lsid:ipni.org:names:426810-1",),
    effective_from=_EFFECTIVE_FROM,
)

CHICKPEA = CropRecord(
    crop_id="crop:cicer-arietinum",
    common_names=(NameVariant(value="chickpea"), NameVariant(value="gram"),
                  NameVariant(value="Bengal gram")),
    scientific_name="Cicer arietinum L.", category=CropCategory.PULSE,
    source_references=("https://powo.science.kew.org/taxon/urn:lsid:ipni.org:names:486336-1",),
    effective_from=_EFFECTIVE_FROM,
)

BLACK_GRAM = CropRecord(
    crop_id="crop:vigna-mungo",
    common_names=(NameVariant(value="black gram"), NameVariant(value="urad"),
                  NameVariant(value="urd")),
    scientific_name="Vigna mungo (L.) Hepper", category=CropCategory.PULSE,
    source_references=("https://powo.science.kew.org/taxon/urn:lsid:ipni.org:names:525443-1",),
    effective_from=_EFFECTIVE_FROM,
)

CROP_REGISTRY = CropRegistry(CROP_REGISTRY_METADATA,
                             (RICE, WHEAT, COTTON, MAIZE, CHICKPEA, BLACK_GRAM))

LOCATION_REGISTRY_METADATA = RegistryMetadata(
    registry_id="rythusaathi:location-seed", version=SEED_VERSION,
    effective_from=_EFFECTIVE_FROM, source_reference="https://www.telangana.gov.in/about/districts/",
    scope_note="Small, source-attributed examples only; not a complete India location catalog or government ID list.",
)

_INDIA = "location:country:in"
_STATE = {code: f"location:state:in-{code}" for code in ("ap", "ka", "pb", "tg")}

LOCATION_RECORDS = (
    LocationRecord(location_id=_INDIA, name="India", level=RegistryLevel.COUNTRY,
                   timezone="Asia/Kolkata", source_reference="https://knowindia.india.gov.in/states-uts/"),
    LocationRecord(location_id=_STATE["ap"], name="Andhra Pradesh", level=RegistryLevel.STATE,
                   parent_id=_INDIA, aliases=(NameVariant(value="AP"),), timezone="Asia/Kolkata",
                   source_reference="https://guntur.ap.gov.in/"),
    LocationRecord(location_id=_STATE["ka"], name="Karnataka", level=RegistryLevel.STATE,
                   parent_id=_INDIA, aliases=(NameVariant(value="KA"),), timezone="Asia/Kolkata",
                   source_reference="https://mysore.nic.in/en/"),
    LocationRecord(location_id=_STATE["pb"], name="Punjab", level=RegistryLevel.STATE,
                   parent_id=_INDIA, aliases=(NameVariant(value="PB"),), timezone="Asia/Kolkata",
                   source_reference="https://ludhiana.nic.in/"),
    LocationRecord(location_id=_STATE["tg"], name="Telangana", level=RegistryLevel.STATE,
                   parent_id=_INDIA, aliases=(NameVariant(value="TG"),), timezone="Asia/Kolkata",
                   source_reference="https://www.telangana.gov.in/about/districts/"),
    LocationRecord(location_id="location:district:in-ap-guntur", name="Guntur",
                   level=RegistryLevel.DISTRICT, parent_id=_STATE["ap"], timezone="Asia/Kolkata",
                   source_reference="https://guntur.ap.gov.in/"),
    LocationRecord(location_id="location:district:in-ka-mysuru", name="Mysuru",
                   level=RegistryLevel.DISTRICT, parent_id=_STATE["ka"], timezone="Asia/Kolkata",
                   source_reference="https://mysore.nic.in/en/"),
    LocationRecord(location_id="location:district:in-pb-ludhiana", name="Ludhiana",
                   level=RegistryLevel.DISTRICT, parent_id=_STATE["pb"], timezone="Asia/Kolkata",
                   source_reference="https://ludhiana.nic.in/"),
    LocationRecord(location_id="location:district:in-tg-nalgonda", name="Nalgonda",
                   level=RegistryLevel.DISTRICT, parent_id=_STATE["tg"], timezone="Asia/Kolkata",
                   source_reference="https://nalgonda.telangana.gov.in/"),
    LocationRecord(location_id="location:district:in-tg-adilabad", name="Adilabad",
                   level=RegistryLevel.DISTRICT, parent_id=_STATE["tg"], timezone="Asia/Kolkata",
                   source_reference="https://adilabad.telangana.gov.in/"),
)

LOCATION_REGISTRY = LocationRegistry(LOCATION_REGISTRY_METADATA, LOCATION_RECORDS)

COVERAGE_REGISTRY_METADATA = RegistryMetadata(
    registry_id="rythusaathi:coverage-seed",
    version=SEED_VERSION,
    effective_from=_EFFECTIVE_FROM,
    source_reference="https://icar.org.in/",
    scope_note="Seeded validated coverage pairs for supported crops and locations.",
)

SEEDED_COVERAGE_PAIRS: tuple[tuple[str, str], ...] = (
    ("crop:gossypium-hirsutum", "location:district:in-tg-adilabad"),
    ("crop:oryza-sativa", "location:district:in-tg-nalgonda"),
    ("crop:oryza-sativa", "location:district:in-pb-ludhiana"),
    ("crop:triticum-aestivum", "location:district:in-pb-ludhiana"),
)

SEEDED_COVERAGE_REGISTRY = CoverageRegistry(COVERAGE_REGISTRY_METADATA, SEEDED_COVERAGE_PAIRS)
