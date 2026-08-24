# Serialización central del record WAL: JSON determinístico y SHA-256 sin duplicar canonicalizadores.
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from ada.alarms.persistence.errors import AlarmPersistenceCorruptionError
from atlanticus.json import JsonDocument, JsonError, decode_json_document, encode_json_document


def build_record_hash(value: Mapping[str, Any]) -> str:
    if not isinstance(value, Mapping):
        raise TypeError('value must be a mapping')
    digest = hashlib.sha256(encode_json_document(value)).hexdigest()
    return f'sha256:{digest}'


def encode_record_line(value: Mapping[str, Any]) -> bytes:
    if not isinstance(value, Mapping):
        raise TypeError('value must be a mapping')
    return encode_json_document(value) + b'\n'


def decode_record_line(content: bytes) -> JsonDocument:
    if not isinstance(content, bytes):
        raise TypeError('content must be bytes')
    if not content.endswith(b'\n'):
        raise AlarmPersistenceCorruptionError('journal record is not newline terminated')
    payload = content[:-1]
    if not payload:
        raise AlarmPersistenceCorruptionError('journal record is empty')
    try:
        return decode_json_document(payload)
    except JsonError as error:
        raise AlarmPersistenceCorruptionError('journal record is not valid JSON') from error
