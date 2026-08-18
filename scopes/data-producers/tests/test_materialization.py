from datetime import UTC, datetime

import pyarrow as pa
import pytest

from atlanticus.connectivity.sql import SqlTableChangeMarker
from atlanticus.data_producers.core import SourceScope, SourceScopeItem
from atlanticus.data_producers.sql import (
    SqlDataProducerMaterializer,
    SqlDataProducerSchemaError,
    SqlSourcePlan,
)
from atlanticus.datasets.parquet import ParquetDatasetStore
from atlanticus.datasets.results import PublicationStatus
from atlanticus.datasets.runtime import DatasetRuntime


def _scope() -> SourceScope:
    return SourceScope(
        token='1|2',
        items=(
            SourceScopeItem(value=1, partition={'year': '2026', 'window': '1'}),
            SourceScopeItem(value=2, partition={'year': '2026', 'window': '2'}),
        ),
    )


def _plan(definition) -> SqlSourcePlan:
    return SqlSourcePlan(
        definition=definition,
        change_marker=SqlTableChangeMarker(
            source_table=definition.source_table,
            generation_token='generation',
            last_user_update_token='token',
            user_updates=1,
        ),
        scope=_scope(),
    )


def test_partitioned_materialization_is_idempotent(tmp_path, scoped_definition) -> None:
    runtime = DatasetRuntime(store=ParquetDatasetStore(root=tmp_path / 'datasets'))
    materializer = SqlDataProducerMaterializer(
        runtime=runtime,
        definitions=(scoped_definition,),
        dataset_namespace=('producer',),
    )
    table = pa.table(
        {
            'scope_id': pa.array([1, 2], type=pa.int64()),
            'moment': pa.array(
                [
                    datetime(2026, 8, 18, 12, 0, tzinfo=UTC),
                    datetime(2026, 8, 18, 11, 0, tzinfo=UTC),
                ],
                type=pa.timestamp('us', tz='UTC'),
            ),
            'value': pa.array([1.0, 2.0], type=pa.float64()),
        }
    )

    first, missing = materializer.publish(plan=_plan(scoped_definition), table=table)
    second, _ = materializer.publish(plan=_plan(scoped_definition), table=table)

    assert missing == ()
    assert [item.publication.status for item in first] == [
        PublicationStatus.COMMITTED,
        PublicationStatus.COMMITTED,
    ]
    assert [item.publication.status for item in second] == [
        PublicationStatus.UNCHANGED,
        PublicationStatus.UNCHANGED,
    ]


def test_partitioned_materialization_reports_missing_scope(tmp_path, scoped_definition) -> None:
    runtime = DatasetRuntime(store=ParquetDatasetStore(root=tmp_path / 'datasets'))
    materializer = SqlDataProducerMaterializer(
        runtime=runtime,
        definitions=(scoped_definition,),
        dataset_namespace=('producer',),
    )
    table = pa.table(
        {
            'scope_id': pa.array([1], type=pa.int64()),
            'moment': pa.array(
                [datetime(2026, 8, 18, 12, 0, tzinfo=UTC)],
                type=pa.timestamp('us', tz='UTC'),
            ),
            'value': pa.array([1.0], type=pa.float64()),
        }
    )

    publications, missing = materializer.publish(plan=_plan(scoped_definition), table=table)

    assert len(publications) == 1
    assert missing == (2,)


def test_partitioned_materialization_rejects_unexpected_scope(tmp_path, scoped_definition) -> None:
    runtime = DatasetRuntime(store=ParquetDatasetStore(root=tmp_path / 'datasets'))
    materializer = SqlDataProducerMaterializer(
        runtime=runtime,
        definitions=(scoped_definition,),
        dataset_namespace=('producer',),
    )
    table = pa.table(
        {
            'scope_id': pa.array([3], type=pa.int64()),
            'moment': pa.array(
                [datetime(2026, 8, 18, 12, 0, tzinfo=UTC)],
                type=pa.timestamp('us', tz='UTC'),
            ),
            'value': pa.array([1.0], type=pa.float64()),
        }
    )

    with pytest.raises(SqlDataProducerSchemaError, match='unexpected scope value'):
        materializer.publish(plan=_plan(scoped_definition), table=table)
