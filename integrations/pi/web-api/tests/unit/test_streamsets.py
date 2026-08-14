from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from atlanticus.connectivity.http import HttpAuthMode, HttpClient, HttpSettings
from atlanticus.integrations.pi.web_api import (
    PiWebApiLimits,
    PiWebApiRequestError,
    PiWebApiResponseError,
    PiWebApiSettings,
)
from atlanticus.integrations.pi.web_api.streamsets import PiStreamSetResource
from atlanticus.integrations.pi.web_api.transport import PiWebApiTransport

_START = datetime(2026, 8, 14, 12, tzinfo=UTC)
_END = datetime(2026, 8, 14, 13, tzinfo=UTC)


def _settings(
    *,
    interpolated_max_web_ids: int = 100,
    recorded_max_web_ids: int = 100,
) -> PiWebApiSettings:
    return PiWebApiSettings(
        pi_server='PISERVER01',
        http=HttpSettings(
            base_url='https://pi.example/piwebapi/',
            auth_mode=HttpAuthMode.BASIC,
            username='user',
            password='password',
        ),
        limits=PiWebApiLimits(
            interpolated_max_web_ids=interpolated_max_web_ids,
            recorded_max_web_ids=recorded_max_web_ids,
        ),
    )


def _resource(settings: PiWebApiSettings) -> tuple[PiStreamSetResource, HttpClient]:
    http_client = HttpClient(settings=settings.http)
    transport = PiWebApiTransport(http_client=http_client)
    return PiStreamSetResource(transport=transport, settings=settings), http_client


def test_get_interpolated_builds_one_exact_pi_request(monkeypatch: pytest.MonkeyPatch) -> None:
    resource, http_client = _resource(_settings())
    captured: dict[str, Any] = {}

    def request_json(method: str, endpoint: str = '', **kwargs: Any) -> Any:
        captured.update(method=method, endpoint=endpoint, kwargs=kwargs)
        return {'Items': []}

    monkeypatch.setattr(http_client, 'request_json', request_json)

    result = resource.get_interpolated(
        ('WEBID-A', 'WEBID-B'),
        start_time_utc=_START,
        end_time_utc=_END,
        interpolation_seconds=10,
    )

    assert result == ()
    assert captured['method'] == 'GET'
    assert captured['endpoint'] == 'streamsets/interpolated'
    assert captured['kwargs']['params'] == [
        ('startTime', '2026-08-14T12:00:00+00:00'),
        ('endTime', '2026-08-14T13:00:00+00:00'),
        ('interval', '10s'),
        ('selectedFields', 'Items.Name;Items.Items.Timestamp;Items.Items.Value'),
        ('webId', 'WEBID-A'),
        ('webId', 'WEBID-B'),
    ]


def test_get_recorded_builds_one_exact_pi_request(monkeypatch: pytest.MonkeyPatch) -> None:
    resource, http_client = _resource(_settings())
    captured: dict[str, Any] = {}

    def request_json(method: str, endpoint: str = '', **kwargs: Any) -> Any:
        captured.update(method=method, endpoint=endpoint, kwargs=kwargs)
        return {'Items': []}

    monkeypatch.setattr(http_client, 'request_json', request_json)

    result = resource.get_recorded(
        ('WEBID-A', 'WEBID-B'),
        start_time_utc=_START,
        end_time_utc=_END,
    )

    assert result == ()
    assert captured['method'] == 'GET'
    assert captured['endpoint'] == 'streamsets/recorded'
    assert captured['kwargs']['params'] == [
        ('startTime', '2026-08-14T12:00:00+00:00'),
        ('endTime', '2026-08-14T13:00:00+00:00'),
        ('selectedFields', 'Items.Name;Items.Items.Timestamp;Items.Items.Value'),
        ('webId', 'WEBID-A'),
        ('webId', 'WEBID-B'),
    ]


