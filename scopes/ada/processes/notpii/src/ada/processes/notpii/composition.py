from __future__ import annotations

from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import dataclass

from ada.processes.notpii.catalog import active_extraction_modes, build_catalog
from ada.processes.notpii.errors import NotPiiCatalogError
from ada.processes.notpii.settings import NotPiiSettings
from atlanticus.configuration import ResolvedConfiguration
from atlanticus.data_producers.notpii import (
    NotPiiDataProducerComponents,
    build_notpii_data_producer,
)
from atlanticus.integrations.pi.contracts import NotPiiSource, PiCatalog
from atlanticus.runtime import (
    JobDefinition,
    RuntimeConfiguration,
    RuntimeExecutionResult,
    execute_job,
)

NOTPII_JOB_DEFINITION = JobDefinition(
    module_name='ada.processes.notpii',
    service_name='notpii',
    job_key='notpii-materialization',
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
class NotPiiComposition:
    configuration: ResolvedConfiguration
    runtime_configuration: RuntimeConfiguration
    settings: NotPiiSettings
    catalog: PiCatalog
    producer: NotPiiDataProducerComponents

    def execute(self, *, argv: Sequence[str] | None = None) -> RuntimeExecutionResult:
        with ExitStack() as stack:
            for receiver in self.producer.receivers.values():
                stack.enter_context(receiver)
            return execute_job(
                definition=NOTPII_JOB_DEFINITION,
                iteration=self.producer.job.run_iteration,
                argv=argv,
                environ=self.configuration.values,
            )


def build_composition(
    *,
    configuration: ResolvedConfiguration,
    catalog: PiCatalog | None = None,
) -> NotPiiComposition:
    if not isinstance(configuration, ResolvedConfiguration):
        raise TypeError('configuration must be a ResolvedConfiguration')
    resolved_catalog = build_catalog() if catalog is None else catalog
    if not isinstance(resolved_catalog, PiCatalog):
        raise TypeError('catalog must be a PiCatalog')
    if not isinstance(resolved_catalog.source, NotPiiSource):
        raise NotPiiCatalogError('catalog source must be NotPiiSource')
    active_modes = active_extraction_modes(resolved_catalog)
    settings = NotPiiSettings.from_configuration(
        configuration,
        active_modes=active_modes,
    )
    runtime_configuration = RuntimeConfiguration.from_sources(environ=configuration.values)
    producer = build_notpii_data_producer(
        runtime_configuration=runtime_configuration,
        catalog=resolved_catalog,
        service_buses=settings.service_buses,
        raw_batch_size=settings.raw_batch_size,
        max_message_count=settings.max_message_count,
        producer_key='notpii',
        dataset_namespace=('pi', 'not_pii'),
    )
    return NotPiiComposition(
        configuration=configuration,
        runtime_configuration=runtime_configuration,
        settings=settings,
        catalog=resolved_catalog,
        producer=producer,
    )
