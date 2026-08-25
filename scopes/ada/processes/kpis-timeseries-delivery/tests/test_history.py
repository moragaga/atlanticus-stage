import json
from datetime import UTC, datetime

import pyarrow as pa

from ada.processes.kpis_timeseries_delivery.history import KpiTimeseriesHistoryRepository
from atlanticus.datasets.runtime import DatasetRuntime
from atlanticus.datasets.runtime.models import TableReadResult


class _Runtime(DatasetRuntime):
    def __init__(self, table: pa.Table) -> None:
        self.table = table
        self.calls: list[dict[str, object]] = []

    def scan_table(self, **kwargs) -> TableReadResult:
        self.calls.append(kwargs)
        return TableReadResult(
            table=self.table,
            targets=tuple(kwargs['targets']),
            artifact_count=1,
            size_bytes=1,
        )


def test_history_reader_selects_native_values_and_keeps_nulls_without_status_semantics() -> None:
    table = pa.Table.from_pylist(
        [
            {
                'timestamp_utc': datetime(2026, 8, 25, 10, 2, tzinfo=UTC),
                'key': 'a',
                'value': json.dumps(10.5),
            },
            {
                'timestamp_utc': datetime(2026, 8, 25, 10, 4, tzinfo=UTC),
                'key': 'a',
                'value': None,
            },
            {
                'timestamp_utc': datetime(2026, 8, 25, 10, 2, tzinfo=UTC),
                'key': 'b',
                'value': json.dumps('RUN'),
            },
        ]
    )
    runtime = _Runtime(table)
    repository = KpiTimeseriesHistoryRepository(runtime=runtime)

    points = repository.read_points(
        keys=('a', 'b'),
        start_utc=datetime(2026, 8, 25, 10, 0, tzinfo=UTC),
        end_utc=datetime(2026, 8, 25, 10, 4, tzinfo=UTC),
        step_seconds=120,
    )

    assert [(point.key, point.value) for point in points] == [
        ('a', 10.5),
        ('b', 'RUN'),
        ('a', None),
    ]
    assert len(runtime.calls) == 1
    assert runtime.calls[0]['columns'] == ('timestamp_utc', 'key', 'value')
