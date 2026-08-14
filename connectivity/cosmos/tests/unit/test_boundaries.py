from __future__ import annotations

import ast
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


def test_azure_local_cosmos_provisioning_is_floci_rest_only() -> None:
    provisioning = (
        _CONNECTIVITY_ROOT / 'docker/azure-local/provisioning/provision_connectivity.py'
    ).read_text()
    tree = ast.parse(provisioning)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == '_provision_cosmos'
    )
    cosmos_provisioning = ast.get_source_segment(provisioning, function)
    assert cosmos_provisioning is not None

    assert 'from azure.cosmos' not in provisioning
    assert 'AzureCosmosClient' not in provisioning
    assert '_refresh_thread' not in provisioning
    assert 'time.sleep(' not in cosmos_provisioning
    assert 'requests.Session()' in cosmos_provisioning
    assert "f'{endpoint}/dbs'" in cosmos_provisioning
    assert "f'{endpoint}/dbs/{database_name}/colls/'" in cosmos_provisioning
    assert 'response.status_code in {201, 409}' in provisioning
