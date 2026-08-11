"""Errores generados por Atlanticus Kernel."""

# Las anotaciones diferidas permiten usar tipos modernos sin evaluarlos antes de tiempo.
from __future__ import annotations


# Todos los errores propios del kernel pueden capturarse mediante esta clase base.
class KernelError(Exception):
    """Error base para fallos propios de Atlanticus Kernel."""


# Este error distingue un ambiente inválido de fallos de infraestructura o de negocio.
class InvalidEnvironmentError(KernelError):
    """Se genera cuando ``ENVIRONMENT`` está ausente o no es un valor oficial."""

    def __init__(self, value: str | None, allowed_values: tuple[str, ...]) -> None:
        # Conservamos el valor original para diagnóstico. No se normaliza ni se reemplaza.
        self.value = value
        # La lista oficial también queda disponible para pruebas y mensajes operacionales.
        self.allowed_values = allowed_values
        # El mensaje presenta el contrato completo para que el error sea corregible.
        allowed = ', '.join(allowed_values)
        super().__init__(f'Invalid environment {value!r}. Allowed values: {allowed}.')
