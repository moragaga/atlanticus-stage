import ada.processes.blockgrade as blockgrade


def test_public_api_exposes_productive_entrypoints() -> None:
    assert callable(blockgrade.run)
    assert callable(blockgrade.build_composition)
    assert callable(blockgrade.build_catalog)
    assert blockgrade.__version__ == '0.1.0'
