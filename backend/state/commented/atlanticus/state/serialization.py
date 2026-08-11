# La normalización evita que JSON cambie tipos silenciosamente y garantiza firmas estables.
"""Normalización JSON estricta y determinística."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any

from atlanticus.state.errors import StateCorruptionError, StateValidationError

type JsonScalar = None | bool | int | float | str
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]
type FrozenJsonValue = JsonScalar | tuple[FrozenJsonValue, ...] | Mapping[str, FrozenJsonValue]
type FrozenJsonObject = Mapping[str, FrozenJsonValue]

_MAX_JSON_DEPTH = 32


# La copia también desacopla el documento persistido de mutaciones posteriores del llamador.
def normalize_json_object(value: Mapping[str, Any]) -> JsonObject:
    """Copia y valida un objeto para impedir conversiones JSON silenciosas."""

    if not isinstance(value, Mapping):
        raise StateValidationError('state value must be a mapping')
    normalized = _normalize_json_value(value, path='value', depth=0, active_ids=set())
    if not isinstance(normalized, dict):
        raise StateValidationError('state value must be a mapping')
    return normalized


def freeze_json_object(value: Mapping[str, Any]) -> FrozenJsonObject:
    """Crea un snapshot profundamente inmutable de un objeto JSON válido."""

    # Primero valida y copia; después congela cada colección anidada.
    return _freeze_json_value(normalize_json_object(value))


def decode_json_object(content: bytes) -> JsonObject:
    """Decodifica JSON UTF-8 rechazando ambigüedades y valores no portables."""

    if not isinstance(content, bytes):
        raise TypeError('content must be bytes')
    try:
        text = content.decode('utf-8')
        payload = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise StateCorruptionError('state document is not valid JSON') from error
    if not isinstance(payload, Mapping):
        raise StateCorruptionError('state document must be a JSON object')
    try:
        return normalize_json_object(payload)
    except StateValidationError as error:
        raise StateCorruptionError('state document contains invalid JSON values') from error


# sort_keys y separadores compactos hacen que dos objetos equivalentes produzcan los mismos bytes.
def encode_canonical_json(value: Mapping[str, Any]) -> bytes:
    """Serializa un objeto estable para firmas y escrituras reproducibles."""

    normalized = normalize_json_object(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(',', ':'),
    ).encode('utf-8')


def _normalize_json_value(
    value: Any,
    *,
    path: str,
    depth: int,
    active_ids: set[int],
) -> JsonValue:
    # JSON estándar no representa NaN ni infinitos de manera interoperable.
    if depth > _MAX_JSON_DEPTH:
        raise StateValidationError(f'{path} exceeds the maximum JSON depth')
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, str):
        _validate_utf8_text(value, path)
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise StateValidationError(f'{path} must contain only finite numbers')
        return value
    if isinstance(value, Mapping):
        return _normalize_mapping(value, path=path, depth=depth, active_ids=active_ids)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return _normalize_sequence(value, path=path, depth=depth, active_ids=active_ids)
    raise StateValidationError(f'{path} contains unsupported type {type(value).__name__}')


def _normalize_mapping(
    value: Mapping[Any, Any],
    *,
    path: str,
    depth: int,
    active_ids: set[int],
) -> dict[str, JsonValue]:
    # active_ids detecta ciclos sin rechazar que un objeto se reutilice en ramas ya finalizadas.
    identity = id(value)
    if identity in active_ids:
        raise StateValidationError(f'{path} contains a cyclic reference')
    active_ids.add(identity)
    try:
        normalized: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise StateValidationError(f'{path} contains a non-string key')
            if not key:
                raise StateValidationError(f'{path} contains an empty key')
            _validate_utf8_text(key, f'{path} key')
            normalized[key] = _normalize_json_value(
                item,
                path=f'{path}.{key}',
                depth=depth + 1,
                active_ids=active_ids,
            )
        return normalized
    finally:
        active_ids.remove(identity)


def _freeze_json_value(value: JsonValue) -> FrozenJsonValue:
    # MappingProxyType impide escritura y las listas se convierten en tuplas.
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json_value(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json_value(item) for item in value)
    return value


def _unique_object(pairs: list[tuple[str, JsonValue]]) -> JsonObject:
    # Dos claves iguales son ambiguas: distintos parsers podrían escoger valores diferentes.
    value: JsonObject = {}
    for key, item in pairs:
        if key in value:
            raise StateCorruptionError('state document contains duplicate JSON keys')
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise StateCorruptionError(f'unsupported JSON constant: {value}')


def _validate_utf8_text(value: str, path: str) -> None:
    try:
        value.encode('utf-8')
    except UnicodeEncodeError as error:
        raise StateValidationError(f'{path} must contain valid Unicode text') from error


def _normalize_sequence(
    value: Sequence[Any],
    *,
    path: str,
    depth: int,
    active_ids: set[int],
) -> list[JsonValue]:
    identity = id(value)
    if identity in active_ids:
        raise StateValidationError(f'{path} contains a cyclic reference')
    active_ids.add(identity)
    try:
        return [
            _normalize_json_value(
                item,
                path=f'{path}[{index}]',
                depth=depth + 1,
                active_ids=active_ids,
            )
            for index, item in enumerate(value)
        ]
    finally:
        active_ids.remove(identity)
