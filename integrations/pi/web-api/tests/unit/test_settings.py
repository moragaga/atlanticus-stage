from __future__ import annotations

import pytest
from atlanticus.connectivity.http import HttpAuthMode, HttpSettings
from atlanticus.integrations.pi.web_api import (
    PiWebApiConfigurationError,
    PiWebApiLimits,
    PiWebApiSettings,
)


def _http_settings(*, auth_mode: HttpAuthMode = HttpAuthMode.BASIC) -> HttpSettings:
    kwargs = {}
    if auth_mode is HttpAuthMode.BASIC:
        kwargs = {'username': 'user', 'password': 'password'}
    return HttpSettings(
        base_url='https://pi.example/piwebapi/',
        auth_mode=auth_mode,
        **kwargs,
    )


def test_limits_use_mlp_defaults() -> None:
    limits = PiWebApiLimits()

    assert limits.points_max_paths == 100
    assert limits.interpolated_max_web_ids == 100
    assert limits.recorded_max_web_ids == 100


def test_limits_are_runtime_configurable() -> None:
    limits = PiWebApiLimits(
        points_max_paths=75,
        interpolated_max_web_ids=150,
        recorded_max_web_ids=50,
    )

    assert limits.points_max_paths == 75
    assert limits.interpolated_max_web_ids == 150
    assert limits.recorded_max_web_ids == 50


@pytest.mark.parametrize(
    'field_name',
    ('points_max_paths', 'interpolated_max_web_ids', 'recorded_max_web_ids'),
)
def test_limits_require_positive_integers(field_name: str) -> None:
    values = {
        'points_max_paths': 100,
        'interpolated_max_web_ids': 100,
        'recorded_max_web_ids': 100,
    }
    values[field_name] = 0

    with pytest.raises(PiWebApiConfigurationError, match='greater than zero'):
        PiWebApiLimits(**values)


def test_settings_keep_http_configuration_separate() -> None:
    http = _http_settings()
    settings = PiWebApiSettings(pi_server='PISERVER01', http=http)

    assert settings.pi_server == 'PISERVER01'
    assert settings.http is http
    assert settings.limits == PiWebApiLimits()
    assert 'password' not in repr(settings)
    assert 'user' not in repr(settings)


def test_settings_require_basic_http_authentication() -> None:
    with pytest.raises(PiWebApiConfigurationError, match='basic HTTP authentication'):
        PiWebApiSettings(pi_server='PISERVER01', http=_http_settings(auth_mode=HttpAuthMode.NONE))


@pytest.mark.parametrize('pi_server', ('', ' PISERVER01', 'PISERVER01 ', r'\\PISERVER01', 'A/B'))
def test_settings_reject_invalid_pi_server(pi_server: str) -> None:
    with pytest.raises(PiWebApiConfigurationError):
        PiWebApiSettings(pi_server=pi_server, http=_http_settings())
