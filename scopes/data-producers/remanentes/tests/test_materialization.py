from __future__ import annotations

from datetime import UTC, datetime

import pyarrow as pa

from atlanticus.data_producers.remanentes import (
    RemanentesLatestMaterializer,
    RemanentesSourceBlob,
    RemanentesStorageSource,
)
from atlanticus.data_producers.remanentes.materialization import (
    _build_dataset_definition,
    _build_latest_dataset_definition,
)
from atlanticus.datasets.parquet import ParquetDatasetStore
from atlanticus.datasets.results import PublicationStatus
from atlanticus.datasets.runtime import DatasetRuntime

from .support import build_test_catalog


def _source_blob(name: str, timestamp: datetime) -> RemanentesSourceBlob:
    return RemanentesSourceBlob(
        name=name,
        source_file_timestamp_utc=timestamp,
        size=1,
        etag=f'etag-{name}',
        last_modified_utc=timestamp,
    )


def _source(definition):
    source = object.__new__(RemanentesStorageSource)
    source._client = object()
    source._container_name = 'dataproduct'
    source.definition = definition
    return source


def test_dataset_route_preserves_legacy_daily_contract() -> None:
    dataset = _build_dataset_definition(build_test_catalog()[0])
    target = dataset.resolve_target(
        materialization='daily',
        partition={'year': '2026', 'month': '08', 'day': '11'},
    )

    assert dataset.resolve_route_segments(target) == (
        'remanentes',
        'stocks',
        'year=2026',
        'month=08',
        'day=11',
    )


def test_latest_dataset_route_is_unpartitioned() -> None:
    dataset = _build_latest_dataset_definition(build_test_catalog()[0])
    target = dataset.resolve_target(materialization='latest')

    assert target.partition is None
    assert dataset.resolve_route_segments(target) == ('remanentes', 'stocks', 'latest')


def test_latest_materializer_only_plans_newest_pending_source(tmp_path, monkeypatch) -> None:
    definition = build_test_catalog()[1]
    source = _source(definition)
    runtime = DatasetRuntime(store=ParquetDatasetStore(root=tmp_path))
    materializer = RemanentesLatestMaterializer(
        source=source,
        runtime=runtime,
        definition=definition,
    )
    first = _source_blob('first.parquet', datetime(2026, 8, 18, 10, 0, tzinfo=UTC))
    latest = _source_blob('latest.parquet', datetime(2026, 8, 18, 10, 15, tzinfo=UTC))
    monkeypatch.setattr(source, 'pending', lambda **_: (first, latest))

    pending = materializer.pending_sources(
        now_utc=datetime(2026, 8, 18, 10, 20, tzinfo=UTC),
        cursor_timestamp_utc=None,
        cursor_blob_name=None,
        cursor_blob_etag=None,
        cursor_blob_last_modified_utc=None,
    )

    assert pending == (latest,)


def test_latest_materializer_recovers_current_source_when_latest_target_is_missing(
    tmp_path, monkeypatch
) -> None:
    definition = build_test_catalog()[1]
    source = _source(definition)
    runtime = DatasetRuntime(store=ParquetDatasetStore(root=tmp_path))
    materializer = RemanentesLatestMaterializer(
        source=source,
        runtime=runtime,
        definition=definition,
    )
    current = _source_blob('current.parquet', datetime(2026, 8, 18, 10, 15, tzinfo=UTC))
    calls = iter(((), (current,)))
    monkeypatch.setattr(source, 'pending', lambda **_: next(calls))

    pending = materializer.pending_sources(
        now_utc=datetime(2026, 8, 18, 10, 20, tzinfo=UTC),
        cursor_timestamp_utc=current.source_file_timestamp_utc,
        cursor_blob_name=current.name,
        cursor_blob_etag=current.etag,
        cursor_blob_last_modified_utc=current.last_modified_utc,
    )

    assert pending == (current,)


def test_latest_materializer_does_not_recover_when_latest_target_exists(
    tmp_path, monkeypatch
) -> None:
    definition = build_test_catalog()[1]
    source = _source(definition)
    runtime = DatasetRuntime(store=ParquetDatasetStore(root=tmp_path))
    materializer = RemanentesLatestMaterializer(
        source=source,
        runtime=runtime,
        definition=definition,
    )
    current = _source_blob('current.parquet', datetime(2026, 8, 18, 10, 15, tzinfo=UTC))
    table = pa.table(
        {
            'Fase': ['CURRENT'],
            'Banco': [300.0],
            'Tipo de material': ['Mineral'],
            'Observación': ['Actual'],
            'Ton (kt)': [30.0],
        }
    )
    monkeypatch.setattr(source, 'download_table', lambda *, blob_name: table)
    materializer.materialize(source_blob=current)

    calls = 0

    def no_pending(**_):
        nonlocal calls
        calls += 1
        return ()

    monkeypatch.setattr(source, 'pending', no_pending)
    pending = materializer.pending_sources(
        now_utc=datetime(2026, 8, 18, 10, 20, tzinfo=UTC),
        cursor_timestamp_utc=current.source_file_timestamp_utc,
        cursor_blob_name=current.name,
        cursor_blob_etag=current.etag,
        cursor_blob_last_modified_utc=current.last_modified_utc,
    )

    assert pending == ()
    assert calls == 1


def test_latest_materializer_replaces_previous_snapshot(tmp_path, monkeypatch) -> None:
    definition = build_test_catalog()[1]
    source = _source(definition)
    runtime = DatasetRuntime(store=ParquetDatasetStore(root=tmp_path))
    materializer = RemanentesLatestMaterializer(
        source=source,
        runtime=runtime,
        definition=definition,
    )
    first = _source_blob('first.parquet', datetime(2026, 8, 18, 10, 0, tzinfo=UTC))
    latest = _source_blob('latest.parquet', datetime(2026, 8, 18, 10, 15, tzinfo=UTC))
    tables = {
        first.name: pa.table(
            {
                'Fase': ['OLD-A', 'OLD-B'],
                'Banco': [100.0, 200.0],
                'Tipo de material': ['Mineral', 'Estéril'],
                'Observación': ['Anterior', 'Anterior'],
                'Ton (kt)': [10.0, 20.0],
            }
        ),
        latest.name: pa.table(
            {
                'Fase': ['CURRENT'],
                'Banco': [300.0],
                'Tipo de material': ['Mineral'],
                'Observación': ['Actual'],
                'Ton (kt)': [30.0],
            }
        ),
    }
    monkeypatch.setattr(source, 'download_table', lambda *, blob_name: tables[blob_name])

    first_result = materializer.materialize(source_blob=first)
    latest_result = materializer.materialize(source_blob=latest)

    target = materializer._dataset.resolve_target(materialization='latest')
    dataframe = runtime.read_dataframe(definition=materializer._dataset, target=target).dataframe

    assert first_result.publication.status is PublicationStatus.COMMITTED
    assert latest_result.publication.status is PublicationStatus.COMMITTED
    assert len(dataframe) == 1
    assert dataframe['fase'].tolist() == ['CURRENT']
    assert dataframe['ton_kt'].tolist() == [30.0]
    assert dataframe['timestamp'].iloc[0].to_pydatetime() == latest.source_file_timestamp_utc
