from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
_CONNECTIVITY_ROOT = _REPOSITORY_ROOT / 'connectivity'
_REDIS_ROOT = _CONNECTIVITY_ROOT / 'redis'
_DOCKER_ROOT = _CONNECTIVITY_ROOT / 'docker'


def test_redis_package_dependency_boundary_is_small() -> None:
    pyproject = (_REDIS_ROOT / 'pyproject.toml').read_text()
    assert 'redis==8.1.0' in pyproject
    assert 'hiredis' not in pyproject
    assert 'redis-py-entraid' not in pyproject
    assert 'pandas' not in pyproject
    assert 'polars' not in pyproject
    assert 'pyarrow' not in pyproject


def test_redis_is_anchored_in_workspace_and_validation_gates() -> None:
    workspace = (_CONNECTIVITY_ROOT / 'pyproject.toml').read_text()
    check_sh = (_CONNECTIVITY_ROOT / 'scripts/validation/check.sh').read_text()
    check_bat = (_CONNECTIVITY_ROOT / 'scripts/validation/check.bat').read_text()
    assert 'atlanticus-redis = { workspace = true }' in workspace
    assert '"redis"' in workspace
    assert '"redis/tests"' in workspace
    assert 'atlanticus-redis' in check_sh
    assert 'atlanticus.connectivity.redis' in check_sh
    assert 'docker/redis/compose.yaml' in check_sh
    assert 'atlanticus-redis' in check_bat
    assert 'atlanticus.connectivity.redis' in check_bat
    assert r'docker\redis\compose.yaml' in check_bat


def test_redis_docker_is_pinned_and_multi_arch() -> None:
    dockerfile = (_DOCKER_ROOT / 'redis/Dockerfile').read_text()
    compose = (_DOCKER_ROOT / 'redis/compose.yaml').read_text()
    redis_integration = compose.split('  redis-integration:', 1)[1]
    assert 'redis:8.8.0-alpine3.23' in compose
    assert '--platform=' not in dockerfile
    assert 'platform:' not in redis_integration


def test_redis_connection_contract_keeps_endpoint_and_secrets_separate() -> None:
    settings = (_REDIS_ROOT / 'src/atlanticus/connectivity/redis/settings.py').read_text()
    compose = (_DOCKER_ROOT / 'redis/compose.yaml').read_text()
    assert 'url: str' in settings
    assert 'username: str' in settings
    assert 'password: str = field(repr=False)' in settings
    assert 'RedisPasswordCredential' not in settings
    assert 'url must not contain Redis credentials' in settings
    assert 'ATLANTICUS_REDIS_URL' in compose
    assert 'ATLANTICUS_REDIS_USERNAME' in compose


def test_redis_package_has_complete_public_shape() -> None:
    package_root = _REDIS_ROOT / 'src/atlanticus/connectivity/redis'
    commented_root = _REDIS_ROOT / 'commented/atlanticus/connectivity/redis'
    assert {path.name for path in package_root.iterdir() if path.is_file()} == {
        '__init__.py',
        'client.py',
        'errors.py',
        'models.py',
        'py.typed',
        'settings.py',
    }
    assert {path.name for path in commented_root.glob('*.py')} == {
        '__init__.py',
        'client.py',
        'errors.py',
        'models.py',
        'settings.py',
    }


def test_redis_runtime_policy_is_explicitly_stable() -> None:
    client = (_REDIS_ROOT / 'src/atlanticus/connectivity/redis/client.py').read_text()
    assert 'protocol=2' in client
    assert 'health_check_interval=0' in client
    assert 'sdk.Retry(sdk.NoBackoff(), 0)' in client
    assert 'decode_responses=False' in client
    assert 'driver_info=None' in client
