from __future__ import annotations

from pathlib import Path

import atlanticus.connectivity.key_vault as key_vault

_PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def test_public_api_and_version_are_stable() -> None:
    assert key_vault.__version__ == '0.1.0'
    assert key_vault.__all__ == [
        'KeyVaultAuthenticationError',
        'KeyVaultAuthorizationError',
        'KeyVaultClient',
        'KeyVaultClosedError',
        'KeyVaultConfigurationError',
        'KeyVaultError',
        'KeyVaultOperationError',
        'KeyVaultSecretNotFoundError',
        'KeyVaultSecretValueError',
        'KeyVaultSettings',
        '__version__',
    ]


def test_package_dependencies_match_the_agreed_boundary() -> None:
    pyproject = (_PACKAGE_ROOT / 'pyproject.toml').read_text(encoding='utf-8')

    assert 'atlanticus-kernel==0.1.0' in pyproject
    assert 'azure-identity==1.25.3' in pyproject
    assert 'azure-keyvault-secrets==4.11.0' in pyproject
    assert 'atlanticus-configuration' not in pyproject
    assert 'atlanticus-observability' not in pyproject
    assert 'ada' not in pyproject.lower()
    assert 'alarm' not in pyproject.lower()
    assert 'kpi' not in pyproject.lower()


def test_package_does_not_define_an_explicit_key_vault_name_contract() -> None:
    package_root = _PACKAGE_ROOT / 'src' / 'atlanticus' / 'connectivity' / 'key_vault'
    source = ''.join(path.read_text(encoding='utf-8') for path in package_root.glob('*.py'))

    assert 'KEY_VAULT_NAME' not in source
    assert 'from_name' not in source
    assert 'from_url' not in source
    assert 'from_mapping' not in source


def test_azure_local_harness_does_not_leak_into_product_contract() -> None:
    product_root = _PACKAGE_ROOT / 'src' / 'atlanticus' / 'connectivity' / 'key_vault'
    product_source = ''.join(path.read_text(encoding='utf-8') for path in product_root.glob('*.py'))

    assert 'floci' not in product_source.lower()
    assert 'ForceHttp' not in product_source
    assert 'ATLANTICUS_FLOCI' not in product_source


def test_azure_local_integration_is_separate_from_specialized_gate() -> None:
    connectivity_root = _PACKAGE_ROOT.parent
    check_sh = (connectivity_root / 'scripts/validation/check.sh').read_text(encoding='utf-8')
    azure_local_check = (connectivity_root / 'scripts/validation/check-azure-local.sh').read_text(
        encoding='utf-8'
    )
    compose = (connectivity_root / 'docker/azure-local/compose.yaml').read_text(encoding='utf-8')

    assert 'docker/azure-local/compose.yaml' not in check_sh
    assert 'key-vault' in azure_local_check
    assert 'storage' in azure_local_check
    assert 'cosmos' in azure_local_check
    assert 'floci/floci-az:0.10.0' in compose
    assert 'floci/floci-az:latest' not in compose
    assert 'FLOCI_AZ_STORAGE_MODE: "memory"' in compose


def test_integration_layout_separates_local_and_azure_local_contracts() -> None:
    connectivity_root = _PACKAGE_ROOT.parent
    local_modules = {
        'http-client': 'test_http_fake_api.py',
        'cosmos': 'test_cosmos_emulator.py',
        'service-bus': 'test_service_bus_emulator.py',
        'sql': 'test_sql_server.py',
        'storage': 'test_storage_blob.py',
        'redis': 'test_redis_server.py',
    }

    for module, test_name in local_modules.items():
        assert (connectivity_root / module / 'tests/integration/local' / test_name).is_file()

    assert (
        connectivity_root / 'key-vault/tests/integration/azure_local/test_key_vault_floci.py'
    ).is_file()
    assert (
        connectivity_root / 'storage/tests/integration/azure_local/test_storage_floci.py'
    ).is_file()
    assert (
        connectivity_root / 'cosmos/tests/integration/azure_local/test_cosmos_floci.py'
    ).is_file()


def test_azure_local_runner_uses_workspace_virtual_environment() -> None:
    connectivity_root = _PACKAGE_ROOT.parent
    runner = (connectivity_root / 'docker/azure-local/run-connectivity.sh').read_text(
        encoding='utf-8'
    )

    assert 'PYTHON=".venv/bin/python"' in runner
    assert '"$PYTHON" docker/azure-local/provisioning/provision_connectivity.py' in runner
    assert 'run_integration()' in runner
    assert 'timeout=60' in runner
    assert 'Azure-local integration timed out after 60 seconds' in runner
    assert 'storage/tests/integration/azure_local' in runner
    assert 'cosmos/tests/integration/azure_local' in runner
