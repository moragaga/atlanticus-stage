from __future__ import annotations

from datetime import UTC, datetime

import pyarrow as pa
import pytest

from ada.processes.pi_web_api import (
    PiAcquisitionResult,
    PiAcquisitionWindow,
    PiSample,
    PiWebApiMaterializationError,
    PiWebApiMaterializer,
)
from atlanticus.datasets.parquet import ParquetDatasetStore
from atlanticus.datasets.results import PublicationStatus
from atlanticus.datasets.runtime import DatasetRuntime, DatasetRuntimeNotFoundError
from atlanticus.integrations.pi.contracts import (
    PiCatalog,
    PiExtractionMode,
    PiMaterialization,
    PiTagDefinition,
    PiValueKind,
    PiWebApiSource,
)
from atlanticus.runtime import (
    JobDefinition,
    JobRuntimeContext,
    RuntimeCancellationRequested,
    RuntimeConfiguration,
)


def _context(tmp_path) -> JobRuntimeContext:
    definition = JobDefinition(
        module_name='tests.pi_materialization',
        service_name='pi-web-api',
        execution_timeout_seconds=30,
        shutdown_grace_seconds=1,
        iteration_timeout_seconds=10,
    )
    configuration = RuntimeConfiguration.from_sources(
        environ={
            'ENVIRONMENT': 'local',
            'APPLICATION': 'ada',
            'VOLUMEN_PATH': str(tmp_path),
        }
    )
    context = JobRuntimeContext.create(
        definition=definition,
        configuration=configuration,
        run_id='run-id',
        correlation_id='correlation-id',
    )
    context._begin_iteration(1)
    return context


def _definition(
    name: str,
    alias: str,
    *,
    mode: PiExtractionMode,
    materializations: tuple[PiMaterialization, ...] = (PiMaterialization.DAILY,),
) -> PiTagDefinition:
    return PiTagDefinition(
        tag_name=name,
        alias=alias,
        value_kind=PiValueKind.NUMBER,
        extraction_mode=mode,
        materializations=materializations,
    )


def _runtime(tmp_path) -> DatasetRuntime:
    return DatasetRuntime(store=ParquetDatasetStore(root=tmp_path / 'ada' / 'datasets'))


def _window(
    first: datetime,
    last: datetime,
    *,
    interpolation_seconds: int = 10,
) -> PiAcquisitionWindow:
    return PiAcquisitionWindow(
        first_slot_utc=first,
        last_slot_utc=last,
        interpolation_seconds=interpolation_seconds,
    )


def test_materializer_recorded_keeps_native_seconds_sparse_and_is_idempotent(tmp_path) -> None:
    catalog = PiCatalog(
        source=PiWebApiSource(interpolation_seconds=10),
        definitions=(
            _definition('TAG_A', 'a', mode=PiExtractionMode.RECORDED),
            _definition('TAG_B', 'b', mode=PiExtractionMode.RECORDED),
        ),
    )
    runtime = _runtime(tmp_path)
    materializer = PiWebApiMaterializer(runtime=runtime, catalog=catalog)
    window = _window(
        datetime(2026, 8, 15, 10, 0, 0, tzinfo=UTC),
        datetime(2026, 8, 15, 10, 0, 10, tzinfo=UTC),
    )
    acquisition = PiAcquisitionResult(
        interpolated=(),
        recorded=(
            PiSample('TAG_A', datetime(2026, 8, 15, 10, 0, 3, tzinfo=UTC), 10),
            PiSample('TAG_B', datetime(2026, 8, 15, 10, 0, 7, tzinfo=UTC), 20),
        ),
    )

    first = materializer.publish(window=window, acquisition=acquisition, context=_context(tmp_path))
    second = materializer.publish(
        window=window, acquisition=acquisition, context=_context(tmp_path)
    )

    assert first.recorded_second_conflict_count == 0
    assert [result.status for result in second.publications] == [PublicationStatus.UNCHANGED]

    dataset = materializer.dataset_for(PiExtractionMode.RECORDED)
    assert dataset is not None
    target = dataset.resolve_target(
        materialization='daily',
        partition={'year': '2026', 'month': '08', 'day': '15'},
    )
    table = runtime.read_table(definition=dataset, target=target).table
    assert table.schema.field('timestamp_utc').type == pa.timestamp('us', tz='UTC')
    assert table.to_pydict() == {
        'timestamp_utc': [
            datetime(2026, 8, 15, 10, 0, 3, tzinfo=UTC),
            datetime(2026, 8, 15, 10, 0, 7, tzinfo=UTC),
        ],
        'a': [10.0, None],
        'b': [None, 20.0],
    }


