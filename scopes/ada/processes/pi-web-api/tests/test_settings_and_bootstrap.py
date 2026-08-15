import json
from pathlib import Path

from ada.processes.pi_web_api import (
    PiWebApiProcessSettings,
    configuration_specs,
    load_configuration,
)
from atlanticus.connectivity.http import HttpAuthMode


def test_settings_defaults_are_safe_and_runtime_configurable(configuration) -> None:
    settings = PiWebApiProcessSettings.from_configuration(configuration)

    assert settings.pi_web_api.pi_server == 'PISERVER'
    assert settings.pi_web_api.http.auth_mode is HttpAuthMode.BASIC
    assert settings.pi_web_api.http.username == 'domain\\user'
    assert settings.pi_web_api.http.password == 'secret'
    assert settings.pi_web_api.limits.points_max_paths == 100
    assert settings.pi_web_api.limits.interpolated_max_web_ids == 100
    assert settings.pi_web_api.limits.recorded_max_web_ids == 100
    assert settings.max_recovery_seconds == 3600


def test_configuration_specs_keep_credentials_sensitive() -> None:
    specs = {item.key: item for item in configuration_specs()}

    assert specs['PI_WEB_API_USERNAME'].sensitive is True
    assert specs['PI_WEB_API_PASSWORD'].sensitive is True
    assert specs['PI_WEB_API_POINTS_MAX_PATHS'].default == '100'
    assert specs['PI_WEB_API_INTERPOLATED_MAX_WEB_IDS'].default == '100'
    assert specs['PI_WEB_API_RECORDED_MAX_WEB_IDS'].default == '100'
    assert specs['PI_WEB_API_MAX_RECOVERY_SECONDS'].default == '3600'
    assert 'COMPANY_ABREV' not in specs
    assert 'PRODUCT_ABREV' not in specs


def test_local_bootstrap_resolves_process_values_without_secret_files(tmp_path: Path) -> None:
    values = {
        'ENVIRONMENT': 'local',
        'APPLICATION': 'ada',
        'VOLUMEN_PATH': str(tmp_path),
        'PI_WEB_API_BASE_URL': 'https://pi.example.local/piwebapi/',
        'PI_WEB_API_SERVER': 'PISERVER',
        'PI_WEB_API_USERNAME': 'domain\\user',
        'PI_WEB_API_PASSWORD': 'secret',
    }

    configuration = load_configuration(process_root=tmp_path, environ=values)

    assert configuration.require('APPLICATION') == 'ada'
    assert configuration.require('PI_WEB_API_USERNAME') == 'domain\\user'
    assert configuration.get_int('PI_WEB_API_POINTS_MAX_PATHS') == 100


def test_deployed_bootstrap_derives_key_vault_from_company_environment_and_product(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import ada.processes.pi_web_api.bootstrap as bootstrap_module

    entries = [
        {
            'var_name': 'COMPANY_ABREV',
            'secret_name': None,
            'value': 'mlp',
            'exists_in_key_vault': False,
        },
        {
            'var_name': 'PRODUCT_ABREV',
            'secret_name': None,
            'value': 'ada',
            'exists_in_key_vault': False,
        },
        {
            'var_name': 'APPLICATION',
            'secret_name': None,
            'value': 'ada',
            'exists_in_key_vault': False,
        },
        {
            'var_name': 'VOLUMEN_PATH',
            'secret_name': None,
            'value': str(tmp_path),
            'exists_in_key_vault': False,
        },
        {
            'var_name': 'PI_WEB_API_BASE_URL',
            'secret_name': None,
            'value': 'https://pi.example/piwebapi/',
            'exists_in_key_vault': False,
        },
        {
            'var_name': 'PI_WEB_API_SERVER',
            'secret_name': None,
            'value': 'PISERVER',
            'exists_in_key_vault': False,
        },
        {
            'var_name': 'PI_WEB_API_USERNAME',
            'secret_name': 'pi-user',
            'value': None,
            'exists_in_key_vault': True,
        },
        {
            'var_name': 'PI_WEB_API_PASSWORD',
            'secret_name': 'pi-password',
            'value': None,
            'exists_in_key_vault': True,
        },
    ]
    (tmp_path / 'secrets.json').write_text(json.dumps(entries), encoding='utf-8')
    captured = {}

    class FakeKeyVaultClient:
        def __init__(self, *, settings) -> None:
            captured['settings'] = settings

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

        def get_secret(self, secret_name: str) -> str:
            return {'pi-user': 'domain\\user', 'pi-password': 'secret'}[secret_name]

    monkeypatch.setattr(bootstrap_module, 'KeyVaultClient', FakeKeyVaultClient)

    configuration = load_configuration(
        process_root=tmp_path,
        environ={'ENVIRONMENT': 'prd'},
    )

    settings = captured['settings']
    assert settings.company_abrev == 'mlp'
    assert str(settings.environment) == 'prd'
    assert settings.product_abrev == 'ada'
    assert settings.vault_name == 'mlp-prd-kv-ada'
    assert configuration.require('PI_WEB_API_USERNAME') == 'domain\\user'
