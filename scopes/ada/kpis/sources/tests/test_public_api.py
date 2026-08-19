import ada.kpis.sources as sources


def test_public_api() -> None:
    assert sources.__version__ == '0.1.0'
    assert sources.KpiSourceLoader is not None
    assert sources.PandasRuntimeFrameContext is not None
    assert sources.MineShiftResolver is not None
    assert sources.PiSourceProvider is not None
    assert sources.build_current_source_registry is not None
