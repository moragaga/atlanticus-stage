# Lee el watermark del proveedor PI seleccionado sin importar paquetes concretos de productores.
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from ada.kpis.core import KpiSource, KpiWatermark
from ada.kpis.sources import PiSourceProvider
from ada.processes.kpis.errors import KpiProcessWatermarkError
from atlanticus.state import AtomicStateStore, StateKey

_PI_WEB_API_SOURCE_KEY = StateKey(namespace=('sources',), name='pi-web-api')
_NOTPII_PRODUCER_KEY = StateKey(namespace=('producers',), name='notpii')
_NOTPII_INTERPOLATED = 'interpolated'
_NOTPII_RECORDED = 'recorded'


@dataclass(frozen=True, slots=True)
class PiClockSnapshot:
    watermark: KpiWatermark | None
    source_watermarks: Mapping[KpiSource, KpiWatermark | None]

    def __post_init__(self) -> None:
        if self.watermark is not None and not isinstance(self.watermark, KpiWatermark):
            raise TypeError('watermark must be KpiWatermark or None')
        if not isinstance(self.source_watermarks, Mapping):
            raise TypeError('source_watermarks must be a mapping')
        normalized: dict[KpiSource, KpiWatermark | None] = {}
        for source, watermark in self.source_watermarks.items():
            if not isinstance(source, KpiSource):
                raise TypeError('source_watermarks keys must be KpiSource values')
            if watermark is not None and not isinstance(watermark, KpiWatermark):
                raise TypeError(f'{source.value}: watermark must be KpiWatermark or None')
            normalized[source] = watermark
        object.__setattr__(self, 'source_watermarks', MappingProxyType(normalized))


@runtime_checkable
class PiClock(Protocol):
    def current(self) -> PiClockSnapshot: ...


class StatePiClock:
    def __init__(self, *, store: AtomicStateStore, provider: PiSourceProvider) -> None:
        if not isinstance(store, AtomicStateStore):
            raise TypeError('store must be AtomicStateStore')
        if not isinstance(provider, PiSourceProvider):
            raise TypeError('provider must be PiSourceProvider')
        self._store = store
        self._provider = provider

    def current(self) -> PiClockSnapshot:
        if self._provider is PiSourceProvider.PI_WEB_API:
            return self._pi_web_api_snapshot()
        return self._notpii_snapshot()

    def _pi_web_api_snapshot(self) -> PiClockSnapshot:
        document = self._store.read(_PI_WEB_API_SOURCE_KEY)
        if document is None:
            return _empty_snapshot()
        value = document.value
        _require_exact_fields(
            value,
            {'source', 'source_watermark_utc'},
            context='PI Web API source state',
        )
        if value.get('source') != 'pi-web-api':
            raise KpiProcessWatermarkError('PI Web API source state has an invalid source')
        watermark = _optional_watermark(
            value.get('source_watermark_utc'),
            context='PI Web API source watermark',
        )
        return PiClockSnapshot(
            watermark=watermark,
            source_watermarks={
                KpiSource.PI_INTERPOLATED: watermark,
                KpiSource.PI_RECORDED: watermark,
            },
        )

    def _notpii_snapshot(self) -> PiClockSnapshot:
        document = self._store.read(_NOTPII_PRODUCER_KEY)
        if document is None:
            return _empty_snapshot()
        value = document.value
        if value.get('producer') != 'notpii':
            raise KpiProcessWatermarkError('NOTPII producer state has an invalid producer')
        streams = value.get('streams')
        if not isinstance(streams, Mapping):
            raise KpiProcessWatermarkError('NOTPII producer state streams must be a mapping')
        interpolated = _notpii_stream_watermark(
            streams,
            mode=_NOTPII_INTERPOLATED,
        )
        recorded = _notpii_optional_recorded_watermark(streams)
        return PiClockSnapshot(
            watermark=interpolated,
            source_watermarks={
                KpiSource.PI_INTERPOLATED: interpolated,
                KpiSource.PI_RECORDED: recorded,
            },
        )


def _notpii_optional_recorded_watermark(
    streams: Mapping[object, object],
) -> KpiWatermark | None:
    return _notpii_stream_watermark(streams, mode=_NOTPII_RECORDED)


def _notpii_stream_watermark(
    streams: Mapping[object, object],
    *,
    mode: str,
) -> KpiWatermark | None:
    raw_stream = streams.get(mode)
    if raw_stream is None:
        return None
    if not isinstance(raw_stream, Mapping):
        raise KpiProcessWatermarkError(f'NOTPII {mode} stream state must be a mapping')
    return _optional_watermark(
        raw_stream.get('source_watermark_utc'),
        context=f'NOTPII {mode} source watermark',
    )


def _optional_watermark(value: object, *, context: str) -> KpiWatermark | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise KpiProcessWatermarkError(f'{context} must be a string or null')
    try:
        return KpiWatermark.parse(value)
    except ValueError as error:
        raise KpiProcessWatermarkError(f'{context} is invalid') from error


def _require_exact_fields(
    value: Mapping[str, object],
    fields: set[str],
    *,
    context: str,
) -> None:
    if set(value) != fields:
        raise KpiProcessWatermarkError(f'{context} has unexpected or missing fields')


def _empty_snapshot() -> PiClockSnapshot:
    return PiClockSnapshot(
        watermark=None,
        source_watermarks={
            KpiSource.PI_INTERPOLATED: None,
            KpiSource.PI_RECORDED: None,
        },
    )
