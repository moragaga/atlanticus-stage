from __future__ import annotations

from dataclasses import dataclass

import pytest

from atlanticus.connectivity.redis import (
    RedisAuthenticationError,
    RedisAuthorizationError,
    RedisClient,
    RedisClosedError,
    RedisConnectionError,
    RedisOperationError,
    RedisPoolExhaustedError,
    RedisResultLimitError,
    RedisSettings,
    RedisTtl,
)
from atlanticus.connectivity.redis.client import _RedisSdk


class FakeRedisError(Exception):
    pass


class FakeAuthenticationError(FakeRedisError):
    pass


class FakeAuthorizationError(FakeRedisError):
    pass


class FakeConnectionError(FakeRedisError):
    pass


class FakeTimeoutError(FakeRedisError):
    pass


class FakeMaxConnectionsError(FakeRedisError):
    pass


@dataclass
class FakeNoBackoff:
    pass


@dataclass
class FakeRetry:
    backoff: object
    retries: int


class FakeRedis:
    calls: list[dict[str, object]] = []

    def __init__(self, **kwargs: object) -> None:
        self.calls.append(kwargs)
        self.data: dict[str, bytes] = {}
        self.ttls: dict[str, int] = {}
        self.error: BaseException | None = None
        self.closed = False
        self.close_error = False

    def _raise(self) -> None:
        if self.error is not None:
            raise self.error

    def ping(self) -> bool:
        self._raise()
        return True

    def get(self, key: str) -> bytes | None:
        self._raise()
        return self.data.get(key)

    def set(self, key: str, value: bytes, *, ex: int | None = None) -> bool:
        self._raise()
        self.data[key] = value
        self.ttls[key] = -1 if ex is None else ex
        return True

    def delete(self, key: str) -> int:
        self._raise()
        existed = key in self.data
        self.data.pop(key, None)
        self.ttls.pop(key, None)
        return int(existed)

    def exists(self, key: str) -> int:
        self._raise()
        return int(key in self.data)

    def expire(self, key: str, seconds: int) -> bool:
        self._raise()
        if key not in self.data:
            return False
        self.ttls[key] = seconds
        return True

    def ttl(self, key: str) -> int:
        self._raise()
        if key not in self.data:
            return -2
        return self.ttls.get(key, -1)

    def mget(self, keys: tuple[str, ...]) -> list[bytes | None]:
        self._raise()
        return [self.data.get(key) for key in keys]

    def close(self) -> None:
        self.closed = True
        if self.close_error:
            raise RuntimeError('private close error')


def _sdk() -> _RedisSdk:
    return _RedisSdk(
        Redis=FakeRedis,
        NoBackoff=FakeNoBackoff,
        Retry=FakeRetry,
        AuthenticationError=FakeAuthenticationError,
        AuthorizationError=FakeAuthorizationError,
        ConnectionError=FakeConnectionError,
        TimeoutError=FakeTimeoutError,
        MaxConnectionsError=FakeMaxConnectionsError,
        RedisSdkError=FakeRedisError,
    )


def _client(*, tls_enabled: bool = True, max_mget_keys: int = 3) -> RedisClient:
    FakeRedis.calls.clear()
    client = RedisClient(
        settings=RedisSettings(
            url='rediss://redis.example:6380' if tls_enabled else 'redis://redis.example:6379',
            username='service',
            password='private-secret',
            database=4,
            allow_insecure_transport=not tls_enabled,
            connection_timeout_seconds=7,
            operation_timeout_seconds=11,
            max_connections=13,
            max_mget_keys=max_mget_keys,
        )
    )
    client._sdk = _sdk()
    return client


def test_client_is_lazy_reused_and_configures_bounded_pool_without_hidden_ops() -> None:
    client = _client()
    assert FakeRedis.calls == []
    assert client.health_check() is True
    assert client.health_check() is True
    assert len(FakeRedis.calls) == 1
    kwargs = FakeRedis.calls[0]
    assert kwargs['host'] == 'redis.example'
    assert kwargs['port'] == 6380
    assert kwargs['db'] == 4
    assert kwargs['username'] == 'service'
    assert kwargs['password'] == 'private-secret'
    assert kwargs['ssl'] is True
    assert kwargs['ssl_cert_reqs'] == 'required'
    assert kwargs['ssl_check_hostname'] is True
    assert kwargs['socket_connect_timeout'] == 7
    assert kwargs['socket_timeout'] == 11
    assert kwargs['socket_keepalive'] is True
    assert kwargs['decode_responses'] is False
    assert kwargs['max_connections'] == 13
    assert kwargs['health_check_interval'] == 0
    assert kwargs['protocol'] == 2
    assert kwargs['legacy_responses'] is True
    assert kwargs['retry_on_error'] == []
    assert kwargs['driver_info'] is None
    assert isinstance(kwargs['retry'], FakeRetry)
    assert kwargs['retry'].retries == 0


