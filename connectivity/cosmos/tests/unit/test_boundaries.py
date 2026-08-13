from __future__ import annotations

from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
_CONNECTIVITY_ROOT = _PACKAGE_ROOT.parent


def test_package_has_no_pandas_or_business_runtime_dependencies() -> None:
    pyproject = (_PACKAGE_ROOT / 'pyproject.toml').read_text()
    assert 'pandas' not in pyproject.lower()
    assert 'atlanticus-job-runtime' not in pyproject
    assert 'alarm' not in pyproject.lower()
    assert 'kpi' not in pyproject.lower()


def test_public_contract_uses_client_naming() -> None:
    source = _PACKAGE_ROOT / 'src/atlanticus/connectivity/cosmos'
    commented = _PACKAGE_ROOT / 'commented/atlanticus/connectivity/cosmos'

    assert (source / 'client.py').is_file()
    assert (commented / 'client.py').is_file()
    assert not (source / 'service.py').exists()
    assert not (commented / 'service.py').exists()


def test_connector_does_not_resolve_environment_variable_names() -> None:
    source = (_PACKAGE_ROOT / 'src/atlanticus/connectivity/cosmos/settings.py').read_text()

    assert 'from_mapping' not in source
    assert 'COSMOS_ENDPOINT' not in source
    assert 'COSMOS_KEY' not in source
    assert 'COSMOS_DATABASE_NAME' not in source


def test_cosmos_has_an_isolated_docker_integration_gate() -> None:
    compose = _CONNECTIVITY_ROOT / 'docker/cosmos/compose.yaml'
    check = (_CONNECTIVITY_ROOT / 'scripts/validation/check.sh').read_text()
    check_windows = (_CONNECTIVITY_ROOT / 'scripts/validation/check.bat').read_text()

    assert compose.is_file()
    compose_text = compose.read_text()
    assert 'cosmos-emulator:' in compose_text
    assert 'cosmos-integration:' in compose_text
    assert 'platform: linux/amd64' not in compose_text
    assert 'atlanticus-cosmos-integration:local' in compose_text
    assert 'cosmos' in check
    assert 'cosmos' in check_windows


def test_cosmos_client_has_deterministic_lifecycle() -> None:
    client_source = (_PACKAGE_ROOT / 'src/atlanticus/connectivity/cosmos/client.py').read_text()
    public_api = (_PACKAGE_ROOT / 'src/atlanticus/connectivity/cosmos/__init__.py').read_text()

    assert 'def close(' in client_source
    assert 'def __enter__(' in client_source
    assert 'def __exit__(' in client_source
    assert 'CosmosClosedError' in client_source
    assert 'CosmosClosedError' in public_api


def test_pinned_azure_cosmos_sdk_exposes_sync_close() -> None:
    from azure.cosmos import CosmosClient as AzureCosmosSdkClient

    pyproject = (_PACKAGE_ROOT / 'pyproject.toml').read_text()
    assert 'azure-cosmos==4.16.3' in pyproject
    assert callable(getattr(AzureCosmosSdkClient, 'close', None))
