from __future__ import annotations

from dataclasses import dataclass

from atlanticus.data_producers.remanentes.errors import RemanentesContractError


@dataclass(frozen=True, slots=True)
class StockMetricDefinition:
    source_value: str
    metric_key: str

    def __post_init__(self) -> None:
        source_value = _required_text(self.source_value, 'source_value').upper()
        metric_key = _required_route_segment(self.metric_key, 'metric_key')
        object.__setattr__(self, 'source_value', source_value)
        object.__setattr__(self, 'metric_key', metric_key)


def validate_stock_metrics(metrics: tuple[StockMetricDefinition, ...]) -> None:
    resolved = tuple(metrics)
    if not resolved or not all(isinstance(item, StockMetricDefinition) for item in resolved):
        raise RemanentesContractError('stock_metrics must contain StockMetricDefinition values')
    source_values = tuple(item.source_value for item in resolved)
    metric_keys = tuple(item.metric_key for item in resolved)
    if len(set(source_values)) != len(source_values):
        raise RemanentesContractError('stock source values must be unique')
    if len(set(metric_keys)) != len(metric_keys):
        raise RemanentesContractError('stock metric keys must be unique')


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RemanentesContractError(f'{field_name} must be a non-empty string')
    return value.strip()


def _required_route_segment(value: object, field_name: str) -> str:
    normalized = _required_text(value, field_name).lower()
    if not normalized.replace('_', '').replace('-', '').isalnum():
        raise RemanentesContractError(
            f'{field_name} must contain only letters, numbers, hyphens or underscores'
        )
    return normalized
