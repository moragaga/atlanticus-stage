from atlanticus.integrations.pi.contracts import (
    NotPiiSource,
    PiExtractionMode,
    PiMaterialization,
    PiTagDefinition,
    PiValueKind,
)

SOURCE = NotPiiSource()

DEFINITIONS: tuple[PiTagDefinition, ...] = (
    PiTagDefinition(
        tag_name='TAG_I_01',
        alias='i_latest',
        value_kind=PiValueKind.NUMBER,
        extraction_mode=PiExtractionMode.INTERPOLATED,
        materializations=(PiMaterialization.LATEST,),
    ),
    PiTagDefinition(
        tag_name='TAG_I_02',
        alias='i_daily',
        value_kind=PiValueKind.NUMBER,
        extraction_mode=PiExtractionMode.INTERPOLATED,
        materializations=(PiMaterialization.DAILY,),
    ),
    PiTagDefinition(
        tag_name='TAG_I_03',
        alias='i_monthly',
        value_kind=PiValueKind.NUMBER,
        extraction_mode=PiExtractionMode.INTERPOLATED,
        materializations=(PiMaterialization.MONTHLY,),
    ),
    PiTagDefinition(
        tag_name='TAG_I_04',
        alias='i_latest_daily',
        value_kind=PiValueKind.NUMBER,
        extraction_mode=PiExtractionMode.INTERPOLATED,
        materializations=(PiMaterialization.LATEST, PiMaterialization.DAILY),
    ),
    PiTagDefinition(
        tag_name='TAG_I_05',
        alias='i_latest_monthly',
        value_kind=PiValueKind.TEXT,
        extraction_mode=PiExtractionMode.INTERPOLATED,
        materializations=(PiMaterialization.LATEST, PiMaterialization.MONTHLY),
    ),
    PiTagDefinition(
        tag_name='TAG_I_06',
        alias='i_daily_monthly',
        value_kind=PiValueKind.NUMBER,
        extraction_mode=PiExtractionMode.INTERPOLATED,
        materializations=(PiMaterialization.DAILY, PiMaterialization.MONTHLY),
    ),
    PiTagDefinition(
        tag_name='TAG_I_07',
        alias='i_all',
        value_kind=PiValueKind.NUMBER,
        extraction_mode=PiExtractionMode.INTERPOLATED,
        materializations=(
            PiMaterialization.LATEST,
            PiMaterialization.DAILY,
            PiMaterialization.MONTHLY,
        ),
    ),
    PiTagDefinition(
        tag_name='TAG_R_01',
        alias='r_daily',
        value_kind=PiValueKind.NUMBER,
        extraction_mode=PiExtractionMode.RECORDED,
        materializations=(PiMaterialization.DAILY,),
    ),
    PiTagDefinition(
        tag_name='TAG_R_02',
        alias='r_monthly',
        value_kind=PiValueKind.TEXT,
        extraction_mode=PiExtractionMode.RECORDED,
        materializations=(PiMaterialization.MONTHLY,),
    ),
    PiTagDefinition(
        tag_name='TAG_R_03',
        alias='r_daily_monthly',
        value_kind=PiValueKind.NUMBER,
        extraction_mode=PiExtractionMode.RECORDED,
        materializations=(PiMaterialization.DAILY, PiMaterialization.MONTHLY),
    ),
)
