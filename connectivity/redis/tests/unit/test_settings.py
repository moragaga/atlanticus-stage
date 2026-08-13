from __future__ import annotations

import pytest

from atlanticus.connectivity.redis import (
    DEFAULT_REDIS_CONNECTION_TIMEOUT_SECONDS,
    DEFAULT_REDIS_DATABASE,
    DEFAULT_REDIS_MAX_CONNECTIONS,
    DEFAULT_REDIS_MAX_MGET_KEYS,
    DEFAULT_REDIS_OPERATION_TIMEOUT_SECONDS,
    DEFAULT_REDIS_PORT,
    RedisConfigurationError,
    RedisSettings,
)
from atlanticus.connectivity.redis.settings import _parse_redis_endpoint


def test_settings_defaults_are_safe_and_explicit() -> None:
    settings = RedisSettings(
        url='rediss://redis.example',
        username='default',
        password='private-secret',
    )
    assert settings.url == f'rediss://redis.example:{DEFAULT_REDIS_PORT}'
    assert settings.username == 'default'
    assert settings.password == 'private-secret'
    assert settings.database == DEFAULT_REDIS_DATABASE
    assert settings.allow_insecure_transport is False
    assert settings.connection_timeout_seconds == DEFAULT_REDIS_CONNECTION_TIMEOUT_SECONDS
    assert settings.operation_timeout_seconds == DEFAULT_REDIS_OPERATION_TIMEOUT_SECONDS
    assert settings.max_connections == DEFAULT_REDIS_MAX_CONNECTIONS
    assert settings.max_mget_keys == DEFAULT_REDIS_MAX_MGET_KEYS
    assert 'private-secret' not in repr(settings)


def test_username_and_password_are_preserved_exactly() -> None:
    settings = RedisSettings(
        url='rediss://redis.example:6380',
        username=' service-user ',
        password='  private password  ',
    )
    assert settings.username == ' service-user '
    assert settings.password == '  private password  '


def test_url_is_canonicalized_without_credentials_or_database() -> None:
    settings = RedisSettings(
        url=' REDISS://Redis.Example:6380/ ',
        username='default',
        password='secret',
    )
    endpoint = _parse_redis_endpoint(settings.url)
    assert settings.url == 'rediss://redis.example:6380'
    assert endpoint.host == 'redis.example'
    assert endpoint.port == 6380
    assert endpoint.tls_enabled is True


def test_ipv6_url_is_canonicalized_safely() -> None:
    settings = RedisSettings(
        url='rediss://[2001:db8::1]:6380',
        username='default',
        password='secret',
    )
    assert settings.url == 'rediss://[2001:db8::1]:6380'
    assert _parse_redis_endpoint(settings.url).host == '2001:db8::1'


def test_redis_scheme_requires_explicit_insecure_opt_in() -> None:
    with pytest.raises(RedisConfigurationError, match='allow_insecure_transport'):
        RedisSettings(url='redis://redis:6379', username='default', password='secret')
    settings = RedisSettings(
        url='redis://redis:6379',
        username='default',
        password='secret',
        allow_insecure_transport=True,
    )
    assert _parse_redis_endpoint(settings.url).tls_enabled is False


@pytest.mark.parametrize(
    'url',
    [
        '',
        '   ',
        'http://redis.example:6379',
        'redis.example:6379',
        'redis://',
        'redis://host:0',
        'redis://host:65536',
        'redis://host:not-a-port',
        'redis://user:secret@host:6379',
        'redis://user@host:6379',
        'redis://host:6379/1',
        'redis://host:6379/path',
        'redis://host:6379?db=1',
        'redis://host:6379#fragment',
        'redis://bad\nhost:6379',
    ],
)
def test_settings_reject_invalid_urls(url: str) -> None:
    with pytest.raises(RedisConfigurationError):
        RedisSettings(
            url=url,
            username='default',
            password='secret',
            allow_insecure_transport=True,
        )


@pytest.mark.parametrize('field', ['username', 'password'])
@pytest.mark.parametrize('value', ['', 'bad\nvalue', 'bad\rvalue', 'bad\x00value'])
def test_settings_reject_invalid_identity_fields(field: str, value: str) -> None:
    kwargs = {
        'url': 'rediss://redis.example:6380',
        'username': 'default',
        'password': 'secret',
    }
    kwargs[field] = value
    with pytest.raises(RedisConfigurationError):
        RedisSettings(**kwargs)


@pytest.mark.parametrize('database', [-1, True, '0'])
def test_settings_reject_invalid_databases(database: object) -> None:
    with pytest.raises(RedisConfigurationError):
        RedisSettings(
            url='rediss://redis.example:6380',
            username='default',
            password='secret',
            database=database,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    'field,value',
    [
        ('connection_timeout_seconds', 0),
        ('operation_timeout_seconds', -1),
        ('max_connections', True),
        ('max_mget_keys', 0),
    ],
)
def test_settings_reject_invalid_operational_limits(field: str, value: object) -> None:
    kwargs = {
        'url': 'rediss://redis.example:6380',
        'username': 'default',
        'password': 'secret',
        field: value,
    }
    with pytest.raises(RedisConfigurationError):
        RedisSettings(**kwargs)


def test_settings_reject_non_boolean_insecure_flag() -> None:
    with pytest.raises(RedisConfigurationError):
        RedisSettings(
            url='rediss://redis.example:6380',
            username='default',
            password='secret',
            allow_insecure_transport=1,  # type: ignore[arg-type]
        )


def test_internal_endpoint_parser_rejects_credentials() -> None:
    endpoint = _parse_redis_endpoint('rediss://redis.example:6380')
    assert endpoint.url == 'rediss://redis.example:6380'
    with pytest.raises(RedisConfigurationError):
        _parse_redis_endpoint('rediss://default:private@redis.example:6380')