def test_recorded_exact_second_coalesces_different_tags(tmp_path) -> None:
    catalog = PiCatalog(
        source=PiWebApiSource(interpolation_seconds=10),
        definitions=(
            _definition('TAG_A', 'a', mode=PiExtractionMode.RECORDED),
            _definition('TAG_B', 'b', mode=PiExtractionMode.RECORDED),
        ),
    )
    runtime = _runtime(tmp_path)
    materializer = PiWebApiMaterializer(runtime=runtime, catalog=catalog)
    window = _window(
        datetime(2026, 8, 15, 10, 0, 0, tzinfo=UTC),
        datetime(2026, 8, 15, 10, 0, 10, tzinfo=UTC),
    )
    timestamp = datetime(2026, 8, 15, 10, 0, 3, tzinfo=UTC)

    result = materializer.publish(
        window=window,
        acquisition=PiAcquisitionResult(
            interpolated=(),
            recorded=(PiSample('TAG_A', timestamp, 10), PiSample('TAG_B', timestamp, 20)),
        ),
        context=_context(tmp_path),
    )

    assert result.recorded_second_conflict_count == 0
    dataset = materializer.dataset_for(PiExtractionMode.RECORDED)
    assert dataset is not None
    target = dataset.resolve_target(
        materialization='daily', partition={'year': '2026', 'month': '08', 'day': '15'}
    )
    assert runtime.read_table(definition=dataset, target=target).table.to_pydict() == {
        'timestamp_utc': [timestamp],
        'a': [10.0],
        'b': [20.0],
    }


def test_recorded_empty_response_creates_no_publication(tmp_path) -> None:
    catalog = PiCatalog(
        source=PiWebApiSource(interpolation_seconds=10),
        definitions=(_definition('TAG_A', 'a', mode=PiExtractionMode.RECORDED),),
    )
    runtime = _runtime(tmp_path)
    materializer = PiWebApiMaterializer(runtime=runtime, catalog=catalog)
    window = _window(
        datetime(2026, 8, 15, 10, 0, 0, tzinfo=UTC),
        datetime(2026, 8, 15, 10, 0, 10, tzinfo=UTC),
    )

    result = materializer.publish(
        window=window,
        acquisition=PiAcquisitionResult(interpolated=(), recorded=()),
        context=_context(tmp_path),
    )

    assert result.publications == ()
    assert result.recorded_second_conflict_count == 0
    dataset = materializer.dataset_for(PiExtractionMode.RECORDED)
    assert dataset is not None
    target = dataset.resolve_target(
        materialization='daily', partition={'year': '2026', 'month': '08', 'day': '15'}
    )
    with pytest.raises(DatasetRuntimeNotFoundError):
        runtime.read_table(definition=dataset, target=target)


