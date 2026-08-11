from __future__ import annotations

import pytest

from atlanticus.connectivity.http import (
    HttpAuthMode,
    HttpConfigurationError,
    HttpSettings,
)


def test_configuration_keys_support_default_and_suffixed_routes() -> None:
    default = HttpSettings.from_mapping(
        values={
            'HTTP_BASE_URL': 'https://api.example.test',
            'HTTP_AUTH_MODE': 'none',
        }
    )
    pi = HttpSettings.from_mapping(
        values={
            'HTTP_BASE_URL_PI_WEB_API': 'https://pi.example.test',
            'HTTP_AUTH_MODE_PI_WEB_API': 'none',
            'HTTP_MAX_RESPONSE_BYTES_PI_WEB_API': '1024',
        },
        suffix=' pi_web_api ',
    )

    assert default.suffix is None
    assert default.max_response_bytes == 64 * 1024 * 1024
    assert pi.suffix == 'PI_WEB_API'
    assert pi.max_response_bytes == 1024


@pytest.mark.parametrize(
    ('value', 'expected'),
    (
        ('none', HttpAuthMode.NONE),
        (' Bearer ', HttpAuthMode.BEARER),
        ('BASIC', HttpAuthMode.BASIC),
    ),
)
def test_auth_mode_is_explicit(value: str, expected: HttpAuthMode) -> None:
    settings = HttpSettings.from_mapping(
        values={
            'HTTP_BASE_URL': 'https://api.example.test',
            'HTTP_AUTH_MODE': value,
            'HTTP_BEARER_TOKEN': 'token' if expected == HttpAuthMode.BEARER else None,
            'HTTP_USERNAME': 'user' if expected == HttpAuthMode.BASIC else None,
            'HTTP_PASSWORD': 'password' if expected == HttpAuthMode.BASIC else None,
        }
    )

    assert settings.auth_mode == expected


def test_from_mapping_builds_basic_settings_without_exposing_secrets() -> None:
    settings = HttpSettings.from_mapping(
        values={
            'HTTP_BASE_URL_PI_WEB_API': 'https://pi.example.test/piwebapi',
            'HTTP_AUTH_MODE_PI_WEB_API': 'basic',
            'HTTP_USERNAME_PI_WEB_API': 'pi-user',
            'HTTP_PASSWORD_PI_WEB_API': 'private-password',
            'HTTP_CONNECT_TIMEOUT_SECONDS_PI_WEB_API': '2.5',
            'HTTP_READ_TIMEOUT_SECONDS_PI_WEB_API': '15',
            'HTTP_WRITE_TIMEOUT_SECONDS_PI_WEB_API': '10',
            'HTTP_POOL_TIMEOUT_SECONDS_PI_WEB_API': '3',
            'HTTP_VERIFY_TLS_PI_WEB_API': 'true',
        },
        suffix='PI_WEB_API',
    )

    assert settings.auth_mode == HttpAuthMode.BASIC
    assert settings.base_url == 'https://pi.example.test/piwebapi/'
    assert settings.connect_timeout_seconds == 2.5
    assert settings.read_timeout_seconds == 15.0
    assert settings.suffix == 'PI_WEB_API'
    assert 'pi-user' not in repr(settings)
    assert 'private-password' not in repr(settings)


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


def test_missing_mapping_reports_only_the_required_route_keys() -> None:
    with pytest.raises(HttpConfigurationError) as captured:
        HttpSettings.from_mapping(
            values={'HTTP_BEARER_TOKEN_INTERNAL': 'private'},
            suffix='INTERNAL',
        )

    message = str(captured.value)
    assert 'HTTP_BASE_URL_INTERNAL' in message
    assert 'HTTP_AUTH_MODE_INTERNAL' in message
    assert 'private' not in message


@pytest.mark.parametrize(
    ('key', 'value'),
    (
        ('HTTP_CONNECT_TIMEOUT_SECONDS', '0'),
        ('HTTP_READ_TIMEOUT_SECONDS', 'not-a-number'),
        ('HTTP_WRITE_TIMEOUT_SECONDS', 'nan'),
        ('HTTP_POOL_TIMEOUT_SECONDS', 'inf'),
        ('HTTP_MAX_RESPONSE_BYTES', '1.5'),
        ('HTTP_MAX_RESPONSE_BYTES', '0'),
        ('HTTP_VERIFY_TLS', 'perhaps'),
        ('HTTP_ALLOW_INSECURE_HTTP', 'sometimes'),
    ),
)
def test_invalid_timeout_and_boolean_values_are_rejected(key: str, value: str) -> None:
    with pytest.raises(HttpConfigurationError) as captured:
        HttpSettings.from_mapping(
            values={
                'HTTP_BASE_URL': 'https://api.example.test',
                'HTTP_AUTH_MODE': 'none',
                key: value,
            }
        )

    assert captured.value.__cause__ is None


@pytest.mark.parametrize(
    ('field', 'value'),
    (
        ('auth_mode', 'none'),
        ('connect_timeout_seconds', True),
        ('read_timeout_seconds', '30'),
        ('write_timeout_seconds', float('nan')),
        ('pool_timeout_seconds', float('inf')),
        ('max_response_bytes', True),
        ('max_response_bytes', 1.5),
        ('verify_tls', 'true'),
        ('allow_insecure_http', 0),
        ('suffix', 7),
    ),
)
def test_direct_settings_require_exact_types(field: str, value: object) -> None:
    values: dict[str, object] = {
        'base_url': 'https://api.example.test',
        'auth_mode': HttpAuthMode.NONE,
    }
    values[field] = value

    with pytest.raises(HttpConfigurationError):
        HttpSettings(**values)


def test_mapping_rejects_non_text_values_without_copying_them() -> None:
    secret = object()

    with pytest.raises(HttpConfigurationError) as captured:
        HttpSettings.from_mapping(
            values={
                'HTTP_BASE_URL': 'https://api.example.test',
                'HTTP_AUTH_MODE': 'none',
                'HTTP_CONNECT_TIMEOUT_SECONDS': secret,
            }
        )

    assert repr(secret) not in repr(captured.value)
