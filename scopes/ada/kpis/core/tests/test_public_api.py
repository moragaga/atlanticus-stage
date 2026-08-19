from __future__ import annotations

import ada.kpis.core as core


def test_public_api_contains_the_closed_core_contract() -> None:
    expected = {
        'DataRuntimeContext',
        'KpiArea',
        'KpiCatalog',
        'KpiColumnNotRequestedError',
        'KpiEvaluation',
        'KpiJsonContainer',
        'KpiJsonValue',
        'KpiMode',
        'KpiNativeValue',
        'KpiResolver',
        'KpiResult',
        'KpiScalar',
        'KpiSource',
        'KpiSourceNotRequestedError',
        'KpiSourceTrace',
        'KpiSpec',
        'KpiStatus',
        'KpiTimeWindow',
        'KpiTimeWindowUnit',
        'KpiValueKind',
        'KpiWatermark',
        'OverKpiResolver',
        'OverKpiSpec',
        'RuntimeFrameContext',
        'ShiftScope',
        'ShiftSelection',
        'SourceRequirement',
    }
    assert set(core.__all__) == expected
