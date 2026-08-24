from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from ada.data.core import (
    DataPartition,
    DataRuntimeContext,
    DataSource,
    DataSourceNotRequestedError,
    DataSourceView,
    RuntimeFrameContext,
)


@dataclass
class FakeFrameContext:
    dataframe: object

    def last_row(self) -> object:
        return {'a': 2}

    def last_value(self, column: str, default: Any = None) -> Any:
        return {'a': 2}.get(column, default)

    def last_value_number(self, column: str, default: float | None = None) -> float | None:
        value = {'a': 2.0}.get(column)
        return default if value is None else value


def test_runtime_frame_contract_exposes_helpers() -> None:
    frame = FakeFrameContext(dataframe=[{'a': 1}, {'a': 2}])
    assert isinstance(frame, RuntimeFrameContext)
    assert frame.last_row() == {'a': 2}
    assert frame.last_value('a') == 2
    assert frame.last_value_number('a') == 2.0


def test_data_runtime_context_keys_frames_by_source_and_partition() -> None:
    latest = FakeFrameContext(dataframe=[])
    daily = FakeFrameContext(dataframe=[])
    latest_view = DataSourceView(DataSource.PI_INTERPOLATED, DataPartition.LATEST)
    daily_view = DataSourceView(DataSource.PI_INTERPOLATED, DataPartition.DAILY)
    data_context = DataRuntimeContext({latest_view: latest, daily_view: daily})

    assert data_context.sources == (DataSource.PI_INTERPOLATED,)
    assert data_context.views == (latest_view, daily_view)
    assert data_context.get(DataSource.PI_INTERPOLATED, DataPartition.LATEST) is latest
    assert data_context.get_view(daily_view) is daily

    with pytest.raises(DataSourceNotRequestedError, match='was not requested'):
        data_context.get(DataSource.PI_INTERPOLATED, DataPartition.MONTHLY)

    with pytest.raises(TypeError, match='source must be DataSource'):
        data_context.get('pi.interpolated', DataPartition.LATEST)  # type: ignore[arg-type]


def test_data_runtime_context_rejects_untyped_mapping_keys() -> None:
    frame = FakeFrameContext(dataframe=[])
    with pytest.raises(TypeError, match='DataSourceView'):
        DataRuntimeContext({'pi.interpolated': frame})  # type: ignore[dict-item]
