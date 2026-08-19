from __future__ import annotations

from datetime import datetime

import pandas as pd

from ada.kpis.sources.errors import KpiSourceReadError, KpiSourceSchemaError
from atlanticus.datasets.models import DatasetDefinition, DatasetTarget
from atlanticus.datasets.runtime import (
    ColumnFilter,
    DatasetRuntime,
    DatasetRuntimeNotFoundError,
    DatasetRuntimeReadError,
    DatasetRuntimeValidationError,
    FilterOperator,
)


class AtlanticusDatasetReader:
    def __init__(self, runtime: DatasetRuntime) -> None:
        if not isinstance(runtime, DatasetRuntime):
            raise TypeError('runtime must be DatasetRuntime')
        self._runtime = runtime

    def read_frame(
        self,
        *,
        definition: DatasetDefinition,
        target: DatasetTarget,
        columns: tuple[str, ...],
        timestamp_column: str | None = None,
        start_utc: datetime | None = None,
        end_utc: datetime | None = None,
    ) -> pd.DataFrame | None:
        try:
            schema = self._runtime.read_schema(definition=definition, target=target)
        except DatasetRuntimeNotFoundError:
            return None
        except DatasetRuntimeValidationError as error:
            raise KpiSourceSchemaError(
                f'invalid schema contract for dataset target {target.identifier}'
            ) from error
        except DatasetRuntimeReadError as error:
            raise KpiSourceReadError(
                f'could not read schema for dataset target {target.identifier}'
            ) from error
        missing = tuple(column for column in columns if column not in schema.names)
        if missing:
            raise KpiSourceSchemaError(
                f'{target.identifier}: requested columns are missing: {missing}'
            )
        filters = _timestamp_filters(
            timestamp_column=timestamp_column,
            start_utc=start_utc,
            end_utc=end_utc,
        )
        try:
            result = self._runtime.scan_dataframe(
                definition=definition,
                targets=(target,),
                columns=columns,
                filters=filters,
            )
        except DatasetRuntimeNotFoundError:
            return None
        except DatasetRuntimeValidationError as error:
            raise KpiSourceSchemaError(
                f'invalid read contract for dataset target {target.identifier}'
            ) from error
        except DatasetRuntimeReadError as error:
            raise KpiSourceReadError(
                f'could not read dataset target {target.identifier}'
            ) from error
        return result.dataframe


def _timestamp_filters(
    *,
    timestamp_column: str | None,
    start_utc: datetime | None,
    end_utc: datetime | None,
) -> tuple[ColumnFilter, ...]:
    if timestamp_column is None:
        if start_utc is not None or end_utc is not None:
            raise ValueError('timestamp bounds require timestamp_column')
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
