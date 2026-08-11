"""Puerto neutral para spans opcionales sin depender de un proveedor externo."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from atlanticus.observability.models import ExecutionContext


@dataclass(frozen=True, slots=True)
class SpanError:
    """Error sanitizado entregado a un bridge de tracing."""

    error_type: str
    message: str

    def __post_init__(self) -> None:
        # El bridge recibe texto ya controlado y nunca una excepción original.
        for name in ('error_type', 'message'):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f'{name} must be a non-empty string')


class SpanHandle(Protocol):
    """Span iniciado que debe cerrarse sin afectar el proceso observado."""

    def end(self, error: SpanError | None = None) -> None:
        """Finaliza el span y registra un error sanitizado cuando corresponde."""


class TraceBridge(Protocol):
    # Este puerto permite integrar OpenTelemetry en otro wheel sin importar su SDK aquí.
    """Extensión inyectable implementada por destinos como OpenTelemetry."""

    def start_span(
        self,
        name: str,
        *,
        context: ExecutionContext,
        attributes: dict[str, Any],
    ) -> SpanHandle:
        """Inicia un span asociado al contexto activo."""

    def close(self) -> None:
        """Libera y sincroniza recursos propios del proveedor."""


class _NoopSpanHandle:
    def end(self, error: SpanError | None = None) -> None:
        return None


class NoopTraceBridge:
    """Implementación por defecto que no crea telemetría adicional."""

    def start_span(
        self,
        name: str,
        *,
        context: ExecutionContext,
        attributes: dict[str, Any],
    ) -> SpanHandle:
        return _NoopSpanHandle()

    def close(self) -> None:
        return None
