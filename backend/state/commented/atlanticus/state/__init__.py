# La fachada exporta solamente el contrato estable que usarán los jobs.
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
from atlanticus.state.store import DEFAULT_MAX_DOCUMENT_BYTES, AtomicStateStore

# La versión pertenece a este wheel; no existe una versión global de Atlanticus.
__version__ = '0.1.0'

# Mantener esta lista pequeña evita que los jobs dependan de helpers internos de serialización.
__all__ = [
    'DEFAULT_MAX_DOCUMENT_BYTES',
    'STATE_SCHEMA_VERSION',
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
