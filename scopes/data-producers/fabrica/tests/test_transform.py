import re

import pandas as pd
import pyarrow as pa

from atlanticus.data_producers.fabrica import (
    FabricaKpiLevel,
    FabricaKpiStreamDefinition,
    FabricaValueKind,
    KpiDatasetDefinition,
    KpiMetricDefinition,
    build_partition_frames,
    merge_partition_frame,
)


def _metrics() -> tuple[KpiMetricDefinition, KpiMetricDefinition]:
    return (
        KpiMetricDefinition(
            id_kpi='OEE_STMG',
            metric_key='oee_stmg',
            value_kind=FabricaValueKind.NUMBER,
        ),
        KpiMetricDefinition(
            id_kpi='OEE_TRANSPORTE',
            metric_key='oee_transporte',
            value_kind=FabricaValueKind.NUMBER,
        ),
    )


def _definition() -> FabricaKpiStreamDefinition:
    oee_stmg, oee_transporte = _metrics()
    return FabricaKpiStreamDefinition(
        source_prefix='source',
        source_filename_pattern=re.compile(r'kpi_(?P<file_timestamp>\d{14})\.parquet$'),
        output_route_segment='kpis',
        datasets=(
            KpiDatasetDefinition(
                name='daily',
                level=FabricaKpiLevel.DAY,
                route_segment='daily',
                metrics=(oee_stmg, oee_transporte),
            ),
            KpiDatasetDefinition(
                name='weekly',
                level=FabricaKpiLevel.SEVEN_LAST_DAYS,
                route_segment='weekly',
                metrics=(oee_stmg,),
            ),
        ),
    )


def test_kpi_datasets_filter_exact_id_and_level_pairs_before_pivot() -> None:
    table = pa.Table.from_pydict(
        {
            'timestamp': ['2026-08-10T10:00:00Z'] * 4,
            'id_kpi': ['OEE_STMG', 'OEE_TRANSPORTE', 'OEE_STMG', 'OEE_TRANSPORTE'],
            'valor': ['91', '87', '90', '999'],
            'nivel': ['DAY', 'DAY', '7LD', '7LD'],
            'timestamp_ejecucion': ['2026-08-10T11:00:00Z'] * 4,
            'particion': ['202608'] * 4,
        }
    )

    result = build_partition_frames(table=table, definition=_definition())

    assert result.source_row_count == 3
    assert result.frames['daily'].columns.tolist() == [
        'timestamp',
        'oee_stmg',
        'oee_transporte',
    ]
    assert result.frames['daily'].loc[0, 'oee_stmg'] == 91.0
    assert result.frames['daily'].loc[0, 'oee_transporte'] == 87.0
    assert result.frames['weekly'].columns.tolist() == ['timestamp', 'oee_stmg']
    assert result.frames['weekly'].loc[0, 'oee_stmg'] == 90.0
    assert 'oee_transporte' not in result.frames['weekly'].columns
    assert result.metrics_expected == 3
    assert result.metrics_present == 3


def test_missing_is_reported_only_for_requested_dataset_metric_pair() -> None:
    table = pa.Table.from_pydict(
        {
            'timestamp': ['2026-08-10T10:00:00Z'] * 2,
            'id_kpi': ['OEE_STMG', 'OEE_TRANSPORTE'],
            'valor': ['91', '87'],
            'nivel': ['DAY', 'DAY'],
            'timestamp_ejecucion': ['2026-08-10T11:00:00Z'] * 2,
            'particion': ['202608'] * 2,
        }
    )

    result = build_partition_frames(table=table, definition=_definition())

    assert result.metrics_expected == 3
    assert result.metrics_present == 2
    assert result.metrics_missing == 1
    assert result.missing_metric_keys == ('oee_stmg',)
    assert result.missing_metric_keys_by_output == (
        ('daily', ()),
        ('weekly', ('oee_stmg',)),
    )


def test_unrequested_kpi_levels_are_ignored_without_warning() -> None:
    table = pa.Table.from_pydict(
        {
            'timestamp': ['2026-08-10T10:00:00Z'] * 2,
            'id_kpi': ['OEE_STMG', 'OEE_STMG'],
            'valor': ['91', '92'],
            'nivel': ['DAY', 'HOUR'],
            'timestamp_ejecucion': ['2026-08-10T11:00:00Z'] * 2,
            'particion': ['202608'] * 2,
        }
    )

    result = build_partition_frames(table=table, definition=_definition())

    assert result.unknown_source_values == ()
    assert result.source_row_count == 1


def test_null_in_new_snapshot_does_not_replace_existing_value() -> None:
    oee_stmg, oee_transporte = _metrics()
    current = pd.DataFrame(
        {
            'timestamp': pd.to_datetime(['2026-08-10T10:00:00Z'], utc=True),
            'oee_stmg': pd.Series([91.0], dtype='Float64'),
            'oee_transporte': pd.Series([87.0], dtype='Float64'),
        }
    )
    incoming = pd.DataFrame(
        {
            'timestamp': pd.to_datetime(['2026-08-10T10:00:00Z'], utc=True),
            'oee_stmg': pd.Series([pd.NA], dtype='Float64'),
            'oee_transporte': pd.Series([pd.NA], dtype='Float64'),
        }
    )
    merged = merge_partition_frame(
        current=current,
        incoming=incoming,
        metrics=(oee_stmg, oee_transporte),
    )
    assert merged.loc[0, 'oee_stmg'] == 91.0
    assert merged.loc[0, 'oee_transporte'] == 87.0


def test_latest_valid_observation_wins_inside_same_timestamp() -> None:
    table = pa.Table.from_pydict(
        {
            'timestamp': ['2026-08-10T10:00:00Z'] * 3,
            'id_kpi': ['OEE_STMG'] * 3,
            'valor': ['90', None, '92'],
            'nivel': ['DAY'] * 3,
            'timestamp_ejecucion': [
                '2026-08-10T11:00:00Z',
                '2026-08-10T12:00:00Z',
                '2026-08-10T13:00:00Z',
            ],
            'particion': ['202607', '202608', '202608'],
        }
    )
    result = build_partition_frames(table=table, definition=_definition())
    assert result.frames['daily'].loc[0, 'oee_stmg'] == 92.0
