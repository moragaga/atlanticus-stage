from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any

from atlanticus.integrations.pi.web_api.errors import PiWebApiRequestError, PiWebApiResponseError
from atlanticus.integrations.pi.web_api.settings import PiWebApiSettings
from atlanticus.integrations.pi.web_api.transport import PiWebApiTransport

# PI Web API solo debe devolver los campos físicos que esta integración necesita transportar.
_STREAMSET_SELECTED_FIELDS = 'Items.Name;Items.Items.Timestamp;Items.Items.Value'


class PiStreamSetResource:
    # El recurso comparte transporte y settings con el cliente, pero no decide política de process.
    def __init__(self, *, transport: PiWebApiTransport, settings: PiWebApiSettings) -> None:
        if not isinstance(transport, PiWebApiTransport):
            raise TypeError('transport must be PiWebApiTransport')
        if not isinstance(settings, PiWebApiSettings):
            raise TypeError('settings must be PiWebApiSettings')
        self._transport = transport
        self._settings = settings

    # Cada invocación produce exactamente una petición a streamsets/interpolated.
    def get_interpolated(
        self,
        web_ids: Iterable[str],
        *,
        start_time_utc: datetime,
        end_time_utc: datetime,
        interpolation_seconds: int,
    ) -> tuple[dict[str, Any], ...]:
        normalized_web_ids = _normalize_web_ids(web_ids)
        _validate_web_id_limit(
            web_ids=normalized_web_ids,
            limit=self._settings.limits.interpolated_max_web_ids,
            field_name='interpolated_max_web_ids',
        )
        start_time, end_time = _validate_time_range(start_time_utc, end_time_utc)
        interval = _validate_interpolation_seconds(interpolation_seconds)
        params: list[tuple[str, object]] = [
            ('startTime', start_time.isoformat()),
            ('endTime', end_time.isoformat()),
            ('interval', f'{interval}s'),
            ('selectedFields', _STREAMSET_SELECTED_FIELDS),
        ]
        params.extend(('webId', web_id) for web_id in normalized_web_ids)
        payload = self._transport.get_json('streamsets/interpolated', params=params)
        return _map_streamsets_response(payload)

    # Recorded usa la misma frontera, pero no necesita intervalo de interpolación.
    def get_recorded(
        self,
        web_ids: Iterable[str],
        *,
        start_time_utc: datetime,
        end_time_utc: datetime,
    ) -> tuple[dict[str, Any], ...]:
        normalized_web_ids = _normalize_web_ids(web_ids)
        _validate_web_id_limit(
            web_ids=normalized_web_ids,
            limit=self._settings.limits.recorded_max_web_ids,
            field_name='recorded_max_web_ids',
        )
        start_time, end_time = _validate_time_range(start_time_utc, end_time_utc)
        params: list[tuple[str, object]] = [
            ('startTime', start_time.isoformat()),
            ('endTime', end_time.isoformat()),
            ('selectedFields', _STREAMSET_SELECTED_FIELDS),
        ]
        params.extend(('webId', web_id) for web_id in normalized_web_ids)
        payload = self._transport.get_json('streamsets/recorded', params=params)
        return _map_streamsets_response(payload)


# Esta validación no divide lotes: solo protege el contrato de una petición ya planificada.
def _normalize_web_ids(values: Iterable[str]) -> tuple[str, ...]:
    if isinstance(values, str | bytes):
        raise PiWebApiRequestError('web_ids must be an iterable of text values')
    try:
        web_ids = tuple(values)
    except TypeError:
        raise PiWebApiRequestError('web_ids must be an iterable of text values') from None
    if not web_ids:
        raise PiWebApiRequestError('web_ids must not be empty')

    seen: set[str] = set()
    for value in web_ids:
        if not isinstance(value, str) or not value:
            raise PiWebApiRequestError('web_ids must contain non-empty text values')
        if value != value.strip():
            raise PiWebApiRequestError('web_ids must not contain surrounding whitespace')
        if value in seen:
            raise PiWebApiRequestError('web_ids must not contain duplicate values')
        seen.add(value)
    return web_ids


def _validate_web_id_limit(*, web_ids: tuple[str, ...], limit: int, field_name: str) -> None:
    if len(web_ids) > limit:
        raise PiWebApiRequestError(
            f'StreamSet request exceeds configured {field_name} limit of {limit}'
        )


# El process debe entregar una ventana UTC ya resuelta; esta capa no calcula ni recorta tiempos.
def _validate_time_range(
    start_time_utc: datetime,
    end_time_utc: datetime,
) -> tuple[datetime, datetime]:
    start_time = _require_utc_datetime(start_time_utc, 'start_time_utc')
    end_time = _require_utc_datetime(end_time_utc, 'end_time_utc')
    if start_time >= end_time:
        raise PiWebApiRequestError('start_time_utc must be earlier than end_time_utc')
    return start_time, end_time


def _require_utc_datetime(value: Any, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise PiWebApiRequestError(f'{field_name} must be a datetime')
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise PiWebApiRequestError(f'{field_name} must be timezone-aware UTC')
    return value


def _validate_interpolation_seconds(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PiWebApiRequestError('interpolation_seconds must be an integer greater than zero')
    return value


# Una respuesta HTTP válida puede contener tags o muestras defectuosas; se conservan las válidas.
def _map_streamsets_response(payload: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(payload, Mapping):
        raise PiWebApiResponseError('PI Web API streamsets response must be a JSON object')
    items = payload.get('Items')
    if not isinstance(items, list):
        raise PiWebApiResponseError('PI Web API streamsets response must contain an Items array')

    records: list[dict[str, Any]] = []
    for stream in items:
        if not isinstance(stream, Mapping):
            continue
        name = _valid_name(stream.get('Name'))
        if name is None:
            continue
        points = stream.get('Items')
        if not isinstance(points, list):
            continue
        for point in points:
            if not isinstance(point, Mapping):
                continue
            timestamp = _valid_timestamp(point.get('Timestamp'))
            if timestamp is None:
                continue
            records.append(
                {
                    'name': name,
                    'timestamp': timestamp,
                    'value': _normalize_pi_value(point.get('Value')),
                }
            )
    return tuple(records)


# Name debe llegar intacto porque una capa posterior lo cruzará con PiTagDefinition.tag_name.
def _valid_name(value: Any) -> str | None:
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    return value


# Se valida que la muestra tenga una marca temporal utilizable, pero se preserva el texto original.
def _valid_timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    candidate = value[:-1] + '+00:00' if value.endswith('Z') else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return value


# Los estados de sistema y estructuras no interpretables representan ausencia de dato.
# Eso no convierte una respuesta válida de PI en un error de proceso.
def _normalize_pi_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Mapping):
        if value.get('IsSystem') is True:
            return None
        if 'Value' not in value:
            return None
        return _normalize_pi_value(value.get('Value'))
    if isinstance(value, list | tuple):
        return None
    return value
