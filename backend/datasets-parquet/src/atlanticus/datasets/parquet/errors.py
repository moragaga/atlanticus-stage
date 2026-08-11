"""Errores públicos del adaptador físico Parquet."""

from __future__ import annotations

from atlanticus.datasets import DatasetError, DatasetValidationError


class ParquetDatasetError(DatasetError):
    """Base para fallos propios de persistencia y lectura Parquet."""


class ParquetValidationError(ParquetDatasetError, DatasetValidationError):
    """Una solicitud física no cumple el contrato del adaptador."""


class ParquetLayoutError(ParquetValidationError):
    """La operación no corresponde al layout declarado."""


class ParquetSchemaError(ParquetValidationError):
    """Los schemas no pueden combinarse sin coerción silenciosa."""


class ParquetPublicationNotFoundError(ParquetDatasetError, FileNotFoundError):
    """El target solicitado todavía no posee una publicación confirmada."""


class ParquetReadError(ParquetDatasetError):
    """Una publicación confirmada no pudo leerse."""


class ParquetCorruptionError(ParquetReadError):
    """El manifiesto o uno de sus artefactos es inconsistente."""


class ParquetWriteError(ParquetDatasetError):
    """Una escritura no pudo confirmarse atómicamente."""
