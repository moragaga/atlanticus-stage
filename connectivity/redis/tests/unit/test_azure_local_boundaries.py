from __future__ import annotations

from pathlib import Path

_REDIS_ROOT = Path(__file__).resolve().parents[2]
_CONNECTIVITY_ROOT = _REDIS_ROOT.parent


def test_redis_azure_local_is_harness_only_and_registered() -> None:
    package_root = _REDIS_ROOT / 'src/atlanticus/connectivity/redis'
    product_source = ''.join(path.read_text() for path in package_root.glob('*.py'))
    compose = (_CONNECTIVITY_ROOT / 'docker/azure-local/compose.yaml').read_text()
    runner = (_CONNECTIVITY_ROOT / 'docker/azure-local/run-connectivity.sh').read_text()
    gate = (_CONNECTIVITY_ROOT / 'scripts/validation/check-azure-local.sh').read_text()

    assert 'floci' not in product_source.lower()
    assert 'ATLANTICUS_FLOCI' not in product_source
    assert 'FLOCI_AZ_SERVICES_REDIS_MOCKED: "false"' in compose
    assert '/var/run/docker.sock:/var/run/docker.sock' in compose
    assert 'redis/tests/integration/azure_local' in runner
    assert 'redis' in gate


def test_redis_azure_local_provisioning_uses_arm_and_key_vault_bridge() -> None:
    provisioning = (
        _CONNECTIVITY_ROOT / 'docker/azure-local/provisioning/provision_connectivity.py'
    ).read_text()
    test_source = (_REDIS_ROOT / 'tests/integration/azure_local/test_redis_floci.py').read_text()

    assert 'providers/Microsoft.Cache/redis' in provisioning
    assert "'provisioningState'" in provisioning
    assert '_REDIS_CREATE_TIMEOUT' in provisioning
    assert '_REDIS_PROVISIONING_TIMEOUT_SECONDS' in provisioning
    assert 'primaryKey' in provisioning
    assert 'from redis' not in provisioning
    assert 'import redis' not in provisioning
    assert 'KeyVaultClient' in test_source
    assert 'RedisSettings' in test_source
    assert 'RedisClient' in test_source
    assert 'allow_insecure_transport=True' in test_source
