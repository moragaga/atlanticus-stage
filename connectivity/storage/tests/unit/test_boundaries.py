from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
_CONNECTIVITY_ROOT = _REPOSITORY_ROOT / 'connectivity'
_STORAGE_ROOT = _CONNECTIVITY_ROOT / 'storage'
_DOCKER_ROOT = _CONNECTIVITY_ROOT / 'docker'


def test_storage_package_dependency_boundary_is_small() -> None:
    pyproject = (_STORAGE_ROOT / 'pyproject.toml').read_text()
    assert 'azure-storage-blob==12.30.0' in pyproject
    assert 'azure-storage-file-datalake' not in pyproject
    assert 'pandas' not in pyproject
    assert 'polars' not in pyproject
    assert 'pyarrow' not in pyproject


def test_storage_is_anchored_in_workspace_and_validation_gates() -> None:
    workspace = (_CONNECTIVITY_ROOT / 'pyproject.toml').read_text()
    check_sh = (_CONNECTIVITY_ROOT / 'scripts/validation/check.sh').read_text()
    check_bat = (_CONNECTIVITY_ROOT / 'scripts/validation/check.bat').read_text()
    assert 'atlanticus-storage = { workspace = true }' in workspace
    assert '"storage"' in workspace
    assert '"storage/tests"' in workspace
    assert 'atlanticus-storage' in check_sh
    assert 'atlanticus.connectivity.storage' in check_sh
    assert 'docker/storage/compose.yaml' in check_sh
    assert 'atlanticus-storage' in check_bat
    assert 'atlanticus.connectivity.storage' in check_bat
    assert r'docker\storage\compose.yaml' in check_bat


def test_storage_docker_client_is_multi_arch() -> None:
    dockerfile = (_DOCKER_ROOT / 'storage/Dockerfile').read_text()
    compose = (_DOCKER_ROOT / 'storage/compose.yaml').read_text()
    storage_integration = compose.split('  storage-integration:', 1)[1]
    assert '--platform=' not in dockerfile
    assert 'platform:' not in storage_integration
    assert 'mcr.microsoft.com/azure-storage/azurite:3.36.0' in compose


def test_storage_package_has_complete_public_shape() -> None:
    package_root = _STORAGE_ROOT / 'src/atlanticus/connectivity/storage'
    commented_root = _STORAGE_ROOT / 'commented/atlanticus/connectivity/storage'
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
