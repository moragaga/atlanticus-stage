import atlanticus.connectivity.redis as redis


def test_public_api_is_explicit_and_versioned() -> None:
    expected = {
        'DEFAULT_REDIS_CONNECTION_TIMEOUT_SECONDS',
        'DEFAULT_REDIS_DATABASE',
        'DEFAULT_REDIS_MAX_CONNECTIONS',
        'DEFAULT_REDIS_MAX_MGET_KEYS',
        'DEFAULT_REDIS_OPERATION_TIMEOUT_SECONDS',
        'DEFAULT_REDIS_PORT',
        'RedisAuthenticationError',
        'RedisAuthorizationError',
        'RedisClient',
        'RedisClosedError',
        'RedisConfigurationError',
        'RedisConnectionError',
        'RedisError',
        'RedisOperationError',
        'RedisPoolExhaustedError',
        'RedisResultLimitError',
        'RedisSettings',
        'RedisTtl',
        '__version__',
    }
    assert set(redis.__all__) == expected
    assert redis.__version__ == '0.1.0'
