import ada.processes.pi_web_api as process


def test_public_api_exposes_only_process_entrypoints() -> None:
    assert set(process.__all__) == {'__version__', 'build_catalog', 'build_composition', 'run'}
    assert callable(process.run)
    assert callable(process.build_composition)
    assert callable(process.build_catalog)
