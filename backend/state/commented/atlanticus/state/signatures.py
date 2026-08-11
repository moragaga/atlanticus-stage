# La firma detecta cambios semánticos sin introducir contadores incrementales.
"""Firmas determinísticas para detectar cambios semánticos."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from atlanticus.state.serialization import encode_canonical_json


def build_state_signature(values: Mapping[str, Any]) -> str:
    """Calcula SHA-256 sobre JSON canónico sin depender del orden de las claves."""

    # El hash representa contenido; no aumenta por el mero paso del tiempo ni por reintentos.
    return hashlib.sha256(encode_canonical_json(values)).hexdigest()
