"""Cliente síncrono y genérico para Redis."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib import import_module
from types import TracebackType
from typing import Any

from atlanticus.connectivity.redis.errors import (
    RedisAuthenticationError,
    RedisAuthorizationError,
    RedisClosedError,
    RedisConfigurationError,
    RedisConnectionError,
    RedisError,
    RedisOperationError,
    RedisPoolExhaustedError,
    RedisResultLimitError,
)
from atlanticus.connectivity.redis.models import RedisTtl
from atlanticus.connectivity.redis.settings import RedisSettings, _parse_redis_endpoint
from atlanticus.observability import ErrorInfo, ResultSummary, runtime_guard

_COMPONENT = 'atlanticus.connectivity.redis'


@dataclass(frozen=True, slots=True)
class _RedisSdk:
    Redis: Any
    NoBackoff: Any
    Retry: Any
    AuthenticationError: type[BaseException]
    AuthorizationError: type[BaseException]
    ConnectionError: type[BaseException]
    TimeoutError: type[BaseException]
    MaxConnectionsError: type[BaseException]
    RedisSdkError: type[BaseException]


def _load_sdk() -> _RedisSdk:
    redis = import_module('redis')
    backoff = import_module('redis.backoff')
    retry = import_module('redis.retry')
    exceptions = import_module('redis.exceptions')
    return _RedisSdk(
        Redis=redis.Redis,
        NoBackoff=backoff.NoBackoff,
        Retry=retry.Retry,
        AuthenticationError=exceptions.AuthenticationError,
        AuthorizationError=exceptions.AuthorizationError,
        ConnectionError=exceptions.ConnectionError,
        TimeoutError=exceptions.TimeoutError,
        MaxConnectionsError=exceptions.MaxConnectionsError,
        RedisSdkError=exceptions.RedisError,
    )


def _safe_parameters(_: tuple[Any, ...], values: Mapping[str, Any]) -> Mapping[str, Any]:
    safe: dict[str, Any] = {}
    if 'ttl_seconds' in values and values.get('ttl_seconds') is not None:
        safe['ttl_seconds'] = values['ttl_seconds']
    value = values.get('value')
    if isinstance(value, bytes | bytearray | memoryview):
        safe['byte_count'] = len(value)
    keys = values.get('keys')
    if isinstance(keys, Sequence) and not isinstance(keys, str | bytes | bytearray):
        safe['key_count'] = len(keys)
    return safe


def _safe_error(error: BaseException) -> ErrorInfo:
    message = str(error) if isinstance(error, RedisError | TypeError) else 'Redis operation failed'
    return ErrorInfo(error_type=type(error).__name__, message=message)


def _get_result(value: Any) -> ResultSummary:
    if isinstance(value, bytes):
        return ResultSummary(metrics={'found': True, 'byte_count': len(value)})
    if value is None:
        return ResultSummary(metrics={'found': False})
    return ResultSummary()


def _mget_result(value: Any) -> ResultSummary:
    if isinstance(value, tuple):
        return ResultSummary(
            metrics={
                'item_count': len(value),
                'hit_count': sum(item is not None for item in value),
            }
        )
    return ResultSummary()


def _ttl_result(value: Any) -> ResultSummary:
    if isinstance(value, RedisTtl):
        return ResultSummary(
            metrics={
                'exists': value.exists,
                'has_expiry': value.has_expiry,
            }
        )
    return ResultSummary()


class RedisClient:
    """Opera keys binarias sin conocer nombres de conexión ni reglas de negocio."""

    def __init__(self, *, settings: RedisSettings) -> None:
        if not isinstance(settings, RedisSettings):
            raise RedisConfigurationError('settings must be RedisSettings')
        self.settings = settings
        self._client: Any | None = None
        self._sdk: _RedisSdk | None = None
        self._closed = False

    def __enter__(self) -> RedisClient:
        self.open()
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        try:
            self.close()
        except RedisConnectionError:
            if exception_type is None:
                raise

    def open(self) -> RedisClient:
        """Construye el cliente redis-py de forma lazy e idempotente."""

        self._get_client()
        return self

    def close(self) -> None:
        """Cierra el pool del cliente una sola vez y finaliza esta instancia."""

        if self._closed:
            return
        self._closed = True
        client, self._client = self._client, None
        if client is None:
            return
        try:
            client.close()
        except Exception:
            raise RedisConnectionError('Could not close Redis client') from None

    @runtime_guard(
        operation='redis.health_check',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        error_mapper=_safe_error,
    )
    def health_check(self) -> bool:
        """Ejecuta un PING explícito; no habilita health checks periódicos."""

        try:
            result = self._get_client().ping()
        except Exception as error:
            raise self._map_error(error) from None
        if result is not True:
            raise RedisOperationError('Redis health check returned an unexpected response')
        return True

    @runtime_guard(
        operation='redis.get',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        result_mapper=_get_result,
        error_mapper=_safe_error,
    )
    def get(self, *, key: str) -> bytes | None:
        """Obtiene bytes sin imponer codificación ni formato de payload."""

        normalized_key = _require_key(key)
        try:
            value = self._get_client().get(normalized_key)
        except Exception as error:
            raise self._map_error(error) from None
        if value is None:
            return None
        if not isinstance(value, bytes):
            raise RedisOperationError('Redis returned an unexpected value type')
        return value

    @runtime_guard(
        operation='redis.set',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        error_mapper=_safe_error,
    )
    def set(
        self, *, key: str, value: bytes | bytearray | memoryview, ttl_seconds: int | None = None
    ) -> None:
        """Guarda un payload binario, opcionalmente con expiración atómica."""

        normalized_key = _require_key(key)
        normalized_value = _require_binary(value)
        normalized_ttl = (
            None
            if ttl_seconds is None
            else _require_runtime_positive_integer(ttl_seconds, 'ttl_seconds')
        )
        try:
            result = self._get_client().set(normalized_key, normalized_value, ex=normalized_ttl)
        except Exception as error:
            raise self._map_error(error) from None
        if result is not True:
            raise RedisOperationError('Redis SET returned an unexpected response')

    @runtime_guard(
        operation='redis.delete',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        error_mapper=_safe_error,
    )
    def delete(self, *, key: str) -> bool:
        """Elimina una key y retorna si existía."""

        normalized_key = _require_key(key)
        try:
            deleted = self._get_client().delete(normalized_key)
        except Exception as error:
            raise self._map_error(error) from None
        if not isinstance(deleted, int) or isinstance(deleted, bool) or deleted not in {0, 1}:
            raise RedisOperationError('Redis DELETE returned an unexpected response')
        return deleted == 1

    @runtime_guard(
        operation='redis.exists',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        error_mapper=_safe_error,
    )
    def exists(self, *, key: str) -> bool:
        """Indica si existe una key."""

        normalized_key = _require_key(key)
        try:
            count = self._get_client().exists(normalized_key)
        except Exception as error:
            raise self._map_error(error) from None
        if not isinstance(count, int) or isinstance(count, bool) or count not in {0, 1}:
            raise RedisOperationError('Redis EXISTS returned an unexpected response')
        return count == 1

    @runtime_guard(
        operation='redis.expire',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        error_mapper=_safe_error,
    )
    def expire(self, *, key: str, ttl_seconds: int) -> bool:
        """Asigna expiración y retorna False cuando la key no existe."""

        normalized_key = _require_key(key)
        normalized_ttl = _require_runtime_positive_integer(ttl_seconds, 'ttl_seconds')
        try:
            result = self._get_client().expire(normalized_key, normalized_ttl)
        except Exception as error:
            raise self._map_error(error) from None
        if not isinstance(result, bool):
            raise RedisOperationError('Redis EXPIRE returned an unexpected response')
        return result

    @runtime_guard(
        operation='redis.ttl',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        result_mapper=_ttl_result,
        error_mapper=_safe_error,
    )
    def ttl(self, *, key: str) -> RedisTtl:
        """Distingue key ausente, persistente y con expiración."""

        normalized_key = _require_key(key)
        try:
            value = self._get_client().ttl(normalized_key)
        except Exception as error:
            raise self._map_error(error) from None
        if not isinstance(value, int) or isinstance(value, bool):
            raise RedisOperationError('Redis TTL returned an unexpected response')
        if value == -2:
            return RedisTtl(exists=False, seconds=None)
        if value == -1:
            return RedisTtl(exists=True, seconds=None)
        if value >= 0:
            return RedisTtl(exists=True, seconds=value)
        raise RedisOperationError('Redis TTL returned an unexpected response')

    @runtime_guard(
        operation='redis.mget',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        result_mapper=_mget_result,
        error_mapper=_safe_error,
    )
    def mget(self, *, keys: Sequence[str]) -> tuple[bytes | None, ...]:
        """Obtiene varias keys con un límite estricto de cardinalidad."""

        normalized_keys = _require_keys(keys)
        if not normalized_keys:
            return ()
        if len(normalized_keys) > self.settings.max_mget_keys:
            raise RedisResultLimitError(max_keys=self.settings.max_mget_keys)
        try:
            values = self._get_client().mget(normalized_keys)
        except Exception as error:
            raise self._map_error(error) from None
        if not isinstance(values, list | tuple) or len(values) != len(normalized_keys):
            raise RedisOperationError('Redis MGET returned an unexpected response')
        result: list[bytes | None] = []
        for value in values:
            if value is not None and not isinstance(value, bytes):
                raise RedisOperationError('Redis MGET returned an unexpected value type')
            result.append(value)
        return tuple(result)

    def _get_sdk(self) -> _RedisSdk:
        if self._sdk is None:
            self._sdk = _load_sdk()
        return self._sdk

    def _get_client(self) -> Any:
        if self._closed:
            raise RedisClosedError('Redis client is closed')
        if self._client is not None:
            return self._client
        sdk = self._get_sdk()
        endpoint = _parse_redis_endpoint(self.settings.url)
        try:
            client = sdk.Redis(
                host=endpoint.host,
                port=endpoint.port,
                db=self.settings.database,
                username=self.settings.username,
                password=self.settings.password,
                ssl=endpoint.tls_enabled,
                ssl_cert_reqs='required' if endpoint.tls_enabled else None,
                ssl_check_hostname=endpoint.tls_enabled,
                socket_connect_timeout=self.settings.connection_timeout_seconds,
                socket_timeout=self.settings.operation_timeout_seconds,
                socket_keepalive=True,
                decode_responses=False,
                max_connections=self.settings.max_connections,
                health_check_interval=0,
                protocol=2,
                legacy_responses=True,
                retry=sdk.Retry(sdk.NoBackoff(), 0),
                retry_on_error=[],
                driver_info=None,
            )
        except Exception as error:
            raise self._map_error(error) from None
        self._client = client
        return client

    def _map_error(self, error: BaseException) -> RedisError:
        if isinstance(error, RedisError):
            return error
        sdk = self._get_sdk()
        if isinstance(error, sdk.AuthenticationError):
            return RedisAuthenticationError('Redis authentication failed')
        if isinstance(error, sdk.AuthorizationError):
            return RedisAuthorizationError('Redis authorization failed')
        if isinstance(error, sdk.MaxConnectionsError):
            return RedisPoolExhaustedError('Redis connection pool is exhausted')
        if isinstance(error, sdk.ConnectionError | sdk.TimeoutError):
            return RedisConnectionError('Redis connection failed')
        if isinstance(error, sdk.RedisSdkError):
            return RedisOperationError('Redis operation failed')
        return RedisOperationError('Redis operation failed')


def _require_runtime_positive_integer(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f'{field_name} must be a positive integer')
    return value


def _require_key(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError('key must be text')
    if value == '':
        raise ValueError('key must not be empty')
    if '\x00' in value:
        raise ValueError('key must not contain null characters')
    return value


def _require_keys(values: Any) -> tuple[str, ...]:
    if isinstance(values, str | bytes | bytearray) or not isinstance(values, Sequence):
        raise TypeError('keys must be a sequence of text keys')
    return tuple(_require_key(value) for value in values)


def _require_binary(value: Any) -> bytes:
    if not isinstance(value, bytes | bytearray | memoryview):
        raise TypeError('value must be bytes-like')
    return bytes(value)