def test_recorded_null_only_event_creates_no_empty_row(tmp_path) -> None:
    catalog = PiCatalog(
        source=PiWebApiSource(interpolation_seconds=10),
        definitions=(_definition('TAG_A', 'a', mode=PiExtractionMode.RECORDED),),
    )
    runtime = _runtime(tmp_path)
    materializer = PiWebApiMaterializer(runtime=runtime, catalog=catalog)
    window = _window(
        datetime(2026, 8, 15, 10, 0, 0, tzinfo=UTC),
        datetime(2026, 8, 15, 10, 0, 10, tzinfo=UTC),
    )

    result = materializer.publish(
        window=window,
        acquisition=PiAcquisitionResult(
            interpolated=(),
            recorded=(PiSample('TAG_A', datetime(2026, 8, 15, 10, 0, 3, tzinfo=UTC), None),),
        ),
        context=_context(tmp_path),
    )

    assert result.publications == ()
    dataset = materializer.dataset_for(PiExtractionMode.RECORDED)
    assert dataset is not None
    target = dataset.resolve_target(
        materialization='daily', partition={'year': '2026', 'month': '08', 'day': '15'}
    )
    with pytest.raises(DatasetRuntimeNotFoundError):
        runtime.read_table(definition=dataset, target=target)


def test_recorded_same_tag_same_second_keeps_newest_native_event(tmp_path) -> None:
    catalog = PiCatalog(
        source=PiWebApiSource(interpolation_seconds=10),
        definitions=(_definition('TAG_A', 'a', mode=PiExtractionMode.RECORDED),),
    )
    runtime = _runtime(tmp_path)
    materializer = PiWebApiMaterializer(runtime=runtime, catalog=catalog)
    window = _window(
        datetime(2026, 8, 15, 10, 0, 0, tzinfo=UTC),
        datetime(2026, 8, 15, 10, 0, 10, tzinfo=UTC),
    )

    result = materializer.publish(
        window=window,
        acquisition=PiAcquisitionResult(
            interpolated=(),
            recorded=(
                PiSample(
                    'TAG_A',
                    datetime(2026, 8, 15, 10, 0, 3, 100_000, tzinfo=UTC),
                    1,
                ),
                PiSample(
                    'TAG_A',
                    datetime(2026, 8, 15, 10, 0, 3, 900_000, tzinfo=UTC),
                    2,
                ),
            ),
        ),
        context=_context(tmp_path),
    )

    assert result.recorded_second_conflict_count == 1
    dataset = materializer.dataset_for(PiExtractionMode.RECORDED)
    assert dataset is not None
    target = dataset.resolve_target(
        materialization='daily', partition={'year': '2026', 'month': '08', 'day': '15'}
    )
    assert runtime.read_table(definition=dataset, target=target).table.to_pydict() == {
        'timestamp_utc': [datetime(2026, 8, 15, 10, 0, 3, tzinfo=UTC)],
        'a': [2.0],
    }


def test_recorded_sparse_replay_preserves_existing_values_on_null(tmp_path) -> None:
    catalog = PiCatalog(
        source=PiWebApiSource(interpolation_seconds=10),
        definitions=(
            _definition('TAG_A', 'a', mode=PiExtractionMode.RECORDED),
            _definition('TAG_B', 'b', mode=PiExtractionMode.RECORDED),
        ),
    )
    runtime = _runtime(tmp_path)
    materializer = PiWebApiMaterializer(runtime=runtime, catalog=catalog)
    window = _window(
        datetime(2026, 8, 15, 10, 0, 0, tzinfo=UTC),
        datetime(2026, 8, 15, 10, 0, 10, tzinfo=UTC),
    )
    timestamp = datetime(2026, 8, 15, 10, 0, 3, tzinfo=UTC)

    materializer.publish(
        window=window,
        acquisition=PiAcquisitionResult(
            interpolated=(),
            recorded=(PiSample('TAG_A', timestamp, 10), PiSample('TAG_B', timestamp, 20)),
        ),
        context=_context(tmp_path),
    )
    replay = materializer.publish(
        window=window,
        acquisition=PiAcquisitionResult(
            interpolated=(),
            recorded=(PiSample('TAG_A', timestamp, None),),
        ),
        context=_context(tmp_path),
    )

    assert replay.publications == ()
    dataset = materializer.dataset_for(PiExtractionMode.RECORDED)
    assert dataset is not None
    target = dataset.resolve_target(
        materialization='daily', partition={'year': '2026', 'month': '08', 'day': '15'}
    )
    assert runtime.read_table(definition=dataset, target=target).table.to_pydict() == {
        'timestamp_utc': [timestamp],
        'a': [10.0],
        'b': [20.0],
    }


