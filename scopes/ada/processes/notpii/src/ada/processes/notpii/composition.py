from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass

from ada.connectors.notpii import NotPiiConnector
from ada.processes.notpii.catalog import active_extraction_modes, build_catalog
from ada.processes.notpii.errors import NotPiiCatalogError
from ada.processes.notpii.job import NotPiiJob
from ada.processes.notpii.materialization import NotPiiMaterializer
from ada.processes.notpii.processor import NotPiiProcessor
from ada.processes.notpii.producer_state import NotPiiProducerState
from ada.processes.notpii.settings import NotPiiSettings
from atlanticus.configuration import ResolvedConfiguration
from atlanticus.connectivity.service_bus import ServiceBusTopicReceiver
from atlanticus.connectivity.storage import StorageSasReader
from atlanticus.datasets.parquet import ParquetDatasetStore
from atlanticus.datasets.runtime import DatasetRuntime
from atlanticus.integrations.pi.contracts import NotPiiSource, PiCatalog, PiExtractionMode
from atlanticus.runtime import (
    JobDefinition,
    RuntimeConfiguration,
    RuntimeExecutionResult,
    execute_job,
)
from atlanticus.state import AtomicStateStore

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

_MODE_ORDER = (PiExtractionMode.INTERPOLATED, PiExtractionMode.RECORDED)


@dataclass(slots=True)
class NotPiiComposition:
    configuration: ResolvedConfiguration
    runtime_configuration: RuntimeConfiguration
    settings: NotPiiSettings
    catalog: PiCatalog
    receivers: Mapping[PiExtractionMode, ServiceBusTopicReceiver]
    processors: Mapping[PiExtractionMode, NotPiiProcessor]
    producer_state: NotPiiProducerState
    job: NotPiiJob

    def execute(self, *, argv: Sequence[str] | None = None) -> RuntimeExecutionResult:
        with ExitStack() as stack:
            for receiver in self.receivers.values():
                stack.enter_context(receiver)
            return execute_job(
                definition=NOTPII_JOB_DEFINITION,
                iteration=self.job.run_iteration,
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
    dataset_runtime = DatasetRuntime(
        store=ParquetDatasetStore(root=runtime_configuration.application_root / 'datasets')
    )
    connector = NotPiiConnector(
        storage_reader=StorageSasReader(),
        raw_batch_size=settings.raw_batch_size,
    )
    processors = {
        mode: NotPiiProcessor(
            connector=connector,
            materializer=NotPiiMaterializer(
                runtime=dataset_runtime,
                catalog=resolved_catalog,
                extraction_mode=mode,
            ),
            catalog=resolved_catalog,
            extraction_mode=mode,
        )
        for mode in active_modes
    }
    receivers = {
        mode: ServiceBusTopicReceiver(settings=settings.service_buses[mode])
        for mode in active_modes
    }
    producer_state = NotPiiProducerState(
        store=AtomicStateStore(
            volume_path=runtime_configuration.volume_path,
            application=runtime_configuration.application,
        )
    )
    return NotPiiComposition(
        configuration=configuration,
        runtime_configuration=runtime_configuration,
        settings=settings,
        catalog=resolved_catalog,
        receivers=receivers,
        processors=processors,
        producer_state=producer_state,
        job=NotPiiJob(
            receivers=receivers,
            processors=processors,
            producer_state=producer_state,
            max_message_count=settings.max_message_count,
        ),
    )
