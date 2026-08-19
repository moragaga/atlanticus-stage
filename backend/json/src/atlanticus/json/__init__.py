from __future__ import annotations

from pkgutil import extend_path

from atlanticus.json.errors import (
    JsonConflictError,
    JsonCorruptionError,
    JsonError,
    JsonReadError,
    JsonValidationError,
    JsonWriteError,
)
from atlanticus.json.serialization import (
    JsonDocument,
    JsonScalar,
    JsonValue,
    decode_json_document,
    encode_json_document,
    normalize_json_document,
)
from atlanticus.json.store import JsonDocumentStore, JsonWriteOnceStatus

__path__ = extend_path(__path__, __name__)

__version__ = '0.1.0'

__all__ = [
    'JsonConflictError',
    'JsonCorruptionError',
    'JsonDocument',
    'JsonDocumentStore',
    'JsonError',
    'JsonReadError',
    'JsonScalar',
    'JsonValidationError',
    'JsonValue',
    'JsonWriteError',
    'JsonWriteOnceStatus',
    '__version__',
    'decode_json_document',
    'encode_json_document',
    'normalize_json_document',
]
