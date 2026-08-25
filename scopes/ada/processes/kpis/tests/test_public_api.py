import ada.processes.kpis as kpis


def test_public_api_is_small_and_explicit() -> None:
    assert kpis.__version__ == '0.2.1'
    assert kpis.KpiProcessSettings is not None
    assert kpis.KpiProcessJob is not None
    assert kpis.KpiProcessComposition is not None
    assert callable(kpis.build_composition)
    assert callable(kpis.run)
