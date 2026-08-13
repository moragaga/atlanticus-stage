from __future__ import annotations

from io import BytesIO
from typing import Any

import httpx
import pytest

import atlanticus.connectivity.http.client as client_module
from atlanticus.connectivity.http import (
    HttpAuthMode,
    HttpClient,
    HttpConnectionError,
    HttpRequestError,
    HttpResponseError,
    HttpSettings,
    HttpStatusError,
    HttpStreamError,
    HttpTimeoutError,
    HttpTimeoutPhase,
)


def _settings(auth_mode: HttpAuthMode = HttpAuthMode.NONE) -> HttpSettings:
    values: dict[str, Any] = {
        'base_url': 'https://api.example.test/root',
        'auth_mode': auth_mode,
    }
    if auth_mode == HttpAuthMode.BEARER:
        values['bearer_token'] = 'private-token'
    if auth_mode == HttpAuthMode.BASIC:
        values['username'] = 'api-user'
        values['password'] = 'private-password'
    return HttpSettings(**values)


def _install_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler,
) -> list[dict[str, Any]]:
    real_client = httpx.Client
    created: list[dict[str, Any]] = []

    def build(**kwargs: Any) -> httpx.Client:
        created.append(dict(kwargs))
        kwargs['transport'] = httpx.MockTransport(handler)
        return real_client(**kwargs)

    monkeypatch.setattr(client_module.httpx, 'Client', build)
    return created


@pytest.mark.parametrize(
    ('auth_mode', 'expected_authorization'),
    (
        (HttpAuthMode.NONE, None),
        (HttpAuthMode.BEARER, 'Bearer private-token'),
        (HttpAuthMode.BASIC, 'Basic YXBpLXVzZXI6cHJpdmF0ZS1wYXNzd29yZA=='),
    ),
)
def test_authentication_modes_send_only_the_expected_header(
    monkeypatch: pytest.MonkeyPatch,
    auth_mode: HttpAuthMode,
    expected_authorization: str | None,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={'ok': True}, request=request)

    created = _install_transport(monkeypatch, handler)
    with HttpClient(settings=_settings(auth_mode)) as client:
        assert client.request_json('GET', 'json') == {'ok': True}
        assert client.request_json('GET', 'json') == {'ok': True}

    assert len(created) == 1
    assert created[0]['follow_redirects'] is False
    assert created[0]['trust_env'] is False
    assert created[0]['verify'] is True
    assert str(requests[0].url) == 'https://api.example.test/root/json'
    assert requests[0].headers.get('Authorization') == expected_authorization


def test_basic_authentication_preserves_whitespace_in_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, request=request)

    _install_transport(monkeypatch, handler)
    settings = HttpSettings(
        base_url='https://api.example.test',
        auth_mode=HttpAuthMode.BASIC,
        username=' api-user ',
        password=' private-password ',
    )

    with HttpClient(settings=settings) as client:
        client.request('GET', 'ready')

    assert requests[0].headers['Authorization'] == (
        'Basic IGFwaS11c2VyIDogcHJpdmF0ZS1wYXNzd29yZCA='
    )


def test_generic_request_supports_json_text_bytes_params_and_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith('/text'):
            return httpx.Response(200, text='respuesta', request=request)
        if request.url.path.endswith('/bytes'):
            return httpx.Response(200, content=b'\x00\x01', request=request)
        return httpx.Response(201, json={'created': True}, request=request)

    _install_transport(monkeypatch, handler)
    with HttpClient(settings=_settings()) as client:
        payload = client.request_json(
            'POST',
            'items',
            params=[('tag', 'one'), ('tag', 'two')],
            headers={'X-Correlation-Id': 'safe-id'},
            json_data={'name': 'item'},
        )
        text = client.request_text('GET', 'text')
        content = client.request_bytes('GET', 'bytes')

    assert payload == {'created': True}
    assert text == 'respuesta'
    assert content == b'\x00\x01'
    assert requests[0].method == 'POST'
    assert requests[0].url.params.get_list('tag') == ['one', 'two']
    assert requests[0].headers['X-Correlation-Id'] == 'safe-id'
    assert requests[0].read() == b'{"name":"item"}'


