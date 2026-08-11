from __future__ import annotations

from pathlib import Path

import atlanticus.configuration as configuration

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def test_public_api_and_version_are_stable() -> None:
    assert configuration.__version__ == '0.1.0'
    assert configuration.__all__ == [
        'ConfigurationBootstrap',
        'ConfigurationError',
        'ConfigurationSource',
        'ConfigurationSourceError',
        'ConfigurationValueError',
        'ConfigurationVariableSpec',
        'MissingConfigurationVariablesError',
        'ResolvedConfiguration',
        'SecretManifestEntry',
        'SecretResolutionError',
        'SecretResolver',
        'SecretsManifest',
        'SecretsManifestError',
        '__version__',
    ]


def test_configuration_has_no_azure_or_business_dependencies() -> None:
    pyproject = (_PACKAGE_ROOT / 'pyproject.toml').read_text(encoding='utf-8')
    source = '\n'.join(
        path.read_text(encoding='utf-8') for path in (_PACKAGE_ROOT / 'src').rglob('*.py')
    )

    assert 'azure-' not in pyproject
    assert 'azure.' not in source
    assert 'ada' not in pyproject.lower()
    assert 'alarm' not in pyproject.lower()
    assert 'kpi' not in pyproject.lower()
    assert 'atlanticus-observability' not in pyproject
