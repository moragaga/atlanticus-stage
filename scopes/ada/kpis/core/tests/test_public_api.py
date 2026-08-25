from __future__ import annotations

import ada.kpis.core as core


def test_public_api_contains_only_kpi_domain_contracts() -> None:
    expected = {
        'KpiArea',
        'KpiCatalog',
        'KpiEvaluation',
        'KpiJsonContainer',
        'KpiJsonValue',
        'KpiMode',
        'KpiNativeValue',
        'KpiResolver',
        'KpiResult',
        'KpiScalar',
        'KpiSourceTrace',
        'KpiSpec',
        'KpiStatus',
        'KpiValueKind',
        'KpiWatermark',
        'OverKpiResolver',
        'OverKpiSpec',
        '__version__',
    }
    assert set(core.__all__) == expected
    assert core.__version__ == '0.2.0'