def test_non_tls_client_is_explicit_and_does_not_request_certificate_validation() -> None:
    client = _client(tls_enabled=False)
    client.open()
    kwargs = FakeRedis.calls[0]
    assert kwargs['ssl'] is False
    assert kwargs['ssl_cert_reqs'] is None
    assert kwargs['ssl_check_hostname'] is False


def test_set_get_exists_delete_and_binary_normalization() -> None:
    client = _client()
    client.set(key='runtime:key', value=bytearray(b'abc'))
    assert client.exists(key='runtime:key') is True
    assert client.get(key='runtime:key') == b'abc'
    assert client.delete(key='runtime:key') is True
    assert client.delete(key='runtime:key') is False
    assert client.get(key='runtime:key') is None


def test_set_with_ttl_and_expire_ttl_states() -> None:
    client = _client()
    assert client.ttl(key='missing') == RedisTtl(exists=False, seconds=None)
    client.set(key='persistent', value=b'x')
    assert client.ttl(key='persistent') == RedisTtl(exists=True, seconds=None)
    assert client.expire(key='persistent', ttl_seconds=30) is True
    assert client.ttl(key='persistent') == RedisTtl(exists=True, seconds=30)
    assert client.expire(key='missing', ttl_seconds=30) is False
    client.set(key='atomic', value=b'x', ttl_seconds=12)
    assert client.ttl(key='atomic') == RedisTtl(exists=True, seconds=12)


def test_mget_is_positional_and_has_strict_limit() -> None:
    client = _client(max_mget_keys=2)
    client.set(key='a', value=b'A')
    assert client.mget(keys=['a', 'missing']) == (b'A', None)
    assert client.mget(keys=[]) == ()
    with pytest.raises(RedisResultLimitError) as captured:
        client.mget(keys=['a', 'b', 'c'])
    assert captured.value.max_keys == 2


@pytest.mark.parametrize(
    'method,kwargs',
    [
        ('get', {'key': ''}),
        ('set', {'key': 'x', 'value': 'text'}),
        ('set', {'key': 'x', 'value': b'x', 'ttl_seconds': 0}),
        ('expire', {'key': 'x', 'ttl_seconds': 0}),
        ('mget', {'keys': 'not-a-sequence-of-keys'}),
    ],
)
def test_public_methods_validate_contract_before_sdk_call(
    method: str, kwargs: dict[str, object]
) -> None:
    client = _client()
    with pytest.raises((TypeError, ValueError)):
        getattr(client, method)(**kwargs)


@pytest.mark.parametrize(
    'sdk_error,public_error',
    [
        (FakeAuthenticationError('password=private-secret'), RedisAuthenticationError),
        (FakeAuthorizationError('private acl'), RedisAuthorizationError),
        (FakeConnectionError('redis.example:6380 private-secret'), RedisConnectionError),
        (FakeTimeoutError('redis.example private-secret'), RedisConnectionError),
        (FakeMaxConnectionsError('pool details'), RedisPoolExhaustedError),
        (FakeRedisError('server says private-secret'), RedisOperationError),
    ],
)
def test_sdk_failures_are_classified_sanitized_and_unlinked(
    sdk_error: BaseException,
    public_error: type[BaseException],
) -> None:
    client = _client()
    redis = client._get_client()
    redis.error = sdk_error
    with pytest.raises(public_error) as captured:
        client.get(key='x')
    assert 'private-secret' not in repr(captured.value)
    assert 'redis.example' not in repr(captured.value)
    assert captured.value.__cause__ is None


def test_close_is_idempotent_and_closed_client_fails() -> None:
    client = _client()
    client.open()
    redis = client._client
    client.close()
    client.close()
    assert redis.closed is True
    with pytest.raises(RedisClosedError):
        client.health_check()


def test_context_manager_does_not_hide_business_error_on_close_failure() -> None:
    client = _client()
    client.open()
    client._client.close_error = True
    with pytest.raises(ValueError, match='business failure'):
        with client:
            raise ValueError('business failure')


def test_close_failure_is_sanitized_when_no_business_error_exists() -> None:
    client = _client()
    client.open()
    client._client.close_error = True
    with pytest.raises(RedisConnectionError) as captured:
        client.close()
    assert 'private' not in repr(captured.value)