def test_recorded_partitions_by_native_event_timestamp_across_day_and_month(tmp_path) -> None:
    catalog = PiCatalog(
        source=PiWebApiSource(interpolation_seconds=10),
        definitions=(
            _definition(
                'TAG_A',
                'a',
                mode=PiExtractionMode.RECORDED,
                materializations=(PiMaterialization.DAILY, PiMaterialization.MONTHLY),
            ),
        ),
    )
    runtime = _runtime(tmp_path)
    materializer = PiWebApiMaterializer(runtime=runtime, catalog=catalog)
    window = _window(
        datetime(2026, 8, 31, 23, 59, 50, tzinfo=UTC),
        datetime(2026, 9, 1, 0, 0, 0, tzinfo=UTC),
    )
    august = datetime(2026, 8, 31, 23, 59, 59, tzinfo=UTC)
    september = datetime(2026, 9, 1, 0, 0, 3, tzinfo=UTC)

    result = materializer.publish(
        window=window,
        acquisition=PiAcquisitionResult(
            interpolated=(),
            recorded=(PiSample('TAG_A', august, 1), PiSample('TAG_A', september, 2)),
        ),
        context=_context(tmp_path),
    )

    assert len(result.publications) == 4
    dataset = materializer.dataset_for(PiExtractionMode.RECORDED)
    assert dataset is not None
    day_august = dataset.resolve_target(
        materialization='daily', partition={'year': '2026', 'month': '08', 'day': '31'}
    )
    day_september = dataset.resolve_target(
        materialization='daily', partition={'year': '2026', 'month': '09', 'day': '01'}
    )
    month_august = dataset.resolve_target(
        materialization='monthly', partition={'year': '2026', 'month': '08'}
    )
    month_september = dataset.resolve_target(
        materialization='monthly', partition={'year': '2026', 'month': '09'}
    )
    assert runtime.read_table(definition=dataset, target=day_august).table[
        'timestamp_utc'
    ].to_pylist() == [august]
    assert runtime.read_table(definition=dataset, target=day_september).table[
        'timestamp_utc'
    ].to_pylist() == [september]
    assert runtime.read_table(definition=dataset, target=month_august).table[
        'timestamp_utc'
    ].to_pylist() == [august]
    assert runtime.read_table(definition=dataset, target=month_september).table[
        'timestamp_utc'
    ].to_pylist() == [september]


