from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ada.processes.blockgrade.catalog import build_catalog
from ada.processes.blockgrade.scope import BlockgradeShiftScopeProvider
from ada.processes.blockgrade.settings import BlockgradeSettings
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

BLOCKGRADE_JOB_DEFINITION = JobDefinition(
    module_name='ada.processes.blockgrade',
    service_name='blockgrade',
    job_key='blockgrade-materialization',
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
class BlockgradeComposition:
    configuration: ResolvedConfiguration
    runtime_configuration: RuntimeConfiguration
    settings: BlockgradeSettings
    catalog: tuple[SqlSourceDefinition, ...]
    producer: SqlDataProducerComponents

    def execute(self, *, argv: Sequence[str] | None = None) -> RuntimeExecutionResult:
        return execute_job(
            definition=BLOCKGRADE_JOB_DEFINITION,
            iteration=self.producer.job.run_iteration,
            argv=argv,
            environ=self.configuration.values,
        )


def build_composition(
    *,
    configuration: ResolvedConfiguration,
    catalog: tuple[SqlSourceDefinition, ...] | None = None,
) -> BlockgradeComposition:
    if not isinstance(configuration, ResolvedConfiguration):
        raise TypeError('configuration must be a ResolvedConfiguration')
    resolved_catalog = build_catalog() if catalog is None else tuple(catalog)
    if not resolved_catalog or not all(
        isinstance(definition, SqlSourceDefinition) for definition in resolved_catalog
    ):
        raise TypeError('catalog must contain SqlSourceDefinition values')
    settings = BlockgradeSettings.from_configuration(configuration)
    runtime_configuration = RuntimeConfiguration.from_sources(environ=configuration.values)
    producer = build_sql_data_producer(
        producer_key='blockgrade',
        definitions=resolved_catalog,
        sql_settings=settings.sql,
        retry_policy=settings.retry_policy,
        runtime_configuration=runtime_configuration,
        dataset_namespace=('blockgrade',),
        scope_provider=BlockgradeShiftScopeProvider(),
        missing_scope_fact_name='missing_shift_ids',
    )
    return BlockgradeComposition(
        configuration=configuration,
        runtime_configuration=runtime_configuration,
        settings=settings,
        catalog=resolved_catalog,
        producer=producer,
    )
