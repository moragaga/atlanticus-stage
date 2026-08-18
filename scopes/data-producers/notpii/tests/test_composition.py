from atlanticus.data_producers.notpii.composition import _active_modes
from atlanticus.integrations.pi.contracts import (
    NotPiiSource,
    PiCatalog,
    PiExtractionMode,
    PiMaterialization,
    PiTagDefinition,
    PiValueKind,
)


def test_active_modes_follow_interpolated_then_recorded_order() -> None:
    catalog = PiCatalog(
        source=NotPiiSource(),
        definitions=(
            PiTagDefinition(
                tag_name='r',
                alias='r',
                extraction_mode=PiExtractionMode.RECORDED,
                value_kind=PiValueKind.NUMBER,
                materializations=(PiMaterialization.DAILY,),
            ),
            PiTagDefinition(
                tag_name='i',
                alias='i',
                extraction_mode=PiExtractionMode.INTERPOLATED,
                value_kind=PiValueKind.NUMBER,
                materializations=(PiMaterialization.DAILY,),
            ),
        ),
    )
    assert _active_modes(catalog) == (
        PiExtractionMode.INTERPOLATED,
        PiExtractionMode.RECORDED,
    )
