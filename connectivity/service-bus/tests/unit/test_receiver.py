from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from azure.servicebus import ServiceBusClient, ServiceBusReceiveMode
from azure.servicebus.exceptions import ServiceBusAuthenticationError as AzureAuthenticationError

from atlanticus.connectivity.service_bus import (
    ServiceBusAuthenticationError,
    ServiceBusConnectionError,
    ServiceBusDeliveryState,
    ServiceBusMessageError,
    ServiceBusReceiveError,
    ServiceBusSettings,
    ServiceBusSettlementError,
    ServiceBusTopicReceiver,
)


@dataclass
class FakeRawMessage:
    body: Any
    message_id: str
    correlation_id: str | None = None
    subject: str | None = None
    content_type: str | None = None
    enqueued_time_utc: datetime | None = None
    sequence_number: int = 1
    delivery_count: int = 0
    application_properties: dict[Any, Any] | None = None


class FakeReceiver:
    def __init__(self, messages: list[FakeRawMessage]) -> None:
        self.messages = messages
        self.receive_calls: list[dict[str, Any]] = []
        self.completed: list[FakeRawMessage] = []
        self.abandoned: list[FakeRawMessage] = []
        self.dead_lettered: list[tuple[FakeRawMessage, str | None, str | None]] = []
        self.renewed: list[FakeRawMessage] = []
        self.closed = False
        self.close_error = False

    def receive_messages(self, **values: Any) -> list[FakeRawMessage]:
        self.receive_calls.append(values)
        count = int(values['max_message_count'])
        selected = self.messages[:count]
        del self.messages[:count]
        return selected

    def complete_message(self, message: FakeRawMessage) -> None:
        self.completed.append(message)

    def abandon_message(self, message: FakeRawMessage) -> None:
        self.abandoned.append(message)

    def dead_letter_message(
        self,
        message: FakeRawMessage,
        *,
        reason: str | None,
        error_description: str | None,
    ) -> None:
        self.dead_lettered.append((message, reason, error_description))

    def renew_message_lock(self, message: FakeRawMessage) -> None:
        self.renewed.append(message)

    def close(self) -> None:
        self.closed = True
        if self.close_error:
            raise RuntimeError('private receiver close failure')


class FakeClient:
    def __init__(self, receiver: FakeReceiver) -> None:
        self.receiver = receiver
        self.receiver_values: dict[str, Any] | None = None
        self.closed = False
        self.close_error = False

    def get_subscription_receiver(self, **values: Any) -> FakeReceiver:
        self.receiver_values = values
        return self.receiver

    def close(self) -> None:
        self.closed = True
        if self.close_error:
            raise RuntimeError('private client close failure')


def _settings() -> ServiceBusSettings:
    return ServiceBusSettings(
        connection_string='Endpoint=sb://secret;SharedAccessKey=private',
        topic_name='pi-events',
        subscription_name='atlanticus',
        max_wait_time_seconds=3,
    )


def _raw(identifier: str) -> FakeRawMessage:
    return FakeRawMessage(
        body=[b'{"id":"', identifier.encode(), b'"}'],
        message_id=identifier,
        correlation_id='correlation',
        subject='blob-created',
        content_type='application/json',
        enqueued_time_utc=datetime(2026, 7, 22, tzinfo=UTC),
        sequence_number=7,
        delivery_count=1,
        application_properties={b'route': 'recorded'},
    )


@pytest.fixture
def fake_sdk(monkeypatch: pytest.MonkeyPatch):
    created: list[tuple[FakeClient, dict[str, Any]]] = []

    def install(messages: list[FakeRawMessage]) -> tuple[FakeClient, FakeReceiver]:
        receiver = FakeReceiver(messages)
        client = FakeClient(receiver)

        def build(**values: Any) -> FakeClient:
            created.append((client, values))
            return client

        monkeypatch.setattr(ServiceBusClient, 'from_connection_string', build)
        return client, receiver

    return install, created


