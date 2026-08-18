# La composición crea un único StorageClient compartido por los tres streams.
# La conexión y el container llegan ya resueltos desde el proceso consumidor; el producer no conoce variables de entorno.

from __future__ import annotations

from dataclasses import dataclass

from atlanticus.connectivity.storage import StorageClient, StorageSettings
from atlanticus.data_producers.remanentes.job import RemanentesJob
from atlanticus.data_producers.remanentes.materialization import RemanentesMaterializer
from atlanticus.data_producers.remanentes.models import (
    RemanentesRowsStreamDefinition,
    RemanentesStocksStreamDefinition,
    RemanentesStreamDefinition,
)
from atlanticus.data_producers.remanentes.producer_state import RemanentesProducerState
from atlanticus.data_producers.remanentes.source import RemanentesStorageSource
from atlanticus.datasets.parquet import ParquetDatasetStore
from atlanticus.datasets.runtime import DatasetRuntime
from atlanticus.runtime import RuntimeConfiguration
from atlanticus.state import AtomicStateStore


@dataclass(frozen=True, slots=True)
class RemanentesStorageConnection:
    settings: StorageSettings
    container_name: str

    def __post_init__(self) -> None:
        if not isinstance(self.settings, StorageSettings):
            raise TypeError('settings must be a StorageSettings')
        normalized = str(self.container_name).strip()
        if not normalized:
            raise ValueError('container_name is required')
        object.__setattr__(self, 'container_name', normalized)


@dataclass(slots=True)
class RemanentesDataProducerComponents:
    dataset_runtime: DatasetRuntime
    storage: StorageClient
    materializers: tuple[RemanentesMaterializer, ...]
    producer_state: RemanentesProducerState
    job: RemanentesJob


def build_remanentes_data_producer(
    *,
    runtime_configuration: RuntimeConfiguration,
    definitions: tuple[RemanentesStreamDefinition, ...],
    connection: RemanentesStorageConnection,
    idle_seconds: int,
    producer_key: str = 'remanentes',
    dataset_namespace: tuple[str, ...] = ('remanentes',),
) -> RemanentesDataProducerComponents:
    if not isinstance(runtime_configuration, RuntimeConfiguration):
        raise TypeError('runtime_configuration must be a RuntimeConfiguration')
    resolved_definitions = tuple(definitions)
    if not resolved_definitions or not all(
        isinstance(definition, RemanentesStocksStreamDefinition | RemanentesRowsStreamDefinition)
        for definition in resolved_definitions
    ):
        raise TypeError('definitions must contain Remanentes stream definitions')
    stream_keys = tuple(definition.stream_key for definition in resolved_definitions)
    if len(set(stream_keys)) != len(stream_keys):
        raise ValueError('Remanentes stream keys must be unique')
    if not isinstance(connection, RemanentesStorageConnection):
        raise TypeError('connection must be a RemanentesStorageConnection')
    if not isinstance(idle_seconds, int) or isinstance(idle_seconds, bool) or idle_seconds <= 0:
        raise ValueError('idle_seconds must be an integer greater than zero')
    storage = StorageClient(settings=connection.settings)
    dataset_runtime = DatasetRuntime(
        store=ParquetDatasetStore(root=runtime_configuration.application_root / 'datasets')
    )
    materializers = tuple(
        RemanentesMaterializer(
            source=RemanentesStorageSource(
                client=storage,
                container_name=connection.container_name,
                definition=definition,
            ),
            runtime=dataset_runtime,
            definition=definition,
            dataset_namespace=dataset_namespace,
        )
        for definition in resolved_definitions
    )
    producer_state = RemanentesProducerState(
        store=AtomicStateStore(
            volume_path=runtime_configuration.volume_path,
            application=runtime_configuration.application,
        ),
        producer_key=producer_key,
    )
    return RemanentesDataProducerComponents(
        dataset_runtime=dataset_runtime,
        storage=storage,
        materializers=materializers,
        producer_state=producer_state,
        job=RemanentesJob(
            materializers=materializers,
            producer_state=producer_state,
            idle_seconds=idle_seconds,
        ),
    )
