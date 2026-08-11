"""Errores públicos de los contratos de datasets."""

from __future__ import annotations


class DatasetError(Exception):
    """Base para errores que deben ser manejados por el dueño de la publicación."""


class DatasetValidationError(DatasetError, ValueError):
    """Una identidad o valor no puede representarse mediante el contrato."""


class DatasetDefinitionError(DatasetValidationError):
    """La definición lógica del dataset es inconsistente."""


class DatasetTargetError(DatasetValidationError):
    """El destino no corresponde a la definición que intenta resolverlo."""
