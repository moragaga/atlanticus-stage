from __future__ import annotations

from typing import Any

import pytest
from atlanticus.connectivity.http import HttpAuthMode, HttpClient, HttpSettings
from atlanticus.integrations.pi.web_api import (
    PiWebApiLimits,
    PiWebApiRequestError,
    PiWebApiResponseError,
    PiWebApiSettings,
)
from atlanticus.integrations.pi.web_api.points import PiPointResource
from atlanticus.integrations.pi.web_api.transport import PiWebApiTransport


def _settings(*, points_max_paths: int = 100) -> PiWebApiSettings:
    return PiWebApiSettings(
        pi_server='PISERVER01',
        http=HttpSettings(
            base_url='https://pi.example/piwebapi/',
            auth_mode=HttpAuthMode.BASIC,
            username='user',
            password='password',
        ),
        limits=PiWebApiLimits(points_max_paths=points_max_paths),
    )


def _resource(settings: PiWebApiSettings) -> tuple[PiPointResource, HttpClient]:
    http_client = HttpClient(settings=settings.http)
    transport = PiWebApiTransport(http_client=http_client)
    return PiPointResource(transport=transport, settings=settings), http_client


def test_resolve_web_ids_builds_one_exact_pi_request(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings()
    resource, http_client = _resource(settings)
    captured: dict[str, Any] = {}

    def request_json(method: str, endpoint: str = '', **kwargs: Any) -> Any:
        captured.update(method=method, endpoint=endpoint, kwargs=kwargs)
        return {
            'Items': [
                {
                    'Identifier': r'\\PISERVER01\TAG_A',
                    'Object': {
                        'Name': 'TAG_A',
                        'Path': r'\\PISERVER01\TAG_A',
                        'WebId': 'WEBID-A',
                    },
                },
                {
                    'Identifier': r'\\PISERVER01\TAG_B',
                    'Object': {
                        'Name': 'TAG_B',
                        'Path': r'\\PISERVER01\TAG_B',
                        'WebId': 'WEBID-B',
                    },
                },
            ]
        }

    monkeypatch.setattr(http_client, 'request_json', request_json)

    results = resource.resolve_web_ids(('TAG_A', 'TAG_B'))

    assert [result.tag_name for result in results] == ['TAG_A', 'TAG_B']
    assert [result.web_id for result in results] == ['WEBID-A', 'WEBID-B']
    assert captured['method'] == 'GET'
    assert captured['endpoint'] == 'points/multiple'
    params = captured['kwargs']['params']
    assert params[0][0] == 'selectedFields'
    assert params[1] == ('asParallel', 'true')
    assert params[2:] == [
        ('path', r'\\PISERVER01\TAG_A'),
        ('path', r'\\PISERVER01\TAG_B'),
    ]


def test_resolve_web_ids_rejects_over_limit_without_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(points_max_paths=1)
    resource, http_client = _resource(settings)

    def request_json(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError('HTTP must not be called')

    monkeypatch.setattr(http_client, 'request_json', request_json)

    with pytest.raises(PiWebApiRequestError, match='limit of 1'):
        resource.resolve_web_ids(('TAG_A', 'TAG_B'))


@pytest.mark.parametrize(
    'tag_names',
    ((), ('',), (' TAG_A',), ('TAG_A', 'tag_a')),
)
def test_resolve_web_ids_rejects_invalid_requests(tag_names: tuple[str, ...]) -> None:
    resource, _ = _resource(_settings())

    with pytest.raises(PiWebApiRequestError):
        resource.resolve_web_ids(tag_names)


def test_points_response_is_correlated_by_identifier_not_response_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource, http_client = _resource(_settings())

    def request_json(*args: Any, **kwargs: Any) -> Any:
        return {
            'Items': [
                {
                    'Identifier': r'\\PISERVER01\TAG_B',
                    'Object': {'Name': 'TAG_B', 'WebId': 'WEBID-B'},
                },
                {
                    'Identifier': r'\\PISERVER01\TAG_A',
                    'Object': {'Name': 'TAG_A', 'WebId': 'WEBID-A'},
                },
            ]
        }

    monkeypatch.setattr(http_client, 'request_json', request_json)

    results = resource.resolve_web_ids(('TAG_A', 'TAG_B'))

    assert [(result.tag_name, result.web_id) for result in results] == [
        ('TAG_A', 'WEBID-A'),
        ('TAG_B', 'WEBID-B'),
    ]


def test_points_response_preserves_partial_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    resource, http_client = _resource(_settings())

    def request_json(*args: Any, **kwargs: Any) -> Any:
        return {
            'Items': [
                {
                    'Identifier': r'\\PISERVER01\TAG_A',
                    'Object': {'Name': 'TAG_A', 'WebId': 'WEBID-A'},
                },
                {
                    'Identifier': r'\\PISERVER01\TAG_B',
                    'Errors': ['Point was not found'],
                },
            ]
        }

    monkeypatch.setattr(http_client, 'request_json', request_json)

    results = resource.resolve_web_ids(('TAG_A', 'TAG_B', 'TAG_C'))

    assert results[0].resolved is True
    assert results[1].resolved is False
    assert results[1].error == 'Point was not found'
    assert results[2].resolved is False
    assert results[2].error == 'PI Web API did not return this requested tag'


@pytest.mark.parametrize('payload', ([], {}, {'Items': {}}, {'Items': [1, 2]}))
def test_points_response_rejects_invalid_root_structure(
    monkeypatch: pytest.MonkeyPatch,
    payload: Any,
) -> None:
    resource, http_client = _resource(_settings(points_max_paths=1))
    monkeypatch.setattr(http_client, 'request_json', lambda *args, **kwargs: payload)

    match = 'unexpected extra items' if payload == {'Items': [1, 2]} else 'points response'
    with pytest.raises(PiWebApiResponseError, match=match):
        resource.resolve_web_ids(('TAG_A',))
