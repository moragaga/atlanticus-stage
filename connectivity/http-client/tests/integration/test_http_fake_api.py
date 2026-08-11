from __future__ import annotations

import os
import time
from io import BytesIO
from typing import Any

import pytest

from atlanticus.connectivity.http import (
    HttpAuthMode,
    HttpClient,
    HttpSettings,
    HttpStatusError,
    HttpTimeoutError,
    HttpTimeoutPhase,
)

pytestmark = pytest.mark.integration

_BASE_URL = os.getenv('ATLANTICUS_FAKE_HTTP_BASE_URL', 'http://http-fake-api:8080')
_BEARER_TOKEN = os.getenv('ATLANTICUS_FAKE_HTTP_BEARER_TOKEN', 'atlanticus-bearer-token')
_BASIC_USERNAME = os.getenv('ATLANTICUS_FAKE_HTTP_BASIC_USERNAME', 'atlanticus-user')
_BASIC_PASSWORD = os.getenv('ATLANTICUS_FAKE_HTTP_BASIC_PASSWORD', 'atlanticus-password')


def test_public_bearer_and_basic_contracts_against_fake_api() -> None:
    _require_integration()
    _wait_until_ready()

    modes = (
        ('public', _settings(suffix='PUBLIC', auth_mode=HttpAuthMode.NONE)),
        ('bearer', _settings(suffix='BEARER', auth_mode=HttpAuthMode.BEARER)),
        ('basic', _settings(suffix='BASIC', auth_mode=HttpAuthMode.BASIC)),
    )
    for route, settings in modes:
        with HttpClient(settings=settings) as client:
            first = client.request(
                'GET',
                f'{route}/json',
                params=[('tag', 'one'), ('tag', 'two')],
                headers={'X-Correlation-Id': f'{route}-correlation'},
            )
            second = client.request('GET', f'{route}/json')
            payload = first.decode_json()

            assert payload['ok'] is True
            assert payload['auth_mode'] == route
            assert payload['query'] == {'tag': ['one', 'two']}
            assert payload['correlation_id'] == f'{route}-correlation'
            assert first.headers['x-fake-connection-id'] == second.headers['x-fake-connection-id']

    public = HttpClient(settings=_settings(suffix='PUBLIC', auth_mode=HttpAuthMode.NONE))
    with public:
        assert public.request_text('GET', 'public/text') == 'respuesta-atlanticus'
        assert public.request_bytes('GET', 'public/bytes') == b'\x00atlanticus-http\xff'
        echoed = public.request_json(
            'POST',
            'public/echo',
            headers={'X-Correlation-Id': 'post-correlation'},
            json_data={'value': 42},
        )
        output = BytesIO()
        stream_result = public.stream_to(
            stream=output,
            endpoint='public/stream',
            chunk_size=4096,
        )

    assert echoed == {
        'method': 'POST',
        'body': {'value': 42},
        'content_type': 'application/json',
        'correlation_id': 'post-correlation',
    }
    assert output.getvalue().startswith(b'atlanticus-http-stream-')
    assert stream_result.bytes_transferred == len(output.getvalue())


