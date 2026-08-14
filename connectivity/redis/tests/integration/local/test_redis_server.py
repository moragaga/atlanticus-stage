from __future__ import annotations

import os
from time import monotonic, sleep

import pytest

from atlanticus.connectivity.redis import (
    RedisAuthenticationError,
    RedisClient,
    RedisSettings,
    RedisTtl,
)

pytestmark = pytest.mark.integration
_PREFIX = 'atlanticus:integration:'


def _require_integration() -> None:
    if os.getenv('ATLANTICUS_RUN_REDIS_INTEGRATION') != '1':
        pytest.skip('Redis integration is disabled')


def _settings(*, database: int = 0, password: str | None = None) -> RedisSettings:
    resolved_password = os.environ['ATLANTICUS_REDIS_PASSWORD'] if password is None else password
    return RedisSettings(
        url=os.environ['ATLANTICUS_REDIS_URL'],
        username=os.environ['ATLANTICUS_REDIS_USERNAME'],
        password=resolved_password,
        database=database,
        allow_insecure_transport=True,
        connection_timeout_seconds=2,
        operation_timeout_seconds=2,
        max_connections=4,
        max_mget_keys=20,
    )


def _wait_until_ready() -> None:
    deadline = monotonic() + 30
    last_error: BaseException | None = None
    while monotonic() < deadline:
        client = RedisClient(settings=_settings())
        try:
            client.health_check()
            client.close()
            return
        except Exception as error:
            last_error = error
            try:
                client.close()
            except Exception:
                pass
            sleep(0.25)
    raise RuntimeError('Redis did not become ready') from last_error


@pytest.fixture(scope='module', autouse=True)
def prepared_redis() -> None:
    _require_integration()
    _wait_until_ready()


def test_binary_roundtrip_health_exists_and_delete() -> None:
    key = f'{_PREFIX}roundtrip'
    with RedisClient(settings=_settings()) as client:
        client.delete(key=key)
        assert client.health_check() is True
        client.set(key=key, value=b'\x00atlanticus\xff')
        assert client.exists(key=key) is True
        assert client.get(key=key) == b'\x00atlanticus\xff'
        assert client.delete(key=key) is True
        assert client.exists(key=key) is False


def test_ttl_distinguishes_missing_persistent_and_expiring() -> None:
    key = f'{_PREFIX}ttl'
    with RedisClient(settings=_settings()) as client:
        client.delete(key=key)
        assert client.ttl(key=key) == RedisTtl(exists=False, seconds=None)
        client.set(key=key, value=b'value')
        assert client.ttl(key=key) == RedisTtl(exists=True, seconds=None)
        assert client.expire(key=key, ttl_seconds=30) is True
        ttl = client.ttl(key=key)
        assert ttl.exists is True
        assert ttl.seconds is not None
        assert 0 <= ttl.seconds <= 30
        client.delete(key=key)


def test_mget_preserves_order_and_missing_positions() -> None:
    keys = [f'{_PREFIX}mget:a', f'{_PREFIX}mget:missing', f'{_PREFIX}mget:b']
    with RedisClient(settings=_settings()) as client:
        for key in keys:
            client.delete(key=key)
        client.set(key=keys[0], value=b'A')
        client.set(key=keys[2], value=b'B')
        assert client.mget(keys=keys) == (b'A', None, b'B')
        client.delete(key=keys[0])
        client.delete(key=keys[2])


def test_database_isolation_supports_independent_composed_connections() -> None:
    key = f'{_PREFIX}database'
    with (
        RedisClient(settings=_settings(database=0)) as zero,
        RedisClient(settings=_settings(database=1)) as one,
    ):
        zero.delete(key=key)
        one.delete(key=key)
        zero.set(key=key, value=b'zero')
        one.set(key=key, value=b'one')
        assert zero.get(key=key) == b'zero'
        assert one.get(key=key) == b'one'
        zero.delete(key=key)
        one.delete(key=key)


def test_bad_password_is_sanitized_and_not_chained() -> None:
    private_password = 'private-wrong-password'
    client = RedisClient(settings=_settings(password=private_password))
    with pytest.raises(RedisAuthenticationError) as captured:
        client.health_check()
    assert private_password not in repr(captured.value)
    assert os.environ['ATLANTICUS_REDIS_URL'] not in repr(captured.value)
    assert captured.value.__cause__ is None
