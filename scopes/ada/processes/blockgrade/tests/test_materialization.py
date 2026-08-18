from datetime import UTC, datetime

import pyarrow as pa
import pytest

from ada.processes.blockgrade.errors import BlockgradeSchemaError
from ada.processes.blockgrade.materialization import BlockgradeMaterializer
from ada.processes.blockgrade.models import BlockgradeSourcePlan
from atlanticus.connectivity.sql import SqlTableChangeMarker
from atlanticus.datasets.parquet import ParquetDatasetStore
from atlanticus.datasets.results import PublicationStatus
from atlanticus.datasets.runtime import DatasetRuntime


def _plan(definition, *, shift_ids=(260817002, 260817001)) -> BlockgradeSourcePlan:
    return BlockgradeSourcePlan(
        definition=definition,
        change_marker=SqlTableChangeMarker(
            source_table=definition.source_table,
            generation_token='generation',
            last_user_update_token='token',
            user_updates=1,
        ),
        scope_token='|'.join(str(value) for value in shift_ids),
        shift_ids=shift_ids,
    )


def test_shift_materialization_replaces_each_present_shift_atomically(
    tmp_path, shift_definition
) -> None:
    runtime = DatasetRuntime(store=ParquetDatasetStore(root=tmp_path / 'datasets'))
    materializer = BlockgradeMaterializer(runtime=runtime, definitions=(shift_definition,))
    table = pa.table(
        {
            'shift_id': pa.array([260817002, 260817001], type=pa.int64()),
            'moment': pa.array(
                [
                    datetime(2026, 8, 17, 22, 0, tzinfo=UTC),
                    datetime(2026, 8, 17, 10, 0, tzinfo=UTC),
                ],
                type=pa.timestamp('us', tz='UTC'),
            ),
            'value': pa.array([1.0, 2.0], type=pa.float64()),
        }
    )

    first, missing = materializer.publish(plan=_plan(shift_definition), table=table)
    second, _ = materializer.publish(plan=_plan(shift_definition), table=table)

    assert missing == ()
    assert [item.publication.status for item in first] == [
        PublicationStatus.COMMITTED,
        PublicationStatus.COMMITTED,
    ]
    assert [item.publication.status for item in second] == [
        PublicationStatus.UNCHANGED,
        PublicationStatus.UNCHANGED,
    ]


def test_shift_materialization_reports_missing_shift_without_deleting_existing(
    tmp_path, shift_definition
) -> None:
    runtime = DatasetRuntime(store=ParquetDatasetStore(root=tmp_path / 'datasets'))
    materializer = BlockgradeMaterializer(runtime=runtime, definitions=(shift_definition,))
    table = pa.table(
        {
            'shift_id': pa.array([260817002], type=pa.int64()),
            'moment': pa.array(
                [datetime(2026, 8, 17, 22, 0, tzinfo=UTC)],
                type=pa.timestamp('us', tz='UTC'),
            ),
            'value': pa.array([1.0], type=pa.float64()),
        }
    )

    publications, missing = materializer.publish(plan=_plan(shift_definition), table=table)

    assert len(publications) == 1
    assert missing == (260817001,)


def test_unexpected_shift_is_rejected(tmp_path, shift_definition) -> None:
    runtime = DatasetRuntime(store=ParquetDatasetStore(root=tmp_path / 'datasets'))
    materializer = BlockgradeMaterializer(runtime=runtime, definitions=(shift_definition,))
    table = pa.table(
        {
            'shift_id': pa.array([260816002], type=pa.int64()),
            'moment': pa.array(
                [datetime(2026, 8, 16, 22, 0, tzinfo=UTC)],
                type=pa.timestamp('us', tz='UTC'),
            ),
            'value': pa.array([1.0], type=pa.float64()),
        }
    )

    with pytest.raises(BlockgradeSchemaError, match='unexpected shift'):
        materializer.publish(plan=_plan(shift_definition), table=table)


class _CancellingContext:
    def __init__(self, *, cancel_on_call: int) -> None:
        self.cancel_on_call = cancel_on_call
        self.calls = 0

    def raise_if_cancelled(self) -> None:
        from atlanticus.runtime import RuntimeCancellationRequested

        self.calls += 1
        if self.calls >= self.cancel_on_call:
            raise RuntimeCancellationRequested('test_cancelled')


def test_shift_materialization_honors_cancellation_between_partitions(
    tmp_path, shift_definition
) -> None:
    from atlanticus.runtime import RuntimeCancellationRequested

    runtime = DatasetRuntime(store=ParquetDatasetStore(root=tmp_path / 'datasets'))
    materializer = BlockgradeMaterializer(runtime=runtime, definitions=(shift_definition,))
    table = pa.table(
        {
            'shift_id': pa.array([260817002, 260817001], type=pa.int64()),
            'moment': pa.array(
                [
                    datetime(2026, 8, 17, 22, 0, tzinfo=UTC),
                    datetime(2026, 8, 17, 10, 0, tzinfo=UTC),
                ],
                type=pa.timestamp('us', tz='UTC'),
            ),
            'value': pa.array([1.0, 2.0], type=pa.float64()),
        }
    )

    with pytest.raises(RuntimeCancellationRequested):
        materializer.publish(
            plan=_plan(shift_definition),
            table=table,
            context=_CancellingContext(cancel_on_call=3),
        )
