import ada.processes.dispatch as dispatch


def test_public_api_exposes_productive_entrypoints() -> None:
    assert callable(dispatch.run)
    assert callable(dispatch.build_composition)
    assert callable(dispatch.build_catalog)
    assert dispatch.__version__ == '0.1.0'
