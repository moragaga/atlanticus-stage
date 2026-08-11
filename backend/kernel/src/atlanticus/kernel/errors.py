"""Errores generados por Atlanticus Kernel."""

from __future__ import annotations


class KernelError(Exception):
    """Error base para fallos propios de Atlanticus Kernel."""


class InvalidEnvironmentError(KernelError):
    """Se genera cuando ``ENVIRONMENT`` está ausente o no es un valor oficial."""

    def __init__(self, value: str | None, allowed_values: tuple[str, ...]) -> None:
        self.value = value
        self.allowed_values = allowed_values
        allowed = ', '.join(allowed_values)
        super().__init__(f'Invalid environment {value!r}. Allowed values: {allowed}.')
