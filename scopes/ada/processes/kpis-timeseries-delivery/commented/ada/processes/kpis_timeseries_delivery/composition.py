# Proceso Series: lee Historian, selecciona timestamps exactos y publica una proyección compacta.
# Compone dependencias concretas sobre contratos ya definidos.

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ada.processes.kpis_timeseries_delivery.configuration import (
    KpiTimeseriesConfigurationRepository,
)
from ada.processes.kpis_timeseries_delivery.history import KpiTimeseriesHistoryRepository
from ada.processes.kpis_timeseries_delivery.job import KpiTimeseriesDeliveryJob
from ada.processes.kpis_timeseries_delivery.repository import KpiTimeseriesSnapshotRepository
from ada.processes.kpis_timeseries_delivery.settings import (
    KpiTimeseriesDeliveryProcessSettings,
)
from ada.processes.kpis_timeseries_delivery.state import (
    KpiHistorianWatermarkStore,
    KpiTimeseriesDeliveryCheckpointStore,
)
from atlanticus.configuration import ResolvedConfiguration
from atlanticus.connectivity.cosmos import CosmosClient
from atlanticus.datasets.parquet import ParquetDatasetStore
from atlanticus.datasets.runtime import DatasetRuntime
from atlanticus.runtime import (
    JobDefinition,
    RuntimeConfiguration,
    RuntimeExecutionResult,
    execute_job,
)
from atlanticus.state import AtomicStateStore


@dataclass(slots=True)
# La clase encapsula una responsabilidad con estado o contrato propio.
class KpiTimeseriesDeliveryComposition:
    configuration: ResolvedConfiguration
    runtime_configuration: RuntimeConfiguration
    settings: KpiTimeseriesDeliveryProcessSettings
    job: KpiTimeseriesDeliveryJob
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
def build_composition(*, configuration: ResolvedConfiguration) -> KpiTimeseriesDeliveryComposition:
    if not isinstance(configuration, ResolvedConfiguration):
        raise TypeError('configuration must be a ResolvedConfiguration')
    settings = KpiTimeseriesDeliveryProcessSettings.from_configuration(configuration)
    runtime_configuration = RuntimeConfiguration.from_sources(environ=configuration.values)
    state_store = AtomicStateStore(
        volume_path=runtime_configuration.volume_path,
        application=runtime_configuration.application,
    )
    history_runtime = DatasetRuntime(
        store=ParquetDatasetStore(root=runtime_configuration.application_root / 'datasets')
    )
    cosmos_client = CosmosClient(settings=settings.cosmos)
    job = KpiTimeseriesDeliveryJob(
        configuration=KpiTimeseriesConfigurationRepository(client=cosmos_client),
        historian_state=KpiHistorianWatermarkStore(store=state_store),
        history=KpiTimeseriesHistoryRepository(runtime=history_runtime),
        checkpoint=KpiTimeseriesDeliveryCheckpointStore(store=state_store),
        snapshots=KpiTimeseriesSnapshotRepository(client=cosmos_client),
    )
    job_definition = _job_definition(poll_interval_seconds=settings.poll_interval_seconds)
    return KpiTimeseriesDeliveryComposition(
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
        module_name='ada.processes.kpis_timeseries_delivery',
        service_name='kpis-timeseries-delivery',
        job_key='kpis-timeseries-delivery',
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
