from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ada.processes.pi_web_api.acquisition import PiStreamSetAcquirer
from ada.processes.pi_web_api.catalog import build_catalog
from ada.processes.pi_web_api.catalog.definitions import SOURCE as CATALOG_SOURCE
from ada.processes.pi_web_api.errors import PiWebApiCatalogError
from ada.processes.pi_web_api.job import PiWebApiJob
from ada.processes.pi_web_api.materialization import PiWebApiMaterializer
from ada.processes.pi_web_api.planning import PiSlotPlanner
from ada.processes.pi_web_api.preparation import PiExecutionPlanPreparer
from ada.processes.pi_web_api.settings import PiWebApiProcessSettings
from ada.processes.pi_web_api.stress_benchmark import (
    PiStressBenchmarkAcquirer,
    PiStressBenchmarkJob,
    PiStressBenchmarkMaterializer,
    PiStressBenchmarkPlanner,
    PiStressBenchmarkSettings,
    build_stress_physical_catalog,
)
from ada.processes.pi_web_api.stress_io_benchmark import PiStressIoBenchmarkJob
from ada.processes.pi_web_api.watermarks import (
    PiProducerState,
    PiSourceState,
    PiWatermarkCoordinator,
)
from ada.processes.pi_web_api.web_ids import WebIdRegistry
from atlanticus.configuration import ResolvedConfiguration
from atlanticus.datasets.parquet import ParquetDatasetStore
from atlanticus.datasets.runtime import DatasetRuntime
from atlanticus.integrations.pi.contracts import PiCatalog, PiWebApiSource
from atlanticus.integrations.pi.web_api import PiWebApiClient
from atlanticus.runtime import (
    JobDefinition,
    RuntimeConfiguration,
    RuntimeExecutionResult,
    execute_job,
)
from atlanticus.state import AtomicStateStore

PI_WEB_API_JOB_DEFINITION = JobDefinition(
    module_name='ada.processes.pi_web_api',
    service_name='pi-web-api',
    job_key='pi-web-api-materialization',
    sleep_seconds=1,
    iteration_timeout_seconds=240,
    execution_timeout_seconds=600,
    shutdown_grace_seconds=10,
    lease_timeout_seconds=30,
    lease_renew_seconds=10,
    lease_wait_seconds=None,
    lease_poll_seconds=1,
    resource_sample_seconds=5,
)


@dataclass(slots=True)
class PiWebApiComposition:
    configuration: ResolvedConfiguration
    runtime_configuration: RuntimeConfiguration
    settings: PiWebApiProcessSettings
    catalog: PiCatalog
    client: PiWebApiClient
    registry: WebIdRegistry
    planner: PiSlotPlanner
    dataset_runtime: DatasetRuntime
    acquirer: PiStreamSetAcquirer
    materializer: PiWebApiMaterializer
    producer_state: PiProducerState
    source_state: PiSourceState
    watermarks: PiWatermarkCoordinator
    job: PiWebApiJob

    def execute(self, *, argv: Sequence[str] | None = None) -> RuntimeExecutionResult:
        with self.client:
            return execute_job(
                definition=PI_WEB_API_JOB_DEFINITION,
                iteration=self.job.run_iteration,
                argv=argv,
                environ=self.configuration.values,
            )


