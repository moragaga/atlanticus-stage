from __future__ import annotations

import pytest

from atlanticus.connectivity.key_vault import (
    KeyVaultConfigurationError,
    KeyVaultSettings,
)
from atlanticus.kernel import Environment


def test_settings_derives_vault_name_and_url_from_contract() -> None:
    settings = KeyVaultSettings(
        company_abrev='MLP',
        environment=Environment.from_value('dev'),
        product_abrev='ADA',
    )

    assert settings.company_abrev == 'MLP'
    assert str(settings.environment) == 'dev'
    assert settings.product_abrev == 'ADA'
    assert settings.vault_name == 'mlp-dev-kv-ada'
    assert settings.vault_url == 'https://mlp-dev-kv-ada.vault.azure.net'


@pytest.mark.parametrize('environment', ['local', 'dev', 'uat', 'stg', 'prd'])
def test_settings_uses_validated_environment_without_own_environment_policy(
    environment: str,
) -> None:
    settings = KeyVaultSettings(
        company_abrev='MLP',
        environment=Environment.from_value(environment),
        product_abrev='ADA',
    )

    assert settings.vault_name == f'mlp-{environment}-kv-ada'


def test_settings_requires_environment_contract_instead_of_raw_text() -> None:
    with pytest.raises(KeyVaultConfigurationError, match='Environment'):
        KeyVaultSettings(
            company_abrev='MLP',
            environment='dev',  # type: ignore[arg-type]
            product_abrev='ADA',
        )


@pytest.mark.parametrize(
    ('company_abrev', 'product_abrev'),
    [
        ('', 'ADA'),
        ('MLP', ''),
        (' MLP', 'ADA'),
        ('1MLP', 'ADA'),
        ('MLP-', 'ADA'),
        ('MLP', 'ADA-'),
        ('VERYLONGCOMPANY', 'VERYLONGPRODUCT'),
    ],
)
def test_settings_rejects_invalid_derived_vault_name(
    company_abrev: str,
    product_abrev: str,
) -> None:
    with pytest.raises(KeyVaultConfigurationError):
        KeyVaultSettings(
            company_abrev=company_abrev,
            environment=Environment.from_value('dev'),
            product_abrev=product_abrev,
        )


def test_settings_has_no_alternative_vault_name_factories() -> None:
    assert not hasattr(KeyVaultSettings, 'from_name')
    assert not hasattr(KeyVaultSettings, 'from_url')
    assert not hasattr(KeyVaultSettings, 'from_mapping')
