from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ada.kpis.persistence import KpiCommitStore, KpiEvaluationRepository, KpiPersistencePaths
from ada.processes.kpis_historian.history import KpiHistoryWriter
from ada.processes.kpis_historian.job import KpiHistorianJob
from ada.processes.kpis_historian.settings import KpiHistorianSettings
from ada.processes.kpis_historian.state import KpiHistorianCommitStore
from atlanticus.configuration import ResolvedConfiguration
from atlanticus.datasets.parquet import ParquetDatasetStore
from atlanticus.datasets.runtime import DatasetRuntime
from atlanticus.json import JsonDocumentStore
from atlanticus.runtime import (
    JobDefinition,
    RuntimeConfiguration,
    RuntimeExecutionResult,
    execute_job,
)
from atlanticus.state import AtomicStateStore


@dataclass(slots=True)
class KpiHistorianComposition:
    configuration: ResolvedConfiguration
    runtime_configuration: RuntimeConfiguration
    settings: KpiHistorianSettings
    job: KpiHistorianJob
    job_definition: JobDefinition

    def execute(self, *, argv: Sequence[str] | None = None) -> RuntimeExecutionResult:
        return execute_job(
            definition=self.job_definition,
            iteration=self.job.run_iteration,
            argv=argv,
            environ=self.configuration.values,
        )


def build_composition(*, configuration: ResolvedConfiguration) -> KpiHistorianComposition:
    if not isinstance(configuration, ResolvedConfiguration):
        raise TypeError('configuration must be a ResolvedConfiguration')
    settings = KpiHistorianSettings.from_configuration(configuration)
    runtime_configuration = RuntimeConfiguration.from_sources(environ=configuration.values)
    state_store = AtomicStateStore(
        volume_path=runtime_configuration.volume_path,
        application=runtime_configuration.application,
    )
    paths = KpiPersistencePaths(runtime_configuration.application_root)
    evaluations = KpiEvaluationRepository(store=JsonDocumentStore(), paths=paths)
    history_runtime = DatasetRuntime(
        store=ParquetDatasetStore(root=runtime_configuration.application_root / 'datasets')
    )
    job = KpiHistorianJob(
        evaluations=evaluations,
        kpi_state=KpiCommitStore(store=state_store),
        historian_state=KpiHistorianCommitStore(store=state_store),
        history=KpiHistoryWriter(runtime=history_runtime),
    )
    job_definition = _job_definition(poll_interval_seconds=settings.poll_interval_seconds)
    return KpiHistorianComposition(
        configuration=configuration,
        runtime_configuration=runtime_configuration,
        settings=settings,
        job=job,
        job_definition=job_definition,
    )


def _job_definition(*, poll_interval_seconds: int) -> JobDefinition:
    return JobDefinition(
        module_name='ada.processes.kpis_historian',
        service_name='kpis-historian',
        job_key='kpis-historian',
        sleep_seconds=poll_interval_seconds,
        iteration_timeout_seconds=240,
        execution_timeout_seconds=600,
        shutdown_grace_seconds=10,
        lease_timeout_seconds=30,
        lease_renew_seconds=10,
        lease_wait_seconds=None,
        lease_poll_seconds=1,
        resource_sample_seconds=5,
    )
