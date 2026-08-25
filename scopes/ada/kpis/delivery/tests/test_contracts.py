from datetime import UTC, datetime

import pytest

from ada.kpis.core import KpiArea, KpiEvaluation, KpiResult, KpiStatus, KpiValueKind, KpiWatermark
from ada.kpis.delivery import (
    KpiDeliveryConfiguration,
    KpiDeliveryStatus,
    KpiDeliveryValidationError,
    KpiTimeseriesManifest,
    KpiTimeseriesPoint,
    KpiTimeseriesSnapshot,
    KpiTimeseriesWindow,
    project_kpi_latest,
    project_kpi_timeseries,
)


def configuration():
    return KpiDeliveryConfiguration.from_document(
        {
            'id': 'kpis',
            'partition_key': 'kpis',
            'document_type': 'ada_kpi_configuration_projection',
            'schema_version': 1,
            'revision': 'config-1',
            'tool_projection_revision': 'tools-1',
            'configuration': {
                'bindings': [
                    {
                        'key': 'production',
                        'destination_keys': ['global', 'mill'],
                        'latest_enabled': True,
                        'series_enabled': True,
                        'series_hours': 1,
                    },
                    {
                        'key': 'state',
                        'destination_keys': ['mill'],
                        'latest_enabled': True,
                        'series_enabled': True,
                        'series_hours': 3,
                    },
                    {
                        'key': 'disabled',
                        'destination_keys': ['time'],
                        'latest_enabled': False,
                        'series_enabled': False,
                        'series_hours': None,
                    },
                ]
            },
        }
    )


def evaluation(watermark):
    return KpiEvaluation(
        watermark=watermark,
        results=(
            KpiResult(
                key='production',
                area=KpiArea.PLANTA,
                status=KpiStatus.OK,
                value_kind=KpiValueKind.VALUE,
                persist_history=True,
                value=66.0,
                parsed_value='66,00',
            ),
            KpiResult(
                key='state',
                area=KpiArea.PLANTA,
                status=KpiStatus.ERROR,
                value_kind=KpiValueKind.VALUE,
                persist_history=True,
                error='RuntimeError',
            ),
        ),
    )


def test_configuration_reads_web_projection_and_filters_channels():
    resolved = configuration()

    assert resolved.revision == 'config-1'
    assert [item.key for item in resolved.latest_bindings] == ['production', 'state']
    assert [item.key for item in resolved.series_bindings] == ['production', 'state']
    assert resolved.destinations_for_latest() == {
        'global': ('production',),
        'mill': ('production', 'state'),
    }


def test_configuration_rejects_series_outside_operational_turn():
    document = {
        'id': 'kpis',
        'partition_key': 'kpis',
        'document_type': 'ada_kpi_configuration_projection',
        'schema_version': 1,
        'revision': 'config-1',
        'tool_projection_revision': 'tools-1',
        'configuration': {
            'bindings': [
                {
                    'key': 'production',
                    'destination_keys': ['global'],
                    'latest_enabled': False,
                    'series_enabled': True,
                    'series_hours': 25,
                }
            ]
        },
    }

    with pytest.raises(KpiDeliveryValidationError, match='1 to 24'):
        KpiDeliveryConfiguration.from_document(document)


def test_latest_revision_changes_when_watermark_advances_even_with_same_values():
    config = configuration()
    t1 = KpiWatermark(datetime(2026, 8, 25, 10, 0, tzinfo=UTC))
    t2 = KpiWatermark(datetime(2026, 8, 25, 10, 0, 30, tzinfo=UTC))

    first = project_kpi_latest(
        evaluation=evaluation(t1),
        configuration=config,
        watermark=t1,
        published_at_utc=datetime(2026, 8, 25, 10, 0, 1, tzinfo=UTC),
    )
    second = project_kpi_latest(
        evaluation=evaluation(t2),
        configuration=config,
        watermark=t2,
        published_at_utc=datetime(2026, 8, 25, 10, 0, 31, tzinfo=UTC),
    )

    assert first.manifest.revision != second.manifest.revision
    assert first.destinations['global']['production'].value == '66,00'
    assert first.destinations['mill']['state'].status is KpiDeliveryStatus.ERROR


def test_latest_missing_is_projected_for_requested_kpi():
    config = configuration()
    watermark = KpiWatermark(datetime(2026, 8, 25, 10, 0, tzinfo=UTC))
    partial = KpiEvaluation(
        watermark=watermark,
        results=(
            KpiResult(
                key='production',
                area=KpiArea.PLANTA,
                status=KpiStatus.OK,
                value_kind=KpiValueKind.VALUE,
                persist_history=True,
                value=66.0,
                parsed_value='66,00',
            ),
        ),
    )

    snapshot = project_kpi_latest(
        evaluation=partial,
        configuration=config,
        watermark=watermark,
        published_at_utc=datetime(2026, 8, 25, 10, 0, 1, tzinfo=UTC),
    )

    assert snapshot.destinations['mill']['state'].status is KpiDeliveryStatus.MISSING
    assert snapshot.destinations['mill']['state'].value is None


def test_timeseries_uses_open_start_closed_end_and_fills_nulls():
    config = configuration()
    end = KpiWatermark(datetime(2026, 8, 25, 11, 0, tzinfo=UTC))
    points = (
        KpiTimeseriesPoint(
            timestamp_utc=datetime(2026, 8, 25, 10, 2, tzinfo=UTC),
            key='production',
            value=10.0,
        ),
        KpiTimeseriesPoint(
            timestamp_utc=datetime(2026, 8, 25, 11, 0, tzinfo=UTC),
            key='production',
            value=20.0,
        ),
    )

    snapshot = project_kpi_timeseries(
        points=points,
        configuration=config,
        end_watermark=end,
        step_seconds=120,
        published_at_utc=datetime(2026, 8, 25, 11, 0, 1, tzinfo=UTC),
    )

    one_hour = next(window for window in snapshot.windows if window.hours == 1)
    assert one_hour.start_utc == datetime(2026, 8, 25, 10, 0, tzinfo=UTC)
    assert len(one_hour.values[0]) == 30
    assert one_hour.values[0][0] == 10.0
    assert one_hour.values[0][-1] == 20.0
    assert one_hour.values[0][1] is None
    three_hours = next(window for window in snapshot.windows if window.hours == 3)
    assert len(three_hours.values[0]) == 90
    assert all(value is None for value in three_hours.values[0])


def test_timeseries_snapshot_rejects_row_length_that_breaks_temporal_contract():
    end = KpiWatermark(datetime(2026, 8, 25, 11, 0, tzinfo=UTC))
    manifest = KpiTimeseriesManifest(
        schema_version=1,
        watermark=end,
        configuration_revision='config-1',
        tool_projection_revision='tools-1',
        published_at_utc=datetime(2026, 8, 25, 11, 0, 1, tzinfo=UTC),
        revision='revision-1',
    )

    with pytest.raises(KpiDeliveryValidationError, match='row length'):
        KpiTimeseriesSnapshot(
            id='timeseries',
            partition_id='kpis',
            document_type='ada_kpi_timeseries_delivery',
            manifest=manifest,
            end_utc=end.timestamp_utc,
            step_seconds=120,
            destinations={'global': ('production',)},
            windows=(
                KpiTimeseriesWindow(
                    hours=1,
                    start_utc=datetime(2026, 8, 25, 10, 0, tzinfo=UTC),
                    keys=('production',),
                    values=((1.0,),),
                ),
            ),
        )
