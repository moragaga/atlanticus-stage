"""Utilidades pequeñas para tiempo UTC."""

from __future__ import annotations

# Se utiliza UTC de la biblioteca estándar; el kernel no incorpora zonas operacionales de negocio.
from datetime import UTC, datetime


def utc_now() -> datetime:
    """Retorna la fecha y hora UTC actual con zona horaria."""

    # Pasar UTC evita obtener un ``datetime`` naive y elimina ambigüedad al persistirlo.
    return datetime.now(UTC)
