from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ada.processes.pi_web_api.catalog import build_catalog
from ada.processes.pi_web_api.errors import PiWebApiCatalogError
from ada.processes.pi_web_api.settings import PiWebApiProcessSettings
from atlanticus.configuration import ResolvedConfiguration
from atlanticus.data_producers.pi import PiDataProducerComponents, build_pi_data_producer
from atlanticus.integrations.pi.contracts import PiCatalog, PiWebApiSource
from atlanticus.integrations.pi.web_api import PiWebApiClient
from atlanticus.runtime import (
    JobDefinition,
    RuntimeConfiguration,
    RuntimeExecutionResult,
    execute_job,
)

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


# La composición mantiene cliente y producer; sus piezas internas no se reexportan desde ADA.
@dataclass(slots=True)
class PiWebApiComposition:
    configuration: ResolvedConfiguration
    runtime_configuration: RuntimeConfiguration
    settings: PiWebApiProcessSettings
    catalog: PiCatalog
    client: PiWebApiClient
    producer: PiDataProducerComponents

    def execute(self, *, argv: Sequence[str] | None = None) -> RuntimeExecutionResult:
        with self.client:
            return execute_job(
                definition=PI_WEB_API_JOB_DEFINITION,
                iteration=self.producer.job.run_iteration,
                argv=argv,
                environ=self.configuration.values,
            )


# Construye el producer PI con la configuración concreta de este proceso.
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
    if resolved_catalog.source.interpolation_seconds is None:
        raise PiWebApiCatalogError('PI Web API catalog must define interpolation_seconds')

    settings = PiWebApiProcessSettings.from_configuration(configuration)
    runtime_configuration = RuntimeConfiguration.from_sources(environ=configuration.values)
    client = PiWebApiClient(settings=settings.pi_web_api)
    producer = build_pi_data_producer(
        runtime_configuration=runtime_configuration,
        catalog=resolved_catalog,
        client=client,
        producer_key='pi-web-api',
        dataset_namespace=('pi', 'web-api'),
        max_recovery_lookback_seconds=settings.max_recovery_lookback_seconds,
        max_recovery_window_seconds=settings.max_recovery_window_seconds,
        max_data_points=settings.max_data_points,
        interpolated_max_parallel_requests=settings.interpolated_max_parallel_requests,
    )
    return PiWebApiComposition(
        configuration=configuration,
        runtime_configuration=runtime_configuration,
        settings=settings,
        catalog=resolved_catalog,
        client=client,
        producer=producer,
    )