def test_receive_one_uses_fixed_peek_lock_and_completes_only_one(fake_sdk) -> None:
    install, created = fake_sdk
    client, sdk_receiver = install([_raw('first'), _raw('second')])

    with ServiceBusTopicReceiver(settings=_settings()) as receiver:
        delivery = receiver.receive_one()
        assert delivery is not None
        assert delivery.message.message_id == 'first'
        assert delivery.message.body == b'{"id":"first"}'
        delivery.complete()

    assert sdk_receiver.receive_calls == [{'max_message_count': 1, 'max_wait_time': 3.0}]
    assert [message.message_id for message in sdk_receiver.completed] == ['first']
    assert [message.message_id for message in sdk_receiver.messages] == ['second']
    assert client.receiver_values is not None
    assert client.receiver_values['receive_mode'] == ServiceBusReceiveMode.PEEK_LOCK
    assert client.receiver_values['prefetch_count'] == 0
    assert created[0][1]['logging_enable'] is False
    assert created[0][1]['retry_total'] == 0
    assert created[0][1]['conn_str'] == _settings().connection_string


def test_same_receiver_processes_backlog_and_new_arrival_sequentially(fake_sdk) -> None:
    install, created = fake_sdk
    _, sdk_receiver = install([_raw(f'backlog-{index}') for index in range(1, 6)])
    completed_ids: list[str | None] = []

    with ServiceBusTopicReceiver(settings=_settings()) as receiver:
        for index in range(1, 6):
            delivery = receiver.receive_one()
            assert delivery is not None
            delivery.complete()
            completed_ids.append(delivery.message.message_id)
            if index == 1:
                sdk_receiver.messages.append(_raw('arrived-during-run'))
        late = receiver.receive_one()
        assert late is not None
        late.complete()
        completed_ids.append(late.message.message_id)
        assert receiver.receive_one() is None

    assert completed_ids == [
        'backlog-1',
        'backlog-2',
        'backlog-3',
        'backlog-4',
        'backlog-5',
        'arrived-during-run',
    ]
    assert len(created) == 1


def test_active_delivery_must_be_settled_before_receiving_again(fake_sdk) -> None:
    install, _ = fake_sdk
    _, sdk_receiver = install([_raw('first'), _raw('second')])

    with ServiceBusTopicReceiver(settings=_settings()) as receiver:
        first = receiver.receive_one()
        assert first is not None
        with pytest.raises(ServiceBusSettlementError):
            receiver.receive_one()
        first.abandon()
        second = receiver.receive_one()
        assert second is not None
        second.dead_letter(reason='InvalidMessage', error_description='missing url')

    assert [message.message_id for message in sdk_receiver.abandoned] == ['first']
    assert sdk_receiver.dead_lettered[0][1:] == ('InvalidMessage', 'missing url')


def test_lock_can_be_renewed_and_completed(fake_sdk) -> None:
    install, _ = fake_sdk
    _, sdk_receiver = install([_raw('one')])

    with ServiceBusTopicReceiver(settings=_settings()) as receiver:
        delivery = receiver.receive_one()
        assert delivery is not None
        delivery.renew_lock()
        delivery.complete()

    assert [message.message_id for message in sdk_receiver.renewed] == ['one']
    assert delivery.state == ServiceBusDeliveryState.COMPLETED


def test_undecodable_message_is_abandoned_before_receiver_can_continue(fake_sdk) -> None:
    install, _ = fake_sdk
    invalid = _raw('invalid')
    invalid.application_properties = {b'\xff': 'invalid'}
    _, sdk_receiver = install([invalid, _raw('valid')])

    with ServiceBusTopicReceiver(settings=_settings()) as receiver:
        with pytest.raises(ServiceBusMessageError):
            receiver.receive_one()
        valid = receiver.receive_one()
        assert valid is not None
        valid.complete()

    assert [message.message_id for message in sdk_receiver.abandoned] == ['invalid']


def test_unsettled_delivery_is_abandoned_when_receiver_closes(fake_sdk) -> None:
    install, _ = fake_sdk
    client, sdk_receiver = install([_raw('one')])
    receiver = ServiceBusTopicReceiver(settings=_settings())

    with receiver:
        delivery = receiver.receive_one()
        assert delivery is not None

    assert delivery.state == ServiceBusDeliveryState.ABANDONED
    assert [message.message_id for message in sdk_receiver.abandoned] == ['one']
    assert sdk_receiver.closed is True
    assert client.closed is True


