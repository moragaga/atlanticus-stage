from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta

from ada.kpis.core import KpiEvaluation, KpiStatus, KpiValueKind, KpiWatermark
from ada.kpis.delivery.configuration import KpiDeliveryConfiguration
from ada.kpis.delivery.models import (
    KpiDeliveryManifest,
    KpiDeliverySnapshot,
    KpiDeliveryStatus,
    KpiDeliveryValue,
    KpiTimeseriesManifest,
    KpiTimeseriesPoint,
    KpiTimeseriesSnapshot,
    KpiTimeseriesWindow,
)

KPI_LATEST_DELIVERY_ID = 'latest'
KPI_LATEST_PARTITION_ID = 'kpis'
KPI_LATEST_DOCUMENT_TYPE = 'ada_kpi_latest_delivery'
KPI_LATEST_SCHEMA_VERSION = 1
KPI_TIMESERIES_DELIVERY_ID = 'timeseries'
KPI_TIMESERIES_PARTITION_ID = 'kpis'
KPI_TIMESERIES_DOCUMENT_TYPE = 'ada_kpi_timeseries_delivery'
KPI_TIMESERIES_SCHEMA_VERSION = 1


def project_kpi_latest(
    *,
    evaluation: KpiEvaluation | None,
    configuration: KpiDeliveryConfiguration,
    watermark: KpiWatermark | None,
    published_at_utc: datetime,
) -> KpiDeliverySnapshot:
    if evaluation is not None and not isinstance(evaluation, KpiEvaluation):
        raise TypeError('evaluation must be KpiEvaluation or None')
    if not isinstance(configuration, KpiDeliveryConfiguration):
        raise TypeError('configuration must be KpiDeliveryConfiguration')
    if watermark is not None and not isinstance(watermark, KpiWatermark):
        raise TypeError('watermark must be KpiWatermark or None')
    if evaluation is not None and evaluation.watermark != watermark:
        raise ValueError('evaluation watermark must match delivery watermark')
    results = {} if evaluation is None else {result.key: result for result in evaluation.results}
    destinations: dict[str, dict[str, KpiDeliveryValue]] = {}
    for binding in configuration.latest_bindings:
        value = _latest_value(results.get(binding.key))
        for destination_key in binding.destination_keys:
            destinations.setdefault(destination_key, {})[binding.key] = value
    revision = calculate_kpi_latest_revision(
        watermark=watermark,
        configuration_revision=configuration.revision,
        tool_projection_revision=configuration.tool_projection_revision,
        destinations=destinations,
    )
    return KpiDeliverySnapshot(
        id=KPI_LATEST_DELIVERY_ID,
        partition_id=KPI_LATEST_PARTITION_ID,
        document_type=KPI_LATEST_DOCUMENT_TYPE,
        manifest=KpiDeliveryManifest(
            schema_version=KPI_LATEST_SCHEMA_VERSION,
            watermark=watermark,
            configuration_revision=configuration.revision,
            tool_projection_revision=configuration.tool_projection_revision,
            published_at_utc=published_at_utc,
            revision=revision,
        ),
        destinations=destinations,
    )


def calculate_kpi_latest_revision(
    *,
    watermark: KpiWatermark | None,
    configuration_revision: str,
    tool_projection_revision: str,
    destinations: Mapping[str, Mapping[str, KpiDeliveryValue]],
) -> str:
    payload = {
        'schema_version': KPI_LATEST_SCHEMA_VERSION,
        'watermark_utc': None if watermark is None else watermark.text,
        'configuration_revision': configuration_revision,
        'tool_projection_revision': tool_projection_revision,
        'destinations': {
            destination: {key: value.as_payload() for key, value in values.items()}
            for destination, values in destinations.items()
        },
    }
    return _revision(payload)


