from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Protocol, runtime_checkable

import pandas as pd
import pyarrow as pa

from ada.data.sources import DataSourceReadError
from atlanticus.datasets.models import DatasetDefinition, DatasetKey, DatasetTarget
from atlanticus.datasets.runtime import (
    ColumnFilter,
    DatasetRuntimeNotFoundError,
    DatasetRuntimeReadError,
    DatasetRuntimeValidationError,
    FilterOperator,
)


@runtime_checkable
class DatasetFrameRuntime(Protocol):
    def scan_dataframe(
        self,
        *,
        definition: DatasetDefinition,
        targets,
        projection_schema: pa.Schema | None = None,
        filters=(),
    ): ...


class DatasetRuntimeSourceReader:
    def __init__(self, *, runtimes: Mapping[DatasetKey, DatasetFrameRuntime]) -> None:
        if not isinstance(runtimes, Mapping):
            raise TypeError('runtimes must be a mapping')
        normalized: dict[DatasetKey, DatasetFrameRuntime] = {}
        for key, runtime in runtimes.items():
            if not isinstance(key, DatasetKey):
                raise TypeError('runtimes keys must be DatasetKey values')
            if not isinstance(runtime, DatasetFrameRuntime):
                raise TypeError(f'{key.identifier}: runtime must implement DatasetFrameRuntime')
            normalized[key] = runtime
        self._runtimes = MappingProxyType(normalized)

    def read_frame(
        self,
        *,
        definition: DatasetDefinition,
        target: DatasetTarget,
        projection_schema: pa.Schema,
        timestamp_column: str | None = None,
        start_utc: datetime | None = None,
        end_utc: datetime | None = None,
    ) -> pd.DataFrame | None:
        if not isinstance(projection_schema, pa.Schema):
            raise TypeError('projection_schema must be pyarrow.Schema')
        try:
            runtime = self._runtimes[definition.key]
        except KeyError as error:
            raise DataSourceReadError(
                f'{definition.key.identifier}: dataset source has no application route'
            ) from error
        filters = _time_filters(
            timestamp_column=timestamp_column,
            start_utc=start_utc,
            end_utc=end_utc,
        )
        try:
            result = runtime.scan_dataframe(
                definition=definition,
                targets=(target,),
                projection_schema=projection_schema,
                filters=filters,
            )
        except DatasetRuntimeNotFoundError:
            return None
        except (DatasetRuntimeReadError, DatasetRuntimeValidationError) as error:
            raise DataSourceReadError(f'{target.identifier}: dataset source read failed') from error
        dataframe = getattr(result, 'dataframe', None)
        if not isinstance(dataframe, pd.DataFrame):
            raise DataSourceReadError(f'{target.identifier}: dataset runtime returned invalid data')
        return dataframe


def _time_filters(
    *,
    timestamp_column: str | None,
    start_utc: datetime | None,
    end_utc: datetime | None,
) -> tuple[ColumnFilter, ...]:
    if timestamp_column is None:
        if start_utc is not None or end_utc is not None:
            raise ValueError('timestamp_column is required when a time boundary is provided')
        return ()
    filters: list[ColumnFilter] = []
    if start_utc is not None:
        filters.append(
            ColumnFilter(
                column=timestamp_column,
                operator=FilterOperator.GREATER_THAN_OR_EQUAL,
                value=start_utc,
            )
        )
    if end_utc is not None:
        filters.append(
            ColumnFilter(
                column=timestamp_column,
                operator=FilterOperator.LESS_THAN_OR_EQUAL,
                value=end_utc,
            )
        )
    return tuple(filters)
