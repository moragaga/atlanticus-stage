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