def test_close_is_idempotent_and_closed_receiver_cannot_reopen(fake_sdk) -> None:
    install, _ = fake_sdk
    install([])
    receiver = ServiceBusTopicReceiver(settings=_settings())
    receiver.open()
    receiver.close()
    receiver.close()

    with pytest.raises(ServiceBusConnectionError):
        receiver.open()


def test_close_error_is_sanitized(fake_sdk) -> None:
    install, _ = fake_sdk
    client, sdk_receiver = install([])
    receiver = ServiceBusTopicReceiver(settings=_settings())
    receiver.open()
    client.close_error = True
    sdk_receiver.close_error = True

    with pytest.raises(
        ServiceBusConnectionError, match='Could not close Service Bus receiver'
    ) as captured:
        receiver.close()

    assert 'private' not in str(captured.value)


def test_context_close_does_not_hide_primary_exception(fake_sdk) -> None:
    install, _ = fake_sdk
    client, _ = install([])
    client.close_error = True

    with pytest.raises(RuntimeError, match='primary'):
        with ServiceBusTopicReceiver(settings=_settings()):
            raise RuntimeError('primary')


def test_receive_batch_holds_multiple_deliveries_and_settles_individually(fake_sdk) -> None:
    install, _ = fake_sdk
    _, sdk_receiver = install([_raw('one'), _raw('two'), _raw('three')])

    with ServiceBusTopicReceiver(settings=_settings()) as receiver:
        deliveries = receiver.receive_batch(max_message_count=2)
        assert [item.message.message_id for item in deliveries] == ['one', 'two']
        with pytest.raises(ServiceBusSettlementError):
            receiver.receive_batch(max_message_count=1)
        deliveries[0].complete()
        deliveries[1].abandon()
        remaining = receiver.receive_batch(max_message_count=10)
        assert [item.message.message_id for item in remaining] == ['three']
        remaining[0].complete()

    assert sdk_receiver.receive_calls == [
        {'max_message_count': 2, 'max_wait_time': 3.0},
        {'max_message_count': 10, 'max_wait_time': 3.0},
    ]
    assert [message.message_id for message in sdk_receiver.completed] == ['one', 'three']
    assert [message.message_id for message in sdk_receiver.abandoned] == ['two']


def test_receive_batch_rejects_invalid_limit(fake_sdk) -> None:
    install, _ = fake_sdk
    install([])
    receiver = ServiceBusTopicReceiver(settings=_settings())

    for value in (0, -1, True):
        with pytest.raises(
            ServiceBusReceiveError, match='max_message_count must be a positive integer'
        ):
            receiver.receive_batch(max_message_count=value)


def test_receive_batch_abandons_all_messages_when_one_cannot_be_decoded(fake_sdk) -> None:
    install, _ = fake_sdk
    invalid = _raw('invalid')
    invalid.application_properties = {b'\xff': 'invalid'}
    _, sdk_receiver = install([_raw('valid'), invalid])

    with ServiceBusTopicReceiver(settings=_settings()) as receiver:
        with pytest.raises(ServiceBusMessageError):
            receiver.receive_batch(max_message_count=2)

    assert [message.message_id for message in sdk_receiver.abandoned] == ['valid', 'invalid']


def test_close_abandons_every_unsettled_batch_delivery(fake_sdk) -> None:
    install, _ = fake_sdk
    _, sdk_receiver = install([_raw('one'), _raw('two')])
    receiver = ServiceBusTopicReceiver(settings=_settings())

    with receiver:
        deliveries = receiver.receive_batch(max_message_count=2)
        deliveries[0].complete()

    assert deliveries[0].state == ServiceBusDeliveryState.COMPLETED
    assert deliveries[1].state == ServiceBusDeliveryState.ABANDONED
    assert [message.message_id for message in sdk_receiver.abandoned] == ['two']


def test_authentication_error_is_mapped_without_sdk_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def build(**_: Any) -> FakeClient:
        raise AzureAuthenticationError(message='private authentication details')

    monkeypatch.setattr(ServiceBusClient, 'from_connection_string', build)
    receiver = ServiceBusTopicReceiver(settings=_settings())

    with pytest.raises(
        ServiceBusAuthenticationError, match='Service Bus authentication failed'
    ) as captured:
        receiver.open()

    assert 'private authentication details' not in str(captured.value)
