# Las rutas se construyen únicamente desde identificadores validados, sin transformaciones ambiguas.
# El código bajo estos comentarios es equivalente al productivo y conserva el mismo comportamiento.

"""Resolución de rutas compartidas por aplicación y runtime efímero."""

from __future__ import annotations

import re
from pathlib import Path

_PATH_SEGMENT_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$')


def validate_path_segment(value: str, *, name: str) -> str:
    """Valida una identidad antes de utilizarla como segmento de ruta."""

    if not isinstance(value, str):
        raise TypeError(f'{name} must be a string')
    if not _PATH_SEGMENT_PATTERN.fullmatch(value):
        raise ValueError(
            f'{name} must contain only letters, numbers, dots, underscores, or hyphens'
        )
    return value


def resolve_application_root(volume_path: str | Path, *, application: str) -> Path:
    """Retorna la raíz funcional que contiene logs y datasets de una aplicación."""

    raw_volume_path = str(volume_path).strip()
    if not raw_volume_path:
        raise ValueError('volume_path must not be empty')
    application_segment = validate_path_segment(application, name='application')
    return Path(raw_volume_path) / application_segment


def resolve_runtime_root(volume_path: str | Path, *, application: str) -> Path:
    """Retorna la raíz oculta destinada a coordinación y estado temporal."""

    # La aplicación es la primera frontera física bajo el volumen compartido.
    return resolve_application_root(volume_path, application=application) / '.runtime'
