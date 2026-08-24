from __future__ import annotations

import ada.data.core as data_core


def test_public_api_contains_shared_operational_data_contracts() -> None:
    expected = {
        'DataColumn',
        'DataColumnNotRequestedError',
        'DataColumnType',
        'DataPartition',
        'DataRequirement',
        'DataRuntimeContext',
        'DataSource',
        'DataSourceNotRequestedError',
        'DataSourceView',
        'OperationalScope',
        'RuntimeFrameContext',
        'ShiftScope',
        'ShiftSelection',
        'TimeWindow',
        'TimeWindowUnit',
        '__version__',
        'normalize_utc_second',
    }
    assert set(data_core.__all__) == expected
    assert data_core.__version__ == '0.1.0'