@pytest.mark.parametrize(
    ('method_name', 'limit_name'),
    (
        ('get_interpolated', 'interpolated_max_web_ids'),
        ('get_recorded', 'recorded_max_web_ids'),
    ),
)
def test_streamsets_reject_over_limit_without_request(
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
    limit_name: str,
) -> None:
    settings = _settings(interpolated_max_web_ids=1, recorded_max_web_ids=1)
    resource, http_client = _resource(settings)

    def request_json(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError('HTTP must not be called')

    monkeypatch.setattr(http_client, 'request_json', request_json)
    method = getattr(resource, method_name)
    kwargs: dict[str, Any] = {
        'start_time_utc': _START,
        'end_time_utc': _END,
    }
    if method_name == 'get_interpolated':
        kwargs['interpolation_seconds'] = 10

    with pytest.raises(PiWebApiRequestError, match=f'{limit_name} limit of 1'):
        method(('WEBID-A', 'WEBID-B'), **kwargs)


@pytest.mark.parametrize(
    'web_ids',
    ((), ('',), (' WEBID-A',), ('WEBID-A ',), ('WEBID-A', 'WEBID-A')),
)
def test_streamsets_reject_invalid_web_ids(web_ids: tuple[str, ...]) -> None:
    resource, _ = _resource(_settings())

    with pytest.raises(PiWebApiRequestError):
        resource.get_recorded(web_ids, start_time_utc=_START, end_time_utc=_END)


@pytest.mark.parametrize(
    ('start_time', 'end_time'),
    (
        (datetime(2026, 8, 14, 12), _END),
        (_START, datetime(2026, 8, 14, 13)),
        (datetime(2026, 8, 14, 12, tzinfo=timezone(timedelta(hours=-4))), _END),
        (_START, _START),
        (_END, _START),
    ),
)
def test_streamsets_require_valid_utc_time_range(
    start_time: datetime,
    end_time: datetime,
) -> None:
    resource, _ = _resource(_settings())

    with pytest.raises(PiWebApiRequestError):
        resource.get_recorded(
            ('WEBID-A',),
            start_time_utc=start_time,
            end_time_utc=end_time,
        )


@pytest.mark.parametrize('interpolation_seconds', (0, -1, True, 1.5, '10'))
def test_interpolated_requires_positive_integer_seconds(
    interpolation_seconds: Any,
) -> None:
    resource, _ = _resource(_settings())

    with pytest.raises(PiWebApiRequestError, match='interpolation_seconds'):
        resource.get_interpolated(
            ('WEBID-A',),
            start_time_utc=_START,
            end_time_utc=_END,
            interpolation_seconds=interpolation_seconds,
        )


def test_streamsets_return_only_name_timestamp_and_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource, http_client = _resource(_settings())

    def request_json(*args: Any, **kwargs: Any) -> Any:
        return {
            'Items': [
                {
                    'Name': 'TAG_A',
                    'WebId': 'SHOULD-NOT-LEAK',
                    'Items': [
                        {
                            'Timestamp': '2026-08-14T12:00:00Z',
                            'Value': 12.5,
                            'Good': True,
                        },
                        {
                            'Timestamp': '2026-08-14T12:00:10Z',
                            'Value': 'RUNNING',
                        },
                    ],
                }
            ]
        }

    monkeypatch.setattr(http_client, 'request_json', request_json)

    result = resource.get_recorded(('WEBID-A',), start_time_utc=_START, end_time_utc=_END)

    assert result == (
        {'name': 'TAG_A', 'timestamp': '2026-08-14T12:00:00Z', 'value': 12.5},
        {'name': 'TAG_A', 'timestamp': '2026-08-14T12:00:10Z', 'value': 'RUNNING'},
    )
    assert all(set(record) == {'name', 'timestamp', 'value'} for record in result)


def test_streamsets_normalize_source_failures_to_none_and_continue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource, http_client = _resource(_settings())

    def request_json(*args: Any, **kwargs: Any) -> Any:
        return {
            'Items': [
                {
                    'Name': 'TAG_A',
                    'Items': [
                        {
                            'Timestamp': '2026-08-14T12:00:00Z',
                            'Value': {'Name': 'No Data', 'Value': 248, 'IsSystem': True},
                        },
                        {
                            'Timestamp': '2026-08-14T12:00:10Z',
                            'Value': {'Name': 'Manual', 'Value': 7, 'IsSystem': False},
                        },
                        {'Timestamp': '2026-08-14T12:00:20Z', 'Value': {'Name': 'Bad'}},
                        {'Timestamp': '2026-08-14T12:00:30Z', 'Value': None},
                        {'Timestamp': '2026-08-14T12:00:40Z', 'Value': [1, 2]},
                    ],
                },
                {
                    'Name': 'TAG_B',
                    'Items': [
                        {'Timestamp': '2026-08-14T12:00:00Z', 'Value': 3.25},
                    ],
                },
            ]
        }

    monkeypatch.setattr(http_client, 'request_json', request_json)

    result = resource.get_recorded(
        ('WEBID-A', 'WEBID-B'),
        start_time_utc=_START,
        end_time_utc=_END,
    )

    assert result == (
        {'name': 'TAG_A', 'timestamp': '2026-08-14T12:00:00Z', 'value': None},
        {'name': 'TAG_A', 'timestamp': '2026-08-14T12:00:10Z', 'value': 7},
        {'name': 'TAG_A', 'timestamp': '2026-08-14T12:00:20Z', 'value': None},
        {'name': 'TAG_A', 'timestamp': '2026-08-14T12:00:30Z', 'value': None},
        {'name': 'TAG_A', 'timestamp': '2026-08-14T12:00:40Z', 'value': None},
        {'name': 'TAG_B', 'timestamp': '2026-08-14T12:00:00Z', 'value': 3.25},
    )


def test_streamsets_skip_invalid_source_items_without_aborting_valid_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource, http_client = _resource(_settings())

    def request_json(*args: Any, **kwargs: Any) -> Any:
        return {
            'Items': [
                None,
                {'Name': '', 'Items': [{'Timestamp': '2026-08-14T12:00:00Z', 'Value': 1}]},
                {'Name': 'TAG_BAD_POINTS', 'Items': 'invalid'},
                {
                    'Name': 'TAG_A',
                    'Items': [
                        None,
                        {'Timestamp': None, 'Value': 1},
                        {'Timestamp': 'not-a-time', 'Value': 2},
                        {'Timestamp': '2026-08-14T12:00:00', 'Value': 3},
                        {'Timestamp': '2026-08-14T12:00:10Z', 'Value': 4},
                    ],
                },
            ]
        }

    monkeypatch.setattr(http_client, 'request_json', request_json)

    result = resource.get_recorded(('WEBID-A',), start_time_utc=_START, end_time_utc=_END)

    assert result == ({'name': 'TAG_A', 'timestamp': '2026-08-14T12:00:10Z', 'value': 4},)


def test_streamsets_empty_items_is_successful_empty_source_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource, http_client = _resource(_settings())
    monkeypatch.setattr(http_client, 'request_json', lambda *args, **kwargs: {'Items': []})

    result = resource.get_recorded(('WEBID-A',), start_time_utc=_START, end_time_utc=_END)

    assert result == ()


@pytest.mark.parametrize('payload', (None, [], {}, {'Items': None}, {'Items': {}}))
def test_streamsets_reject_invalid_root_structure(
    monkeypatch: pytest.MonkeyPatch,
    payload: Any,
) -> None:
    resource, http_client = _resource(_settings())
    monkeypatch.setattr(http_client, 'request_json', lambda *args, **kwargs: payload)

    with pytest.raises(PiWebApiResponseError, match='streamsets response'):
        resource.get_recorded(('WEBID-A',), start_time_utc=_START, end_time_utc=_END)
