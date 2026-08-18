from __future__ import annotations

from collections.abc import Iterable

import pyarrow as pa
import pytest

from atlanticus.connectivity.sql import (
    SqlBatch,
    SqlClient,
    SqlConnectionError,
    SqlQueryContractError,
    SqlSettings,
    SqlTableChangeMarker,
)
from atlanticus.data_producers.core import SourceScope, SourceScopeItem
from atlanticus.data_producers.sql import (
    SqlDataProducerReader,
    SqlDataProducerReadError,
    SqlRetryPolicy,
    SqlSourcePlan,
)


class _Stream:
    def __init__(self, batches: Iterable[SqlBatch]) -> None:
        self._batches = iter(batches)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def __iter__(self):
        return self._batches


class _Sql(SqlClient):
    def __init__(self) -> None:
        super().__init__(
            settings=SqlSettings(
                connection_string='Server=example;Database=example',
                max_query_rows=10,
            )
        )
        self.statement = None
        self.parameters = None
        self.marker_calls = 0
        self.marker_failures: list[Exception] = []
        self.batches: tuple[SqlBatch, ...] = ()

    def table_change_markers(self, source_tables):
        self.marker_calls += 1
        if self.marker_failures:
            raise self.marker_failures.pop(0)
        return tuple(
            SqlTableChangeMarker(
                source_table=table,
                generation_token='generation',
                last_user_update_token='token',
                user_updates=1,
            )
            for table in source_tables
        )

    def iter_batches(self, statement, parameters=None, *, batch_size=None):
        self.statement = statement
        self.parameters = tuple(parameters or ())
        return _Stream(self.batches)


def _scope() -> SourceScope:
    return SourceScope(
        token='1|2',
        items=(
            SourceScopeItem(value=1, partition={'year': '2026', 'window': '1'}),
            SourceScopeItem(value=2, partition={'year': '2026', 'window': '2'}),
        ),
    )


def _plan(definition) -> SqlSourcePlan:
    return SqlSourcePlan(
        definition=definition,
        change_marker=SqlTableChangeMarker(
            source_table=definition.source_table,
            generation_token='generation',
            last_user_update_token='token',
            user_updates=1,
        ),
        scope=_scope(),
    )


def test_reader_builds_scoped_projection(scoped_definition) -> None:
    sql = _Sql()
    sql.batches = (
        SqlBatch(
            columns=('scope_id', 'moment', 'value'),
            rows=((1, None, 2.5),),
            batch_number=1,
            row_offset=0,
            duration_ms=1.0,
        ),
    )
    reader = SqlDataProducerReader(sql=sql, retry_policy=SqlRetryPolicy(attempts=1))

    table = reader.read_source(_plan(scoped_definition))

    assert isinstance(table, pa.Table)
    assert table.num_rows == 1
    assert '[ScopeId] AS [scope_id]' in sql.statement
    assert '[ScopeId] IN (?, ?)' in sql.statement
    assert sql.parameters == (1, 2)


def test_reader_retries_only_transient_connection_errors(scoped_definition, monkeypatch) -> None:
    sql = _Sql()
    sql.marker_failures = [SqlConnectionError('offline')]
    reader = SqlDataProducerReader(
        sql=sql,
        retry_policy=SqlRetryPolicy(attempts=2, delay_seconds=0),
    )
    monkeypatch.setattr('atlanticus.data_producers.sql.extraction.time.sleep', lambda _: None)

    markers = reader.read_change_markers((scoped_definition,))

    assert markers['source_scoped'].user_updates == 1
    assert sql.marker_calls == 2


def test_reader_does_not_retry_contract_errors(scoped_definition) -> None:
    sql = _Sql()
    sql.marker_failures = [SqlQueryContractError('invalid')]
    reader = SqlDataProducerReader(
        sql=sql,
        retry_policy=SqlRetryPolicy(attempts=10, delay_seconds=0),
    )

    with pytest.raises(SqlQueryContractError):
        reader.read_change_markers((scoped_definition,))

    assert sql.marker_calls == 1


def test_reader_enforces_row_limit(scoped_definition) -> None:
    sql = _Sql()
    sql.batches = (
        SqlBatch(
            columns=('scope_id', 'moment', 'value'),
            rows=((1, None, 2.5), (1, None, 3.5)),
            batch_number=1,
            row_offset=0,
            duration_ms=1.0,
        ),
    )
    reader = SqlDataProducerReader(
        sql=sql,
        retry_policy=SqlRetryPolicy(attempts=1),
        max_rows=1,
    )

    with pytest.raises(SqlDataProducerReadError, match='row limit'):
        reader.read_source(_plan(scoped_definition))
