from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from atlanticus.connectivity.http import HttpAuthMode, HttpSettings
from atlanticus.integrations.pi.web_api import PiWebApiClient, PiWebApiSettings


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path != '/piwebapi/points/multiple':
            self.send_response(404)
            self.end_headers()
            return

        expected_auth = 'Basic ' + base64.b64encode(b'user:password').decode('ascii')
        if self.headers.get('Authorization') != expected_auth:
            self.send_response(401)
            self.end_headers()
            return

        query = parse_qs(parsed.query)
        paths = query.get('path', [])
        payload = {
            'Items': [
                {
                    'Identifier': path,
                    'Object': {
                        'Name': path.rsplit('\\', 1)[-1],
                        'Path': path,
                        'WebId': f'WEBID-{path.rsplit("\\", 1)[-1]}',
                    },
                }
                for path in reversed(paths)
            ]
        }
        body = json.dumps(payload).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def test_points_use_real_http_client_with_basic_authentication() -> None:
    server = ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        settings = PiWebApiSettings(
            pi_server='PISERVER01',
            http=HttpSettings(
                base_url=f'http://127.0.0.1:{port}/piwebapi/',
                auth_mode=HttpAuthMode.BASIC,
                username='user',
                password='password',
                allow_insecure_http=True,
            ),
        )

        with PiWebApiClient(settings=settings) as client:
            results = client.points.resolve_web_ids(('TAG_A', 'TAG_B'))

        assert [(result.tag_name, result.web_id) for result in results] == [
            ('TAG_A', 'WEBID-TAG_A'),
            ('TAG_B', 'WEBID-TAG_B'),
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
