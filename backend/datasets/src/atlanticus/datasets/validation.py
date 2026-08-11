"""Validaciones compartidas para identidades y dimensiones lógicas."""

from __future__ import annotations

import re

from atlanticus.datasets.errors import DatasetValidationError

_IDENTITY_PATTERN = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,119})?')
_DIMENSION_PATTERN = re.compile(r'[A-Za-z][A-Za-z0-9_]{0,119}')
_VALUE_PATTERN = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,239})?')


def validate_identity_segment(
    value: str,
    *,
    field: str,
    error_type: type[DatasetValidationError] = DatasetValidationError,
) -> str:
    """Valida un segmento estable sin normalizar ni crear colisiones silenciosas."""

    if not isinstance(value, str) or not _IDENTITY_PATTERN.fullmatch(value):
        raise error_type(f'{field} must use 1-120 letters, numbers, dots, underscores or hyphens')
    if value in {'.', '..'}:
        raise error_type(f'{field} must not be a relative path')
    return value


def validate_dimension_name(
    value: str,
    *,
    field: str,
    error_type: type[DatasetValidationError] = DatasetValidationError,
) -> str:
    """Valida un nombre de dimensión apto para pares `nombre=valor`."""

    if not isinstance(value, str) or not _DIMENSION_PATTERN.fullmatch(value):
        raise error_type(
            f'{field} must start with a letter and use only letters, numbers or underscores'
        )
    return value


def validate_dimension_value(
    value: str,
    *,
    field: str,
    error_type: type[DatasetValidationError] = DatasetValidationError,
) -> str:
    """Valida un valor explícito y seguro sin convertir tipos automáticamente."""

    if not isinstance(value, str) or not _VALUE_PATTERN.fullmatch(value):
        raise error_type(f'{field} must use 1-240 letters, numbers, dots, underscores or hyphens')
    if value in {'.', '..'}:
        raise error_type(f'{field} must not be a relative path')
    return value
