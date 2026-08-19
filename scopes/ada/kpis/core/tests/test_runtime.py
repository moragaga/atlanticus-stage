from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from ada.kpis.core import (
    DataRuntimeContext,
    KpiSource,
    KpiSourceNotRequestedError,
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


def test_runtime_frame_contract_exposes_legacy_helpers() -> None:
    frame = FakeFrameContext(dataframe=[{'a': 1}, {'a': 2}])
    assert isinstance(frame, RuntimeFrameContext)
    assert frame.last_row() == {'a': 2}
    assert frame.last_value('a') == 2
    assert frame.last_value_number('a') == 2.0


def test_data_runtime_context_uses_typed_sources_and_rejects_unrequested_access() -> None:
    frame = FakeFrameContext(dataframe=[])
    data_context = DataRuntimeContext({KpiSource.PI_INTERPOLATED: frame})

    assert data_context.sources == (KpiSource.PI_INTERPOLATED,)
    assert data_context.get(KpiSource.PI_INTERPOLATED) is frame

    with pytest.raises(KpiSourceNotRequestedError, match='was not requested'):
        data_context.get(KpiSource.DISPATCH_TIEMPOS_MLP)

    with pytest.raises(TypeError, match='source must be KpiSource'):
        data_context.get('pi.interpolated')  # type: ignore[arg-type]


def test_data_runtime_context_rejects_untyped_mapping_keys() -> None:
    frame = FakeFrameContext(dataframe=[])
    with pytest.raises(TypeError, match='KpiSource values'):
        DataRuntimeContext({'pi.interpolated': frame})  # type: ignore[dict-item]
