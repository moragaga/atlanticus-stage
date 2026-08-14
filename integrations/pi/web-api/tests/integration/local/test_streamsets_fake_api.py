from __future__ import annotations

import base64
import json
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from atlanticus.connectivity.http import HttpAuthMode, HttpSettings
from atlanticus.integrations.pi.web_api import PiWebApiClient, PiWebApiSettings


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path not in {
            '/piwebapi/streamsets/interpolated',
            '/piwebapi/streamsets/recorded',
        }:
            self.send_response(404)
            self.end_headers()
            return

        expected_auth = 'Basic ' + base64.b64encode(b'user:password').decode('ascii')
        if self.headers.get('Authorization') != expected_auth:
            self.send_response(401)
            self.end_headers()
            return

        query = parse_qs(parsed.query)
        web_ids = query.get('webId', [])
        selected_fields = query.get('selectedFields', [])
        if selected_fields != ['Items.Name;Items.Items.Timestamp;Items.Items.Value']:
            self.send_response(400)
            self.end_headers()
            return
        if parsed.path.endswith('/interpolated') and query.get('interval') != ['10s']:
            self.send_response(400)
            self.end_headers()
            return

        payload = {
            'Items': [
                {
                    'Name': f'TAG_{index}',
                    'Items': [
                        {
                            'Timestamp': '2026-08-14T12:00:00Z',
                            'Value': index,
                        },
                        {
                            'Timestamp': '2026-08-14T12:00:10Z',
                            'Value': {'Name': 'No Data', 'Value': 248, 'IsSystem': True},
                        },
                    ],
                }
                for index, _ in enumerate(web_ids, start=1)
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


def test_streamsets_use_real_http_client_and_normalize_pi_source_values() -> None:
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
        start = datetime(2026, 8, 14, 12, tzinfo=UTC)
        end = datetime(2026, 8, 14, 13, tzinfo=UTC)

        with PiWebApiClient(settings=settings) as client:
            interpolated = client.streamsets.get_interpolated(
                ('WEBID-A', 'WEBID-B'),
                start_time_utc=start,
                end_time_utc=end,
                interpolation_seconds=10,
            )
            recorded = client.streamsets.get_recorded(
                ('WEBID-A',),
                start_time_utc=start,
                end_time_utc=end,
            )

        assert interpolated == (
            {'name': 'TAG_1', 'timestamp': '2026-08-14T12:00:00Z', 'value': 1},
            {'name': 'TAG_1', 'timestamp': '2026-08-14T12:00:10Z', 'value': None},
            {'name': 'TAG_2', 'timestamp': '2026-08-14T12:00:00Z', 'value': 2},
            {'name': 'TAG_2', 'timestamp': '2026-08-14T12:00:10Z', 'value': None},
        )
        assert recorded == (
            {'name': 'TAG_1', 'timestamp': '2026-08-14T12:00:00Z', 'value': 1},
            {'name': 'TAG_1', 'timestamp': '2026-08-14T12:00:10Z', 'value': None},
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