@pytest.mark.parametrize(
    'method',
    ('GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'),
)
def test_standard_http_methods_are_supported(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204, request=request)

    _install_transport(monkeypatch, handler)
    with HttpClient(settings=_settings()) as client:
        response = client.request(method, 'resource')

    assert response.status_code == 204
    assert requests[0].method == method


@pytest.mark.parametrize(
    ('method', 'endpoint', 'headers', 'json_data', 'content'),
    (
        ('TRACE', 'items', None, None, None),
        ('GET', 'https://other.example.test/items', None, None, None),
        ('GET', 'items?token=private', None, None, None),
        ('GET', '../items', None, None, None),
        ('GET', '%2e%2e/items', None, None, None),
        ('GET', 'items', {'Authorization': 'Bearer override'}, None, None),
        ('GET', 'items', {'X-Bad\nHeader': 'value'}, None, None),
        ('GET', 'items', {'X-Header': 'bad\r\nvalue'}, None, None),
        ('POST', 'items', None, {'value': 1}, b'body'),
    ),
)
def test_unsafe_requests_are_rejected_before_network_use(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    endpoint: str,
    headers: dict[str, str] | None,
    json_data: Any,
    content: bytes | None,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, request=request)

    _install_transport(monkeypatch, handler)
    client = HttpClient(settings=_settings())
    kwargs: dict[str, Any] = {'headers': headers, 'content': content}
    if json_data is not None:
        kwargs['json_data'] = json_data

    with pytest.raises(HttpRequestError):
        client.request(method, endpoint, **kwargs)

    assert calls == 0


def test_status_error_does_not_include_url_headers_or_response_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            headers={'Set-Cookie': 'private-cookie'},
            content=b'private-body',
            request=request,
        )

    _install_transport(monkeypatch, handler)
    client = HttpClient(settings=_settings())

    with pytest.raises(HttpStatusError) as captured:
        client.request('GET', 'failed', params={'token': 'private-query'})

    error_text = repr(captured.value)
    assert captured.value.status_code == 503
    assert 'private' not in error_text
    assert 'example.test' not in error_text


@pytest.mark.parametrize(
    ('sdk_error', 'phase'),
    (
        (httpx.ConnectTimeout('private'), HttpTimeoutPhase.CONNECT),
        (httpx.ReadTimeout('private'), HttpTimeoutPhase.READ),
        (httpx.WriteTimeout('private'), HttpTimeoutPhase.WRITE),
        (httpx.PoolTimeout('private'), HttpTimeoutPhase.POOL),
    ),
)
def test_timeout_is_classified_and_never_retried(
    monkeypatch: pytest.MonkeyPatch,
    sdk_error: httpx.TimeoutException,
    phase: HttpTimeoutPhase,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise sdk_error

    _install_transport(monkeypatch, handler)
    client = HttpClient(settings=_settings())

    with pytest.raises(HttpTimeoutError) as captured:
        client.request('POST', 'items', json_data={'operation': 'must-not-repeat'})

    assert calls == 1
    assert captured.value.phase == phase
    assert 'private' not in repr(captured.value)
    assert captured.value.__cause__ is None


def test_network_error_is_sanitized_and_never_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError('https://api.example.test?token=private', request=request)

    _install_transport(monkeypatch, handler)
    client = HttpClient(settings=_settings())

    with pytest.raises(HttpConnectionError) as captured:
        client.request('GET', 'items')

    assert calls == 1
    assert 'private' not in repr(captured.value)
    assert 'example.test' not in repr(captured.value)
    assert captured.value.__cause__ is None


def test_streaming_writes_in_chunks_without_accumulating_the_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'abcdefghij', request=request)

    _install_transport(monkeypatch, handler)
    output = BytesIO()
    with HttpClient(settings=_settings()) as client:
        result = client.stream_to(stream=output, endpoint='stream', chunk_size=3)

    assert output.getvalue() == b'abcdefghij'
    assert result.bytes_transferred == 10
    assert result.status_code == 200


def test_partial_stream_write_reports_only_the_written_byte_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class PartialStream:
        def write(self, value: bytes) -> int:
            return max(0, len(value) - 1)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'abcdef', request=request)

    _install_transport(monkeypatch, handler)
    client = HttpClient(settings=_settings())

    with pytest.raises(HttpStreamError) as captured:
        client.stream_to(stream=PartialStream(), endpoint='stream', chunk_size=3)

    assert captured.value.bytes_transferred == 2


