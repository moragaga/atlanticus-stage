# Proceso Latest: congela configuración por job, observa watermark fresco y publica sólo cuando corresponde.
# Compone dependencias concretas sobre contratos ya definidos.

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ada.kpis.persistence import KpiCommitStore, KpiLatestRepository, KpiPersistencePaths
from ada.processes.kpis_delivery.configuration import KpiDeliveryConfigurationRepository
from ada.processes.kpis_delivery.job import KpiLatestDeliveryJob
from ada.processes.kpis_delivery.repository import KpiLatestSnapshotRepository
from ada.processes.kpis_delivery.settings import KpiDeliveryProcessSettings
from ada.processes.kpis_delivery.state import KpiLatestDeliveryCheckpointStore
from atlanticus.configuration import ResolvedConfiguration
from atlanticus.connectivity.cosmos import CosmosClient
from atlanticus.json import JsonDocumentStore
from atlanticus.runtime import (
    JobDefinition,
    RuntimeConfiguration,
    RuntimeExecutionResult,
    execute_job,
)
from atlanticus.state import AtomicStateStore


@dataclass(slots=True)
# La clase encapsula una responsabilidad con estado o contrato propio.
class KpiDeliveryComposition:
    configuration: ResolvedConfiguration
    runtime_configuration: RuntimeConfiguration
    settings: KpiDeliveryProcessSettings
    job: KpiLatestDeliveryJob
    job_definition: JobDefinition
    cosmos_client: CosmosClient

    def execute(self, *, argv: Sequence[str] | None = None) -> RuntimeExecutionResult:
        with self.cosmos_client:
            return execute_job(
                definition=self.job_definition,
                iteration=self.job.run_iteration,
                argv=argv,
                environ=self.configuration.values,
            )


# La función mantiene una operación pequeña y verificable de esta frontera.
def build_composition(*, configuration: ResolvedConfiguration) -> KpiDeliveryComposition:
    if not isinstance(configuration, ResolvedConfiguration):
        raise TypeError('configuration must be a ResolvedConfiguration')
    settings = KpiDeliveryProcessSettings.from_configuration(configuration)
    runtime_configuration = RuntimeConfiguration.from_sources(environ=configuration.values)
    persistence_paths = KpiPersistencePaths(runtime_configuration.application_root)
    state_store = AtomicStateStore(
        volume_path=runtime_configuration.volume_path,
        application=runtime_configuration.application,
    )
    latest = KpiLatestRepository(
        store=JsonDocumentStore(),
        paths=persistence_paths,
    )
    cosmos_client = CosmosClient(settings=settings.cosmos)
    job = KpiLatestDeliveryJob(
        configuration=KpiDeliveryConfigurationRepository(client=cosmos_client),
        kpi_state=KpiCommitStore(store=state_store),
        latest=latest,
        checkpoint=KpiLatestDeliveryCheckpointStore(store=state_store),
        snapshots=KpiLatestSnapshotRepository(client=cosmos_client),
    )
    job_definition = _job_definition(poll_interval_seconds=settings.poll_interval_seconds)
    return KpiDeliveryComposition(
        configuration=configuration,
        runtime_configuration=runtime_configuration,
        settings=settings,
        job=job,
        job_definition=job_definition,
        cosmos_client=cosmos_client,
    )


# La función mantiene una operación pequeña y verificable de esta frontera.
def _job_definition(*, poll_interval_seconds: int) -> JobDefinition:
    return JobDefinition(
        module_name='ada.processes.kpis_delivery',
        service_name='kpis-delivery',
        job_key='kpis-delivery',
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
