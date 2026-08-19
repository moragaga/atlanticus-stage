from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

from atlanticus.json.errors import JsonCorruptionError, JsonValidationError

# El contrato acepta únicamente tipos JSON reales. No hace conversiones silenciosas de objetos Python.
type JsonScalar = None | bool | int | float | str
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonDocument = dict[str, JsonValue]

# El límite protege contra estructuras patológicas sin imponer un límite de tamaño al documento.
_MAX_JSON_DEPTH = 64


def normalize_json_document(value: Mapping[str, Any]) -> JsonDocument:
    # Todo documento tiene un objeto JSON en la raíz; una lista o escalar no representa un documento Atlanticus.
    if not isinstance(value, Mapping):
        raise JsonValidationError('JSON document must be a mapping')
    normalized = _normalize_json_value(value, path='document', depth=0, active_ids=set())
    if not isinstance(normalized, dict):
        raise JsonValidationError('JSON document must be a mapping')
    return normalized


def encode_json_document(value: Mapping[str, Any]) -> bytes:
    # sort_keys + separators compactos producen bytes deterministas, útiles para idempotencia.
    normalized = normalize_json_document(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(',', ':'),
    ).encode('utf-8')


def decode_json_document(content: bytes) -> JsonDocument:
    if not isinstance(content, bytes):
        raise TypeError('content must be bytes')
    try:
        # object_pairs_hook permite detectar claves duplicadas, que json.loads normalmente sobrescribiría.
        text = content.decode('utf-8')
        payload = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise JsonCorruptionError('document is not valid JSON') from error
    if not isinstance(payload, Mapping):
        raise JsonCorruptionError('document must be a JSON object')
    try:
        return normalize_json_document(payload)
    except JsonValidationError as error:
        raise JsonCorruptionError('document contains invalid JSON values') from error


def _normalize_json_value(
    value: Any,
    *,
    path: str,
    depth: int,
    active_ids: set[int],
) -> JsonValue:
    if depth > _MAX_JSON_DEPTH:
        raise JsonValidationError(f'{path} exceeds the maximum JSON depth')
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, str):
        _validate_utf8_text(value, path)
        return value
    if isinstance(value, float):
        # JSON interoperable no admite NaN ni infinitos aunque el módulo json de Python pueda emitirlos.
        if not math.isfinite(value):
            raise JsonValidationError(f'{path} must contain only finite numbers')
        return value
    if isinstance(value, Mapping):
        return _normalize_mapping(value, path=path, depth=depth, active_ids=active_ids)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return _normalize_sequence(value, path=path, depth=depth, active_ids=active_ids)
    raise JsonValidationError(f'{path} contains an unsupported JSON value')


def _normalize_mapping(
    value: Mapping[Any, Any],
    *,
    path: str,
    depth: int,
    active_ids: set[int],
) -> dict[str, JsonValue]:
    # active_ids detecta referencias cíclicas antes de delegar al serializador estándar.
    identity = id(value)
    if identity in active_ids:
        raise JsonValidationError(f'{path} contains a cyclic reference')
    active_ids.add(identity)
    try:
        normalized: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise JsonValidationError(f'{path} contains a non-string object key')
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


def _normalize_sequence(
    value: Sequence[Any],
    *,
    path: str,
    depth: int,
    active_ids: set[int],
) -> list[JsonValue]:
    identity = id(value)
    if identity in active_ids:
        raise JsonValidationError(f'{path} contains a cyclic reference')
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


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise JsonCorruptionError(f'document contains duplicate object key: {key}')
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise JsonCorruptionError(f'document contains invalid JSON constant: {value}')


def _validate_utf8_text(value: str, path: str) -> None:
    try:
        value.encode('utf-8')
    except UnicodeEncodeError as error:
        raise JsonValidationError(f'{path} must be valid UTF-8 text') from error
