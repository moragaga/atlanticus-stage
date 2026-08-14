# Recurso points: cada invocación ejecuta exactamente una solicitud lógica.
# No hace chunks ni reintentos; esas políticas pertenecen al process.
# El path se construye aquí porque la conexión conoce pi_server y el catálogo tag_name.

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from atlanticus.integrations.pi.web_api.errors import PiWebApiRequestError, PiWebApiResponseError
from atlanticus.integrations.pi.web_api.models import PiPointWebIdResult
from atlanticus.integrations.pi.web_api.settings import PiWebApiSettings
from atlanticus.integrations.pi.web_api.transport import PiWebApiTransport

_POINT_SELECTED_FIELDS = (
    'Items.Identifier;'
    'Items.IdentifierType;'
    'Items.Errors;'
    'Items.Object.Name;'
    'Items.Object.WebId;'
    'Items.Object.Path;'
    'Items.Object.Errors'
)


class PiPointResource:
    def __init__(self, *, transport: PiWebApiTransport, settings: PiWebApiSettings) -> None:
        if not isinstance(transport, PiWebApiTransport):
            raise TypeError('transport must be PiWebApiTransport')
        if not isinstance(settings, PiWebApiSettings):
            raise TypeError('settings must be PiWebApiSettings')
        self._transport = transport
        self._settings = settings

    def resolve_web_ids(self, tag_names: Iterable[str]) -> tuple[PiPointWebIdResult, ...]:
        normalized_tag_names = _normalize_tag_names(tag_names)
        limit = self._settings.limits.points_max_paths
        if len(normalized_tag_names) > limit:
            raise PiWebApiRequestError(
                f'Point request exceeds configured points_max_paths limit of {limit}'
            )

        requested = tuple(
            (tag_name, _build_point_path(self._settings.pi_server, tag_name))
            for tag_name in normalized_tag_names
        )
        params: list[tuple[str, object]] = [
            ('selectedFields', _POINT_SELECTED_FIELDS),
            ('asParallel', 'true'),
        ]
        params.extend(('path', path) for _, path in requested)
        payload = self._transport.get_json('points/multiple', params=params)
        return _map_points_response(requested=requested, payload=payload)


def _normalize_tag_names(values: Iterable[str]) -> tuple[str, ...]:
    if isinstance(values, str | bytes):
        raise PiWebApiRequestError('tag_names must be an iterable of text values')
    try:
        tag_names = tuple(values)
    except TypeError:
        raise PiWebApiRequestError('tag_names must be an iterable of text values') from None
    if not tag_names:
        raise PiWebApiRequestError('tag_names must not be empty')

    normalized: list[str] = []
    seen: set[str] = set()
    for value in tag_names:
        if not isinstance(value, str) or not value:
            raise PiWebApiRequestError('tag_names must contain non-empty text values')
        if value != value.strip():
            raise PiWebApiRequestError('tag_names must not contain surrounding whitespace')
        key = value.casefold()
        if key in seen:
            raise PiWebApiRequestError('tag_names must not contain duplicate values')
        seen.add(key)
        normalized.append(value)
    return tuple(normalized)


def _build_point_path(pi_server: str, tag_name: str) -> str:
    return f'\\\\{pi_server}\\{tag_name}'


def _map_points_response(
    *,
    requested: tuple[tuple[str, str], ...],
    payload: Any,
) -> tuple[PiPointWebIdResult, ...]:
    if not isinstance(payload, Mapping):
        raise PiWebApiResponseError('PI Web API points response must be a JSON object')
    items = payload.get('Items')
    if not isinstance(items, list):
        raise PiWebApiResponseError('PI Web API points response must contain an Items array')
    if len(items) > len(requested):
        raise PiWebApiResponseError('PI Web API points response contains unexpected extra items')

    indexes_by_path = {path.casefold(): index for index, (_, path) in enumerate(requested)}
    results: list[PiPointWebIdResult | None] = [None] * len(requested)

    for item_index, item in enumerate(items):
        target_index = _resolve_target_index(
            item=item,
            item_index=item_index,
            requested=requested,
            indexes_by_path=indexes_by_path,
            results=results,
        )
        if target_index is None:
            raise PiWebApiResponseError('PI Web API points response could not be correlated')
        if results[target_index] is not None:
            raise PiWebApiResponseError('PI Web API points response contains duplicate items')
        tag_name, requested_path = requested[target_index]
        results[target_index] = _map_point_item(
            tag_name=tag_name,
            requested_path=requested_path,
            item=item,
        )

    for index, result in enumerate(results):
        if result is None:
            tag_name, path = requested[index]
            results[index] = PiPointWebIdResult(
                tag_name=tag_name,
                path=path,
                point_name=None,
                web_id=None,
                error='PI Web API did not return this requested tag',
            )

    return tuple(result for result in results if result is not None)


def _resolve_target_index(
    *,
    item: Any,
    item_index: int,
    requested: tuple[tuple[str, str], ...],
    indexes_by_path: dict[str, int],
    results: list[PiPointWebIdResult | None],
) -> int | None:
    if isinstance(item, Mapping):
        point = _extract_point(item)
        for candidate in (item.get('Identifier'), point.get('Path')):
            text = _optional_text(candidate)
            if text is not None:
                target = indexes_by_path.get(text.casefold())
                if target is not None:
                    return target
    if item_index < len(requested) and results[item_index] is None:
        return item_index
    return next((index for index, result in enumerate(results) if result is None), None)


def _map_point_item(
    *,
    tag_name: str,
    requested_path: str,
    item: Any,
) -> PiPointWebIdResult:
    if not isinstance(item, Mapping):
        return PiPointWebIdResult(
            tag_name=tag_name,
            path=requested_path,
            point_name=None,
            web_id=None,
            error='PI Web API point item must be a JSON object',
        )

    point = _extract_point(item)
    path = (
        _optional_text(point.get('Path'))
        or _optional_text(item.get('Identifier'))
        or requested_path
    )
    point_name = _optional_text(point.get('Name'))
    web_id = _optional_text(point.get('WebId'))
    if web_id is not None:
        return PiPointWebIdResult(
            tag_name=tag_name,
            path=path,
            point_name=point_name,
            web_id=web_id,
        )

    return PiPointWebIdResult(
        tag_name=tag_name,
        path=path,
        point_name=point_name,
        web_id=None,
        error=_point_error(item=item, point=point),
    )


def _extract_point(item: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ('Object', 'Content'):
        value = item.get(key)
        if isinstance(value, Mapping):
            return value
    return item


def _point_error(*, item: Mapping[str, Any], point: Mapping[str, Any]) -> str:
    errors = item.get('Errors') or point.get('Errors')
    if errors is not None:
        text = _error_text(errors)
        if text:
            return text
    return 'PI Web API did not return WebId for this tag'


def _error_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list | tuple):
        return '; '.join(text for item in value if (text := _optional_text(item)) is not None)
    if isinstance(value, Mapping):
        return '; '.join(f'{key}: {item}' for key, item in value.items())
    return str(value).strip()


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    normalized = value.strip()
    return normalized or None
