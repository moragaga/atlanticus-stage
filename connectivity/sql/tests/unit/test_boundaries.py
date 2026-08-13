from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
_CONNECTIVITY_ROOT = _REPOSITORY_ROOT / 'connectivity'
_SQL_ROOT = _CONNECTIVITY_ROOT / 'sql'
_DOCKER_ROOT = _CONNECTIVITY_ROOT / 'docker'


def test_sql_package_uses_only_mssql_python_driver() -> None:
    pyproject = (_SQL_ROOT / 'pyproject.toml').read_text()

    assert 'mssql-python==1.13.0' in pyproject
    assert 'pyodbc' not in pyproject
    assert 'pyarrow' not in pyproject


def test_sql_is_anchored_in_connectivity_workspace_and_validation_gates() -> None:
    workspace = (_CONNECTIVITY_ROOT / 'pyproject.toml').read_text()
    check_sh = (_CONNECTIVITY_ROOT / 'scripts/validation/check.sh').read_text()
    check_bat = (_CONNECTIVITY_ROOT / 'scripts/validation/check.bat').read_text()

    assert 'atlanticus-sql = { workspace = true }' in workspace
    assert '"sql"' in workspace
    assert '"sql/tests"' in workspace
    assert 'atlanticus-sql' in check_sh
    assert 'atlanticus.connectivity.sql' in check_sh
    assert 'docker/sql/compose.yaml' in check_sh
    assert 'atlanticus-sql' in check_bat
    assert 'atlanticus.connectivity.sql' in check_bat
    assert r'docker\sql\compose.yaml' in check_bat


def test_sql_integration_client_is_multi_arch_and_has_no_external_odbc_install() -> None:
    dockerfile = (_DOCKER_ROOT / 'sql/Dockerfile').read_text()
    compose = (_DOCKER_ROOT / 'sql/compose.yaml').read_text()
    sql_integration = compose.split('  sql-integration:', 1)[1]

    assert 'msodbcsql17' not in dockerfile
    assert 'msodbcsql18' not in dockerfile
    assert 'unixodbc' not in dockerfile.lower()
    assert 'libltdl7' in dockerfile
    assert 'libkrb5-3' in dockerfile
    assert 'libgssapi-krb5-2' in dockerfile
    assert 'platform: linux/amd64' not in sql_integration
    assert (
        'sql-server:\n    image: mcr.microsoft.com/mssql/server:2019-latest\n'
        '    platform: linux/amd64'
    ) in compose


def test_sql_package_has_complete_public_package_shape() -> None:
    package_root = _SQL_ROOT / 'src/atlanticus/connectivity/sql'
    commented_root = _SQL_ROOT / 'commented/atlanticus/connectivity/sql'

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
