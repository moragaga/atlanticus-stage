from __future__ import annotations

import pytest

from atlanticus.connectivity.http import (
    HttpAuthMode,
    HttpConfigurationError,
    HttpSettings,
)


def test_direct_settings_apply_stable_defaults() -> None:
    settings = HttpSettings(
        base_url='https://api.example.test/root',
        auth_mode=HttpAuthMode.NONE,
    )

    assert settings.base_url == 'https://api.example.test/root/'
    assert settings.connect_timeout_seconds == 5.0
    assert settings.read_timeout_seconds == 30.0
    assert settings.write_timeout_seconds == 30.0
    assert settings.pool_timeout_seconds == 5.0
    assert settings.max_response_bytes == 64 * 1024 * 1024
    assert settings.verify_tls is True
    assert settings.allow_insecure_http is False


def test_basic_credentials_preserve_exact_values_without_exposing_them() -> None:
    settings = HttpSettings(
        base_url='https://api.example.test',
        auth_mode=HttpAuthMode.BASIC,
        username=' api-user ',
        password=' private-password ',
    )

    assert settings.username == ' api-user '
    assert settings.password == ' private-password '
    assert 'api-user' not in repr(settings)
    assert 'private-password' not in repr(settings)


def test_empty_credentials_are_treated_as_absent_without_trimming_non_empty_values() -> None:
    settings = HttpSettings(
        base_url='https://api.example.test',
        auth_mode=HttpAuthMode.NONE,
        bearer_token='',
        username='',
        password='',
    )

    assert settings.bearer_token is None
    assert settings.username is None
    assert settings.password is None


def test_bearer_and_public_routes_require_their_exact_credentials() -> None:
    bearer = HttpSettings(
        base_url='https://api.example.test',
        auth_mode=HttpAuthMode.BEARER,
        bearer_token='secret-token',
    )
    public = HttpSettings(
        base_url='https://api.example.test',
        auth_mode=HttpAuthMode.NONE,
    )

    assert bearer.auth_mode == HttpAuthMode.BEARER
    assert bearer.bearer_token == 'secret-token'
    assert public.auth_mode == HttpAuthMode.NONE
    assert 'secret-token' not in repr(bearer)


@pytest.mark.parametrize(
    'settings',
    (
        {'auth_mode': 'none', 'bearer_token': 'unexpected'},
        {'auth_mode': 'none', 'username': 'unexpected'},
        {'auth_mode': 'bearer'},
        {'auth_mode': 'bearer', 'bearer_token': 'token', 'username': 'unexpected'},
        {'auth_mode': 'basic', 'username': 'user'},
        {
            'auth_mode': 'basic',
            'username': 'user',
            'password': 'password',
            'bearer_token': 'unexpected',
        },
        {'auth_mode': 'bearer', 'bearer_token': 'token with spaces'},
        {'auth_mode': 'basic', 'username': 'user:name', 'password': 'password'},
        {'auth_mode': 'basic', 'username': 'user', 'password': 'line\nbreak'},
    ),
)
def test_incompatible_authentication_settings_are_rejected(settings: dict[str, str]) -> None:
    settings['auth_mode'] = HttpAuthMode(settings['auth_mode'])
    with pytest.raises(HttpConfigurationError):
        HttpSettings(base_url='https://api.example.test', **settings)


def test_http_requires_an_explicit_insecure_opt_in() -> None:
    with pytest.raises(HttpConfigurationError):
        HttpSettings(base_url='http://api.example.test', auth_mode=HttpAuthMode.NONE)

    settings = HttpSettings(
        base_url='http://api.example.test/root',
        auth_mode=HttpAuthMode.NONE,
        allow_insecure_http=True,
    )

    assert settings.base_url == 'http://api.example.test/root/'


@pytest.mark.parametrize(
    'base_url',
    (
        'api.example.test',
        'ftp://api.example.test',
        'https://user:password@api.example.test',
        'https://api.example.test?token=secret',
        'https://api.example.test/#fragment',
        'https://api.example.test:invalid',
        'https://api.example.test:70000',
        'https://:443',
    ),
)
def test_unsafe_or_non_http_base_urls_are_rejected(base_url: str) -> None:
    with pytest.raises(HttpConfigurationError):
        HttpSettings(base_url=base_url, auth_mode=HttpAuthMode.NONE)


@pytest.mark.parametrize(
    ('field', 'value'),
    (
        ('auth_mode', 'none'),
        ('connect_timeout_seconds', True),
        ('connect_timeout_seconds', 0),
        ('read_timeout_seconds', '30'),
        ('write_timeout_seconds', float('nan')),
        ('pool_timeout_seconds', float('inf')),
        ('max_response_bytes', True),
        ('max_response_bytes', 1.5),
        ('max_response_bytes', 0),
        ('verify_tls', 'true'),
        ('allow_insecure_http', 0),
    ),
)
def test_direct_settings_require_exact_types_and_valid_ranges(field: str, value: object) -> None:
    values: dict[str, object] = {
        'base_url': 'https://api.example.test',
        'auth_mode': HttpAuthMode.NONE,
    }
    values[field] = value

    with pytest.raises(HttpConfigurationError):
        HttpSettings(**values)


@pytest.mark.parametrize(
    ('field', 'value'),
    (
        ('bearer_token', object()),
        ('username', object()),
        ('password', object()),
    ),
)
def test_credentials_reject_non_text_values_without_copying_them(
    field: str,
    value: object,
) -> None:
    values: dict[str, object] = {
        'base_url': 'https://api.example.test',
        'auth_mode': HttpAuthMode.NONE,
        field: value,
    }

    with pytest.raises(HttpConfigurationError) as captured:
        HttpSettings(**values)

    assert repr(value) not in repr(captured.value)