def build_composition(
    *,
    configuration: ResolvedConfiguration,
    catalog: PiCatalog | None = None,
) -> PiWebApiComposition:
    if not isinstance(configuration, ResolvedConfiguration):
        raise TypeError('configuration must be a ResolvedConfiguration')
    stress = PiStressBenchmarkSettings.from_configuration(configuration)
    if stress.enabled and catalog is not None:
        raise PiWebApiCatalogError('catalog override is not allowed in stress benchmark mode')
    if stress.enabled:
        if not isinstance(CATALOG_SOURCE, PiWebApiSource):
            raise PiWebApiCatalogError(
                'productive catalog source must be PiWebApiSource for stress benchmark'
            )
        if CATALOG_SOURCE.interpolation_seconds is None:
            raise PiWebApiCatalogError(
                'productive catalog source must define interpolation for stress benchmark'
            )
        resolved_catalog = build_stress_physical_catalog(
            interpolation_seconds=CATALOG_SOURCE.interpolation_seconds,
            physical_tag_limit=stress.physical_tag_limit,
        )
    else:
        resolved_catalog = build_catalog() if catalog is None else catalog
    if not isinstance(resolved_catalog, PiCatalog):
        raise TypeError('catalog must be a PiCatalog')
    if not isinstance(resolved_catalog.source, PiWebApiSource):
        raise PiWebApiCatalogError('catalog source must be PiWebApiSource')
    interpolation_seconds = resolved_catalog.source.interpolation_seconds
    if interpolation_seconds is None:
        raise PiWebApiCatalogError('PI Web API catalog must define interpolation_seconds')

    settings = PiWebApiProcessSettings.from_configuration(configuration)
    runtime_configuration = RuntimeConfiguration.from_sources(environ=configuration.values)
    registry = WebIdRegistry.from_runtime_configuration(runtime_configuration)
    client = PiWebApiClient(settings=settings.pi_web_api)
    state_store = AtomicStateStore(
        volume_path=runtime_configuration.volume_path,
        application=runtime_configuration.application,
    )
    producer_state = PiProducerState(store=state_store)
    source_state = PiSourceState(store=state_store)
    planner: PiSlotPlanner = PiSlotPlanner(
        interpolation_seconds=interpolation_seconds,
        max_recovery_lookback_seconds=settings.max_recovery_lookback_seconds,
        max_recovery_window_seconds=settings.max_recovery_window_seconds,
    )
    dataset_runtime = DatasetRuntime(
        store=ParquetDatasetStore(root=runtime_configuration.application_root / 'datasets')
    )
    acquirer: PiStreamSetAcquirer = PiStreamSetAcquirer(
        client=client,
        max_data_points=settings.max_data_points,
        interpolated_max_parallel_requests=settings.interpolated_max_parallel_requests,
    )
    materializer: PiWebApiMaterializer = PiWebApiMaterializer(
        runtime=dataset_runtime,
        catalog=resolved_catalog,
    )
    if stress.enabled and stress.kind == 'capacity':
        assert stress.end_utc is not None
        planner = PiStressBenchmarkPlanner(
            interpolation_seconds=interpolation_seconds,
            max_recovery_lookback_seconds=settings.max_recovery_lookback_seconds,
            max_recovery_window_seconds=settings.max_recovery_window_seconds,
            benchmark_end_utc=stress.end_utc,
            lookback_hours=stress.lookback_hours,
        )
        acquirer = PiStressBenchmarkAcquirer(
            client=client,
            max_data_points=settings.max_data_points,
            interpolated_max_parallel_requests=settings.interpolated_max_parallel_requests,
        )
        materializer = PiStressBenchmarkMaterializer(
            runtime=dataset_runtime,
            physical_catalog=resolved_catalog,
            logical_tag_count=stress.logical_tag_count,
        )
    watermarks = PiWatermarkCoordinator(producer=producer_state, source=source_state)
    preparer = PiExecutionPlanPreparer(client=client, registry=registry)
    job: PiWebApiJob
    if stress.enabled and stress.kind == 'capacity':
        assert stress.end_utc is not None
        job = PiStressBenchmarkJob(
            benchmark_end_utc=stress.end_utc,
            preparer=preparer,
            catalog=resolved_catalog,
            planner=planner,
            producer_state=producer_state,
            acquirer=acquirer,
            materializer=materializer,
            watermarks=watermarks,
        )
    elif stress.enabled and stress.kind == 'io':
        assert stress.end_utc is not None
        job = PiStressIoBenchmarkJob(
            client=client,
            benchmark_end_utc=stress.end_utc,
            interpolation_seconds=interpolation_seconds,
            chunk_limit=stress.io_chunk_limit,
            max_workers=stress.io_max_workers,
            preparer=preparer,
            catalog=resolved_catalog,
            planner=planner,
            producer_state=producer_state,
            acquirer=acquirer,
            materializer=materializer,
            watermarks=watermarks,
        )
    else:
        job = PiWebApiJob(
            preparer=preparer,
            catalog=resolved_catalog,
            planner=planner,
            producer_state=producer_state,
            acquirer=acquirer,
            materializer=materializer,
            watermarks=watermarks,
        )
    return PiWebApiComposition(
        configuration=configuration,
        runtime_configuration=runtime_configuration,
        settings=settings,
        catalog=resolved_catalog,
        client=client,
        registry=registry,
        planner=planner,
        dataset_runtime=dataset_runtime,
        acquirer=acquirer,
        materializer=materializer,
        producer_state=producer_state,
        source_state=source_state,
        watermarks=watermarks,
        job=job,
    )
