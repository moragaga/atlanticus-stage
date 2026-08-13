from __future__ import annotations

from types import SimpleNamespace

import pytest
from azure.core.exceptions import (
    ClientAuthenticationError,
    HttpResponseError,
    ResourceNotFoundError,
)

import atlanticus.connectivity.key_vault.client as client_module
from atlanticus.connectivity.key_vault import (
    KeyVaultAuthenticationError,
    KeyVaultAuthorizationError,
    KeyVaultClient,
    KeyVaultClosedError,
    KeyVaultConfigurationError,
    KeyVaultOperationError,
    KeyVaultSecretNotFoundError,
    KeyVaultSecretValueError,
    KeyVaultSettings,
)
from atlanticus.kernel import Environment


class FakeCredential:
    def __init__(self, *, close_error: Exception | None = None) -> None:
        self.close_error = close_error
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


class FakeSecretClient:
    def __init__(
        self,
        *,
        vault_url: str,
        credential: FakeCredential,
        values: dict[str, str] | None = None,
        get_error: Exception | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self.vault_url = vault_url
        self.credential = credential
        self.values = values or {}
        self.get_error = get_error
        self.close_error = close_error
        self.requests: list[str] = []
        self.close_calls = 0

    def get_secret(self, secret_name: str):
        self.requests.append(secret_name)
        if self.get_error is not None:
            raise self.get_error
        return SimpleNamespace(value=self.values.get(secret_name))

    def close(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


def _settings() -> KeyVaultSettings:
    return KeyVaultSettings(
        company_abrev='MLP',
        environment=Environment.from_value('dev'),
        product_abrev='ADA',
    )


def _install_sdk(
    monkeypatch: pytest.MonkeyPatch,
    *,
    values: dict[str, str] | None = None,
    get_error: Exception | None = None,
    credential_close_error: Exception | None = None,
    client_close_error: Exception | None = None,
) -> tuple[list[FakeCredential], list[FakeSecretClient]]:
    credentials: list[FakeCredential] = []
    clients: list[FakeSecretClient] = []

    def credential_factory() -> FakeCredential:
        credential = FakeCredential(close_error=credential_close_error)
        credentials.append(credential)
        return credential

    def client_factory(*, vault_url: str, credential: FakeCredential) -> FakeSecretClient:
        client = FakeSecretClient(
            vault_url=vault_url,
            credential=credential,
            values=values,
            get_error=get_error,
            close_error=client_close_error,
        )
        clients.append(client)
        return client

    monkeypatch.setattr(client_module, 'DefaultAzureCredential', credential_factory)
    monkeypatch.setattr(client_module, 'SecretClient', client_factory)
    return credentials, clients


def _http_error(status_code: int) -> HttpResponseError:
    error = HttpResponseError('private-sdk-details')
    error.status_code = status_code
    return error


def test_get_secret_opens_lazily_reuses_resources_and_preserves_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials, clients = _install_sdk(
        monkeypatch,
        values={
            'service-bus-secret': '  Endpoint=private  ',
            'blank-secret': ' ',
        },
    )
    client = KeyVaultClient(settings=_settings())

    assert credentials == []
    assert clients == []
    assert client.get_secret('service-bus-secret') == '  Endpoint=private  '
    assert client.get_secret('blank-secret') == ' '
    assert len(credentials) == 1
    assert len(clients) == 1
    assert clients[0].vault_url == 'https://mlp-dev-kv-ada.vault.azure.net'
    assert clients[0].requests == ['service-bus-secret', 'blank-secret']

    client.close()

    assert clients[0].close_calls == 1
    assert credentials[0].close_calls == 1


def test_context_manager_opens_once_and_close_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials, clients = _install_sdk(monkeypatch, values={'token': 'secret'})

    with KeyVaultClient(settings=_settings()) as client:
        client.open()
        assert client.get_secret('token') == 'secret'

    client.close()

    assert len(credentials) == 1
    assert len(clients) == 1
    assert clients[0].close_calls == 1
    assert credentials[0].close_calls == 1
    with pytest.raises(KeyVaultClosedError):
        client.open()
    with pytest.raises(KeyVaultClosedError):
        client.get_secret('token')


def test_empty_secret_value_is_rejected_but_whitespace_is_valid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_sdk(monkeypatch, values={'empty': '', 'spaces': '   '})
    client = KeyVaultClient(settings=_settings())

    with pytest.raises(KeyVaultSecretValueError):
        client.get_secret('empty')
    assert client.get_secret('spaces') == '   '


def test_secret_name_is_validated_exactly_without_stripping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_sdk(monkeypatch, values={'token': 'secret'})
    client = KeyVaultClient(settings=_settings())

    with pytest.raises(KeyVaultConfigurationError):
        client.get_secret(' token')
    with pytest.raises(KeyVaultConfigurationError):
        client.get_secret('token ')
    with pytest.raises(KeyVaultConfigurationError):
        client.get_secret('invalid_name')


def test_specific_sdk_failures_are_mapped_to_sanitized_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases = [
        (
            ResourceNotFoundError('private-sdk-details'),
            KeyVaultSecretNotFoundError,
        ),
        (
            ClientAuthenticationError('private-sdk-details'),
            KeyVaultAuthenticationError,
        ),
        (_http_error(401), KeyVaultAuthenticationError),
        (_http_error(403), KeyVaultAuthorizationError),
        (_http_error(500), KeyVaultOperationError),
        (RuntimeError('private-sdk-details'), KeyVaultOperationError),
    ]

    for sdk_error, expected_error in cases:
        _install_sdk(monkeypatch, get_error=sdk_error)
        client = KeyVaultClient(settings=_settings())
        with pytest.raises(expected_error) as captured:
            client.get_secret('token')
        assert 'private-sdk-details' not in str(captured.value)


def test_open_failure_is_sanitized_and_closes_created_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials: list[FakeCredential] = []

    def credential_factory() -> FakeCredential:
        credential = FakeCredential()
        credentials.append(credential)
        return credential

    def client_factory(*, vault_url: str, credential: FakeCredential) -> FakeSecretClient:
        raise RuntimeError('private-client-construction-details')

    monkeypatch.setattr(client_module, 'DefaultAzureCredential', credential_factory)
    monkeypatch.setattr(client_module, 'SecretClient', client_factory)
    client = KeyVaultClient(settings=_settings())

    with pytest.raises(KeyVaultOperationError) as captured:
        client.open()

    assert 'private-client-construction-details' not in str(captured.value)
    assert len(credentials) == 1
    assert credentials[0].close_calls == 1


def test_close_attempts_both_resources_even_when_both_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials, clients = _install_sdk(
        monkeypatch,
        values={'token': 'secret'},
        credential_close_error=RuntimeError('private-credential-close'),
        client_close_error=RuntimeError('private-client-close'),
    )
    client = KeyVaultClient(settings=_settings())
    assert client.get_secret('token') == 'secret'

    with pytest.raises(KeyVaultOperationError) as captured:
        client.close()

    assert 'private-client-close' not in str(captured.value)
    assert 'private-credential-close' not in str(captured.value)
    assert clients[0].close_calls == 1
    assert credentials[0].close_calls == 1
    client.close()


def test_context_manager_preserves_original_error_when_close_also_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_sdk(
        monkeypatch,
        values={'token': 'secret'},
        client_close_error=RuntimeError('private-close-details'),
    )

    with pytest.raises(RuntimeError, match='original-operation-error'):
        with KeyVaultClient(settings=_settings()) as client:
            assert client.get_secret('token') == 'secret'
            raise RuntimeError('original-operation-error')


def test_context_manager_reports_close_error_when_body_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_sdk(
        monkeypatch,
        values={'token': 'secret'},
        client_close_error=RuntimeError('private-close-details'),
    )

    with pytest.raises(KeyVaultOperationError):
        with KeyVaultClient(settings=_settings()) as client:
            assert client.get_secret('token') == 'secret'


def test_constructor_exposes_no_credential_or_sdk_client_injection() -> None:
    with pytest.raises(TypeError):
        KeyVaultClient(settings=_settings(), credential=object())  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        KeyVaultClient(settings=_settings(), secret_client=object())  # type: ignore[call-arg]


def test_secret_name_accepts_azure_max_length_and_rejects_longer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    max_name = 'A' * 127
    _install_sdk(monkeypatch, values={max_name: 'secret'})
    client = KeyVaultClient(settings=_settings())

    assert client.get_secret(max_name) == 'secret'
    with pytest.raises(KeyVaultConfigurationError):
        client.get_secret('A' * 128)


def test_constructor_rejects_invalid_settings() -> None:
    with pytest.raises(KeyVaultConfigurationError):
        KeyVaultClient(settings=object())  # type: ignore[arg-type]


def test_open_authentication_failure_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def credential_factory() -> FakeCredential:
        raise ClientAuthenticationError('private-credential-details')

    monkeypatch.setattr(client_module, 'DefaultAzureCredential', credential_factory)
    client = KeyVaultClient(settings=_settings())

    with pytest.raises(KeyVaultAuthenticationError) as captured:
        client.open()

    assert 'private-credential-details' not in str(captured.value)
