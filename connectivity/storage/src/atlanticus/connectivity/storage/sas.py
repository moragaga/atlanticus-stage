"""Lectura efímera de blobs referenciados mediante SAS dinámico."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any, BinaryIO
from urllib.parse import urlsplit, urlunsplit

from atlanticus.connectivity.storage.errors import (
    StorageAuthenticationError,
    StorageAuthorizationError,
    StorageBlobNotFoundError,
    StorageConfigurationError,
    StorageConnectionError,
    StorageError,
    StorageOperationError,
)
from atlanticus.connectivity.storage.settings import (
    DEFAULT_STORAGE_CONNECTION_TIMEOUT_SECONDS,
    DEFAULT_STORAGE_READ_TIMEOUT_SECONDS,
    _require_positive_integer,
)
from atlanticus.observability import ErrorInfo, ResultSummary, runtime_guard

_COMPONENT = 'atlanticus.connectivity.storage'


@dataclass(frozen=True, slots=True)
class _SasSdk:
    BlobClient: Any
    HttpResponseError: type[BaseException]
    ServiceRequestError: type[BaseException]
    ServiceResponseError: type[BaseException]


def _load_sdk() -> _SasSdk:
    blob = import_module('azure.storage.blob')
    exceptions = import_module('azure.core.exceptions')
    return _SasSdk(
        BlobClient=blob.BlobClient,
        HttpResponseError=exceptions.HttpResponseError,
        ServiceRequestError=exceptions.ServiceRequestError,
        ServiceResponseError=exceptions.ServiceResponseError,
    )


def _safe_parameters(_: tuple[Any, ...], __: Mapping[str, Any]) -> Mapping[str, Any]:
    return {'credential_scope': 'dynamic_sas'}


def _safe_error(error: BaseException) -> ErrorInfo:
    message = (
        str(error)
        if isinstance(error, StorageError | TypeError)
        else 'Storage SAS operation failed'
    )
    return ErrorInfo(error_type=type(error).__name__, message=message)


def _bytes_result(value: Any) -> ResultSummary:
    if isinstance(value, bytes):
        return ResultSummary(metrics={'byte_count': len(value)})
    return ResultSummary()


def _count_result(value: Any) -> ResultSummary:
    if isinstance(value, int):
        return ResultSummary(metrics={'byte_count': value})
    return ResultSummary()


@dataclass(frozen=True, slots=True)
class StorageSasReference:
    """Referencia firmada a un único blob sin exponer el SAS en ``repr``."""

    url: str = field(repr=False)
    sas_token: str | None = field(default=None, repr=False)
    allow_insecure_http: bool = False

    def __post_init__(self) -> None:
        clean_url, token = _normalize_reference(
            url=self.url,
            sas_token=self.sas_token,
            allow_insecure_http=self.allow_insecure_http,
        )
        object.__setattr__(self, 'url', clean_url)
        object.__setattr__(self, 'sas_token', token)

    @classmethod
    def from_values(
        cls,
        *,
        sas_url: str,
        sas_token: str | None = None,
        blob_name: str | None = None,
        allow_insecure_http: bool = False,
    ) -> StorageSasReference:
        url = sas_url if blob_name is None else _append_blob_name(sas_url, blob_name)
        return cls(
            url=url,
            sas_token=sas_token,
            allow_insecure_http=allow_insecure_http,
        )

    def _signed_url(self) -> str:
        parsed = urlsplit(self.url)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, self.sas_token or '', ''))


class StorageSasReader:
    """Lee referencias SAS independientes sin conservar clientes ni credenciales globales."""

    def __init__(
        self,
        *,
        connection_timeout_seconds: int = DEFAULT_STORAGE_CONNECTION_TIMEOUT_SECONDS,
        read_timeout_seconds: int = DEFAULT_STORAGE_READ_TIMEOUT_SECONDS,
    ) -> None:
        self.connection_timeout_seconds = _require_positive_integer(
            connection_timeout_seconds,
            'connection_timeout_seconds',
        )
        self.read_timeout_seconds = _require_positive_integer(
            read_timeout_seconds,
            'read_timeout_seconds',
        )
        self._sdk: _SasSdk | None = None

    @runtime_guard(
        operation='storage.sas.download',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        result_mapper=_bytes_result,
        error_mapper=_safe_error,
    )
    def download(self, *, reference: StorageSasReference) -> bytes:
        client = self._build_client(reference)
        try:
            return bytes(client.download_blob().readall())
        except Exception as error:
            raise self._map_error(error) from None
        finally:
            _close(client)

    @runtime_guard(
        operation='storage.sas.download_to',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        result_mapper=_count_result,
        error_mapper=_safe_error,
    )
    def download_to(self, *, reference: StorageSasReference, target: BinaryIO) -> int:
        if not callable(getattr(target, 'write', None)):
            raise TypeError('target must be a writable binary stream')
        client = self._build_client(reference)
        try:
            return int(client.download_blob().readinto(target))
        except Exception as error:
            raise self._map_error(error) from None
        finally:
            _close(client)

    def _build_client(self, reference: StorageSasReference) -> Any:
        if not isinstance(reference, StorageSasReference):
            raise TypeError('reference must be StorageSasReference')
        sdk = self._get_sdk()
        try:
            return sdk.BlobClient.from_blob_url(
                blob_url=reference._signed_url(),
                connection_timeout=self.connection_timeout_seconds,
                read_timeout=self.read_timeout_seconds,
                logging_enable=False,
            )
        except Exception as error:
            raise self._map_error(error) from None

    def _get_sdk(self) -> _SasSdk:
        if self._sdk is None:
            self._sdk = _load_sdk()
        return self._sdk

    def _map_error(self, error: BaseException) -> StorageError:
        sdk = self._get_sdk()
        if isinstance(error, sdk.ServiceRequestError | sdk.ServiceResponseError):
            return StorageConnectionError('Storage connection failed')
        if isinstance(error, sdk.HttpResponseError):
            status = getattr(error, 'status_code', None)
            if status == 401:
                return StorageAuthenticationError('Storage authentication failed')
            if status == 403:
                return StorageAuthorizationError('Storage authorization failed')
            if status == 404:
                return StorageBlobNotFoundError('Storage blob not found')
        return StorageOperationError('Storage operation failed')


def _normalize_reference(
    *,
    url: Any,
    sas_token: Any,
    allow_insecure_http: Any,
) -> tuple[str, str]:
    if not isinstance(allow_insecure_http, bool):
        raise StorageConfigurationError('allow_insecure_http must be a boolean')
    if not isinstance(url, str) or not url.strip():
        raise StorageConfigurationError('sas_url is required')
    normalized_url = url.strip()
    try:
        parsed = urlsplit(normalized_url)
        port = parsed.port
    except ValueError:
        raise StorageConfigurationError('sas_url must be a valid HTTP or HTTPS URL') from None
    scheme = parsed.scheme.lower()
    if scheme not in {'http', 'https'} or not parsed.netloc or parsed.hostname is None:
        raise StorageConfigurationError('sas_url must be an absolute HTTP or HTTPS URL')
    if port is not None and not 1 <= port <= 65535:
        raise StorageConfigurationError('sas_url must contain a valid port')
    if parsed.username is not None or parsed.password is not None:
        raise StorageConfigurationError('sas_url must not contain user credentials')
    if parsed.fragment:
        raise StorageConfigurationError('sas_url must not contain a fragment')
    if scheme == 'http' and not allow_insecure_http:
        raise StorageConfigurationError('HTTP sas_url requires allow_insecure_http=True')

    query_token = parsed.query.strip().lstrip('?')
    if sas_token is None:
        explicit_token = ''
    elif isinstance(sas_token, str):
        explicit_token = sas_token.strip().lstrip('?')
    else:
        raise StorageConfigurationError('sas_token must be text or None')
    if query_token and explicit_token and query_token != explicit_token:
        raise StorageConfigurationError('sas_url query and sas_token contain different values')
    token = explicit_token or query_token
    if not token:
        raise StorageConfigurationError('sas_token is required')
    if any(character in token for character in '\x00\r\n'):
        raise StorageConfigurationError('sas_token must not contain control characters')

    clean_url = urlunsplit((scheme, parsed.netloc, parsed.path.rstrip('/'), '', ''))
    return clean_url, token


def _append_blob_name(sas_url: str, blob_name: str) -> str:
    if not isinstance(sas_url, str) or not sas_url.strip():
        raise StorageConfigurationError('sas_url is required')
    if not isinstance(blob_name, str) or not blob_name.strip():
        raise StorageConfigurationError('blob_name is required')
    parsed = urlsplit(sas_url.strip())
    path = f'{parsed.path.rstrip("/")}/{blob_name.strip().lstrip("/")}'
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ''))


def _close(value: Any) -> None:
    close = getattr(value, 'close', None)
    if callable(close):
        close()
