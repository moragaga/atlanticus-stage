# Valida que cada vista solicitada tenga binding físico antes de iniciar el proceso.
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ada.kpis.core import KpiCatalog
from ada.kpis.evaluation import KpiEvaluator
from ada.kpis.persistence import (
    KpiCommitStore,
    KpiEvaluationCommitter,
    KpiEvaluationRepository,
    KpiLatestRepository,
    KpiPersistencePaths,
)
from ada.kpis.sources import KpiSourceLoader, KpiSourceRegistry, build_current_source_registry
from ada.processes.kpis.catalog import build_catalog
from ada.processes.kpis.clock import StatePiClock
from ada.processes.kpis.job import KpiProcessJob
from ada.processes.kpis.reader import DatasetFrameRuntime, DatasetRuntimeSourceReader
from ada.processes.kpis.settings import KpiProcessSettings, catalog_sources
from atlanticus.configuration import ResolvedConfiguration
from atlanticus.datasets.models import DatasetKey
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
class KpiProcessComposition:
    configuration: ResolvedConfiguration
    runtime_configuration: RuntimeConfiguration
    settings: KpiProcessSettings
    catalog: KpiCatalog
    job: KpiProcessJob
    job_definition: JobDefinition

    def execute(self, *, argv: Sequence[str] | None = None) -> RuntimeExecutionResult:
        return execute_job(
            definition=self.job_definition,
            iteration=self.job.run_iteration,
            argv=argv,
            environ=self.configuration.values,
        )


def build_composition(
    *,
    configuration: ResolvedConfiguration,
    catalog: KpiCatalog | None = None,
) -> KpiProcessComposition:
    if not isinstance(configuration, ResolvedConfiguration):
        raise TypeError('configuration must be a ResolvedConfiguration')
    resolved_catalog = build_catalog() if catalog is None else catalog
    if not isinstance(resolved_catalog, KpiCatalog):
        raise TypeError('catalog must be KpiCatalog')

    settings = KpiProcessSettings.from_configuration(configuration)
    settings.source_applications.validate_catalog(resolved_catalog)
    runtime_configuration = RuntimeConfiguration.from_sources(environ=configuration.values)

    kpi_state_store = AtomicStateStore(
        volume_path=runtime_configuration.volume_path,
        application=runtime_configuration.application,
    )
    pi_state_store = AtomicStateStore(
        volume_path=runtime_configuration.volume_path,
        application=settings.source_applications.pi,
    )
    source_registry = build_current_source_registry(pi_source=settings.pi_source)
    source_runtimes = _build_source_runtimes(
        runtime_configuration=runtime_configuration,
        settings=settings,
        catalog=resolved_catalog,
        registry=source_registry,
    )
    source_loader = KpiSourceLoader(
        reader=DatasetRuntimeSourceReader(runtimes=source_runtimes),
        registry=source_registry,
    )
    evaluator = KpiEvaluator(source_loader=source_loader)

    persistence_paths = KpiPersistencePaths(runtime_configuration.application_root)
    json_store = JsonDocumentStore()
    evaluations = KpiEvaluationRepository(store=json_store, paths=persistence_paths)
    latest = KpiLatestRepository(store=json_store, paths=persistence_paths)
    commit_store = KpiCommitStore(store=kpi_state_store)
    committer = KpiEvaluationCommitter(
        evaluations=evaluations,
        latest=latest,
        state=commit_store,
    )
    job = KpiProcessJob(
        catalog=resolved_catalog,
        clock=StatePiClock(store=pi_state_store, provider=settings.pi_source),
        evaluator=evaluator,
        evaluations=evaluations,
        committer=committer,
        state=commit_store,
    )
    job_definition = _job_definition(poll_interval_seconds=settings.poll_interval_seconds)
    return KpiProcessComposition(
        configuration=configuration,
        runtime_configuration=runtime_configuration,
        settings=settings,
        catalog=resolved_catalog,
        job=job,
        job_definition=job_definition,
    )


def _build_source_runtimes(
    *,
    runtime_configuration: RuntimeConfiguration,
    settings: KpiProcessSettings,
    catalog: KpiCatalog,
    registry: KpiSourceRegistry,
) -> dict[DatasetKey, DatasetFrameRuntime]:
    routes: dict[DatasetKey, DatasetFrameRuntime] = {}
    runtimes_by_application: dict[str, DatasetRuntime] = {}
    for spec in catalog.specs:
        for requirement in spec.requirements:
            registry.get_view(requirement.view)
    for source in catalog_sources(catalog):
        application = settings.source_applications.application_for(source)
        runtime = runtimes_by_application.get(application)
        if runtime is None:
            source_configuration = RuntimeConfiguration(
                environment=runtime_configuration.environment,
                application=application,
                volume_path=runtime_configuration.volume_path,
            )
            runtime = DatasetRuntime(
                store=ParquetDatasetStore(root=source_configuration.application_root / 'datasets')
            )
            runtimes_by_application[application] = runtime
        binding = registry.get(source)
        routes[binding.definition.key] = runtime
    return routes


def _job_definition(*, poll_interval_seconds: int) -> JobDefinition:
    return JobDefinition(
        module_name='ada.processes.kpis',
        service_name='kpis',
        job_key='kpi-evaluation',
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
