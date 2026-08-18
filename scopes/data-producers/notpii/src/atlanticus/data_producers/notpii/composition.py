from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from atlanticus.connectivity.service_bus import ServiceBusSettings, ServiceBusTopicReceiver
from atlanticus.connectivity.storage import StorageSasReader
from atlanticus.data_producers.notpii.connector import NotPiiConnector
from atlanticus.data_producers.notpii.errors import NotPiiCatalogError
from atlanticus.data_producers.notpii.job import NotPiiJob
from atlanticus.data_producers.notpii.materialization import NotPiiMaterializer
from atlanticus.data_producers.notpii.processor import NotPiiProcessor
from atlanticus.data_producers.notpii.producer_state import NotPiiProducerState
from atlanticus.datasets.parquet import ParquetDatasetStore
from atlanticus.datasets.runtime import DatasetRuntime
from atlanticus.integrations.pi.contracts import NotPiiSource, PiCatalog, PiExtractionMode
from atlanticus.runtime import RuntimeConfiguration
from atlanticus.state import AtomicStateStore

_MODE_ORDER = (PiExtractionMode.INTERPOLATED, PiExtractionMode.RECORDED)


@dataclass(slots=True)
class NotPiiDataProducerComponents:
    dataset_runtime: DatasetRuntime
    connector: NotPiiConnector
    receivers: Mapping[PiExtractionMode, ServiceBusTopicReceiver]
    processors: Mapping[PiExtractionMode, NotPiiProcessor]
    producer_state: NotPiiProducerState
    job: NotPiiJob


def build_notpii_data_producer(
    *,
    runtime_configuration: RuntimeConfiguration,
    catalog: PiCatalog,
    service_buses: Mapping[PiExtractionMode, ServiceBusSettings],
    raw_batch_size: int,
    max_message_count: int,
    producer_key: str = 'notpii',
    dataset_namespace: tuple[str, ...] = ('pi', 'not_pii'),
) -> NotPiiDataProducerComponents:
    if not isinstance(runtime_configuration, RuntimeConfiguration):
        raise TypeError('runtime_configuration must be a RuntimeConfiguration')
    if not isinstance(catalog, PiCatalog):
        raise TypeError('catalog must be a PiCatalog')
    if not isinstance(catalog.source, NotPiiSource):
        raise NotPiiCatalogError('catalog source must be NotPiiSource')
    active_modes = _active_modes(catalog)
    if not active_modes:
        raise NotPiiCatalogError('NOT PII catalog must contain at least one active extraction mode')
    if set(service_buses) != set(active_modes):
        raise ValueError('service_buses must match the active catalog extraction modes')

    dataset_runtime = DatasetRuntime(
        store=ParquetDatasetStore(root=runtime_configuration.application_root / 'datasets')
    )
    connector = NotPiiConnector(
        storage_reader=StorageSasReader(),
        raw_batch_size=raw_batch_size,
    )
    processors = {
        mode: NotPiiProcessor(
            connector=connector,
            materializer=NotPiiMaterializer(
                runtime=dataset_runtime,
                catalog=catalog,
                extraction_mode=mode,
                dataset_namespace=dataset_namespace,
            ),
            catalog=catalog,
            extraction_mode=mode,
        )
        for mode in active_modes
    }
    receivers = {
        mode: ServiceBusTopicReceiver(settings=service_buses[mode]) for mode in active_modes
    }
    producer_state = NotPiiProducerState(
        store=AtomicStateStore(
            volume_path=runtime_configuration.volume_path,
            application=runtime_configuration.application,
        ),
        producer_key=producer_key,
    )
    job = NotPiiJob(
        receivers=receivers,
        processors=processors,
        producer_state=producer_state,
        max_message_count=max_message_count,
    )
    return NotPiiDataProducerComponents(
        dataset_runtime=dataset_runtime,
        connector=connector,
        receivers=receivers,
        processors=processors,
        producer_state=producer_state,
        job=job,
    )


def _active_modes(catalog: PiCatalog) -> tuple[PiExtractionMode, ...]:
    configured = {item.extraction_mode for item in catalog.definitions if item.is_active}
    return tuple(mode for mode in _MODE_ORDER if mode in configured)
