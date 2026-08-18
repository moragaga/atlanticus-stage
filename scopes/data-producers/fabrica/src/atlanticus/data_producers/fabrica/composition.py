from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from atlanticus.connectivity.storage import StorageClient, StorageSettings
from atlanticus.data_producers.fabrica.job import FabricaJob
from atlanticus.data_producers.fabrica.materialization import FabricaMaterializer
from atlanticus.data_producers.fabrica.models import (
    FabricaKpiStreamDefinition,
    FabricaPlanStreamDefinition,
    FabricaStreamDefinition,
)
from atlanticus.data_producers.fabrica.producer_state import FabricaProducerState
from atlanticus.data_producers.fabrica.source import FabricaStorageSource
from atlanticus.datasets.parquet import ParquetDatasetStore
from atlanticus.datasets.runtime import DatasetRuntime
from atlanticus.runtime import RuntimeConfiguration
from atlanticus.state import AtomicStateStore


@dataclass(frozen=True, slots=True)
class FabricaStorageConnection:
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
class FabricaDataProducerComponents:
    dataset_runtime: DatasetRuntime
    storages: Mapping[str, StorageClient]
    materializers: tuple[FabricaMaterializer, ...]
    producer_state: FabricaProducerState
    job: FabricaJob


def build_fabrica_data_producer(
    *,
    runtime_configuration: RuntimeConfiguration,
    definitions: tuple[FabricaStreamDefinition, ...],
    connections: Mapping[str, FabricaStorageConnection],
    idle_seconds: int,
    producer_key: str = 'fabrica',
    dataset_namespace: tuple[str, ...] = ('fabrica',),
) -> FabricaDataProducerComponents:
    if not isinstance(runtime_configuration, RuntimeConfiguration):
        raise TypeError('runtime_configuration must be a RuntimeConfiguration')
    resolved_definitions = tuple(definitions)
    if not resolved_definitions or not all(
        isinstance(definition, FabricaPlanStreamDefinition | FabricaKpiStreamDefinition)
        for definition in resolved_definitions
    ):
        raise TypeError('definitions must contain Fabrica stream definitions')
    required_connections = {definition.stream_key for definition in resolved_definitions}
    if set(connections) != required_connections:
        raise ValueError('connections must match the configured Fabrica streams')
    if not isinstance(idle_seconds, int) or isinstance(idle_seconds, bool) or idle_seconds <= 0:
        raise ValueError('idle_seconds must be an integer greater than zero')

    enabled_definitions = tuple(
        definition for definition in resolved_definitions if definition.metrics
    )
    storages = {
        definition.stream_key: StorageClient(settings=connections[definition.stream_key].settings)
        for definition in enabled_definitions
    }
    dataset_runtime = DatasetRuntime(
        store=ParquetDatasetStore(root=runtime_configuration.application_root / 'datasets')
    )
    materializers = tuple(
        FabricaMaterializer(
            source=FabricaStorageSource(
                client=storages[definition.stream_key],
                container_name=connections[definition.stream_key].container_name,
                definition=definition,
            ),
            runtime=dataset_runtime,
            definition=definition,
            dataset_namespace=dataset_namespace,
        )
        for definition in enabled_definitions
    )
    producer_state = FabricaProducerState(
        store=AtomicStateStore(
            volume_path=runtime_configuration.volume_path,
            application=runtime_configuration.application,
        ),
        producer_key=producer_key,
    )
    return FabricaDataProducerComponents(
        dataset_runtime=dataset_runtime,
        storages=MappingProxyType(storages),
        materializers=materializers,
        producer_state=producer_state,
        job=FabricaJob(
            materializers=materializers,
            producer_state=producer_state,
            idle_seconds=idle_seconds,
        ),
    )
