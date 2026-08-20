import json

from ada.kpis.core import KpiStatus, KpiValueKind
from ada.processes.kpis_historian.history import KpiHistoryWriter, history_definition
from atlanticus.datasets.parquet import ParquetDatasetStore
from atlanticus.datasets.runtime import DatasetRuntime
from tests.support import evaluation, watermark


def _runtime(tmp_path):
    return DatasetRuntime(store=ParquetDatasetStore(root=tmp_path / 'datasets'))


def test_history_writer_filters_persist_history_and_preserves_actual_evaluation_ticks(
    tmp_path,
) -> None:
    runtime = _runtime(tmp_path)
    writer = KpiHistoryWriter(runtime=runtime)
    first = evaluation(watermark(19, 10), key='kept-a', value=10, persist_history=True)
    skipped = evaluation(watermark(19, 20), key='not-kept', value=20, persist_history=False)
    third = evaluation(
        watermark(19, 50),
        key='kept-json',
        value={'b': 2, 'a': [1, True]},
        persist_history=True,
        value_kind=KpiValueKind.JSON,
    )

    result = writer.write(evaluations=(first, skipped, third))

    target = history_definition().resolve_target(
        materialization='daily',
        partition={'year': '2026', 'month': '08', 'day': '19'},
    )
    table = runtime.read_table(definition=history_definition(), target=target).table
    assert result.evaluation_count == 3
    assert result.row_count == 2
    assert result.publication_count == 1
    assert result.last_watermark == third.watermark
    assert (
        tmp_path
        / 'datasets'
        / 'kpis'
        / 'history'
        / 'year=2026'
        / 'month=08'
        / 'day=19'
        / 'data.parquet'
    ).is_file()
    assert table.column('key').to_pylist() == ['kept-a', 'kept-json']
    assert table.column('timestamp_utc').to_pylist() == [
        first.watermark.timestamp_utc,
        third.watermark.timestamp_utc,
    ]
    assert json.loads(table.column('value_json')[0].as_py()) == 10
    assert table.column('value_json')[1].as_py() == '{"a":[1,true],"b":2}'


def test_history_writer_persists_non_ok_observation_with_null_value(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    writer = KpiHistoryWriter(runtime=runtime)
    missing = evaluation(
        watermark(19, 10),
        key='missing-kpi',
        persist_history=True,
        status=KpiStatus.MISSING,
    )

    writer.write(evaluations=(missing,))

    target = history_definition().resolve_target(
        materialization='daily',
        partition={'year': '2026', 'month': '08', 'day': '19'},
    )
    table = runtime.read_table(definition=history_definition(), target=target).table
    assert table.column('status').to_pylist() == ['missing']
    assert table.column('value_json').to_pylist() == [None]


def test_history_writer_partitions_by_utc_day_and_retry_is_idempotent(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    writer = KpiHistoryWriter(runtime=runtime)
    first = evaluation(watermark(19, 10), key='kpi-a', value=10)
    second = evaluation(watermark(20, 10), key='kpi-a', value=11)

    writer.write(evaluations=(first, second))
    retry = writer.write(evaluations=(first, second))

    definition = history_definition()
    first_target = definition.resolve_target(
        materialization='daily',
        partition={'year': '2026', 'month': '08', 'day': '19'},
    )
    second_target = definition.resolve_target(
        materialization='daily',
        partition={'year': '2026', 'month': '08', 'day': '20'},
    )
    assert runtime.read_table(definition=definition, target=first_target).table.num_rows == 1
    assert runtime.read_table(definition=definition, target=second_target).table.num_rows == 1
    assert retry.evaluation_count == 2
    assert retry.row_count == 2
    assert retry.publication_count == 2
