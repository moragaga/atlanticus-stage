from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from re import Pattern
from zoneinfo import ZoneInfo

from atlanticus.data_producers.remanentes.contracts import (
    StockMetricDefinition,
    validate_stock_metrics,
)
from atlanticus.data_producers.remanentes.errors import RemanentesContractError


@dataclass(frozen=True, slots=True)
class RemanentesSourceBlob:
    name: str
    source_file_timestamp_utc: datetime
    size: int | None
    etag: str | None
    last_modified_utc: datetime | None


@dataclass(frozen=True, slots=True)
class RemanentesStocksStreamDefinition:
    stream_key: str
    source_prefix: str
    source_filename_pattern: Pattern[str]
    stock_metrics: tuple[StockMetricDefinition, ...]
    source_timezone_name: str = 'America/Santiago'

    def __post_init__(self) -> None:
        _validate_stream(self)
        metrics = tuple(self.stock_metrics)
        validate_stock_metrics(metrics)
        object.__setattr__(self, 'stock_metrics', metrics)

    def source_day_prefix(self, value: date) -> str:
        return _source_day_prefix(self, value)

    def source_local_date(self, value: datetime) -> date:
        return _source_local_date(self, value)


@dataclass(frozen=True, slots=True)
class RemanentesRowsStreamDefinition:
    stream_key: str
    source_prefix: str
    source_filename_pattern: Pattern[str]
    source_timezone_name: str = 'America/Santiago'

    def __post_init__(self) -> None:
        _validate_stream(self)

    def source_day_prefix(self, value: date) -> str:
        return _source_day_prefix(self, value)

    def source_local_date(self, value: datetime) -> date:
        return _source_local_date(self, value)


type RemanentesStreamDefinition = RemanentesStocksStreamDefinition | RemanentesRowsStreamDefinition


def parse_source_timestamp(
    *,
    definition: RemanentesStreamDefinition,
    blob_name: str,
) -> datetime | None:
    match = definition.source_filename_pattern.search(blob_name)
    if match is None:
        return None
    try:
        partition_date = f'{match.group("year")}{match.group("month")}{match.group("day")}'
        file_date = match.group('file_date')
        if file_date != partition_date:
            return None
        parsed = datetime.strptime(
            f'{file_date}{match.group("file_time")}',
            '%Y%m%d%H%M',
        )
    except IndexError, ValueError:
        return None
    timezone = ZoneInfo(definition.source_timezone_name)
    return parsed.replace(tzinfo=timezone).astimezone(UTC)


def _validate_stream(definition: RemanentesStreamDefinition) -> None:
    stream_key = _required_route_segment(definition.stream_key, 'stream_key')
    source_prefix = _required_source_prefix(definition.source_prefix)
    pattern = definition.source_filename_pattern
    if not hasattr(pattern, 'search'):
        raise RemanentesContractError('source_filename_pattern must be a compiled pattern')
    timezone_name = _required_text(definition.source_timezone_name, 'source_timezone_name')
    try:
        ZoneInfo(timezone_name)
    except Exception:
        raise RemanentesContractError(
            'source_timezone_name must be a valid IANA timezone'
        ) from None
    object.__setattr__(definition, 'stream_key', stream_key)
    object.__setattr__(definition, 'source_prefix', source_prefix)
    object.__setattr__(definition, 'source_timezone_name', timezone_name)


def _source_day_prefix(definition: RemanentesStreamDefinition, value: date) -> str:
    return f'{definition.source_prefix}/year={value:%Y}/month={value:%m}/day={value:%d}/'


def _source_local_date(definition: RemanentesStreamDefinition, value: datetime) -> date:
    normalized = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return normalized.astimezone(ZoneInfo(definition.source_timezone_name)).date()


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


def _required_source_prefix(value: object) -> str:
    normalized = _required_text(value, 'source_prefix').strip('/')
    if not normalized:
        raise RemanentesContractError('source_prefix must be a non-empty path')
    return normalized
