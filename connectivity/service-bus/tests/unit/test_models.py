from __future__ import annotations

from datetime import UTC, datetime

import pytest

from atlanticus.connectivity.service_bus import ServiceBusMessage


def test_message_keeps_opaque_body_and_immutable_properties() -> None:
    body = bytearray(b'{"url":"private"}')
    properties = {'source': 'notpii'}
    message = ServiceBusMessage(
        body=body,
        message_id='message-1',
        enqueued_time_utc=datetime(2026, 7, 22, tzinfo=UTC),
        application_properties=properties,
    )
    body[:] = b'x' * len(body)
    properties['source'] = 'changed'

    assert message.body == b'{"url":"private"}'
    assert message.decode_text() == '{"url":"private"}'
    assert message.application_properties == {'source': 'notpii'}
    assert 'private' not in repr(message)
    assert 'notpii' not in repr(message)

    with pytest.raises(TypeError):
        message.application_properties['new'] = 'value'  # type: ignore[index]
