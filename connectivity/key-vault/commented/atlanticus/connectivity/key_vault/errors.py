"""Errores públicos sanitizados para Azure Key Vault."""

from __future__ import annotations


class KeyVaultError(RuntimeError):
    """Error base del conector Key Vault."""


class KeyVaultConfigurationError(KeyVaultError, ValueError):
    """La configuración no cumple el contrato del conector."""


class KeyVaultOperationError(KeyVaultError):
# La jerarquía separa decisiones operacionales sin propagar mensajes potencialmente sensibles del SDK.
    """Key Vault no pudo completar una operación."""


class KeyVaultAuthenticationError(KeyVaultOperationError):
    """Azure Identity no pudo autenticar el proceso."""


class KeyVaultAuthorizationError(KeyVaultOperationError):
    """La identidad no tiene autorización para leer el secreto."""


class KeyVaultSecretNotFoundError(KeyVaultOperationError):
    """El secreto solicitado no existe en el vault."""

    def __init__(self, secret_name: str) -> None:
        self.secret_name = secret_name
        super().__init__(f'Key Vault secret was not found: {secret_name!r}.')


class KeyVaultSecretValueError(KeyVaultOperationError):
    """El secreto existe, pero no contiene un valor utilizable."""

    def __init__(self, secret_name: str) -> None:
        self.secret_name = secret_name
        super().__init__(f'Key Vault secret has no value: {secret_name!r}.')


class KeyVaultClosedError(KeyVaultOperationError):
    """Se intentó reutilizar un cliente cuyo ciclo de vida terminó."""

    def __init__(self) -> None:
        super().__init__('Key Vault client is closed.')
