"""Receiver síncrono con una sola entrega PeekLock activa a la vez."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from types import TracebackType
from typing import Any, NoReturn

from azure.servicebus import (
    AutoLockRenewer,
    ServiceBusClient,
    ServiceBusReceiveMode as AzureServiceBusReceiveMode,
)
from azure.servicebus.exceptions import (
    ServiceBusAuthenticationError as AzureServiceBusAuthenticationError,
    ServiceBusAuthorizationError as AzureServiceBusAuthorizationError,
    ServiceBusCommunicationError as AzureServiceBusCommunicationError,
    ServiceBusConnectionError as AzureServiceBusConnectionError,
    ServiceBusError as AzureServiceBusError,
)

from atlanticus.connectivity.service_bus.delivery import ServiceBusDelivery
from atlanticus.connectivity.service_bus.errors import (
    ServiceBusAuthenticationError,
    ServiceBusAuthorizationError,
    ServiceBusConfigurationError,
    ServiceBusConnectionError,
    ServiceBusError,
    ServiceBusMessageError,
    ServiceBusReceiveError,
    ServiceBusSettlementError,
)
from atlanticus.connectivity.service_bus.models import ServiceBusMessage
from atlanticus.connectivity.service_bus.settings import ServiceBusSettings
from atlanticus.observability import ErrorInfo, ResultSummary, runtime_guard

_COMPONENT = 'atlanticus.connectivity.service_bus'


def _safe_parameters(args: tuple[Any, ...], _: Mapping[str, Any]) -> Mapping[str, Any]:
    instance = args[0] if args else None
    settings = getattr(instance, 'settings', None)
    if not isinstance(settings, ServiceBusSettings):
        return {}
    return {
        'topic_name': settings.topic_name,
        'subscription_name': settings.subscription_name,
        'max_wait_time_seconds': settings.max_wait_time_seconds,
    }


def _safe_error(error: BaseException) -> ErrorInfo:
    message = str(error) if isinstance(error, ServiceBusError | TypeError) else 'Service Bus failed'
    return ErrorInfo(error_type=type(error).__name__, message=message)


def _receive_result(value: Any) -> ResultSummary:
    return ResultSummary(metrics={'message_count': 0 if value is None else 1})


class ServiceBusTopicReceiver:
    """Reutiliza un receiver PeekLock y conserva como máximo una entrega activa."""

    def __init__(self, *, settings: ServiceBusSettings) -> None:
        if not isinstance(settings, ServiceBusSettings):
            raise ServiceBusConfigurationError('settings must be ServiceBusSettings')
        self.settings = settings
        self._client: Any | None = None
        self._receiver: Any | None = None
        self._active_delivery: ServiceBusDelivery | None = None
        self._closed = False

    def __enter__(self) -> ServiceBusTopicReceiver:
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            self.close()
        except ServiceBusConnectionError:
            if exc_value is None:
                raise

    @runtime_guard(
        operation='service_bus.receiver.open',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        error_mapper=_safe_error,
        emit_started=False,
    )
    def open(self) -> None:
        """Crea el cliente y receiver sin adquirir todavía una entrega."""

        if self._closed:
            raise ServiceBusConnectionError('Service Bus receiver is closed')
        if self._receiver is not None:
            return
        try:
            client = ServiceBusClient.from_connection_string(
                conn_str=self.settings.connection_string,
                logging_enable=False,
                retry_total=0,
            )
            receiver = client.get_subscription_receiver(
                topic_name=self.settings.topic_name,
                subscription_name=self.settings.subscription_name,
                receive_mode=AzureServiceBusReceiveMode.PEEK_LOCK,
                prefetch_count=0,
            )
        except Exception as error:
            _raise_sdk_error(
                error,
                default_error=ServiceBusConnectionError,
                default_message='Could not open Service Bus receiver',
            )
        self._client = client
        self._receiver = receiver

    @runtime_guard(
        operation='service_bus.receiver.receive_one',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        result_mapper=_receive_result,
        error_mapper=_safe_error,
    )
    def receive_one(self) -> ServiceBusDelivery | None:
        """Recibe cero o una entrega; otra lectura exige resolver primero la activa."""

        if self._active_delivery is not None and self._active_delivery.can_settle:
            raise ServiceBusSettlementError(
                'Settle the active Service Bus delivery before receiving another message'
            )
        self.open()
        try:
            messages = self._require_receiver().receive_messages(
                max_message_count=1,
                max_wait_time=self.settings.max_wait_time_seconds,
            )
        except Exception as error:
            _raise_sdk_error(
                error,
                default_error=ServiceBusReceiveError,
                default_message='Could not receive Service Bus message',
            )
        if not messages:
            return None

        raw_message = messages[0]
        try:
            message = _build_message(raw_message)
        except TypeError, ValueError, UnicodeError:
            self._release_undecodable_message(raw_message)
            raise ServiceBusMessageError('Could not decode Service Bus message') from None

        delivery = ServiceBusDelivery(
            message=message,
            raw_message=raw_message,
            owner=self,
        )
        self._active_delivery = delivery
        return delivery

    @runtime_guard(
        operation='service_bus.delivery.complete',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        error_mapper=_safe_error,
    )
    def _complete_delivery(self, delivery: ServiceBusDelivery) -> None:
        self._require_owned_active_delivery(delivery)
        try:
            self._require_receiver().complete_message(delivery._raw_message)
        except Exception as error:
            _raise_sdk_error(
                error,
                default_error=ServiceBusSettlementError,
                default_message='Could not complete Service Bus delivery',
            )
        self._active_delivery = None

    @runtime_guard(
        operation='service_bus.delivery.abandon',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        error_mapper=_safe_error,
    )
    def _abandon_delivery(self, delivery: ServiceBusDelivery) -> None:
        self._require_owned_active_delivery(delivery)
        try:
            self._require_receiver().abandon_message(delivery._raw_message)
        except Exception as error:
            _raise_sdk_error(
                error,
                default_error=ServiceBusSettlementError,
                default_message='Could not abandon Service Bus delivery',
            )
        self._active_delivery = None

    @runtime_guard(
        operation='service_bus.delivery.dead_letter',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        error_mapper=_safe_error,
    )
    def _dead_letter_delivery(
        self,
        delivery: ServiceBusDelivery,
        *,
        reason: str | None,
        error_description: str | None,
    ) -> None:
        self._require_owned_active_delivery(delivery)
        try:
            self._require_receiver().dead_letter_message(
                delivery._raw_message,
                reason=reason,
                error_description=error_description,
            )
        except Exception as error:
            _raise_sdk_error(
                error,
                default_error=ServiceBusSettlementError,
                default_message='Could not dead-letter Service Bus delivery',
            )
        self._active_delivery = None

    @runtime_guard(
        operation='service_bus.delivery.renew_lock',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        error_mapper=_safe_error,
        emit_started=False,
    )
    def _renew_delivery_lock(self, delivery: ServiceBusDelivery) -> None:
        self._require_owned_active_delivery(delivery)
        try:
            self._require_receiver().renew_message_lock(delivery._raw_message)
        except Exception as error:
            _raise_sdk_error(
                error,
                default_error=ServiceBusSettlementError,
                default_message='Could not renew Service Bus delivery lock',
            )

    @contextmanager
    def _auto_renew_delivery_lock(
        self,
        delivery: ServiceBusDelivery,
        *,
        max_duration_seconds: float,
    ) -> Iterator[None]:
        self._require_owned_active_delivery(delivery)
        if isinstance(max_duration_seconds, bool) or not isinstance(
            max_duration_seconds, int | float
        ):
            raise ServiceBusSettlementError('max_duration_seconds must be a positive number')
        duration = float(max_duration_seconds)
        if duration <= 0:
            raise ServiceBusSettlementError('max_duration_seconds must be a positive number')
        renewer = AutoLockRenewer(max_lock_renewal_duration=duration)
        try:
            renewer.register(
                self._require_receiver(),
                delivery._raw_message,
                max_lock_renewal_duration=duration,
            )
        except Exception as error:
            try:
                renewer.close(wait=False)
            except Exception:
                pass
            _raise_sdk_error(
                error,
                default_error=ServiceBusSettlementError,
                default_message='Could not start Service Bus automatic lock renewal',
            )
        try:
            yield
        finally:
            try:
                renewer.close(wait=True)
            except Exception as error:
                _raise_sdk_error(
                    error,
                    default_error=ServiceBusSettlementError,
                    default_message='Could not stop Service Bus automatic lock renewal',
                )

    def close(self) -> None:
        """Abandona defensivamente una entrega activa y cierra recursos una sola vez."""

        receiver = self._receiver
        client = self._client
        delivery = self._active_delivery
        self._receiver = None
        self._client = None
        self._active_delivery = None
        self._closed = True

        if receiver is not None and delivery is not None and delivery.can_settle:
            try:
                receiver.abandon_message(delivery._raw_message)
            except Exception:
                pass
            delivery._mark_abandoned_on_close()

        close_failed = False
        for value in (receiver, client):
            if value is None:
                continue
            try:
                value.close()
            except Exception:
                close_failed = True
        if close_failed:
            raise ServiceBusConnectionError('Could not close Service Bus receiver') from None

    def _require_receiver(self) -> Any:
        if self._receiver is None:
            raise ServiceBusConnectionError('Service Bus receiver is not open')
        return self._receiver

    def _require_owned_active_delivery(self, delivery: ServiceBusDelivery) -> None:
        if delivery is not self._active_delivery or not delivery.can_settle:
            raise ServiceBusSettlementError('Service Bus delivery is not active on this receiver')
        self._require_receiver()

    def _release_undecodable_message(self, raw_message: Any) -> None:
        try:
            self._require_receiver().abandon_message(raw_message)
        except Exception:
            try:
                self.close()
            except ServiceBusConnectionError:
                pass


def _raise_sdk_error(
    error: BaseException,
    *,
    default_error: type[ServiceBusError],
    default_message: str,
) -> NoReturn:
    if isinstance(error, AzureServiceBusAuthenticationError):
        raise ServiceBusAuthenticationError('Service Bus authentication failed') from None
    if isinstance(error, AzureServiceBusAuthorizationError):
        raise ServiceBusAuthorizationError('Service Bus authorization failed') from None
    if isinstance(error, AzureServiceBusConnectionError | AzureServiceBusCommunicationError):
        raise ServiceBusConnectionError('Could not connect to Service Bus') from None
    if isinstance(error, AzureServiceBusError | TypeError | ValueError):
        raise default_error(default_message) from None
    raise default_error(default_message) from None


def _build_message(raw_message: Any) -> ServiceBusMessage:
    return ServiceBusMessage(
        body=_read_body(raw_message),
        message_id=_optional_string(getattr(raw_message, 'message_id', None)),
        correlation_id=_optional_string(getattr(raw_message, 'correlation_id', None)),
        subject=_optional_string(getattr(raw_message, 'subject', None)),
        content_type=_optional_string(getattr(raw_message, 'content_type', None)),
        enqueued_time_utc=getattr(raw_message, 'enqueued_time_utc', None),
        sequence_number=_optional_int(getattr(raw_message, 'sequence_number', None)),
        delivery_count=_optional_int(getattr(raw_message, 'delivery_count', None)),
        application_properties=_normalize_application_properties(
            getattr(raw_message, 'application_properties', None)
        ),
    )


def _read_body(raw_message: Any) -> bytes:
    body = getattr(raw_message, 'body', raw_message)
    if isinstance(body, bytes | bytearray | memoryview):
        return bytes(body)
    if isinstance(body, str):
        return body.encode()

    chunks: list[bytes] = []
    for chunk in body:
        if isinstance(chunk, str):
            chunks.append(chunk.encode())
        else:
            chunks.append(bytes(chunk))
    return b''.join(chunks)


def _normalize_application_properties(value: Any) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError('application_properties must be a mapping')
    return {_property_name(key): item for key, item in value.items()}


def _property_name(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode('utf-8')
    return str(value)


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)
