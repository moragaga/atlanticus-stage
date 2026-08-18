# Espejo comentado del Data Producer Fábrica. La lógica ejecutable es idéntica al source productivo; este archivo existe para revisión en español.
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from re import Pattern
from zoneinfo import ZoneInfo

from atlanticus.data_producers.fabrica.contracts import (
    KpiDatasetDefinition,
    KpiMetricDefinition,
    PlanMetricDefinition,
    PlanPartitionDefinition,
    validate_kpi_catalog,
    validate_plan_catalog,
)


@dataclass(frozen=True, slots=True)
class FabricaSourceBlob:
    name: str
    source_file_timestamp_utc: datetime
    size: int | None
    etag: str | None
    last_modified_utc: datetime | None


@dataclass(frozen=True, slots=True)
class FabricaPlanStreamDefinition:
    source_prefix: str
    source_filename_pattern: Pattern[str]
    output_route_segment: str
    partitions: tuple[PlanPartitionDefinition, ...]
    metrics: tuple[PlanMetricDefinition, ...]
    source_partition_timezone_name: str = 'America/Santiago'
    source_file_timezone_name: str = 'UTC'
    stream_key: str = field(default='planes', init=False)

    def __post_init__(self) -> None:
        validate_plan_catalog(partitions=self.partitions, metrics=self.metrics)
        _validate_stream(self)

    def source_day_prefix(self, value: datetime) -> str:
        return _source_day_prefix(self, value)


@dataclass(frozen=True, slots=True)
class FabricaKpiStreamDefinition:
    source_prefix: str
    source_filename_pattern: Pattern[str]
    output_route_segment: str
    datasets: tuple[KpiDatasetDefinition, ...]
    source_partition_timezone_name: str = 'America/Santiago'
    source_file_timezone_name: str = 'UTC'
    stream_key: str = field(default='kpis', init=False)

    def __post_init__(self) -> None:
        datasets = tuple(self.datasets)
        validate_kpi_catalog(datasets=datasets)
        object.__setattr__(self, 'datasets', datasets)
        _validate_stream(self)

    @property
    def metrics(self) -> tuple[KpiMetricDefinition, ...]:
        ordered: dict[object, KpiMetricDefinition] = {}
        for dataset in self.datasets:
            for metric in dataset.metrics:
                ordered.setdefault(metric.id_kpi, metric)
        return tuple(ordered.values())

    def source_day_prefix(self, value: datetime) -> str:
        return _source_day_prefix(self, value)


type FabricaStreamDefinition = FabricaPlanStreamDefinition | FabricaKpiStreamDefinition


def parse_source_file_timestamp(
    *,
    definition: FabricaStreamDefinition,
    blob_name: str,
) -> datetime | None:
    match = definition.source_filename_pattern.search(blob_name)
    if match is None:
        return None
    try:
        parsed = datetime.strptime(match.group('file_timestamp'), '%Y%m%d%H%M%S')
    except IndexError, ValueError:
        return None
    timezone = ZoneInfo(definition.source_file_timezone_name)
    return parsed.replace(tzinfo=timezone).astimezone(UTC)


def _source_day_prefix(definition: FabricaStreamDefinition, value: datetime) -> str:
    normalized = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    local_date: date = normalized.astimezone(
        ZoneInfo(definition.source_partition_timezone_name)
    ).date()
    prefix = definition.source_prefix.strip().strip('/')
    return f'{prefix}/year={local_date:%Y}/month={local_date:%m}/day={local_date:%d}/'


def _validate_stream(definition: FabricaStreamDefinition) -> None:
    if not definition.source_prefix.strip().strip('/'):
        raise ValueError('source_prefix is required')
    if not definition.output_route_segment.strip().strip('/'):
        raise ValueError('output_route_segment is required')
    ZoneInfo(definition.source_partition_timezone_name)
    ZoneInfo(definition.source_file_timezone_name)
