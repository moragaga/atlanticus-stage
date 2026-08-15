from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ada.processes.pi_web_api.catalog import build_catalog
from ada.processes.pi_web_api.errors import PiWebApiCatalogError
from ada.processes.pi_web_api.job import PiWebApiJob
from ada.processes.pi_web_api.planning import PiSlotPlanner
from ada.processes.pi_web_api.preparation import PiExecutionPlanPreparer
from ada.processes.pi_web_api.settings import PiWebApiProcessSettings
from ada.processes.pi_web_api.watermarks import (
    PiProducerState,
    PiSourceState,
    PiWatermarkCoordinator,
)
from ada.processes.pi_web_api.web_ids import WebIdRegistry
from atlanticus.configuration import ResolvedConfiguration
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
    execution_timeout_seconds=595,
    shutdown_grace_seconds=15,
    lease_timeout_seconds=120,
    lease_renew_seconds=30,
    lease_wait_seconds=0,
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
    planner = PiSlotPlanner(
        interpolation_seconds=interpolation_seconds,
        max_recovery_seconds=settings.max_recovery_seconds,
    )
    watermarks = PiWatermarkCoordinator(producer=producer_state, source=source_state)
    job = PiWebApiJob(
        preparer=PiExecutionPlanPreparer(client=client, registry=registry),
        catalog=resolved_catalog,
        planner=planner,
        producer_state=producer_state,
    )
    return PiWebApiComposition(
        configuration=configuration,
        runtime_configuration=runtime_configuration,
        settings=settings,
        catalog=resolved_catalog,
        client=client,
        registry=registry,
        planner=planner,
        producer_state=producer_state,
        source_state=source_state,
        watermarks=watermarks,
        job=job,
    )
