from __future__ import annotations

import math

import pytest

from atlanticus.connectivity.service_bus import ServiceBusConfigurationError, ServiceBusSettings


def test_settings_preserve_connection_string_exactly() -> None:
    value = ' Endpoint=sb://private;SharedAccessKey= secret value; '
    settings = ServiceBusSettings(
        connection_string=value,
        topic_name='topic',
        subscription_name='subscription',
        max_wait_time_seconds=2,
    )

    assert settings.connection_string == value
    assert settings.max_wait_time_seconds == 2.0
    assert value not in repr(settings)


@pytest.mark.parametrize('value', (None, 1, b'value'))
def test_connection_string_must_be_text(value: object) -> None:
    with pytest.raises(ServiceBusConfigurationError):
        ServiceBusSettings(
            connection_string=value,  # type: ignore[arg-type]
            topic_name='topic',
            subscription_name='subscription',
        )


def test_empty_connection_string_is_rejected() -> None:
    with pytest.raises(ServiceBusConfigurationError):
        ServiceBusSettings(
            connection_string='',
            topic_name='topic',
            subscription_name='subscription',
        )


@pytest.mark.parametrize('field_name', ('topic_name', 'subscription_name'))
def test_entity_names_reject_surrounding_whitespace(field_name: str) -> None:
    values = {
        'connection_string': 'Endpoint=sb://private',
        'topic_name': 'topic',
        'subscription_name': 'subscription',
    }
    values[field_name] = ' value '
    with pytest.raises(ServiceBusConfigurationError):
        ServiceBusSettings(**values)


@pytest.mark.parametrize('value', (0, -1, True, '2', math.inf, math.nan))
def test_max_wait_time_must_be_a_finite_positive_number(value: object) -> None:
    with pytest.raises(ServiceBusConfigurationError):
        ServiceBusSettings(
            connection_string='Endpoint=sb://private',
            topic_name='topic',
            subscription_name='subscription',
            max_wait_time_seconds=value,  # type: ignore[arg-type]
        )
