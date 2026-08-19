# El proceso ADA declara runtime y entrega configuración resuelta al producer reutilizable.
# No contiene lectura, transformación, materialización ni state propios.

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ada.processes.remanentes.catalog import build_catalog
from ada.processes.remanentes.settings import RemanentesSettings
from atlanticus.configuration import ResolvedConfiguration
from atlanticus.data_producers.remanentes import (
    RemanentesDataProducerComponents,
    RemanentesStreamDefinition,
    build_remanentes_data_producer,
)
from atlanticus.runtime import (
    JobDefinition,
    RuntimeConfiguration,
    RuntimeExecutionResult,
    execute_job,
)

REMANENTES_JOB_DEFINITION = JobDefinition(
    module_name='ada.processes.remanentes',
    service_name='remanentes',
    job_key='remanentes-materialization',
    run_once=True,
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
class RemanentesComposition:
    configuration: ResolvedConfiguration
    runtime_configuration: RuntimeConfiguration
    settings: RemanentesSettings
    catalog: tuple[RemanentesStreamDefinition, ...]
    producer: RemanentesDataProducerComponents

    def execute(self, *, argv: Sequence[str] | None = None) -> RuntimeExecutionResult:
        with self.producer.storage:
            return execute_job(
                definition=REMANENTES_JOB_DEFINITION,
                iteration=self.producer.job.run_iteration,
                argv=argv,
                environ=self.configuration.values,
            )


def build_composition(
    *,
    configuration: ResolvedConfiguration,
    catalog: tuple[RemanentesStreamDefinition, ...] | None = None,
) -> RemanentesComposition:
    if not isinstance(configuration, ResolvedConfiguration):
        raise TypeError('configuration must be a ResolvedConfiguration')
    settings = RemanentesSettings.from_configuration(configuration)
    resolved_catalog = (
        build_catalog(source_timezone_name=settings.source_timezone_name)
        if catalog is None
        else tuple(catalog)
    )
    if not resolved_catalog:
        raise ValueError('Remanentes composition requires at least one stream definition')
    runtime_configuration = RuntimeConfiguration.from_sources(environ=configuration.values)
    producer = build_remanentes_data_producer(
        runtime_configuration=runtime_configuration,
        definitions=resolved_catalog,
        connection=settings.connection,
        idle_seconds=settings.idle_seconds,
        producer_key='remanentes',
        dataset_namespace=('remanentes',),
    )
    return RemanentesComposition(
        configuration=configuration,
        runtime_configuration=runtime_configuration,
        settings=settings,
        catalog=resolved_catalog,
        producer=producer,
    )
