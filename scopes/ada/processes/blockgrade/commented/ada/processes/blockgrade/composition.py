# Ensambla explícitamente SQL, state, datasets, planner, processor y job runtime.
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ada.processes.blockgrade.catalog import build_catalog
from ada.processes.blockgrade.extraction import BlockgradeSqlReader
from ada.processes.blockgrade.job import BlockgradeJob
from ada.processes.blockgrade.materialization import BlockgradeMaterializer
from ada.processes.blockgrade.models import BlockgradeSourceDefinition
from ada.processes.blockgrade.planning import BlockgradePlanner
from ada.processes.blockgrade.processor import BlockgradeSourceProcessor
from ada.processes.blockgrade.producer_state import BlockgradeProducerState
from ada.processes.blockgrade.settings import BlockgradeSettings
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

BLOCKGRADE_JOB_DEFINITION = JobDefinition(
    module_name='ada.processes.blockgrade',
    service_name='blockgrade',
    job_key='blockgrade-materialization',
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
class BlockgradeComposition:
    configuration: ResolvedConfiguration
    runtime_configuration: RuntimeConfiguration
    settings: BlockgradeSettings
    catalog: tuple[BlockgradeSourceDefinition, ...]
    reader: BlockgradeSqlReader
    producer_state: BlockgradeProducerState
    planner: BlockgradePlanner
    materializer: BlockgradeMaterializer
    processor: BlockgradeSourceProcessor
    job: BlockgradeJob

    def execute(self, *, argv: Sequence[str] | None = None) -> RuntimeExecutionResult:
        return execute_job(
            definition=BLOCKGRADE_JOB_DEFINITION,
            iteration=self.job.run_iteration,
            argv=argv,
            environ=self.configuration.values,
        )


def build_composition(
    *,
    configuration: ResolvedConfiguration,
    catalog: tuple[BlockgradeSourceDefinition, ...] | None = None,
) -> BlockgradeComposition:
    if not isinstance(configuration, ResolvedConfiguration):
        raise TypeError('configuration must be a ResolvedConfiguration')
    resolved_catalog = build_catalog() if catalog is None else tuple(catalog)
    if not resolved_catalog or not all(
        isinstance(definition, BlockgradeSourceDefinition) for definition in resolved_catalog
    ):
        raise TypeError('catalog must contain BlockgradeSourceDefinition values')
    settings = BlockgradeSettings.from_configuration(configuration)
    runtime_configuration = RuntimeConfiguration.from_sources(environ=configuration.values)
    reader = BlockgradeSqlReader(
        sql=SqlClient(settings=settings.sql),
        retry_policy=settings.retry_policy,
        max_rows=settings.sql.max_query_rows,
    )
    producer_state = BlockgradeProducerState(
        store=AtomicStateStore(
            volume_path=runtime_configuration.volume_path,
            application=runtime_configuration.application,
        )
    )
    planner = BlockgradePlanner(reader=reader, producer_state=producer_state)
    materializer = BlockgradeMaterializer(
        runtime=DatasetRuntime(
            store=ParquetDatasetStore(root=runtime_configuration.application_root / 'datasets')
        ),
        definitions=resolved_catalog,
    )
    processor = BlockgradeSourceProcessor(reader=reader, materializer=materializer)
    job = BlockgradeJob(
        definitions=resolved_catalog,
        planner=planner,
        producer_state=producer_state,
        executor=processor,
    )
    return BlockgradeComposition(
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
