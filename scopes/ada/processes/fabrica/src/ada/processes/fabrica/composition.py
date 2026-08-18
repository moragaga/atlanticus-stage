from __future__ import annotations

from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import dataclass

from ada.processes.fabrica.catalog import build_catalog
from ada.processes.fabrica.settings import FabricaSettings
from atlanticus.configuration import ResolvedConfiguration
from atlanticus.data_producers.fabrica import (
    FabricaDataProducerComponents,
    FabricaStreamDefinition,
    build_fabrica_data_producer,
)
from atlanticus.runtime import (
    JobDefinition,
    RuntimeConfiguration,
    RuntimeExecutionResult,
    execute_job,
)

FABRICA_JOB_DEFINITION = JobDefinition(
    module_name='ada.processes.fabrica',
    service_name='fabrica',
    job_key='fabrica-materialization',
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
class FabricaComposition:
    configuration: ResolvedConfiguration
    runtime_configuration: RuntimeConfiguration
    settings: FabricaSettings
    catalog: tuple[FabricaStreamDefinition, ...]
    producer: FabricaDataProducerComponents

    def execute(self, *, argv: Sequence[str] | None = None) -> RuntimeExecutionResult:
        with ExitStack() as stack:
            for storage in self.producer.storages.values():
                stack.enter_context(storage)
            return execute_job(
                definition=FABRICA_JOB_DEFINITION,
                iteration=self.producer.job.run_iteration,
                argv=argv,
                environ=self.configuration.values,
            )


def build_composition(
    *,
    configuration: ResolvedConfiguration,
    catalog: tuple[FabricaStreamDefinition, ...] | None = None,
) -> FabricaComposition:
    if not isinstance(configuration, ResolvedConfiguration):
        raise TypeError('configuration must be a ResolvedConfiguration')
    resolved_catalog = build_catalog() if catalog is None else tuple(catalog)
    if not resolved_catalog:
        raise ValueError('Fabrica composition requires at least one stream definition')
    settings = FabricaSettings.from_configuration(configuration)
    runtime_configuration = RuntimeConfiguration.from_sources(environ=configuration.values)
    producer = build_fabrica_data_producer(
        runtime_configuration=runtime_configuration,
        definitions=resolved_catalog,
        connections=settings.connections,
        idle_seconds=settings.idle_seconds,
        producer_key='fabrica',
        dataset_namespace=('fabrica',),
    )
    return FabricaComposition(
        configuration=configuration,
        runtime_configuration=runtime_configuration,
        settings=settings,
        catalog=resolved_catalog,
        producer=producer,
    )
