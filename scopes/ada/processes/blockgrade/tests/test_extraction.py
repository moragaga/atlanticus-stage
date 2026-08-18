from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

import pyarrow as pa
import pytest

from ada.processes.blockgrade.errors import BlockgradeSqlReadError
from ada.processes.blockgrade.extraction import BlockgradeSqlReader
from ada.processes.blockgrade.models import BlockgradeSourcePlan
from ada.processes.blockgrade.settings import BlockgradeSqlRetryPolicy
from atlanticus.connectivity.sql import (
    SqlBatch,
    SqlClient,
    SqlConnectionError,
    SqlQueryContractError,
    SqlSettings,
    SqlTableChangeMarker,
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
        self.marker_calls = 0
        self.stream_calls = 0
        self.statement = None
        self.parameters = None
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
                last_user_update_token='2026-08-17T22:00:00',
                user_updates=1,
            )
            for table in source_tables
        )

    def iter_batches(self, statement, parameters=None, *, batch_size=None):
        self.stream_calls += 1
        self.statement = statement
        self.parameters = tuple(parameters or ())
        return _Stream(self.batches)


def _plan(definition) -> BlockgradeSourcePlan:
    return BlockgradeSourcePlan(
        definition=definition,
        change_marker=SqlTableChangeMarker(
            source_table=definition.source_table,
            generation_token='generation',
            last_user_update_token='token',
            user_updates=1,
        ),
        scope_token='260817002|260817001',
        shift_ids=(260817002, 260817001),
    )


def test_reader_builds_projected_shift_query_and_arrow_table(shift_definition) -> None:
    sql = _Sql()
    sql.batches = (
        SqlBatch(
            columns=('shift_id', 'moment', 'value'),
            rows=((260817002, datetime(2026, 8, 17, 18, 0), 1.5),),
            batch_number=1,
            row_offset=0,
            duration_ms=1.0,
        ),
    )
    reader = BlockgradeSqlReader(sql=sql, retry_policy=BlockgradeSqlRetryPolicy(attempts=1))

    table = reader.read_source(_plan(shift_definition))

    assert isinstance(table, pa.Table)
    assert table.column_names == ['shift_id', 'moment', 'value']
    assert table.num_rows == 1
    assert sql.parameters == (260817002, 260817001)
    assert '[ShiftId] IN (?, ?)' in sql.statement
    assert '[Moment] AS [moment]' in sql.statement


def test_real_blockgrade_catalog_builds_shiftindex_query() -> None:
    from ada.processes.blockgrade.catalog import build_catalog

    definition = build_catalog()[0]
    sql = _Sql()
    reader = BlockgradeSqlReader(sql=sql, retry_policy=BlockgradeSqlRetryPolicy(attempts=1))

    table = reader.read_source(_plan(definition))

    assert table.num_rows == 0
    assert 'FROM [dbo].[mms_blockgradedetailsbucket]' in sql.statement
    assert '[shiftindex] AS [shift_id]' in sql.statement
    assert '[shiftindex] IN (?, ?)' in sql.statement
    assert sql.parameters == (260817002, 260817001)


def test_change_marker_retry_recovers_transient_connection_error(
    shift_definition, monkeypatch
) -> None:
    sql = _Sql()
    sql.marker_failures = [SqlConnectionError('offline')]
    reader = BlockgradeSqlReader(
        sql=sql,
        retry_policy=BlockgradeSqlRetryPolicy(attempts=2, delay_seconds=0),
    )
    monkeypatch.setattr('ada.processes.blockgrade.extraction.time.sleep', lambda _: None)

    markers = reader.read_change_markers((shift_definition,))

    assert markers['source_shift'].user_updates == 1
    assert sql.marker_calls == 2


def test_contract_error_is_not_retried(shift_definition) -> None:
    sql = _Sql()
    sql.marker_failures = [SqlQueryContractError('invalid contract')]
    reader = BlockgradeSqlReader(
        sql=sql,
        retry_policy=BlockgradeSqlRetryPolicy(attempts=10, delay_seconds=0),
    )

    with pytest.raises(SqlQueryContractError):
        reader.read_change_markers((shift_definition,))

    assert sql.marker_calls == 1


def test_reader_enforces_source_row_limit(shift_definition) -> None:
    sql = _Sql()
    sql.batches = (
        SqlBatch(
            columns=('shift_id', 'moment', 'value'),
            rows=((260817002, datetime(2026, 8, 17, 18, 0), 1.0),) * 2,
            batch_number=1,
            row_offset=0,
            duration_ms=1.0,
        ),
    )
    reader = BlockgradeSqlReader(
        sql=sql,
        retry_policy=BlockgradeSqlRetryPolicy(attempts=1),
        max_rows=1,
    )

    with pytest.raises(BlockgradeSqlReadError, match='row limit'):
        reader.read_source(_plan(shift_definition))


class _CancellingContext:
    def __init__(self, *, cancel_on_call: int) -> None:
        self.cancel_on_call = cancel_on_call
        self.calls = 0

    def raise_if_cancelled(self) -> None:
        from atlanticus.runtime import RuntimeCancellationRequested

        self.calls += 1
        if self.calls >= self.cancel_on_call:
            raise RuntimeCancellationRequested('test_cancelled')

    def wait(self, _seconds: float) -> bool:
        return True


def test_reader_honors_runtime_cancellation_between_sql_batches(shift_definition) -> None:
    from atlanticus.runtime import RuntimeCancellationRequested

    sql = _Sql()
    sql.batches = (
        SqlBatch(
            columns=('shift_id', 'moment', 'value'),
            rows=((260817002, datetime(2026, 8, 17, 18, 0), 1.0),),
            batch_number=1,
            row_offset=0,
            duration_ms=1.0,
        ),
        SqlBatch(
            columns=('shift_id', 'moment', 'value'),
            rows=((260817001, datetime(2026, 8, 17, 6, 0), 2.0),),
            batch_number=2,
            row_offset=1,
            duration_ms=1.0,
        ),
    )
    reader = BlockgradeSqlReader(sql=sql, retry_policy=BlockgradeSqlRetryPolicy(attempts=1))

    with pytest.raises(RuntimeCancellationRequested):
        reader.read_source(
            _plan(shift_definition),
            context=_CancellingContext(cancel_on_call=4),
        )


def test_retry_wait_is_interruptible_by_runtime_context(shift_definition) -> None:
    from atlanticus.runtime import RuntimeCancellationRequested

    class _RetryContext:
        def __init__(self) -> None:
            self.cancelled = False

        def raise_if_cancelled(self) -> None:
            if self.cancelled:
                raise RuntimeCancellationRequested('test_cancelled')

        def wait(self, _seconds: float) -> bool:
            self.cancelled = True
            return False

    sql = _Sql()
    sql.marker_failures = [SqlConnectionError('offline')]
    reader = BlockgradeSqlReader(
        sql=sql,
        retry_policy=BlockgradeSqlRetryPolicy(attempts=2, delay_seconds=5),
    )

    with pytest.raises(RuntimeCancellationRequested):
        reader.read_change_markers((shift_definition,), context=_RetryContext())
