from __future__ import annotations

from dataclasses import dataclass

from atlanticus.data_producers.pi.acquisition import PiStreamSetAcquirer
from atlanticus.data_producers.pi.errors import PiDataProducerCatalogError
from atlanticus.data_producers.pi.job import PiDataProducerJob
from atlanticus.data_producers.pi.materialization import PiDataProducerMaterializer
from atlanticus.data_producers.pi.planning import PiSlotPlanner
from atlanticus.data_producers.pi.preparation import PiExecutionPlanPreparer
from atlanticus.data_producers.pi.watermarks import (
    PiProducerState,
    PiSourceState,
    PiWatermarkCoordinator,
)
from atlanticus.data_producers.pi.web_ids import WebIdRegistry
from atlanticus.datasets.parquet import ParquetDatasetStore
from atlanticus.datasets.runtime import DatasetRuntime
from atlanticus.integrations.pi.contracts import PiCatalog, PiWebApiSource
from atlanticus.runtime import RuntimeConfiguration
from atlanticus.state import AtomicStateStore


@dataclass(slots=True)
class PiDataProducerComponents:
    registry: WebIdRegistry
    planner: PiSlotPlanner
    dataset_runtime: DatasetRuntime
    acquirer: PiStreamSetAcquirer
    materializer: PiDataProducerMaterializer
    producer_state: PiProducerState
    source_state: PiSourceState
    watermarks: PiWatermarkCoordinator
    preparer: PiExecutionPlanPreparer
    job: PiDataProducerJob


def build_pi_data_producer(
    *,
    runtime_configuration: RuntimeConfiguration,
    catalog: PiCatalog,
    client,
    producer_key: str = 'pi-web-api',
    dataset_namespace: tuple[str, ...] = ('pi', 'web-api'),
    max_recovery_lookback_seconds: int = 3600,
    max_recovery_window_seconds: int = 3600,
    max_data_points: int = 150_000,
    interpolated_max_parallel_requests: int = 3,
) -> PiDataProducerComponents:
    if not isinstance(runtime_configuration, RuntimeConfiguration):
        raise TypeError('runtime_configuration must be a RuntimeConfiguration')
    if not isinstance(catalog, PiCatalog):
        raise TypeError('catalog must be a PiCatalog')
    if not isinstance(catalog.source, PiWebApiSource):
        raise PiDataProducerCatalogError('catalog source must be PiWebApiSource')
    interpolation_seconds = catalog.source.interpolation_seconds
    if interpolation_seconds is None:
        raise PiDataProducerCatalogError('PI Web API catalog must define interpolation_seconds')
    if not hasattr(client, 'points') or not hasattr(client, 'streamsets'):
        raise TypeError('client must expose points and streamsets')

    registry = WebIdRegistry.from_runtime_configuration(
        runtime_configuration,
        producer_key=producer_key,
    )
    state_store = AtomicStateStore(
        volume_path=runtime_configuration.volume_path,
        application=runtime_configuration.application,
    )
    producer_state = PiProducerState(store=state_store, producer_key=producer_key)
    source_state = PiSourceState(store=state_store, producer_key=producer_key)
    planner = PiSlotPlanner(
        interpolation_seconds=interpolation_seconds,
        max_recovery_lookback_seconds=max_recovery_lookback_seconds,
        max_recovery_window_seconds=max_recovery_window_seconds,
    )
    dataset_runtime = DatasetRuntime(
        store=ParquetDatasetStore(root=runtime_configuration.application_root / 'datasets')
    )
    acquirer = PiStreamSetAcquirer(
        client=client,
        max_data_points=max_data_points,
        interpolated_max_parallel_requests=interpolated_max_parallel_requests,
    )
    materializer = PiDataProducerMaterializer(
        runtime=dataset_runtime,
        catalog=catalog,
        dataset_namespace=dataset_namespace,
    )
    watermarks = PiWatermarkCoordinator(producer=producer_state, source=source_state)
    preparer = PiExecutionPlanPreparer(client=client, registry=registry)
    job = PiDataProducerJob(
        preparer=preparer,
        catalog=catalog,
        planner=planner,
        producer_state=producer_state,
        acquirer=acquirer,
        materializer=materializer,
        watermarks=watermarks,
    )
    return PiDataProducerComponents(
        registry=registry,
        planner=planner,
        dataset_runtime=dataset_runtime,
        acquirer=acquirer,
        materializer=materializer,
        producer_state=producer_state,
        source_state=source_state,
        watermarks=watermarks,
        preparer=preparer,
        job=job,
    )
