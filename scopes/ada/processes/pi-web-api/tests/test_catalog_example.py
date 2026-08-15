from pathlib import Path
from runpy import run_path

from atlanticus.integrations.pi.contracts import PiExtractionMode, PiMaterialization


def test_catalog_example_documents_all_supported_materialization_combinations() -> None:
    root = Path(__file__).resolve().parents[1]
    example_path = (
        root / 'src' / 'ada' / 'processes' / 'pi_web_api' / 'catalog' / '_definitions.example.py'
    )

    namespace = run_path(str(example_path))
    definitions = namespace['EXAMPLE_DEFINITIONS']

    interpolated = {
        frozenset(item.materializations)
        for item in definitions
        if item.extraction_mode is PiExtractionMode.INTERPOLATED
    }
    recorded = {
        frozenset(item.materializations)
        for item in definitions
        if item.extraction_mode is PiExtractionMode.RECORDED
    }

    assert interpolated == {
        frozenset({PiMaterialization.LATEST}),
        frozenset({PiMaterialization.DAILY}),
        frozenset({PiMaterialization.MONTHLY}),
        frozenset({PiMaterialization.LATEST, PiMaterialization.DAILY}),
        frozenset({PiMaterialization.LATEST, PiMaterialization.MONTHLY}),
        frozenset({PiMaterialization.DAILY, PiMaterialization.MONTHLY}),
        frozenset(
            {
                PiMaterialization.LATEST,
                PiMaterialization.DAILY,
                PiMaterialization.MONTHLY,
            }
        ),
    }
    assert recorded == {
        frozenset({PiMaterialization.DAILY}),
        frozenset({PiMaterialization.MONTHLY}),
        frozenset({PiMaterialization.DAILY, PiMaterialization.MONTHLY}),
    }
