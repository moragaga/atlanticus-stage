from __future__ import annotations

import pytest

from atlanticus.connectivity.http import (
    HttpResponse,
    HttpResponseError,
    HttpStatusError,
    HttpStreamError,
    HttpStreamResult,
    HttpTimeoutError,
)


def test_response_copies_sensitive_content_and_headers_without_showing_them() -> None:
    content = bytearray(b'{"token":"private"}')
    headers = {'Set-Cookie': 'session=private'}
    response = HttpResponse(
        status_code=200,
        method='GET',
        headers=headers,
        content=content,
    )
    content[:] = b'x' * len(content)
    headers['Set-Cookie'] = 'changed'

    assert response.decode_json() == {'token': 'private'}
    assert response.decode_text() == '{"token":"private"}'
    assert response.headers['set-cookie'] == 'session=private'
    assert 'private' not in repr(response)
    with pytest.raises(TypeError):
        response.headers['new'] = 'value'  # type: ignore[index]


def test_invalid_json_and_text_raise_safe_response_errors() -> None:
    invalid_json = HttpResponse(status_code=200, method='GET', headers={}, content=b'{secret')
    invalid_text = HttpResponse(status_code=200, method='GET', headers={}, content=b'\xff')

    with pytest.raises(HttpResponseError) as json_error:
        invalid_json.decode_json()
    with pytest.raises(HttpResponseError) as text_error:
        invalid_text.decode_text()

    assert 'secret' not in repr(json_error.value)
    assert json_error.value.__cause__ is None
    assert text_error.value.__cause__ is None


@pytest.mark.parametrize(
    'content',
    (
        b'{"key":1,"key":2}',
        b'{"value":NaN}',
        b'{"value":Infinity}',
        b'{"value":1e9999}',
        b'\xff',
    ),
)
def test_json_decoder_rejects_ambiguous_non_finite_or_non_utf8_payloads(content: bytes) -> None:
    response = HttpResponse(status_code=200, method='GET', headers={}, content=content)

    with pytest.raises(HttpResponseError):
        response.decode_json()


def test_stream_result_hides_headers_and_keeps_transfer_count() -> None:
    result = HttpStreamResult(
        status_code=200,
        method='GET',
        bytes_transferred=1024,
        headers={'X-Private': 'secret'},
    )

    assert result.bytes_transferred == 1024
    assert result.headers['x-private'] == 'secret'
    assert 'secret' not in repr(result)


@pytest.mark.parametrize(
    'values',
    (
        {'status_code': True, 'method': 'GET', 'headers': {}, 'content': b''},
        {'status_code': 99, 'method': 'GET', 'headers': {}, 'content': b''},
        {'status_code': 200, 'method': 'get', 'headers': {}, 'content': b''},
        {'status_code': 200, 'method': 'GET', 'headers': [], 'content': b''},
        {'status_code': 200, 'method': 'GET', 'headers': {}, 'content': 'text'},
    ),
)
def test_response_rejects_ambiguous_direct_contracts(values: dict[str, object]) -> None:
    with pytest.raises(TypeError):
        HttpResponse(**values)


@pytest.mark.parametrize('bytes_transferred', (True, -1, 1.5))
def test_stream_result_rejects_invalid_transfer_counts(bytes_transferred: object) -> None:
    with pytest.raises(TypeError):
        HttpStreamResult(
            status_code=200,
            method='GET',
            bytes_transferred=bytes_transferred,
            headers={},
        )


@pytest.mark.parametrize(
    'build',
    (
        lambda: HttpStatusError(status_code=True, method='GET'),
        lambda: HttpStatusError(status_code=99, method='GET'),
        lambda: HttpStatusError(status_code=500, method='get'),
        lambda: HttpTimeoutError(phase='read'),
        lambda: HttpTimeoutError(phase=None, bytes_transferred=0),
        lambda: HttpStreamError(bytes_transferred=True),
        lambda: HttpStreamError(bytes_transferred=-1),
    ),
)
def test_error_models_reject_ambiguous_direct_contracts(build) -> None:
    with pytest.raises(TypeError):
        build()
