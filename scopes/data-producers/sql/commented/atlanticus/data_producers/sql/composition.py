# Compone el productor SQL sobre runtime, state, datasets y connectivity sin depender de ADA.
from __future__ import annotations

from dataclasses import dataclass

from atlanticus.connectivity.sql import SqlClient, SqlSettings
from atlanticus.data_producers.core import SourceScopeProvider
from atlanticus.data_producers.sql.extraction import SqlDataProducerReader
from atlanticus.data_producers.sql.job import SqlDataProducerJob
from atlanticus.data_producers.sql.materialization import SqlDataProducerMaterializer
from atlanticus.data_producers.sql.models import SqlSourceDefinition
from atlanticus.data_producers.sql.planning import SqlDataProducerPlanner
from atlanticus.data_producers.sql.processor import SqlDataProducerProcessor
from atlanticus.data_producers.sql.producer_state import SqlProducerState
from atlanticus.data_producers.sql.settings import SqlRetryPolicy
from atlanticus.datasets.parquet import ParquetDatasetStore
from atlanticus.datasets.runtime import DatasetRuntime
from atlanticus.runtime import RuntimeConfiguration
from atlanticus.state.store import AtomicStateStore


@dataclass(slots=True)
class SqlDataProducerComponents:
    reader: SqlDataProducerReader
    producer_state: SqlProducerState
    planner: SqlDataProducerPlanner
    materializer: SqlDataProducerMaterializer
    processor: SqlDataProducerProcessor
    job: SqlDataProducerJob


def build_sql_data_producer(
    *,
    producer_key: str,
    definitions: tuple[SqlSourceDefinition, ...],
    sql_settings: SqlSettings,
    retry_policy: SqlRetryPolicy,
    runtime_configuration: RuntimeConfiguration,
    dataset_namespace: tuple[str, ...],
    scope_provider: SourceScopeProvider | None = None,
    missing_scope_fact_name: str = 'missing_scope_values',
) -> SqlDataProducerComponents:
    if not isinstance(runtime_configuration, RuntimeConfiguration):
        raise TypeError('runtime_configuration must be a RuntimeConfiguration')
    reader = SqlDataProducerReader(
        sql=SqlClient(settings=sql_settings),
        retry_policy=retry_policy,
        max_rows=sql_settings.max_query_rows,
    )
    producer_state = SqlProducerState(
        store=AtomicStateStore(
            volume_path=runtime_configuration.volume_path,
            application=runtime_configuration.application,
        ),
        producer_key=producer_key,
    )
    planner = SqlDataProducerPlanner(
        reader=reader,
        producer_state=producer_state,
        scope_provider=scope_provider,
    )
    materializer = SqlDataProducerMaterializer(
        runtime=DatasetRuntime(
            store=ParquetDatasetStore(root=runtime_configuration.application_root / 'datasets')
        ),
        definitions=definitions,
        dataset_namespace=dataset_namespace,
    )
    processor = SqlDataProducerProcessor(reader=reader, materializer=materializer)
    job = SqlDataProducerJob(
        producer_key=producer_key,
        definitions=definitions,
        planner=planner,
        producer_state=producer_state,
        executor=processor,
        missing_scope_fact_name=missing_scope_fact_name,
    )
    return SqlDataProducerComponents(
        reader=reader,
        producer_state=producer_state,
        planner=planner,
        materializer=materializer,
        processor=processor,
        job=job,
    )
