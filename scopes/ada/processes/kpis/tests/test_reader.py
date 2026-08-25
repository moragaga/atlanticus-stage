from datetime import UTC, datetime
from types import SimpleNamespace

import pandas as pd
import pyarrow as pa
import pytest

from ada.data.sources import DataSourceReadError
from ada.processes.kpis.reader import DatasetRuntimeSourceReader
from atlanticus.datasets.layouts import SingleArtifactLayout
from atlanticus.datasets.models import DatasetDefinition, DatasetKey, MaterializationDefinition
from atlanticus.datasets.runtime import (
    DatasetRuntimeNotFoundError,
    DatasetRuntimeReadError,
    FilterOperator,
)


class FakeRuntime:
    def __init__(self, *, dataframe=None, error=None):
        self.dataframe = dataframe
        self.error = error
        self.calls = []

    def scan_dataframe(self, *, definition, targets, projection_schema=None, filters=()):
        self.calls.append((definition, targets, projection_schema, filters))
        if self.error is not None:
            raise self.error
        return SimpleNamespace(dataframe=self.dataframe.copy())


def _definition(namespace=('pi', 'web-api'), name='interpolated'):
    definition = DatasetDefinition(
        key=DatasetKey(namespace=namespace, name=name),
        materializations=(MaterializationDefinition(name='latest', layout=SingleArtifactLayout()),),
    )
    return definition, definition.resolve_target(materialization='latest')


def _schema(*fields: tuple[str, pa.DataType]) -> pa.Schema:
    return pa.schema([pa.field(name, data_type, nullable=True) for name, data_type in fields])


def test_reader_forwards_typed_projection_and_inclusive_time_filters() -> None:
    definition, target = _definition()
    runtime = FakeRuntime(dataframe=pd.DataFrame({'timestamp_utc': [], 'a': []}))
    reader = DatasetRuntimeSourceReader(runtimes={definition.key: runtime})
    start = datetime(2026, 8, 19, 20, 0, tzinfo=UTC)
    end = datetime(2026, 8, 19, 21, 0, tzinfo=UTC)
    projection_schema = _schema(
        ('a', pa.float64()),
        ('timestamp_utc', pa.timestamp('us', tz='UTC')),
    )

    result = reader.read_frame(
        definition=definition,
        target=target,
        projection_schema=projection_schema,
        timestamp_column='timestamp_utc',
        start_utc=start,
        end_utc=end,
    )

    assert isinstance(result, pd.DataFrame)
    _, targets, forwarded_schema, filters = runtime.calls[0]
    assert targets == (target,)
    assert forwarded_schema == projection_schema
    assert tuple(item.operator for item in filters) == (
        FilterOperator.GREATER_THAN_OR_EQUAL,
        FilterOperator.LESS_THAN_OR_EQUAL,
    )
    assert tuple(item.value for item in filters) == (start, end)


def test_reader_routes_each_dataset_key_to_its_own_application_runtime() -> None:
    pi_definition, pi_target = _definition()
    rem_definition, rem_target = _definition(('remanentes',), 'stocks')
    pi_runtime = FakeRuntime(dataframe=pd.DataFrame({'a': [1.0]}))
    rem_runtime = FakeRuntime(dataframe=pd.DataFrame({'a': [2.0]}))
    reader = DatasetRuntimeSourceReader(
        runtimes={
            pi_definition.key: pi_runtime,
            rem_definition.key: rem_runtime,
        }
    )
    projection_schema = _schema(('a', pa.float64()))

    pi = reader.read_frame(
        definition=pi_definition,
        target=pi_target,
        projection_schema=projection_schema,
    )
    rem = reader.read_frame(
        definition=rem_definition,
        target=rem_target,
        projection_schema=projection_schema,
    )

    assert pi['a'].tolist() == [1.0]
    assert rem['a'].tolist() == [2.0]
    assert len(pi_runtime.calls) == 1
    assert len(rem_runtime.calls) == 1


def test_reader_rejects_dataset_without_application_route() -> None:
    definition, target = _definition()
    reader = DatasetRuntimeSourceReader(runtimes={})

    with pytest.raises(DataSourceReadError, match='no application route'):
        reader.read_frame(
            definition=definition,
            target=target,
            projection_schema=_schema(('a', pa.float64())),
        )


def test_reader_returns_none_for_unpublished_target() -> None:
    definition, target = _definition()
    reader = DatasetRuntimeSourceReader(
        runtimes={
            definition.key: FakeRuntime(error=DatasetRuntimeNotFoundError('missing')),
        }
    )

    assert (
        reader.read_frame(
            definition=definition,
            target=target,
            projection_schema=_schema(('a', pa.float64())),
        )
        is None
    )


def test_reader_translates_runtime_read_failures() -> None:
    definition, target = _definition()
    reader = DatasetRuntimeSourceReader(
        runtimes={definition.key: FakeRuntime(error=DatasetRuntimeReadError('bad'))}
    )

    with pytest.raises(DataSourceReadError, match='dataset source read failed'):
        reader.read_frame(
            definition=definition,
            target=target,
            projection_schema=_schema(('a', pa.float64())),
        )