def test_active_partition_preserves_retired_columns_and_new_partition_uses_current_catalog(
    tmp_path,
) -> None:
    runtime = _runtime(tmp_path)
    initial_catalog = PiCatalog(
        source=PiWebApiSource(interpolation_seconds=10),
        definitions=(
            _definition('TAG_A', 'a', mode=PiExtractionMode.INTERPOLATED),
            _definition('TAG_OLD', 'old', mode=PiExtractionMode.INTERPOLATED),
        ),
    )
    initial = PiWebApiMaterializer(runtime=runtime, catalog=initial_catalog)
    initial_window = _window(
        datetime(2026, 8, 15, 23, 59, 40, tzinfo=UTC),
        datetime(2026, 8, 15, 23, 59, 40, tzinfo=UTC),
    )
    initial.publish(
        window=initial_window,
        acquisition=PiAcquisitionResult(
            interpolated=(
                PiSample('TAG_A', initial_window.first_slot_utc, 1),
                PiSample('TAG_OLD', initial_window.first_slot_utc, 9),
            ),
            recorded=(),
        ),
        context=_context(tmp_path),
    )

    current_catalog = PiCatalog(
        source=PiWebApiSource(interpolation_seconds=10),
        definitions=(
            _definition('TAG_A', 'a', mode=PiExtractionMode.INTERPOLATED),
            _definition('TAG_NEW', 'new', mode=PiExtractionMode.INTERPOLATED),
        ),
    )
    current = PiWebApiMaterializer(runtime=runtime, catalog=current_catalog)
    same_day_window = _window(
        datetime(2026, 8, 15, 23, 59, 50, tzinfo=UTC),
        datetime(2026, 8, 15, 23, 59, 50, tzinfo=UTC),
    )
    current.publish(
        window=same_day_window,
        acquisition=PiAcquisitionResult(
            interpolated=(
                PiSample('TAG_A', same_day_window.first_slot_utc, 2),
                PiSample('TAG_NEW', same_day_window.first_slot_utc, 3),
            ),
            recorded=(),
        ),
        context=_context(tmp_path),
    )

    dataset = current.dataset_for(PiExtractionMode.INTERPOLATED)
    assert dataset is not None
    day_15 = dataset.resolve_target(
        materialization='daily',
        partition={'year': '2026', 'month': '08', 'day': '15'},
    )
    table_15 = runtime.read_table(definition=dataset, target=day_15).table
    assert table_15.column_names == ['timestamp_utc', 'a', 'old', 'new']
    assert table_15['old'].to_pylist() == [9.0, None]
    assert table_15['new'].to_pylist() == [None, 3.0]

    next_day_window = _window(
        datetime(2026, 8, 16, 0, 0, 0, tzinfo=UTC),
        datetime(2026, 8, 16, 0, 0, 0, tzinfo=UTC),
    )
    current.publish(
        window=next_day_window,
        acquisition=PiAcquisitionResult(
            interpolated=(
                PiSample('TAG_A', next_day_window.first_slot_utc, 4),
                PiSample('TAG_NEW', next_day_window.first_slot_utc, 5),
            ),
            recorded=(),
        ),
        context=_context(tmp_path),
    )
    day_16 = dataset.resolve_target(
        materialization='daily',
        partition={'year': '2026', 'month': '08', 'day': '16'},
    )
    table_16 = runtime.read_table(definition=dataset, target=day_16).table
    assert table_16.column_names == ['timestamp_utc', 'a', 'new']
    assert table_16.to_pydict()['new'] == [5.0]


