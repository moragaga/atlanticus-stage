"""Fachada estructurada para diagnósticos que no depende de ``logging``."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from atlanticus.kernel import OperationStatus
from atlanticus.observability.models import (
    ErrorInfo,
    EventAudience,
    EventCategory,
    EventSeverity,
    ObservabilityEvent,
)
from atlanticus.observability.state import emit_event


class ObservabilityLogger:
    """Emite mensajes estructurados conservando una API pequeña."""

    def __init__(self, component: str) -> None:
        if not isinstance(component, str) or not component.strip():
            raise ValueError('component must not be empty')
        self._component = component

    def debug(
        self,
        message: str,
        *,
        audience: EventAudience = EventAudience.LOCAL,
        event_name: str = 'diagnostic.log',
        **attributes: Any,
    ) -> bool:
        return self.log(
            EventSeverity.DEBUG,
            message,
            event_name=event_name,
            audience=audience,
            attributes=attributes,
        )

    def info(
        self,
        message: str,
        *,
        audience: EventAudience = EventAudience.LOCAL,
        event_name: str = 'diagnostic.log',
        **attributes: Any,
    ) -> bool:
        return self.log(
            EventSeverity.INFO,
            message,
            event_name=event_name,
            audience=audience,
            attributes=attributes,
        )

    def warning(
        self,
        message: str,
        *,
        audience: EventAudience = EventAudience.OPERATIONS,
        event_name: str = 'diagnostic.log',
        **attributes: Any,
    ) -> bool:
        return self.log(
            EventSeverity.WARNING,
            message,
            event_name=event_name,
            audience=audience,
            attributes=attributes,
        )

    def error(
        self,
        message: str,
        *,
        audience: EventAudience = EventAudience.OPERATIONS,
        event_name: str = 'diagnostic.log',
        **attributes: Any,
    ) -> bool:
        return self.log(
            EventSeverity.ERROR,
            message,
            event_name=event_name,
            audience=audience,
            attributes=attributes,
        )

    def critical(
        self,
        message: str,
        *,
        audience: EventAudience = EventAudience.OPERATIONS,
        event_name: str = 'diagnostic.log',
        **attributes: Any,
    ) -> bool:
        return self.log(
            EventSeverity.CRITICAL,
            message,
            event_name=event_name,
            audience=audience,
            attributes=attributes,
        )

    def exception(
        self,
        message: str,
        error: BaseException,
        audience: EventAudience = EventAudience.OPERATIONS,
        *,
        event_name: str = 'diagnostic.log',
        **attributes: Any,
    ) -> bool:
        """Emite el error entregado explícitamente; no depende de ``sys.exc_info``."""

        return self.log(
            EventSeverity.ERROR,
            message,
            event_name=event_name,
            audience=audience,
            attributes=attributes,
            error=ErrorInfo.from_exception(error),
        )

    def log(
        self,
        severity: EventSeverity,
        message: str,
        *,
        event_name: str = 'diagnostic.log',
        metrics: Mapping[str, int | float] | None = None,
        attributes: Mapping[str, Any] | None = None,
        error: ErrorInfo | None = None,
        audience: EventAudience = EventAudience.LOCAL,
    ) -> bool:
        if not isinstance(severity, EventSeverity):
            raise TypeError('severity must be an EventSeverity')
        status = None
        if severity is EventSeverity.WARNING:
            status = OperationStatus.WARNING
        elif severity in {EventSeverity.ERROR, EventSeverity.CRITICAL}:
            status = OperationStatus.ERROR
        return emit_event(
            ObservabilityEvent(
                name=event_name,
                category=EventCategory.DIAGNOSTIC,
                audience=audience,
                severity=severity,
                status=status,
                message=message,
                metrics=dict(metrics or {}),
                attributes={'component': self._component, **dict(attributes or {})},
                error=error,
            )
        )


def get_observability_logger(component: str) -> ObservabilityLogger:
    """Crea una fachada liviana asociada a un componente."""

    return ObservabilityLogger(component)
