# Espejo pedagógico del módulo productivo de configuración.
# Conserva exactamente su comportamiento y agrega contexto para mantenimiento.
"""Contratos mínimos requeridos por la resolución de configuración."""

from __future__ import annotations

from typing import Protocol


class SecretResolver(Protocol):
    """Obtiene un valor secreto desde una fuente externa."""

    def get_secret(self, secret_name: str) -> str:
        """Recupera el valor asociado al nombre exacto del secreto."""
