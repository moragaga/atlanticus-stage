"""Errores públicos de la frontera operacional de datasets."""

from __future__ import annotations


class DatasetRuntimeError(Exception):
    """Base para errores al convertir, publicar o leer datos tabulares."""


class DatasetRuntimeValidationError(DatasetRuntimeError, ValueError):
    """La solicitud no cumple las invariantes comunes del runtime."""


class DatasetConversionError(DatasetRuntimeError):
    """La conversión entre Pandas y PyArrow no pudo completarse."""


class DatasetRuntimeReadError(DatasetRuntimeError):
    """El store no pudo entregar una publicación confirmada."""


class DatasetRuntimeNotFoundError(DatasetRuntimeReadError, FileNotFoundError):
    """El target solicitado todavía no posee una publicación confirmada."""


class DatasetRuntimeWriteError(DatasetRuntimeError):
    """El store no pudo confirmar la publicación solicitada."""