def test_materializer_keeps_latest_on_current_catalog_only(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    initial_catalog = PiCatalog(
        source=PiWebApiSource(interpolation_seconds=10),
        definitions=(
            _definition(
                'TAG_A',
                'a',
                mode=PiExtractionMode.INTERPOLATED,
                materializations=(PiMaterialization.LATEST,),
            ),
            _definition(
                'TAG_OLD',
                'old',
                mode=PiExtractionMode.INTERPOLATED,
                materializations=(PiMaterialization.LATEST,),
            ),
        ),
    )
    slot = datetime(2026, 8, 15, 10, tzinfo=UTC)
    initial = PiWebApiMaterializer(runtime=runtime, catalog=initial_catalog)
    initial.publish(
        window=_window(slot, slot),
        acquisition=PiAcquisitionResult(
            interpolated=(PiSample('TAG_A', slot, 1), PiSample('TAG_OLD', slot, 2)),
            recorded=(),
        ),
        context=_context(tmp_path),
    )

    current_catalog = PiCatalog(
        source=PiWebApiSource(interpolation_seconds=10),
        definitions=(
            _definition(
                'TAG_A',
                'a',
                mode=PiExtractionMode.INTERPOLATED,
                materializations=(PiMaterialization.LATEST,),
            ),
        ),
    )
    current = PiWebApiMaterializer(runtime=runtime, catalog=current_catalog)
    next_slot = datetime(2026, 8, 15, 10, 0, 10, tzinfo=UTC)
    current.publish(
        window=_window(next_slot, next_slot),
        acquisition=PiAcquisitionResult(
            interpolated=(PiSample('TAG_A', next_slot, 3),),
            recorded=(),
        ),
        context=_context(tmp_path),
    )

    dataset = current.dataset_for(PiExtractionMode.INTERPOLATED)
    assert dataset is not None
    latest = dataset.resolve_target(materialization='latest')
    table = runtime.read_table(definition=dataset, target=latest).table
    assert table.column_names == ['timestamp_utc', 'a']
    assert table.to_pydict()['a'] == [3.0]


def test_materializer_replay_completes_partial_publication_without_duplicates(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = PiCatalog(
        source=PiWebApiSource(interpolation_seconds=10),
        definitions=(
            _definition('TAG_A', 'a', mode=PiExtractionMode.INTERPOLATED),
            _definition('TAG_B', 'b', mode=PiExtractionMode.RECORDED),
        ),
    )
    runtime = _runtime(tmp_path)
    materializer = PiWebApiMaterializer(runtime=runtime, catalog=catalog)
    slot = datetime(2026, 8, 15, 10, 0, 0, tzinfo=UTC)
    window = _window(slot, slot)
    acquisition = PiAcquisitionResult(
        interpolated=(PiSample('TAG_A', slot, 1),),
        recorded=(PiSample('TAG_B', slot, 2),),
    )
    original_merge = runtime.merge

    def fail_recorded(*, definition, target, data, key_columns, order_by=()):
        if definition.key.name == PiExtractionMode.RECORDED.value:
            raise RuntimeError('controlled recorded publication failure')
        return original_merge(
            definition=definition,
            target=target,
            data=data,
            key_columns=key_columns,
            order_by=order_by,
        )

    monkeypatch.setattr(runtime, 'merge', fail_recorded)
    with pytest.raises(RuntimeError, match='controlled recorded publication failure'):
        materializer.publish(
            window=window,
            acquisition=acquisition,
            context=_context(tmp_path),
        )

    interpolated_dataset = materializer.dataset_for(PiExtractionMode.INTERPOLATED)
    recorded_dataset = materializer.dataset_for(PiExtractionMode.RECORDED)
    assert interpolated_dataset is not None
    assert recorded_dataset is not None
    partition = {'year': '2026', 'month': '08', 'day': '15'}
    interpolated_target = interpolated_dataset.resolve_target(
        materialization='daily', partition=partition
    )
    recorded_target = recorded_dataset.resolve_target(materialization='daily', partition=partition)
    assert (
        runtime.read_table(
            definition=interpolated_dataset, target=interpolated_target
        ).table.num_rows
        == 1
    )
    with pytest.raises(DatasetRuntimeNotFoundError):
        runtime.read_table(definition=recorded_dataset, target=recorded_target)

    monkeypatch.setattr(runtime, 'merge', original_merge)
    replay = materializer.publish(
        window=window,
        acquisition=acquisition,
        context=_context(tmp_path),
    )

    assert [publication.status for publication in replay.publications] == [
        PublicationStatus.UNCHANGED,
        PublicationStatus.COMMITTED,
    ]
    assert (
        runtime.read_table(
            definition=interpolated_dataset, target=interpolated_target
        ).table.num_rows
        == 1
    )
    assert (
        runtime.read_table(definition=recorded_dataset, target=recorded_target).table.num_rows == 1
    )


def test_materializer_checks_cancellation_before_first_write(tmp_path) -> None:
    catalog = PiCatalog(
        source=PiWebApiSource(interpolation_seconds=10),
        definitions=(_definition('TAG_A', 'a', mode=PiExtractionMode.INTERPOLATED),),
    )
    runtime = _runtime(tmp_path)
    materializer = PiWebApiMaterializer(runtime=runtime, catalog=catalog)
    slot = datetime(2026, 8, 15, 10, 0, 0, tzinfo=UTC)
    context = _context(tmp_path)
    context.request_stop('lease_lost')

    with pytest.raises(RuntimeCancellationRequested, match='lease_lost'):
        materializer.publish(
            window=_window(slot, slot),
            acquisition=PiAcquisitionResult(
                interpolated=(PiSample('TAG_A', slot, 1),),
                recorded=(),
            ),
            context=context,
        )

    dataset = materializer.dataset_for(PiExtractionMode.INTERPOLATED)
    assert dataset is not None
    target = dataset.resolve_target(
        materialization='daily',
        partition={'year': '2026', 'month': '08', 'day': '15'},
    )
    with pytest.raises(DatasetRuntimeNotFoundError):
        runtime.read_table(definition=dataset, target=target)


def test_active_partition_rejects_type_change_for_existing_alias(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    slot = datetime(2026, 8, 15, 10, 0, 0, tzinfo=UTC)
    initial_catalog = PiCatalog(
        source=PiWebApiSource(interpolation_seconds=10),
        definitions=(_definition('TAG_A', 'a', mode=PiExtractionMode.INTERPOLATED),),
    )
    PiWebApiMaterializer(runtime=runtime, catalog=initial_catalog).publish(
        window=_window(slot, slot),
        acquisition=PiAcquisitionResult(
            interpolated=(PiSample('TAG_A', slot, 1),),
            recorded=(),
        ),
        context=_context(tmp_path),
    )

    changed_catalog = PiCatalog(
        source=PiWebApiSource(interpolation_seconds=10),
        definitions=(
            PiTagDefinition(
                tag_name='TAG_A',
                alias='a',
                value_kind=PiValueKind.TEXT,
                extraction_mode=PiExtractionMode.INTERPOLATED,
                materializations=(PiMaterialization.DAILY,),
            ),
        ),
    )
    changed = PiWebApiMaterializer(runtime=runtime, catalog=changed_catalog)
    next_slot = datetime(2026, 8, 15, 10, 0, 10, tzinfo=UTC)

    with pytest.raises(PiWebApiMaterializationError, match='column type is incompatible: a'):
        changed.publish(
            window=_window(next_slot, next_slot),
            acquisition=PiAcquisitionResult(
                interpolated=(PiSample('TAG_A', next_slot, 'RUNNING'),),
                recorded=(),
            ),
            context=_context(tmp_path),
        )


def test_materializer_stops_before_next_target_when_ownership_is_lost(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = PiCatalog(
        source=PiWebApiSource(interpolation_seconds=10),
        definitions=(
            _definition('TAG_A', 'a', mode=PiExtractionMode.INTERPOLATED),
            _definition('TAG_B', 'b', mode=PiExtractionMode.RECORDED),
        ),
    )
    runtime = _runtime(tmp_path)
    materializer = PiWebApiMaterializer(runtime=runtime, catalog=catalog)
    slot = datetime(2026, 8, 15, 10, 0, 0, tzinfo=UTC)
    context = _context(tmp_path)
    original_merge = runtime.merge

    def stop_after_interpolated(*, definition, target, data, key_columns, order_by=()):
        result = original_merge(
            definition=definition,
            target=target,
            data=data,
            key_columns=key_columns,
            order_by=order_by,
        )
        if definition.key.name == PiExtractionMode.INTERPOLATED.value:
            context.request_stop('lease_lost')
        return result

    monkeypatch.setattr(runtime, 'merge', stop_after_interpolated)

    with pytest.raises(RuntimeCancellationRequested, match='lease_lost'):
        materializer.publish(
            window=_window(slot, slot),
            acquisition=PiAcquisitionResult(
                interpolated=(PiSample('TAG_A', slot, 1),),
                recorded=(PiSample('TAG_B', slot, 2),),
            ),
            context=context,
        )

    interpolated_dataset = materializer.dataset_for(PiExtractionMode.INTERPOLATED)
    recorded_dataset = materializer.dataset_for(PiExtractionMode.RECORDED)
    assert interpolated_dataset is not None
    assert recorded_dataset is not None
    partition = {'year': '2026', 'month': '08', 'day': '15'}
    assert (
        runtime.read_table(
            definition=interpolated_dataset,
            target=interpolated_dataset.resolve_target(
                materialization='daily', partition=partition
            ),
        ).table.num_rows
        == 1
    )
    with pytest.raises(DatasetRuntimeNotFoundError):
        runtime.read_table(
            definition=recorded_dataset,
            target=recorded_dataset.resolve_target(materialization='daily', partition=partition),
        )
