from __future__ import annotations

import base64
import hmac
import json
import os
import sys
import time
from collections import Counter
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from typing import Any
from urllib.parse import parse_qs, urlsplit

_BEARER_TOKEN = os.getenv('ATLANTICUS_FAKE_HTTP_BEARER_TOKEN', 'atlanticus-bearer-token')
_BASIC_USERNAME = os.getenv('ATLANTICUS_FAKE_HTTP_BASIC_USERNAME', 'atlanticus-user')
_BASIC_PASSWORD = os.getenv('ATLANTICUS_FAKE_HTTP_BASIC_PASSWORD', 'atlanticus-password')
_MAX_REQUEST_BODY_BYTES = 1024 * 1024
_STREAM_PAYLOAD = b'atlanticus-http-stream-' * 8192


class FakeApiState:
    def __init__(self) -> None:
        self._lock = Lock()
        self._counts: Counter[str] = Counter()

    def increment(self, key: str) -> int:
        with self._lock:
            self._counts[key] += 1
            return self._counts[key]

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    def handle_error(self, request: object, client_address: object) -> None:
        error = sys.exc_info()[1]
        if isinstance(error, (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


class FakeApiHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'AtlanticusFakeHttp/0.1.0'
    sys_version = ''
    state = FakeApiState()

    def do_GET(self) -> None:
        self._handle_request()

    def do_POST(self) -> None:
        self._handle_request()

    def do_PUT(self) -> None:
        self._handle_request()

    def do_PATCH(self) -> None:
        self._handle_request()

    def do_DELETE(self) -> None:
        self._handle_request()

    def do_OPTIONS(self) -> None:
        self._handle_request()

    def log_message(self, _: str, *__: object) -> None:
        return

    def _handle_request(self) -> None:
        parsed = urlsplit(self.path)
        segments = [segment for segment in parsed.path.split('/') if segment]
        if segments == ['health']:
            self._send_json(HTTPStatus.OK, {'status': 'ok'})
            return
        if segments == ['admin', 'counts']:
            self._send_json(HTTPStatus.OK, {'counts': self.state.snapshot()})
            return
        if segments == ['admin', 'reset'] and self.command == 'POST':
            self.state.reset()
            self._send_json(HTTPStatus.OK, {'reset': True})
            return
        if len(segments) < 2 or segments[0] not in {'public', 'bearer', 'basic'}:
            self._send_json(HTTPStatus.NOT_FOUND, {'error': 'not_found'})
            return

        auth_mode = segments[0]
        operation = '/'.join(segments[1:])
        if not self._is_authorized(auth_mode):
            self._send_json(
                HTTPStatus.UNAUTHORIZED,
                {'error': 'unauthorized'},
                headers={'WWW-Authenticate': _challenge(auth_mode)},
            )
            return

        self.state.increment(f'{auth_mode}.{operation}')
        if operation == 'json':
            self._send_json(
                HTTPStatus.OK,
                {
                    'ok': True,
                    'auth_mode': auth_mode,
                    'method': self.command,
                    'query': parse_qs(parsed.query, keep_blank_values=True),
                    'correlation_id': self.headers.get('X-Correlation-Id'),
                    'connection_id': str(id(self.connection)),
                },
            )
            return
        if operation == 'text':
            self._send_bytes(
                HTTPStatus.OK,
                'respuesta-atlanticus'.encode(),
                content_type='text/plain; charset=utf-8',
            )
            return
        if operation == 'bytes':
            self._send_bytes(
                HTTPStatus.OK,
                b'\x00atlanticus-http\xff',
                content_type='application/octet-stream',
            )
            return
        if operation == 'stream':
            self._send_stream(_STREAM_PAYLOAD)
            return
        if operation == 'echo' and self.command in {'POST', 'PUT', 'PATCH'}:
            self._send_json(
                HTTPStatus.OK,
                {
                    'method': self.command,
                    'body': self._read_json_body(),
                    'content_type': self.headers.get('Content-Type'),
                    'correlation_id': self.headers.get('X-Correlation-Id'),
                },
            )
            return
        if operation.startswith('status/'):
            self._send_status(operation)
            return
        if operation == 'slow':
            delay = float(parse_qs(parsed.query).get('delay_seconds', ['1'])[0])
            time.sleep(max(0.0, min(delay, 5.0)))
            self._send_json(HTTPStatus.OK, {'delayed': True})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {'error': 'not_found'})

    def _is_authorized(self, auth_mode: str) -> bool:
        authorization = self.headers.get('Authorization')
        if auth_mode == 'public':
            return authorization is None
        if auth_mode == 'bearer':
            expected = f'Bearer {_BEARER_TOKEN}'
            return authorization is not None and hmac.compare_digest(authorization, expected)
        credentials = base64.b64encode(f'{_BASIC_USERNAME}:{_BASIC_PASSWORD}'.encode()).decode()
        expected = f'Basic {credentials}'
        return authorization is not None and hmac.compare_digest(authorization, expected)

    def _read_json_body(self) -> Any:
        length = int(self.headers.get('Content-Length', '0'))
        if length < 0 or length > _MAX_REQUEST_BODY_BYTES:
            raise ValueError('request body is too large')
        payload = self.rfile.read(length)
        return json.loads(payload)

    def _send_status(self, operation: str) -> None:
        try:
            status_code = int(operation.removeprefix('status/'))
            status = HTTPStatus(status_code)
        except ValueError, TypeError:
            status = HTTPStatus.BAD_REQUEST
        self._send_json(status, {'error': 'private-response-body-must-not-leak'})

    def _send_json(
        self,
        status: HTTPStatus,
        value: Any,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        payload = json.dumps(value, separators=(',', ':')).encode()
        self._send_bytes(status, payload, content_type='application/json', headers=headers)

    def _send_bytes(
        self,
        status: HTTPStatus,
        payload: bytes,
        *,
        content_type: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('X-Fake-Connection-Id', str(id(self.connection)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != 'HEAD':
            try:
                self.wfile.write(payload)
            except BrokenPipeError, ConnectionResetError:
                pass

    def _send_stream(self, payload: bytes) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('X-Fake-Connection-Id', str(id(self.connection)))
        self.end_headers()
        try:
            for offset in range(0, len(payload), 4096):
                self.wfile.write(payload[offset : offset + 4096])
                self.wfile.flush()
        except BrokenPipeError, ConnectionResetError:
            pass


def _challenge(auth_mode: str) -> str:
    return 'Basic realm="atlanticus"' if auth_mode == 'basic' else 'Bearer'


def main() -> None:
    host = os.getenv('ATLANTICUS_FAKE_HTTP_HOST', '0.0.0.0')
    port = int(os.getenv('ATLANTICUS_FAKE_HTTP_PORT', '8080'))
    server = QuietThreadingHTTPServer((host, port), FakeApiHandler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == '__main__':
    main()
