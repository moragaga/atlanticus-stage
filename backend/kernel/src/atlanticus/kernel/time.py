"""Utilidades pequeñas para tiempo UTC."""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Retorna la fecha y hora UTC actual con zona horaria."""

    return datetime.now(UTC)