def project_kpi_timeseries(
    *,
    points: Iterable[KpiTimeseriesPoint],
    configuration: KpiDeliveryConfiguration,
    end_watermark: KpiWatermark,
    step_seconds: int,
    published_at_utc: datetime,
) -> KpiTimeseriesSnapshot:
    if not isinstance(configuration, KpiDeliveryConfiguration):
        raise TypeError('configuration must be KpiDeliveryConfiguration')
    if not isinstance(end_watermark, KpiWatermark):
        raise TypeError('end_watermark must be KpiWatermark')
    if not isinstance(step_seconds, int) or isinstance(step_seconds, bool) or step_seconds <= 0:
        raise ValueError('step_seconds must be an integer greater than zero')
    normalized_points = tuple(points)
    if not all(isinstance(point, KpiTimeseriesPoint) for point in normalized_points):
        raise TypeError('points must contain KpiTimeseriesPoint values')
    indexed = {(point.timestamp_utc, point.key): point.value for point in normalized_points}
    windows: list[KpiTimeseriesWindow] = []
    bindings_by_hours: dict[int, list[str]] = {}
    for binding in configuration.series_bindings:
        if binding.series_hours is None:
            raise ValueError('series binding requires series_hours')
        bindings_by_hours.setdefault(binding.series_hours, []).append(binding.key)
    for hours, keys in sorted(bindings_by_hours.items()):
        start_utc = end_watermark.timestamp_utc - timedelta(hours=hours)
        timestamps = _timestamps(
            start_utc=start_utc,
            end_utc=end_watermark.timestamp_utc,
            step_seconds=step_seconds,
        )
        rows = tuple(
            tuple(indexed.get((timestamp, key)) for timestamp in timestamps) for key in keys
        )
        windows.append(
            KpiTimeseriesWindow(
                hours=hours,
                start_utc=start_utc,
                keys=tuple(keys),
                values=rows,
            )
        )
    destinations = configuration.destinations_for_series()
    revision = calculate_kpi_timeseries_revision(
        end_watermark=end_watermark,
        step_seconds=step_seconds,
        configuration_revision=configuration.revision,
        tool_projection_revision=configuration.tool_projection_revision,
        destinations=destinations,
        windows=windows,
    )
    return KpiTimeseriesSnapshot(
        id=KPI_TIMESERIES_DELIVERY_ID,
        partition_id=KPI_TIMESERIES_PARTITION_ID,
        document_type=KPI_TIMESERIES_DOCUMENT_TYPE,
        manifest=KpiTimeseriesManifest(
            schema_version=KPI_TIMESERIES_SCHEMA_VERSION,
            watermark=end_watermark,
            configuration_revision=configuration.revision,
            tool_projection_revision=configuration.tool_projection_revision,
            published_at_utc=published_at_utc,
            revision=revision,
        ),
        end_utc=end_watermark.timestamp_utc,
        step_seconds=step_seconds,
        destinations=destinations,
        windows=tuple(windows),
    )


def calculate_kpi_timeseries_revision(
    *,
    end_watermark: KpiWatermark,
    step_seconds: int,
    configuration_revision: str,
    tool_projection_revision: str,
    destinations: Mapping[str, tuple[str, ...]],
    windows: Iterable[KpiTimeseriesWindow],
) -> str:
    payload = {
        'schema_version': KPI_TIMESERIES_SCHEMA_VERSION,
        'watermark_utc': end_watermark.text,
        'step_seconds': step_seconds,
        'configuration_revision': configuration_revision,
        'tool_projection_revision': tool_projection_revision,
        'destinations': {key: list(values) for key, values in destinations.items()},
        'windows': [
            {
                'hours': window.hours,
                'start_utc': _format_utc(window.start_utc),
                'keys': list(window.keys),
                'values': [list(row) for row in window.values],
            }
            for window in windows
        ],
    }
    return _revision(payload)


def _latest_value(result) -> KpiDeliveryValue:
    if result is None:
        return KpiDeliveryValue(
            status=KpiDeliveryStatus.MISSING,
            value_kind=None,
            value=None,
        )
    if result.status is KpiStatus.ERROR:
        return KpiDeliveryValue(
            status=KpiDeliveryStatus.ERROR,
            value_kind=result.value_kind,
            value=None,
        )
    return KpiDeliveryValue(
        status=KpiDeliveryStatus.OK,
        value_kind=result.value_kind,
        value=result.parsed_value if result.value_kind is KpiValueKind.VALUE else result.value,
    )


def _timestamps(
    *,
    start_utc: datetime,
    end_utc: datetime,
    step_seconds: int,
) -> tuple[datetime, ...]:
    duration_seconds = int((end_utc - start_utc).total_seconds())
    if duration_seconds % step_seconds != 0:
        raise ValueError('timeseries window duration must be divisible by step_seconds')
    count = duration_seconds // step_seconds
    step = timedelta(seconds=step_seconds)
    return tuple(start_utc + step * index for index in range(1, count + 1))


def _revision(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()[:16]


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec='microseconds').replace('+00:00', 'Z')
