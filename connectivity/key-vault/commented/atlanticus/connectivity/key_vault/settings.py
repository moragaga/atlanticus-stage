"""Configuración inmutable de un Azure Key Vault derivado por ambiente."""

from __future__ import annotations

import re
from dataclasses import dataclass

from atlanticus.connectivity.key_vault.errors import KeyVaultConfigurationError
from atlanticus.kernel import Environment

_VAULT_NAME_PATTERN = re.compile(r'^[a-z][a-z0-9-]{1,22}[a-z0-9]$')
# El nombre final se valida como recurso Azure; las piezas de entrada no se corrigen silenciosamente.
_VAULT_HOST_SUFFIX = '.vault.azure.net'


@dataclass(frozen=True, slots=True)
class KeyVaultSettings:
    """Identidad corporativa y ambiente necesarios para derivar el vault."""

    company_abrev: str
    environment: Environment
    product_abrev: str

    def __post_init__(self) -> None:
        # Environment llega validado desde kernel; este módulo solo valida su propia composición.
        _require_component(self.company_abrev, 'company_abrev')
        if not isinstance(self.environment, Environment):
            raise KeyVaultConfigurationError('environment must be Environment.')
        _require_component(self.product_abrev, 'product_abrev')
        _validate_vault_name(self.vault_name)

    @property
    def vault_name(self) -> str:
        # La convención histórica combina compañía, ambiente y producto y normaliza solo el nombre derivado.
        """Deriva el nombre oficial del vault para el ambiente actual."""

        return f'{self.company_abrev}-{self.environment.name.value}-kv-{self.product_abrev}'.lower()

    @property
    def vault_url(self) -> str:
        """Deriva la URL pública oficial del vault."""

        return f'https://{self.vault_name}{_VAULT_HOST_SUFFIX}'


def _require_component(value: str, field_name: str) -> None:
    # No se aplica strip: un espacio en la configuración debe producir un nombre inválido, no corregirse.
    if not isinstance(value, str) or value == '':
        raise KeyVaultConfigurationError(f'{field_name} must be a non-empty string.')


def _validate_vault_name(value: str) -> None:
    if _VAULT_NAME_PATTERN.fullmatch(value) is None or '--' in value:
        raise KeyVaultConfigurationError(
            'Derived vault_name must contain 3-24 letters, numbers or valid hyphens.'
        )
