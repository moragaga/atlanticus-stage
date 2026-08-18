from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ada.processes.dispatch.catalog import build_catalog
from ada.processes.dispatch.scope import DispatchShiftScopeProvider
from ada.processes.dispatch.settings import DispatchSettings
from atlanticus.configuration import ResolvedConfiguration
from atlanticus.data_producers.sql import (
    SqlDataProducerComponents,
    SqlSourceDefinition,
    build_sql_data_producer,
)
from atlanticus.runtime import (
    JobDefinition,
    RuntimeConfiguration,
    RuntimeExecutionResult,
    execute_job,
)

DISPATCH_JOB_DEFINITION = JobDefinition(
    module_name='ada.processes.dispatch',
    service_name='dispatch',
    job_key='dispatch-materialization',
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
class DispatchComposition:
    configuration: ResolvedConfiguration
    runtime_configuration: RuntimeConfiguration
    settings: DispatchSettings
    catalog: tuple[SqlSourceDefinition, ...]
    producer: SqlDataProducerComponents

    @property
    def reader(self):
        return self.producer.reader

    @property
    def producer_state(self):
        return self.producer.producer_state

    @property
    def planner(self):
        return self.producer.planner

    @property
    def materializer(self):
        return self.producer.materializer

    @property
    def processor(self):
        return self.producer.processor

    @property
    def job(self):
        return self.producer.job

    def execute(self, *, argv: Sequence[str] | None = None) -> RuntimeExecutionResult:
        return execute_job(
            definition=DISPATCH_JOB_DEFINITION,
            iteration=self.producer.job.run_iteration,
            argv=argv,
            environ=self.configuration.values,
        )


def build_composition(
    *,
    configuration: ResolvedConfiguration,
    catalog: tuple[SqlSourceDefinition, ...] | None = None,
) -> DispatchComposition:
    if not isinstance(configuration, ResolvedConfiguration):
        raise TypeError('configuration must be a ResolvedConfiguration')
    resolved_catalog = build_catalog() if catalog is None else tuple(catalog)
    if not resolved_catalog or not all(
        isinstance(definition, SqlSourceDefinition) for definition in resolved_catalog
    ):
        raise TypeError('catalog must contain SqlSourceDefinition values')
    settings = DispatchSettings.from_configuration(configuration)
    runtime_configuration = RuntimeConfiguration.from_sources(environ=configuration.values)
    producer = build_sql_data_producer(
        producer_key='dispatch',
        definitions=resolved_catalog,
        sql_settings=settings.sql,
        retry_policy=settings.retry_policy,
        runtime_configuration=runtime_configuration,
        dataset_namespace=('dispatch',),
        scope_provider=DispatchShiftScopeProvider(),
        missing_scope_fact_name='missing_shift_ids',
    )
    return DispatchComposition(
        configuration=configuration,
        runtime_configuration=runtime_configuration,
        settings=settings,
        catalog=resolved_catalog,
        producer=producer,
    )
