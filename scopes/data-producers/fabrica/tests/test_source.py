import re
from datetime import UTC, datetime

from atlanticus.connectivity.storage import (
    StorageBlobProperties,
    StorageClient,
    StorageSasCredential,
    StorageSettings,
)
from atlanticus.data_producers.fabrica import (
    FabricaKpiStreamDefinition,
    FabricaStorageSource,
)


def _definition() -> FabricaKpiStreamDefinition:
    return FabricaKpiStreamDefinition(
        source_prefix='MLP/kpi',
        source_filename_pattern=re.compile(r'kpi_(?P<file_timestamp>\d{14})\.parquet$'),
        output_route_segment='kpis',
        datasets=(),
    )


def test_latest_uses_explicit_container_and_selects_latest_filename_timestamp(monkeypatch) -> None:
    client = StorageClient(
        settings=StorageSettings(
            credential=StorageSasCredential(
                account_url='https://example.blob.core.windows.net',
                sas_token='sv=test',
            )
        )
    )
    calls = []

    def list_blobs(*, container_name: str, prefix: str):
        calls.append((container_name, prefix))
        return (
            StorageBlobProperties(name='x/kpi_20260818100000.parquet', size=10, etag='a'),
            StorageBlobProperties(name='x/kpi_20260818103000.parquet', size=11, etag='b'),
            StorageBlobProperties(name='x/not-a-kpi.parquet', size=12, etag='c'),
        )

    monkeypatch.setattr(client, 'list_blobs', list_blobs)
    source = FabricaStorageSource(client=client, container_name='kpis', definition=_definition())

    latest = source.latest(prefix='MLP/kpi/year=2026/month=08/day=18/')

    assert calls == [('kpis', 'MLP/kpi/year=2026/month=08/day=18/')]
    assert latest is not None
    assert latest.name == 'x/kpi_20260818103000.parquet'
    assert latest.source_file_timestamp_utc == datetime(2026, 8, 18, 10, 30, tzinfo=UTC)
