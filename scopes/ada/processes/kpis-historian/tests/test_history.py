import json

from ada.kpis.core import KpiStatus, KpiValueKind
from ada.processes.kpis_historian.history import (
    KpiHistoryWriter,
    error_history_definition,
    history_definition,
)
from atlanticus.datasets.parquet import ParquetDatasetStore
from atlanticus.datasets.runtime import DatasetRuntime
from tests.support import evaluation, watermark


def _runtime(tmp_path):
    return DatasetRuntime(store=ParquetDatasetStore(root=tmp_path / 'datasets'))


def _target(definition, day: str):
    return definition.resolve_target(
        materialization='daily',
        partition={'year': '2026', 'month': '08', 'day': day},
    )


def test_history_keeps_only_persist_history_and_uses_value_parsed_value_contract(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    writer = KpiHistoryWriter(runtime=runtime)
    first = evaluation(
        watermark(19, 10),
        key='kept-a',
        value=10,
        parsed_value='10,00',
        persist_history=True,
    )
    skipped = evaluation(watermark(19, 20), key='not-kept', value=20, persist_history=False)
    third = evaluation(
        watermark(19, 50),
        key='kept-json',
        value={'b': 2, 'a': [1, True]},
        persist_history=True,
        value_kind=KpiValueKind.JSON,
    )

    result = writer.write(evaluations=(first, skipped, third))

    table = runtime.read_table(
        definition=history_definition(),
        target=_target(history_definition(), '19'),
    ).table
    assert result.evaluation_count == 3
    assert result.history_row_count == 2
    assert result.error_row_count == 0
    assert result.history_publication_count == 1
    assert result.error_publication_count == 0
    assert result.last_watermark == third.watermark
    assert table.column_names == ['timestamp_utc', 'key', 'status', 'value', 'parsed_value']
    assert table.column('key').to_pylist() == ['kept-a', 'kept-json']
    assert table.column('status').to_pylist() == ['ok', 'ok']
    assert json.loads(table.column('value')[0].as_py()) == 10
    assert json.loads(table.column('parsed_value')[0].as_py()) == '10,00'
    assert table.column('value')[1].as_py() == '{"a":[1,true],"b":2}'
    assert table.column('parsed_value')[1].as_py() is None


def test_error_is_kept_in_functional_history_and_projected_to_error_history(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    writer = KpiHistoryWriter(runtime=runtime)
    failed = evaluation(
        watermark(19, 10),
        key='failed-kpi',
        persist_history=True,
        status=KpiStatus.ERROR,
        error='Required source column was not available',
    )

    result = writer.write(evaluations=(failed,))

    history = runtime.read_table(
        definition=history_definition(),
        target=_target(history_definition(), '19'),
    ).table
    errors = runtime.read_table(
        definition=error_history_definition(),
        target=_target(error_history_definition(), '19'),
    ).table
    assert history.column('status').to_pylist() == ['error']
    assert history.column('value').to_pylist() == [None]
    assert history.column('parsed_value').to_pylist() == [None]
    assert errors.column_names == ['timestamp_utc', 'key', 'error']
    assert errors.column('key').to_pylist() == ['failed-kpi']
    assert errors.column('error').to_pylist() == ['Required source column was not available']
    assert result.history_row_count == 1
    assert result.error_row_count == 1


def test_error_history_is_independent_of_persist_history(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    writer = KpiHistoryWriter(runtime=runtime)
    failed = evaluation(
        watermark(19, 10),
        key='latest-only-failed',
        persist_history=False,
        status=KpiStatus.ERROR,
        error='Resolver failed',
    )

    result = writer.write(evaluations=(failed,))

    assert result.history_row_count == 0
    assert result.history_publication_count == 0
    assert result.error_row_count == 1
    assert result.error_publication_count == 1
    errors = runtime.read_table(
        definition=error_history_definition(),
        target=_target(error_history_definition(), '19'),
    ).table
    assert errors.to_pylist() == [
        {
            'timestamp_utc': failed.watermark.timestamp_utc,
            'key': 'latest-only-failed',
            'error': 'Resolver failed',
        }
    ]


def test_history_and_error_history_partition_by_utc_day_and_retry_idempotently(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    writer = KpiHistoryWriter(runtime=runtime)
    first = evaluation(watermark(19, 10), key='kpi-a', value=10)
    second = evaluation(
        watermark(20, 10),
        key='kpi-a',
        status=KpiStatus.ERROR,
        error='Failed on next day',
    )

    writer.write(evaluations=(first, second))
    retry = writer.write(evaluations=(first, second))

    assert (
        runtime.read_table(
            definition=history_definition(),
            target=_target(history_definition(), '19'),
        ).table.num_rows
        == 1
    )
    assert (
        runtime.read_table(
            definition=history_definition(),
            target=_target(history_definition(), '20'),
        ).table.num_rows
        == 1
    )
    assert (
        runtime.read_table(
            definition=error_history_definition(),
            target=_target(error_history_definition(), '20'),
        ).table.num_rows
        == 1
    )
    assert retry.evaluation_count == 2
    assert retry.history_row_count == 2
    assert retry.error_row_count == 1
    assert retry.history_publication_count == 2
    assert retry.error_publication_count == 1
