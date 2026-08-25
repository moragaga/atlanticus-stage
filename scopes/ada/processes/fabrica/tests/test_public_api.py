import ada.processes.fabrica as process


def test_public_api_exposes_only_process_entrypoints() -> None:
    assert set(process.__all__) == {'__version__', 'build_catalog', 'build_composition', 'run'}
    assert process.__version__ == '0.1.1'
    assert callable(process.run)
    assert callable(process.build_composition)
    assert callable(process.build_catalog)
