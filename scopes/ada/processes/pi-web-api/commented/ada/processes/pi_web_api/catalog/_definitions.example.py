from atlanticus.integrations.pi.contracts import (
    PiExtractionMode,
    PiMaterialization,
    PiTagDefinition,
    PiValueKind,
    PiWebApiSource,
)

# Este archivo es solo una guía: provider.py importa definitions.py, nunca este ejemplo.
# interpolation_seconds define el eje de los tags INTERPOLATED. RECORDED conserva sus timestamps nativos.
EXAMPLE_SOURCE = PiWebApiSource(interpolation_seconds=10)

# INTERPOLATED admite cualquier combinación no vacía de LATEST, DAILY y MONTHLY.
# RECORDED admite DAILY, MONTHLY o ambas; LATEST está prohibido por contrato.
# PiValueKind puede ser NUMBER o TEXT y cada alias del catálogo real debe ser único.
EXAMPLE_DEFINITIONS = (
    PiTagDefinition(
        tag_name='INTERPOLATED_LATEST_TAG',
        alias='interpolated_latest',
        value_kind=PiValueKind.NUMBER,
        extraction_mode=PiExtractionMode.INTERPOLATED,
        materializations=(PiMaterialization.LATEST,),
    ),
    PiTagDefinition(
        tag_name='INTERPOLATED_DAILY_TAG',
        alias='interpolated_daily',
        value_kind=PiValueKind.NUMBER,
        extraction_mode=PiExtractionMode.INTERPOLATED,
        materializations=(PiMaterialization.DAILY,),
    ),
    PiTagDefinition(
        tag_name='INTERPOLATED_MONTHLY_TAG',
        alias='interpolated_monthly',
        value_kind=PiValueKind.NUMBER,
        extraction_mode=PiExtractionMode.INTERPOLATED,
        materializations=(PiMaterialization.MONTHLY,),
    ),
    PiTagDefinition(
        tag_name='INTERPOLATED_LATEST_DAILY_TAG',
        alias='interpolated_latest_daily',
        value_kind=PiValueKind.NUMBER,
        extraction_mode=PiExtractionMode.INTERPOLATED,
        materializations=(PiMaterialization.LATEST, PiMaterialization.DAILY),
    ),
    PiTagDefinition(
        tag_name='INTERPOLATED_LATEST_MONTHLY_TAG',
        alias='interpolated_latest_monthly',
        value_kind=PiValueKind.TEXT,
        extraction_mode=PiExtractionMode.INTERPOLATED,
        materializations=(PiMaterialization.LATEST, PiMaterialization.MONTHLY),
    ),
    PiTagDefinition(
        tag_name='INTERPOLATED_DAILY_MONTHLY_TAG',
        alias='interpolated_daily_monthly',
        value_kind=PiValueKind.NUMBER,
        extraction_mode=PiExtractionMode.INTERPOLATED,
        materializations=(PiMaterialization.DAILY, PiMaterialization.MONTHLY),
    ),
    PiTagDefinition(
        tag_name='INTERPOLATED_LATEST_DAILY_MONTHLY_TAG',
        alias='interpolated_latest_daily_monthly',
        value_kind=PiValueKind.NUMBER,
        extraction_mode=PiExtractionMode.INTERPOLATED,
        materializations=(
            PiMaterialization.LATEST,
            PiMaterialization.DAILY,
            PiMaterialization.MONTHLY,
        ),
    ),
    PiTagDefinition(
        tag_name='RECORDED_DAILY_TAG',
        alias='recorded_daily',
        value_kind=PiValueKind.NUMBER,
        extraction_mode=PiExtractionMode.RECORDED,
        materializations=(PiMaterialization.DAILY,),
    ),
    PiTagDefinition(
        tag_name='RECORDED_MONTHLY_TAG',
        alias='recorded_monthly',
        value_kind=PiValueKind.TEXT,
        extraction_mode=PiExtractionMode.RECORDED,
        materializations=(PiMaterialization.MONTHLY,),
    ),
    PiTagDefinition(
        tag_name='RECORDED_DAILY_MONTHLY_TAG',
        alias='recorded_daily_monthly',
        value_kind=PiValueKind.NUMBER,
        extraction_mode=PiExtractionMode.RECORDED,
        materializations=(PiMaterialization.DAILY, PiMaterialization.MONTHLY),
    ),
)
