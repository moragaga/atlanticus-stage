from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from ada.kpis.core import KpiColumnNotRequestedError


@dataclass(frozen=True, slots=True)
class PandasRuntimeFrameContext:
    _dataframe: pd.DataFrame
    _requested_columns: tuple[str, ...]
    _latest: pd.Series = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self._dataframe, pd.DataFrame):
            raise TypeError('dataframe must be pandas.DataFrame')
        columns = tuple(self._requested_columns)
        if not columns:
            raise ValueError('requested_columns must not be empty')
        if len(columns) != len(set(columns)):
            raise ValueError('requested_columns must not contain duplicates')
        missing = tuple(column for column in columns if column not in self._dataframe.columns)
        if missing:
            raise ValueError(f'dataframe is missing requested columns: {missing}')
        frame = self._dataframe.loc[:, list(columns)].copy(deep=False).reset_index(drop=True)
        latest = pd.Series(dtype='object') if frame.empty else frame.iloc[-1].copy()
        object.__setattr__(self, '_dataframe', frame)
        object.__setattr__(self, '_requested_columns', columns)
        object.__setattr__(self, '_latest', latest)

    @property
    def dataframe(self) -> pd.DataFrame:
        return self._dataframe.copy(deep=False)

    def last_row(self) -> pd.Series:
        return self._latest.copy()

    def last_value(self, column: str, default: Any = None) -> Any:
        self._require_column(column)
        if self._latest.empty:
            return default
        value = self._latest[column]
        return default if _is_missing(value) else value

    def last_value_number(self, column: str, default: float | None = None) -> float | None:
        self._require_column(column)
        if self._latest.empty:
            return default
        value = self._latest[column]
        if _is_missing(value):
            return default
        numeric = pd.to_numeric(pd.Series([value]), errors='coerce').iloc[0]
        if _is_missing(numeric):
            return default
        return float(numeric)

    def _require_column(self, column: str) -> None:
        if not isinstance(column, str) or not column.strip():
            raise ValueError('column must be a non-empty string')
        if column not in self._requested_columns:
            raise KpiColumnNotRequestedError(f'{column}: column was not requested by this KPI')


def _is_missing(value: object) -> bool:
    try:
        result = pd.isna(value)
    except (TypeError, ValueError):
        return False
    try:
        return bool(result)
    except (TypeError, ValueError):
        return False
