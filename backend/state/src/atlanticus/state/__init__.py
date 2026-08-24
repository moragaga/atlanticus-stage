"""Contratos compactos de estado para jobs Atlanticus."""

from atlanticus.state.errors import (
    StateCorruptionError,
    StateError,
    StateReadError,
    StateSchemaError,
    StateTooLargeError,
    StateValidationError,
    StateWriteError,
)
from atlanticus.state.expiring import ExpiringKeySet
from atlanticus.state.models import STATE_SCHEMA_VERSION, StateDocument, StateKey
from atlanticus.state.signatures import build_state_signature
from atlanticus.state.store import DEFAULT_MAX_DOCUMENT_BYTES, AtomicJsonStore, AtomicStateStore

__version__ = '0.2.0'

__all__ = [
    'DEFAULT_MAX_DOCUMENT_BYTES',
    'STATE_SCHEMA_VERSION',
    'AtomicJsonStore',
    'AtomicStateStore',
    'ExpiringKeySet',
    'StateCorruptionError',
    'StateDocument',
    'StateError',
    'StateKey',
    'StateReadError',
    'StateSchemaError',
    'StateTooLargeError',
    'StateValidationError',
    'StateWriteError',
    'build_state_signature',
    '__version__',
]
