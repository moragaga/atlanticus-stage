# Catálogo concreto del process PI Web API.
# Los tags permanecen en ADA; adquisición, planificación y materialización viven en Data Producers.

from atlanticus.integrations.pi.contracts import (
    PiExtractionMode,
    PiMaterialization,
    PiTagDefinition,
    PiValueKind,
    PiWebApiSource,
)

# Intervalo común del catálogo PI Web API.
SOURCE = PiWebApiSource(interpolation_seconds=10)

# Definiciones concretas usadas por ADA para esta fuente.
DEFINITIONS: tuple[PiTagDefinition, ...] = (
    PiTagDefinition(
        tag_name='ML001ARUN',
        alias='estado_sag_1_inst',
        value_kind=PiValueKind.TEXT,
        extraction_mode=PiExtractionMode.INTERPOLATED,
        materializations=(PiMaterialization.LATEST,),
    ),
    PiTagDefinition(
        tag_name='320:L1.F80(INCH)',
        alias='f80_sag_1_inst',
        value_kind=PiValueKind.NUMBER,
        extraction_mode=PiExtractionMode.INTERPOLATED,
        materializations=(PiMaterialization.DAILY,),
    ),
    PiTagDefinition(
        tag_name='330:RECCU_AJUST.H',
        alias='recuperacion_ajustada_hora_inst',
        value_kind=PiValueKind.NUMBER,
        extraction_mode=PiExtractionMode.RECORDED,
        materializations=(PiMaterialization.MONTHLY,),
    ),
)
