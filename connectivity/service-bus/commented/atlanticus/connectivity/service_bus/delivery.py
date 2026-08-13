"""Entrega individual y operaciones explícitas de settlement."""
# Espejo pedagógico: conserva exactamente el contrato ejecutable y agrega contexto de diseño.

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from atlanticus.connectivity.service_bus.errors import ServiceBusSettlementError
from atlanticus.connectivity.service_bus.models import ServiceBusDeliveryState, ServiceBusMessage

if TYPE_CHECKING:
    from atlanticus.connectivity.service_bus.receiver import ServiceBusTopicReceiver


# La entrega encapsula el lock; el consumidor decide explícitamente complete, abandon o dead-letter.
class ServiceBusDelivery:
    """Une un mensaje neutral con el receiver que conserva su lock."""

    __slots__ = ('_owner', '_raw_message', '_state', 'message')

    def __init__(
        self,
        *,
        message: ServiceBusMessage,
        raw_message: Any,
        owner: ServiceBusTopicReceiver,
        state: ServiceBusDeliveryState = ServiceBusDeliveryState.ACTIVE,
    ) -> None:
        self.message = message
        self._raw_message = raw_message
        self._owner = owner
        self._state = state

    @property
    def state(self) -> ServiceBusDeliveryState:
        """Retorna el último estado confirmado localmente."""

        return self._state

    @property
    def can_settle(self) -> bool:
        """Indica si la entrega conserva un lock activo."""

        return self._state == ServiceBusDeliveryState.ACTIVE

    def complete(self) -> None:
        """Confirma esta entrega después del procesamiento exitoso."""

        self._require_active('complete')
        self._owner._complete_delivery(self)
        self._state = ServiceBusDeliveryState.COMPLETED

    def abandon(self) -> None:
        """Libera esta entrega para que pueda volver a recibirse."""

        self._require_active('abandon')
        self._owner._abandon_delivery(self)
        self._state = ServiceBusDeliveryState.ABANDONED

    def dead_letter(
        self,
        *,
        reason: str | None = None,
        error_description: str | None = None,
    ) -> None:
        """Mueve esta entrega a dead-letter cuando reintentar no sirve."""

        self._require_active('dead-letter')
        self._owner._dead_letter_delivery(
            self,
            reason=_optional_text(reason),
            error_description=_optional_text(error_description),
        )
        self._state = ServiceBusDeliveryState.DEAD_LETTERED

    def renew_lock(self) -> None:
        """Renueva el lock de esta entrega sin modificar su estado."""

        self._require_active('renew')
        self._owner._renew_delivery_lock(self)

    @contextmanager
    def auto_renew_lock(self, *, max_duration_seconds: float) -> Iterator[None]:
        """Mantiene el lock mientras dura un trabajo acotado."""

        self._require_active('auto-renew')
        with self._owner._auto_renew_delivery_lock(
            self,
            max_duration_seconds=max_duration_seconds,
        ):
            yield

    def _require_active(self, operation: str) -> None:
        if self._state != ServiceBusDeliveryState.ACTIVE:
            raise ServiceBusSettlementError(
                f'Cannot {operation} a delivery in state {self._state.value!r}'
            )

    def _mark_abandoned_on_close(self) -> None:
        if self._state == ServiceBusDeliveryState.ACTIVE:
            self._state = ServiceBusDeliveryState.ABANDONED


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ServiceBusSettlementError('Settlement text values must be text')
    normalized = value.strip()
    return normalized or None
