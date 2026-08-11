"""Cliente síncrono de solo lectura para Azure Key Vault."""

from __future__ import annotations

import re
from types import TracebackType

from azure.core.exceptions import (
    ClientAuthenticationError,
    HttpResponseError,
    ResourceNotFoundError,
)
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

from atlanticus.connectivity.key_vault.errors import (
    KeyVaultAuthenticationError,
    KeyVaultAuthorizationError,
    KeyVaultClosedError,
    KeyVaultConfigurationError,
    KeyVaultOperationError,
    KeyVaultSecretNotFoundError,
    KeyVaultSecretValueError,
)
from atlanticus.connectivity.key_vault.settings import KeyVaultSettings

_SECRET_NAME_PATTERN = re.compile(r'^[0-9A-Za-z-]{1,127}$')


class KeyVaultClient:
    """Reutiliza una credencial y un cliente durante el bootstrap del proceso."""

    def __init__(self, *, settings: KeyVaultSettings) -> None:
        if not isinstance(settings, KeyVaultSettings):
            raise KeyVaultConfigurationError('settings must be KeyVaultSettings.')
        self.settings = settings
        self._credential: DefaultAzureCredential | None = None
        self._secret_client: SecretClient | None = None
        self._closed = False

    def __enter__(self) -> KeyVaultClient:
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            self.close()
        except KeyVaultOperationError:
            if exc_value is None:
                raise

    def open(self) -> None:
        """Crea una única credencial y un único cliente reutilizables."""

        if self._closed:
            raise KeyVaultClosedError()
        if self._secret_client is not None:
            return

        credential: DefaultAzureCredential | None = None
        try:
            credential = DefaultAzureCredential()
            secret_client = SecretClient(
                vault_url=self.settings.vault_url,
                credential=credential,
            )
        except ClientAuthenticationError:
            _close_quietly(credential)
            raise KeyVaultAuthenticationError(
                'Azure Identity could not authenticate the Key Vault client.'
            ) from None
        except Exception:
            _close_quietly(credential)
            raise KeyVaultOperationError('Could not open Key Vault client.') from None

        self._credential = credential
        self._secret_client = secret_client

    def get_secret(self, secret_name: str) -> str:
        """Obtiene el valor exacto de un secreto por su nombre."""

        normalized_name = _validate_secret_name(secret_name)
        client = self._require_client()
        try:
            secret = client.get_secret(normalized_name)
        except ResourceNotFoundError:
            raise KeyVaultSecretNotFoundError(normalized_name) from None
        except ClientAuthenticationError:
            raise KeyVaultAuthenticationError(
                'Azure Identity could not authenticate the Key Vault client.'
            ) from None
        except HttpResponseError as error:
            status_code = getattr(error, 'status_code', None)
            if status_code == 401:
                raise KeyVaultAuthenticationError(
                    'Azure Identity could not authenticate the Key Vault client.'
                ) from None
            if status_code == 403:
                raise KeyVaultAuthorizationError(
                    f'Access to Key Vault secret {normalized_name!r} was denied.'
                ) from None
            raise KeyVaultOperationError(
                f'Could not retrieve Key Vault secret {normalized_name!r}.'
            ) from None
        except Exception:
            raise KeyVaultOperationError(
                f'Could not retrieve Key Vault secret {normalized_name!r}.'
            ) from None

        value = getattr(secret, 'value', None)
        if not isinstance(value, str) or value == '':
            raise KeyVaultSecretValueError(normalized_name)
        return value

    def close(self) -> None:
        """Cierra ambos recursos de forma idempotente e independiente."""

        secret_client = self._secret_client
        credential = self._credential
        self._secret_client = None
        self._credential = None
        self._closed = True

        failed = False
        if secret_client is not None:
            try:
                secret_client.close()
            except Exception:
                failed = True
        if credential is not None:
            try:
                credential.close()
            except Exception:
                failed = True
        if failed:
            raise KeyVaultOperationError('Could not close Key Vault client.') from None

    def _require_client(self) -> SecretClient:
        self.open()
        if self._secret_client is None:
            raise KeyVaultOperationError('Key Vault client is not open.')
        return self._secret_client


def _validate_secret_name(value: str) -> str:
    if not isinstance(value, str):
        raise KeyVaultConfigurationError('secret_name must be a string.')
    if _SECRET_NAME_PATTERN.fullmatch(value) is None:
        raise KeyVaultConfigurationError(
            'secret_name must contain 1-127 letters, numbers or hyphens.'
        )
    return value


def _close_quietly(resource: object | None) -> None:
    if resource is None:
        return
    close = getattr(resource, 'close', None)
    if not callable(close):
        return
    try:
        close()
    except Exception:
        return