def test_closed_client_cannot_be_reopened(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request)

    _install_transport(monkeypatch, handler)
    client = HttpClient(settings=_settings())
    client.request('GET', 'ready')
    client.close()
    client.close()

    with pytest.raises(HttpConnectionError):
        client.request('GET', 'ready')


def test_declared_response_size_is_rejected_before_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = HttpSettings(
        base_url='https://api.example.test',
        auth_mode=HttpAuthMode.NONE,
        max_response_bytes=4,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={'Content-Length': '5'},
            content=b'abcde',
            request=request,
        )

    _install_transport(monkeypatch, handler)

    with pytest.raises(HttpResponseError):
        HttpClient(settings=settings).request('GET', 'large')


def test_actual_response_size_is_limited_without_content_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnknownLengthStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'abc'
            yield b'def'

    settings = HttpSettings(
        base_url='https://api.example.test',
        auth_mode=HttpAuthMode.NONE,
        max_response_bytes=5,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=UnknownLengthStream(), request=request)

    _install_transport(monkeypatch, handler)

    with pytest.raises(HttpResponseError):
        HttpClient(settings=settings).request('GET', 'large')


@pytest.mark.parametrize(
    ('field', 'value'),
    (
        ('method', True),
        ('endpoint', 7),
        ('params', 'token=private'),
        ('headers', [('X-Test', 'value')]),
        ('content', object()),
    ),
)
def test_request_rejects_ambiguous_types_before_opening_connection(
    field: str,
    value: object,
) -> None:
    client = HttpClient(settings=_settings())
    values: dict[str, object] = {'method': 'GET', 'endpoint': 'items'}
    values[field] = value

    with pytest.raises(HttpRequestError):
        client.request(**values)

    assert client._client is None


@pytest.mark.parametrize('json_data', ({'value': float('nan')}, {'value': float('inf')}))
def test_request_rejects_non_finite_json_before_network_use(json_data: object) -> None:
    client = HttpClient(settings=_settings())

    with pytest.raises(HttpRequestError):
        client.request('POST', 'items', json_data=json_data)

    assert client._client is None


def test_stream_write_exception_is_sanitized_and_reports_confirmed_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingStream:
        def write(self, value: bytes) -> int:
            raise TypeError('private-stream-value')

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'abcdef', request=request)

    _install_transport(monkeypatch, handler)

    with pytest.raises(HttpStreamError) as captured:
        HttpClient(settings=_settings()).stream_to(
            stream=FailingStream(),
            endpoint='stream',
        )

    assert captured.value.bytes_transferred == 0
    assert 'private' not in repr(captured.value)


def test_response_close_failure_does_not_hide_status_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingResponse:
        status_code = 503
        headers: dict[str, str] = {}

        def close(self) -> None:
            raise TypeError('private-close-value')

    monkeypatch.setattr(HttpClient, '_send', lambda *args, **kwargs: FailingResponse())

    with pytest.raises(HttpStatusError) as captured:
        HttpClient(settings=_settings()).request('GET', 'failed')

    assert captured.value.status_code == 503
    assert 'private' not in repr(captured.value)


def test_response_close_failure_after_success_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingResponse:
        status_code = 200
        headers: dict[str, str] = {}

        def iter_bytes(self, *, chunk_size: int):
            yield b'ok'

        def close(self) -> None:
            raise TypeError('private-close-value')

    monkeypatch.setattr(HttpClient, '_send', lambda *args, **kwargs: FailingResponse())

    with pytest.raises(HttpConnectionError) as captured:
        HttpClient(settings=_settings()).request('GET', 'ready')

    assert 'private' not in repr(captured.value)


def test_client_close_failure_preserves_an_existing_context_error() -> None:
    class FailingClient:
        def close(self) -> None:
            raise TypeError('private-client-close-value')

    client = HttpClient(settings=_settings())
    client._client = FailingClient()

    with pytest.raises(ValueError, match='business-error'):
        with client:
            raise ValueError('business-error')


def test_safe_error_mapper_never_copies_uncontrolled_messages() -> None:
    mapped = client_module._safe_error(TypeError('private-token'))

    assert mapped.error_type == 'TypeError'
    assert 'private-token' not in mapped.message
