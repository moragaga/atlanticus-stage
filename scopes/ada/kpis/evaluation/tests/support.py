from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ada.kpis.core import DataRuntimeContext, KpiPartition, KpiSource, KpiSourceView


@dataclass
class FakeFrame:
    values: Mapping[str, Any]

    @property
    def dataframe(self) -> object:
        return self.values

    def last_row(self) -> Mapping[str, Any]:
        return dict(self.values)

    def last_value(self, column: str, default: Any = None) -> Any:
        return self.values.get(column, default)

    def last_value_number(self, column: str, default: float | None = None) -> float | None:
        value = self.values.get(column)
        if value is None:
            return default
        try:
            return float(value)
        except TypeError, ValueError:
            return default


def context(
    source: KpiSource,
    values: Mapping[str, Any],
    *,
    partition: KpiPartition = KpiPartition.LATEST,
) -> DataRuntimeContext:
    view = KpiSourceView(source=source, partition=partition)
    return DataRuntimeContext(frames={view: FakeFrame(values)})
