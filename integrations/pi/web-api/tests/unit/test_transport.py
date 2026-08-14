from __future__ import annotations

from typing import Any

import pytest
from atlanticus.connectivity.http import (
    HttpAuthMode,
    HttpClient,
    HttpConnectionError,
    HttpRequestError,
    HttpResponseError,
    HttpSettings,
    HttpStatusError,
    HttpTimeoutError,
    HttpTimeoutPhase,
)
from atlanticus.integrations.pi.web_api import (
    PiWebApiConnectionError,
    PiWebApiRequestError,
    PiWebApiResponseError,
    PiWebApiStatusError,
    PiWebApiTimeoutError,
)
from atlanticus.integrations.pi.web_api.transport import PiWebApiTransport


def _transport() -> tuple[PiWebApiTransport, HttpClient]:
    client = HttpClient(
        settings=HttpSettings(
            base_url='https://pi.example/piwebapi/',
            auth_mode=HttpAuthMode.BASIC,
            username='user',
            password='password',
        )
    )
    return PiWebApiTransport(http_client=client), client


@pytest.mark.parametrize(
    ('source_error', 'expected_type'),
    (
        (HttpConnectionError('failed'), PiWebApiConnectionError),
        (HttpRequestError('failed'), PiWebApiRequestError),
        (HttpResponseError('failed'), PiWebApiResponseError),
    ),
)
def test_transport_translates_http_errors(
    monkeypatch: pytest.MonkeyPatch,
    source_error: Exception,
    expected_type: type[Exception],
) -> None:
    transport, client = _transport()

    def request_json(*args: Any, **kwargs: Any) -> Any:
        raise source_error

    monkeypatch.setattr(client, 'request_json', request_json)

    with pytest.raises(expected_type):
        transport.get_json('points/multiple')


def test_transport_exposes_timeout_as_retryable_pi_error(monkeypatch: pytest.MonkeyPatch) -> None:
    transport, client = _transport()

    def request_json(*args: Any, **kwargs: Any) -> Any:
        raise HttpTimeoutError(phase=HttpTimeoutPhase.READ)

    monkeypatch.setattr(client, 'request_json', request_json)

    with pytest.raises(PiWebApiTimeoutError) as captured:
        transport.get_json('points/multiple')

    assert captured.value.phase == 'read'


def test_transport_preserves_http_status_code(monkeypatch: pytest.MonkeyPatch) -> None:
    transport, client = _transport()

    def request_json(*args: Any, **kwargs: Any) -> Any:
        raise HttpStatusError(status_code=503, method='GET')

    monkeypatch.setattr(client, 'request_json', request_json)

    with pytest.raises(PiWebApiStatusError) as captured:
        transport.get_json('points/multiple')

    assert captured.value.status_code == 503
