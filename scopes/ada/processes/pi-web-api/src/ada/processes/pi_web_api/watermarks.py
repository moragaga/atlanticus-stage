from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from ada.processes.pi_web_api.errors import PiWebApiWatermarkError
from atlanticus.state import AtomicStateStore, StateKey

_PRODUCER_KEY = StateKey(namespace=('producers',), name='pi-web-api')
_SOURCE_KEY = StateKey(namespace=('sources',), name='pi-web-api')


@dataclass(frozen=True, slots=True)
class PiProducerWatermark:
    committed_watermark_utc: datetime | None


@dataclass(frozen=True, slots=True)
class PiSourceWatermark:
    source_watermark_utc: datetime | None


class PiProducerState:
    def __init__(self, *, store: AtomicStateStore) -> None:
        if not isinstance(store, AtomicStateStore):
            raise TypeError('store must be an AtomicStateStore')
        self._store = store
        self._current: PiProducerWatermark | None = None

    def current(self) -> PiProducerWatermark:
        if self._current is None:
            document = self._store.read(_PRODUCER_KEY)
            self._current = (
                PiProducerWatermark(committed_watermark_utc=None)
                if document is None
                else _producer_from_value(document.value)
            )
        return self._current

    def commit(self, value: datetime) -> PiProducerWatermark:
        watermark = _normalize_utc_second(value, field_name='committed_watermark_utc')
        current = self.current()
        if (
            current.committed_watermark_utc is not None
            and watermark < current.committed_watermark_utc
        ):
            raise PiWebApiWatermarkError('committed watermark must not move backwards')
        if current.committed_watermark_utc == watermark:
            return current
        updated = PiProducerWatermark(committed_watermark_utc=watermark)
        self._store.replace(
            _PRODUCER_KEY,
            {
                'producer': 'pi-web-api',
                'committed_watermark_utc': _format_utc_second(watermark),
            },
        )
        self._current = updated
        return updated


class PiSourceState:
    def __init__(self, *, store: AtomicStateStore) -> None:
        if not isinstance(store, AtomicStateStore):
            raise TypeError('store must be an AtomicStateStore')
        self._store = store
        self._current: PiSourceWatermark | None = None

    def current(self) -> PiSourceWatermark:
        if self._current is None:
            document = self._store.read(_SOURCE_KEY)
            self._current = (
                PiSourceWatermark(source_watermark_utc=None)
                if document is None
                else _source_from_value(document.value)
            )
        return self._current

    def publish(self, value: datetime) -> PiSourceWatermark:
        watermark = _normalize_utc_second(value, field_name='source_watermark_utc')
        current = self.current()
        if current.source_watermark_utc is not None and watermark < current.source_watermark_utc:
            raise PiWebApiWatermarkError('source watermark must not move backwards')
        if current.source_watermark_utc == watermark:
            return current
        updated = PiSourceWatermark(source_watermark_utc=watermark)
        self._store.replace(
            _SOURCE_KEY,
            {
                'source': 'pi-web-api',
                'source_watermark_utc': _format_utc_second(watermark),
            },
        )
        self._current = updated
        return updated


class PiWatermarkCoordinator:
    def __init__(self, *, producer: PiProducerState, source: PiSourceState) -> None:
        if not isinstance(producer, PiProducerState):
            raise TypeError('producer must be a PiProducerState')
        if not isinstance(source, PiSourceState):
            raise TypeError('source must be a PiSourceState')
        self._producer = producer
        self._source = source

    def commit_materialized(self, value: datetime) -> tuple[PiSourceWatermark, PiProducerWatermark]:
        source = self._source.publish(value)
        producer = self._producer.commit(value)
        return source, producer


def _producer_from_value(value) -> PiProducerWatermark:
    _require_exact_fields(value, {'producer', 'committed_watermark_utc'}, kind='producer')
    if value.get('producer') != 'pi-web-api':
        raise PiWebApiWatermarkError('PI Web API producer state has an invalid producer')
    return PiProducerWatermark(
        committed_watermark_utc=_optional_utc_second(
            value.get('committed_watermark_utc'),
            field_name='committed_watermark_utc',
        )
    )


def _source_from_value(value) -> PiSourceWatermark:
    _require_exact_fields(value, {'source', 'source_watermark_utc'}, kind='source')
    if value.get('source') != 'pi-web-api':
        raise PiWebApiWatermarkError('PI Web API source state has an invalid source')
    return PiSourceWatermark(
        source_watermark_utc=_optional_utc_second(
            value.get('source_watermark_utc'),
            field_name='source_watermark_utc',
        )
    )


def _require_exact_fields(value, fields: set[str], *, kind: Literal['producer', 'source']) -> None:
    if not hasattr(value, 'keys') or set(value.keys()) != fields:
        raise PiWebApiWatermarkError(f'PI Web API {kind} state has unexpected or missing fields')


def _optional_utc_second(value, *, field_name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PiWebApiWatermarkError(f'{field_name} must be a string or null')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as error:
        raise PiWebApiWatermarkError(f'{field_name} is invalid') from error
    return _require_stored_utc_second(parsed, field_name=field_name)


def _normalize_utc_second(value: datetime, *, field_name: str) -> datetime:
    normalized = _require_utc(value, field_name=field_name)
    return normalized.replace(microsecond=0)


def _require_stored_utc_second(value: datetime, *, field_name: str) -> datetime:
    normalized = _require_utc(value, field_name=field_name)
    if normalized.microsecond != 0:
        raise PiWebApiWatermarkError(f'{field_name} must not contain microseconds')
    return normalized


def _require_utc(value: datetime, *, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise PiWebApiWatermarkError(f'{field_name} must be a timezone-aware datetime')
    if value.utcoffset() != timedelta(0):
        raise PiWebApiWatermarkError(f'{field_name} must use UTC')
    return value.astimezone(UTC)


def _format_utc_second(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec='seconds').replace('+00:00', 'Z')