def test_invalid_credentials_status_errors_and_timeout_are_safe_and_not_retried() -> None:
    _require_integration()
    _wait_until_ready()
    admin = HttpClient(settings=_settings(suffix='PUBLIC', auth_mode=HttpAuthMode.NONE))
    with admin:
        admin.request_json('POST', 'admin/reset')

    invalid_bearer = HttpSettings(
        base_url=_BASE_URL,
        auth_mode=HttpAuthMode.BEARER,
        bearer_token='wrong-bearer-token',
        allow_insecure_http=True,
    )
    invalid_basic = HttpSettings(
        base_url=_BASE_URL,
        auth_mode=HttpAuthMode.BASIC,
        username='wrong-user',
        password='wrong-password',
        allow_insecure_http=True,
    )
    valid_bearer = _settings(suffix='BEARER', auth_mode=HttpAuthMode.BEARER)
    valid_basic = _settings(suffix='BASIC', auth_mode=HttpAuthMode.BASIC)
    for settings, endpoint in (
        (invalid_bearer, 'bearer/json'),
        (invalid_basic, 'basic/json'),
        (valid_bearer, 'basic/json'),
        (valid_bearer, 'public/json'),
        (valid_basic, 'bearer/json'),
    ):
        with HttpClient(settings=settings) as client:
            with pytest.raises(HttpStatusError) as captured:
                client.request('GET', endpoint)
        assert captured.value.status_code == 401
        assert 'wrong-' not in repr(captured.value)

    public = HttpClient(settings=_settings(suffix='PUBLIC', auth_mode=HttpAuthMode.NONE))
    with public:
        for status_code in (404, 503):
            with pytest.raises(HttpStatusError) as captured:
                public.request('GET', f'public/status/{status_code}')
            assert captured.value.status_code == status_code
            assert 'private-response-body' not in repr(captured.value)

    timeout_settings = HttpSettings(
        base_url=_BASE_URL,
        auth_mode=HttpAuthMode.NONE,
        connect_timeout_seconds=2,
        read_timeout_seconds=0.1,
        write_timeout_seconds=2,
        pool_timeout_seconds=2,
        allow_insecure_http=True,
    )
    with HttpClient(settings=timeout_settings) as timeout_client:
        with pytest.raises(HttpTimeoutError) as captured:
            timeout_client.request(
                'GET',
                'public/slow',
                params={'delay_seconds': '0.5'},
            )

    assert captured.value.phase == HttpTimeoutPhase.READ
    assert captured.value.__cause__ is None
    with HttpClient(settings=_settings(suffix='PUBLIC', auth_mode=HttpAuthMode.NONE)) as client:
        counts = client.request_json('GET', 'admin/counts')['counts']
    assert counts['public.slow'] == 1


def _settings(*, suffix: str, auth_mode: HttpAuthMode) -> HttpSettings:
    values: dict[str, Any] = {
        f'HTTP_BASE_URL_{suffix}': _BASE_URL,
        f'HTTP_AUTH_MODE_{suffix}': auth_mode.value,
        f'HTTP_CONNECT_TIMEOUT_SECONDS_{suffix}': '2',
        f'HTTP_READ_TIMEOUT_SECONDS_{suffix}': '2',
        f'HTTP_WRITE_TIMEOUT_SECONDS_{suffix}': '2',
        f'HTTP_POOL_TIMEOUT_SECONDS_{suffix}': '2',
        f'HTTP_ALLOW_INSECURE_HTTP_{suffix}': 'true',
    }
    if auth_mode == HttpAuthMode.BEARER:
        values[f'HTTP_BEARER_TOKEN_{suffix}'] = _BEARER_TOKEN
    if auth_mode == HttpAuthMode.BASIC:
        values[f'HTTP_USERNAME_{suffix}'] = _BASIC_USERNAME
        values[f'HTTP_PASSWORD_{suffix}'] = _BASIC_PASSWORD
    return HttpSettings.from_mapping(values=values, suffix=suffix)


def _require_integration() -> None:
    if os.getenv('ATLANTICUS_RUN_HTTP_INTEGRATION') != '1':
        pytest.skip('HTTP integration is disabled. Set ATLANTICUS_RUN_HTTP_INTEGRATION=1.')


def _wait_until_ready() -> None:
    settings = HttpSettings(
        base_url=_BASE_URL,
        auth_mode=HttpAuthMode.NONE,
        connect_timeout_seconds=1,
        read_timeout_seconds=1,
        write_timeout_seconds=1,
        pool_timeout_seconds=1,
        allow_insecure_http=True,
    )
    last_error: Exception | None = None
    for _ in range(60):
        try:
            with HttpClient(settings=settings) as client:
                if client.request_json('GET', 'health') == {'status': 'ok'}:
                    return
        except Exception as error:
            last_error = error
            time.sleep(1)
    raise RuntimeError('HTTP Fake API did not become ready') from last_error
