from __future__ import annotations

import os
import time
import urllib.request
from uuid import uuid4

import pytest
from azure.servicebus import ServiceBusClient, ServiceBusMessage as AzureServiceBusMessage

from atlanticus.connectivity.service_bus import (
    ServiceBusDeliveryState,
    ServiceBusSettings,
    ServiceBusTopicReceiver,
)

pytestmark = pytest.mark.integration

_RUN = os.getenv('ATLANTICUS_RUN_SERVICE_BUS_INTEGRATION') == '1'
_CONNECTION_STRING = os.getenv(
    'ATLANTICUS_SERVICE_BUS_CONNECTION_STRING',
    'Endpoint=sb://servicebus-emulator;SharedAccessKeyName=RootManageSharedAccessKey;'
    'SharedAccessKey=SAS_KEY_VALUE;UseDevelopmentEmulator=true;',
)
_TOPIC_NAME = os.getenv('ATLANTICUS_SERVICE_BUS_TOPIC_NAME', 'atlanticus.events')
_SUBSCRIPTION_NAME = os.getenv('ATLANTICUS_SERVICE_BUS_SUBSCRIPTION_NAME', 'atlanticus.integration')
_READY_URL = os.getenv('ATLANTICUS_SERVICE_BUS_READY_URL', 'http://servicebus-emulator:5300/health')


@pytest.mark.skipif(not _RUN, reason='Service Bus emulator integration is disabled')
def test_service_bus_contract_against_official_emulator() -> None:
    _wait_until_ready()
    run_id = uuid4().hex
    backlog_ids = [f'{run_id}-backlog-{index}' for index in range(1, 6)]
    late_id = f'{run_id}-arrived-during-run'
    unsettled_id = f'{run_id}-unsettled-on-close'
    _send_messages(
        *(
            AzureServiceBusMessage(
                f'{{"delivery":{index}}}'.encode(),
                message_id=message_id,
            )
            for index, message_id in enumerate(backlog_ids, start=1)
        )
    )
    settings = ServiceBusSettings(
        connection_string=_CONNECTION_STRING,
        topic_name=_TOPIC_NAME,
        subscription_name=_SUBSCRIPTION_NAME,
        max_wait_time_seconds=2,
    )
    completed_ids: list[str | None] = []

    with ServiceBusTopicReceiver(settings=settings) as receiver:
        first = receiver.receive_one()
        assert first is not None
        assert first.message.message_id in backlog_ids
        first.complete()
        assert first.state == ServiceBusDeliveryState.COMPLETED
        completed_ids.append(first.message.message_id)

        _send_messages(AzureServiceBusMessage(b'{"delivery":6}', message_id=late_id))

        for _ in range(5):
            delivery = receiver.receive_one()
            assert delivery is not None
            delivery.complete()
            completed_ids.append(delivery.message.message_id)

        assert receiver.receive_one() is None

    assert len(completed_ids) == 6
    assert set(completed_ids) == {*backlog_ids, late_id}

    _send_messages(AzureServiceBusMessage(b'{"delivery":7}', message_id=unsettled_id))
    with ServiceBusTopicReceiver(settings=settings) as receiver:
        unsettled = receiver.receive_one()
        assert unsettled is not None
        assert unsettled.message.message_id == unsettled_id

    assert unsettled.state == ServiceBusDeliveryState.ABANDONED

    with ServiceBusTopicReceiver(settings=settings) as receiver:
        redelivered = receiver.receive_one()
        assert redelivered is not None
        assert redelivered.message.message_id == unsettled_id
        redelivered.dead_letter(
            reason='IntegrationValidated',
            error_description='Intentional dead-letter after close verification',
        )
        assert redelivered.state == ServiceBusDeliveryState.DEAD_LETTERED
        assert receiver.receive_one() is None


def _wait_until_ready() -> None:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(_READY_URL, timeout=3) as response:
                if 200 <= response.status < 300:
                    return
        except Exception:
            pass
        time.sleep(2)
    raise RuntimeError('Service Bus emulator did not become ready')


def _send_messages(*messages: AzureServiceBusMessage) -> None:
    with ServiceBusClient.from_connection_string(
        conn_str=_CONNECTION_STRING,
        logging_enable=False,
        retry_total=0,
    ) as client:
        with client.get_topic_sender(topic_name=_TOPIC_NAME) as sender:
            sender.send_messages(list(messages))
