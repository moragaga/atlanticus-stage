from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ada.processes.dispatch.catalog import build_catalog
from ada.processes.dispatch.extraction import DispatchSqlReader
from ada.processes.dispatch.job import DispatchJob
from ada.processes.dispatch.materialization import DispatchMaterializer
from ada.processes.dispatch.models import DispatchSourceDefinition
from ada.processes.dispatch.planning import DispatchPlanner
from ada.processes.dispatch.processor import DispatchSourceProcessor
from ada.processes.dispatch.producer_state import DispatchProducerState
from ada.processes.dispatch.settings import DispatchSettings
from atlanticus.configuration import ResolvedConfiguration
from atlanticus.connectivity.sql import SqlClient
from atlanticus.datasets.parquet import ParquetDatasetStore
from atlanticus.datasets.runtime import DatasetRuntime
from atlanticus.runtime import (
    JobDefinition,
    RuntimeConfiguration,
    RuntimeExecutionResult,
    execute_job,
)
from atlanticus.state.store import AtomicStateStore

DISPATCH_JOB_DEFINITION = JobDefinition(
    module_name='ada.processes.dispatch',
    service_name='dispatch',
    job_key='dispatch-materialization',
    sleep_seconds=0,
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
class DispatchComposition:
    configuration: ResolvedConfiguration
    runtime_configuration: RuntimeConfiguration
    settings: DispatchSettings
    catalog: tuple[DispatchSourceDefinition, ...]
    reader: DispatchSqlReader
    producer_state: DispatchProducerState
    planner: DispatchPlanner
    materializer: DispatchMaterializer
    processor: DispatchSourceProcessor
    job: DispatchJob

    def execute(self, *, argv: Sequence[str] | None = None) -> RuntimeExecutionResult:
        return execute_job(
            definition=DISPATCH_JOB_DEFINITION,
            iteration=self.job.run_iteration,
            argv=argv,
            environ=self.configuration.values,
        )


def build_composition(
    *,
    configuration: ResolvedConfiguration,
    catalog: tuple[DispatchSourceDefinition, ...] | None = None,
) -> DispatchComposition:
    if not isinstance(configuration, ResolvedConfiguration):
        raise TypeError('configuration must be a ResolvedConfiguration')
    resolved_catalog = build_catalog() if catalog is None else tuple(catalog)
    if not resolved_catalog or not all(
        isinstance(definition, DispatchSourceDefinition) for definition in resolved_catalog
    ):
        raise TypeError('catalog must contain DispatchSourceDefinition values')
    settings = DispatchSettings.from_configuration(configuration)
    runtime_configuration = RuntimeConfiguration.from_sources(environ=configuration.values)
    reader = DispatchSqlReader(
        sql=SqlClient(settings=settings.sql),
        retry_policy=settings.retry_policy,
        max_rows=settings.sql.max_query_rows,
    )
    producer_state = DispatchProducerState(
        store=AtomicStateStore(
            volume_path=runtime_configuration.volume_path,
            application=runtime_configuration.application,
        )
    )
    planner = DispatchPlanner(reader=reader, producer_state=producer_state)
    materializer = DispatchMaterializer(
        runtime=DatasetRuntime(
            store=ParquetDatasetStore(root=runtime_configuration.application_root / 'datasets')
        ),
        definitions=resolved_catalog,
    )
    processor = DispatchSourceProcessor(reader=reader, materializer=materializer)
    job = DispatchJob(
        definitions=resolved_catalog,
        planner=planner,
        producer_state=producer_state,
        executor=processor,
    )
    return DispatchComposition(
        configuration=configuration,
        runtime_configuration=runtime_configuration,
        settings=settings,
        catalog=resolved_catalog,
        reader=reader,
        producer_state=producer_state,
        planner=planner,
        materializer=materializer,
        processor=processor,
        job=job,
    )
