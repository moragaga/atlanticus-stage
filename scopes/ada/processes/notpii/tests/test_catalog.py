from __future__ import annotations

from pathlib import Path

from atlanticus.integrations.pi.contracts import NotPiiSource, PiCatalog


def test_example_catalog_contains_ten_valid_combinations() -> None:
    root = Path(__file__).resolve().parents[1]
    namespace: dict[str, object] = {}
    exec((root / 'src/ada/processes/notpii/catalog/_definitions.example.py').read_text(), namespace)
    definitions = namespace['DEFINITIONS']
    assert isinstance(namespace['SOURCE'], NotPiiSource)
    assert isinstance(definitions, tuple)
    assert len(definitions) == 10
    PiCatalog(source=namespace['SOURCE'], definitions=definitions)


def test_productive_catalog_file_is_deliberately_empty() -> None:
    root = Path(__file__).resolve().parents[1]
    namespace: dict[str, object] = {}
    exec((root / 'src/ada/processes/notpii/catalog/definitions.py').read_text(), namespace)
    assert isinstance(namespace['SOURCE'], NotPiiSource)
    assert namespace['DEFINITIONS'] == ()
