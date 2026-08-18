from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique

from atlanticus.data_producers.fabrica.errors import FabricaContractError


class FabricaValueKind(StrEnum):
    NUMBER = 'number'
    INTEGER = 'integer'
    TEXT = 'text'
    BOOLEAN = 'boolean'
    DATETIME = 'datetime'


class FabricaPlanPartition(StrEnum):
    DAY = 'day'
    WEEKLY = 'weekly'


@unique
class FabricaKpiLevel(StrEnum):
    DAY = 'DAY'
    SEVEN_LAST_DAYS = '7LD'


@dataclass(frozen=True, slots=True)
class PlanPartitionDefinition:
    key: FabricaPlanPartition
    source_value: str
    route_segment: str

    def __post_init__(self) -> None:
        if not isinstance(self.key, FabricaPlanPartition):
            raise FabricaContractError('key must be a FabricaPlanPartition')
        object.__setattr__(self, 'source_value', _required_text(self.source_value, 'source_value'))
        object.__setattr__(
            self,
            'route_segment',
            _required_route_segment(self.route_segment, 'route_segment'),
        )


@dataclass(frozen=True, slots=True)
class PlanMetricDefinition:
    id_kpi: str
    metric_key: str
    value_kind: FabricaValueKind
    partitions: tuple[FabricaPlanPartition, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, 'id_kpi', _required_text(self.id_kpi, 'id_kpi').upper())
        object.__setattr__(
            self, 'metric_key', _required_route_segment(self.metric_key, 'metric_key')
        )
        if not isinstance(self.value_kind, FabricaValueKind):
            raise FabricaContractError('value_kind must be a FabricaValueKind')
        partitions = tuple(self.partitions)
        if not partitions or not all(isinstance(item, FabricaPlanPartition) for item in partitions):
            raise FabricaContractError('partitions must contain FabricaPlanPartition values')
        if len(set(partitions)) != len(partitions):
            raise FabricaContractError('partitions must not contain duplicates')
        object.__setattr__(self, 'partitions', partitions)


@dataclass(frozen=True, slots=True)
class KpiMetricDefinition:
    id_kpi: str
    metric_key: str
    value_kind: FabricaValueKind

    def __post_init__(self) -> None:
        object.__setattr__(self, 'id_kpi', _required_text(self.id_kpi, 'id_kpi').upper())
        object.__setattr__(
            self, 'metric_key', _required_route_segment(self.metric_key, 'metric_key')
        )
        if not isinstance(self.value_kind, FabricaValueKind):
            raise FabricaContractError('value_kind must be a FabricaValueKind')


@dataclass(frozen=True, slots=True)
class KpiDatasetDefinition:
    name: str
    level: FabricaKpiLevel
    route_segment: str
    metrics: tuple[KpiMetricDefinition, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, 'name', _required_route_segment(self.name, 'name'))
        if not isinstance(self.level, FabricaKpiLevel):
            raise FabricaContractError('level must be a FabricaKpiLevel')
        object.__setattr__(
            self,
            'route_segment',
            _required_route_segment(self.route_segment, 'route_segment'),
        )
        metrics = tuple(self.metrics)
        _validate_metric_catalog(metrics)
        object.__setattr__(self, 'metrics', metrics)


def validate_plan_catalog(
    *,
    partitions: tuple[PlanPartitionDefinition, ...],
    metrics: tuple[PlanMetricDefinition, ...],
) -> None:
    _validate_partition_catalog(partitions)
    _validate_metric_catalog(metrics)
    known = {item.key for item in partitions}
    if any(partition not in known for metric in metrics for partition in metric.partitions):
        raise FabricaContractError('plan metric partitions must be declared in the plan catalog')


def validate_kpi_catalog(*, datasets: tuple[KpiDatasetDefinition, ...]) -> None:
    resolved = tuple(datasets)
    names = tuple(item.name for item in resolved)
    levels = tuple(item.level for item in resolved)
    route_segments = tuple(item.route_segment for item in resolved)
    if len(set(names)) != len(names):
        raise FabricaContractError('kpi dataset names must be unique')
    if len(set(levels)) != len(levels):
        raise FabricaContractError('kpi levels must belong to exactly one dataset')
    if len(set(route_segments)) != len(route_segments):
        raise FabricaContractError('kpi dataset route segments must be unique')
    definitions: dict[str, KpiMetricDefinition] = {}
    for dataset in resolved:
        for metric in dataset.metrics:
            current = definitions.get(metric.id_kpi)
            if current is None:
                definitions[metric.id_kpi] = metric
                continue
            if current != metric:
                raise FabricaContractError(
                    'the same kpi id must reuse the same metric definition across datasets'
                )


def _validate_partition_catalog(values: tuple[object, ...]) -> None:
    partitions = tuple(values)
    if not partitions:
        raise FabricaContractError('partition catalog must not be empty')
    keys = tuple(getattr(item, 'key', None) for item in partitions)
    source_values = tuple(str(getattr(item, 'source_value', '')).upper() for item in partitions)
    route_segments = tuple(getattr(item, 'route_segment', None) for item in partitions)
    if len(set(keys)) != len(keys):
        raise FabricaContractError('partition keys must be unique')
    if len(set(source_values)) != len(source_values):
        raise FabricaContractError('source_value must belong to exactly one partition')
    if len(set(route_segments)) != len(route_segments):
        raise FabricaContractError('partition route segments must be unique')


def _validate_metric_catalog(values: tuple[object, ...]) -> None:
    metrics = tuple(values)
    ids = tuple(getattr(item, 'id_kpi', None) for item in metrics)
    keys = tuple(getattr(item, 'metric_key', None) for item in metrics)
    if len(set(ids)) != len(ids):
        raise FabricaContractError('metric id_kpi values must be unique')
    if len(set(keys)) != len(keys):
        raise FabricaContractError('metric_key values must be unique')


def _required_text(value: str | None, field_name: str) -> str:
    normalized = None if value is None else str(value).strip()
    if not normalized:
        raise FabricaContractError(f'{field_name} is required')
    return normalized


def _required_route_segment(value: str | None, field_name: str) -> str:
    normalized = _required_text(value, field_name).lower()
    if not normalized.replace('_', '').replace('-', '').isalnum():
        raise FabricaContractError(
            f'{field_name} must contain only letters, numbers, hyphens or underscores'
        )
    return normalized
