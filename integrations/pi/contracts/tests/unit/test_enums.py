from atlanticus.integrations.pi.contracts import (
    PiExtractionMode,
    PiMaterialization,
    PiValueKind,
)


def test_extraction_modes_are_pi_modes_only() -> None:
    assert tuple(PiExtractionMode) == (
        PiExtractionMode.INTERPOLATED,
        PiExtractionMode.RECORDED,
    )
    assert PiExtractionMode.INTERPOLATED.value == 'interpolated'
    assert PiExtractionMode.RECORDED.value == 'recorded'


def test_materializations_are_stable_strings() -> None:
    assert tuple(PiMaterialization) == (
        PiMaterialization.LATEST,
        PiMaterialization.DAILY,
        PiMaterialization.MONTHLY,
    )


def test_value_kinds_are_minimal_and_stable() -> None:
    assert tuple(PiValueKind) == (
        PiValueKind.NUMBER,
        PiValueKind.TEXT,
    )
