# Espejo explicativo: current.json es la única autoridad de un conjunto de partes.
"""Serialización y validación estricta del manifiesto de un file set."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa

from atlanticus.datasets.parquet.errors import ParquetCorruptionError

_FORMAT_VERSION = 1
_SIGNATURE_PATTERN = re.compile(r'sha256:[0-9a-f]{64}')


@dataclass(frozen=True, slots=True)
class _ManifestPart:
    value: str
    path: str
    item_count: int
    size_bytes: int
    content_signature: str

    def to_payload(self, *, dimension: str) -> dict[str, Any]:
        return {
            'dimension': dimension,
            'value': self.value,
            'path': self.path,
            'item_count': self.item_count,
            'size_bytes': self.size_bytes,
            'content_signature': self.content_signature,
        }


@dataclass(frozen=True, slots=True)
class _Manifest:
    publication_token: str
    target: str
    committed_at_utc: datetime
    part_dimension: str
    item_count: int
    content_signature: str
    schema: pa.Schema
    schema_signature: str
    parts: tuple[_ManifestPart, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            'format_version': _FORMAT_VERSION,
            'publication_token': self.publication_token,
            'target': self.target,
            'committed_at_utc': _format_utc(self.committed_at_utc),
            'part_dimension': self.part_dimension,
            'item_count': self.item_count,
            'content_signature': self.content_signature,
            'schema': {
                'serialized': base64.b64encode(self.schema.serialize().to_pybytes()).decode(
                    'ascii'
                ),
                'signature': self.schema_signature,
                'fields': [
                    {
                        'name': field.name,
                        'type': str(field.type),
                        'nullable': field.nullable,
                    }
                    for field in self.schema
                ],
            },
            'parts': [part.to_payload(dimension=self.part_dimension) for part in self.parts],
        }


def _encode_manifest(manifest: _Manifest) -> bytes:
    return (
        json.dumps(
            manifest.to_payload(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + '\n'
    ).encode('utf-8')


def _decode_manifest(
    content: bytes,
    *,
    expected_target: str,
    expected_part_dimension: str,
) -> _Manifest:
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ParquetCorruptionError('current manifest is not valid JSON') from error
    if not isinstance(payload, dict):
        raise ParquetCorruptionError('current manifest root must be an object')
    if payload.get('format_version') != _FORMAT_VERSION:
        raise ParquetCorruptionError('current manifest format_version is not supported')
    publication_token = _required_string(payload, 'publication_token')
    target = _required_string(payload, 'target')
    if target != expected_target:
        raise ParquetCorruptionError('current manifest references a different target')
    part_dimension = _required_string(payload, 'part_dimension')
    if part_dimension != expected_part_dimension:
        raise ParquetCorruptionError('current manifest part dimension does not match the layout')
    committed_at_utc = _parse_utc(_required_string(payload, 'committed_at_utc'))
    item_count = _required_positive_integer(payload, 'item_count')
    content_signature = _required_signature(payload, 'content_signature')
    schema_payload = payload.get('schema')
    if not isinstance(schema_payload, dict):
        raise ParquetCorruptionError('current manifest schema must be an object')
    schema = _decode_schema(_required_string(schema_payload, 'serialized'))
    schema_signature = _required_signature(schema_payload, 'signature')
    if schema_signature != _schema_signature(schema):
        raise ParquetCorruptionError('current manifest schema signature does not match its schema')
    fields = schema_payload.get('fields')
    expected_fields = [
        {'name': field.name, 'type': str(field.type), 'nullable': field.nullable}
        for field in schema
    ]
    if fields != expected_fields:
        raise ParquetCorruptionError('current manifest schema fields do not match its schema')
    parts_payload = payload.get('parts')
    if not isinstance(parts_payload, list) or not parts_payload:
        raise ParquetCorruptionError('current manifest parts must be a non-empty list')
    parts = tuple(_decode_part(item, expected_dimension=part_dimension) for item in parts_payload)
    if len({part.value for part in parts}) != len(parts):
        raise ParquetCorruptionError('current manifest contains duplicate part values')
    if sum(part.item_count for part in parts) != item_count:
        raise ParquetCorruptionError('current manifest item_count does not match its parts')
    expected_content_signature = _publication_signature(
        schema_signature=schema_signature,
        parts=parts,
    )
    if content_signature != expected_content_signature:
        raise ParquetCorruptionError('current manifest content signature does not match its parts')
    return _Manifest(
        publication_token=publication_token,
        target=target,
        committed_at_utc=committed_at_utc,
        part_dimension=part_dimension,
        item_count=item_count,
        content_signature=content_signature,
        schema=schema,
        schema_signature=schema_signature,
        parts=parts,
    )


def _decode_part(value: Any, *, expected_dimension: str) -> _ManifestPart:
    if not isinstance(value, dict):
        raise ParquetCorruptionError('current manifest part must be an object')
    if _required_string(value, 'dimension') != expected_dimension:
        raise ParquetCorruptionError('current manifest part dimension is inconsistent')
    part_value = _required_string(value, 'value')
    path = _required_string(value, 'path')
    if Path(path).name != path or not path.endswith('.parquet'):
        raise ParquetCorruptionError('current manifest part path must be a local parquet filename')
    content_signature = _required_signature(value, 'content_signature')
    digest = content_signature.removeprefix('sha256:')
    expected_path = f'{expected_dimension}={part_value}--{digest}.parquet'
    if path != expected_path:
        raise ParquetCorruptionError('current manifest part path does not match its identity')
    return _ManifestPart(
        value=part_value,
        path=path,
        item_count=_required_positive_integer(value, 'item_count'),
        size_bytes=_required_positive_integer(value, 'size_bytes'),
        content_signature=content_signature,
    )


def _decode_schema(value: str) -> pa.Schema:
    try:
        content = base64.b64decode(value, validate=True)
        return pa.ipc.read_schema(pa.BufferReader(content))
    except (ValueError, pa.ArrowException) as error:
        raise ParquetCorruptionError('current manifest schema is not valid Arrow IPC') from error


def _schema_signature(schema: pa.Schema) -> str:
    import hashlib

    return f'sha256:{hashlib.sha256(schema.serialize().to_pybytes()).hexdigest()}'


def _publication_signature(
    *,
    schema_signature: str,
    parts: tuple[_ManifestPart, ...],
) -> str:
    import hashlib

    payload = {
        'schema_signature': schema_signature,
        'parts': [
            {
                'value': part.value,
                'content_signature': part.content_signature,
                'item_count': part.item_count,
            }
            for part in sorted(parts, key=lambda item: item.value)
        ],
    }
    encoded = json.dumps(payload, separators=(',', ':'), sort_keys=True).encode('utf-8')
    return f'sha256:{hashlib.sha256(encoded).hexdigest()}'


def _required_string(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise ParquetCorruptionError(f'current manifest {field} must be a non-empty string')
    return value


def _required_positive_integer(payload: dict[str, Any], field: str) -> int:
    value = payload.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ParquetCorruptionError(f'current manifest {field} must be a positive integer')
    return value


def _required_signature(payload: dict[str, Any], field: str) -> str:
    value = _required_string(payload, field)
    if not _SIGNATURE_PATTERN.fullmatch(value):
        raise ParquetCorruptionError(f'current manifest {field} must be a sha256 signature')
    return value


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as error:
        raise ParquetCorruptionError('current manifest committed_at_utc is invalid') from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ParquetCorruptionError('current manifest committed_at_utc must be timezone-aware')
    return parsed.astimezone(UTC)


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace('+00:00', 'Z')
