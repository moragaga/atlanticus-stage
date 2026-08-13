"""Cliente síncrono y genérico para Azure Blob Storage."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from importlib import import_module
from itertools import islice
from types import TracebackType
from typing import Any, BinaryIO

from atlanticus.connectivity.storage.errors import (
    StorageAuthenticationError,
    StorageAuthorizationError,
    StorageBlobNotFoundError,
    StorageClosedError,
    StorageConfigurationError,
    StorageConflictError,
    StorageConnectionError,
    StorageContainerNotFoundError,
    StorageError,
    StorageOperationError,
    StorageResultLimitError,
)
from atlanticus.connectivity.storage.models import StorageBlobProperties
from atlanticus.connectivity.storage.settings import (
    StorageConnectionStringCredential,
    StorageSasCredential,
    StorageSettings,
    _require_positive_integer,
)
from atlanticus.observability import ErrorInfo, ResultSummary, runtime_guard

_COMPONENT = 'atlanticus.connectivity.storage'


@dataclass(frozen=True, slots=True)
class _StorageSdk:
    BlobServiceClient: Any
    ContentSettings: Any
    HttpResponseError: type[BaseException]
    ServiceRequestError: type[BaseException]
    ServiceResponseError: type[BaseException]


def _load_sdk() -> _StorageSdk:
    blob = import_module('azure.storage.blob')
    exceptions = import_module('azure.core.exceptions')
    return _StorageSdk(
        BlobServiceClient=blob.BlobServiceClient,
        ContentSettings=blob.ContentSettings,
        HttpResponseError=exceptions.HttpResponseError,
        ServiceRequestError=exceptions.ServiceRequestError,
        ServiceResponseError=exceptions.ServiceResponseError,
    )


def _safe_parameters(_: tuple[Any, ...], values: Mapping[str, Any]) -> Mapping[str, Any]:
    safe: dict[str, Any] = {}
    for name in ('container_name', 'max_items', 'overwrite'):
        value = values.get(name)
        if value is not None:
            safe[name] = value
    metadata = values.get('metadata')
    if isinstance(metadata, Mapping):
        safe['metadata_count'] = len(metadata)
    return safe


def _safe_error(error: BaseException) -> ErrorInfo:
    message = (
        str(error) if isinstance(error, StorageError | TypeError) else 'Storage operation failed'
    )
    return ErrorInfo(error_type=type(error).__name__, message=message)


def _bytes_result(value: Any) -> ResultSummary:
    if isinstance(value, bytes):
        return ResultSummary(metrics={'byte_count': len(value)})
    return ResultSummary()


def _list_result(value: Any) -> ResultSummary:
    if isinstance(value, tuple):
        return ResultSummary(metrics={'item_count': len(value)})
    return ResultSummary()


def _properties_result(value: Any) -> ResultSummary:
    if isinstance(value, StorageBlobProperties):
        return ResultSummary(metrics={'byte_count': value.size})
    return ResultSummary()


class StorageClient:
    """Opera containers y blobs sin conocer nombres de conexión ni reglas de negocio."""

    def __init__(self, *, settings: StorageSettings) -> None:
        if not isinstance(settings, StorageSettings):
            raise StorageConfigurationError('settings must be StorageSettings')
        self.settings = settings
        self._client: Any | None = None
        self._sdk: _StorageSdk | None = None
        self._closed = False

    def __enter__(self) -> StorageClient:
        self.open()
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        try:
            self.close()
        except StorageConnectionError:
            if exception_type is None:
                raise

    def open(self) -> StorageClient:
        """Construye el cliente SDK de forma lazy e idempotente."""

        self._get_client()
        return self

    def close(self) -> None:
        """Cierra el cliente SDK una sola vez y marca esta instancia como finalizada."""

        if self._closed:
            return
        self._closed = True
        client, self._client = self._client, None
        if client is None:
            return
        try:
            client.close()
        except Exception:
            raise StorageConnectionError('Could not close Storage client') from None

    @runtime_guard(
        operation='storage.health_check',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        error_mapper=_safe_error,
    )
    def health_check(self, *, container_name: str) -> bool:
        """Comprueba acceso al container configurado por el consumidor."""

        container_name = _require_container_name(container_name)
        client = self._get_client()
        try:
            exists = client.get_container_client(container_name).exists()
        except Exception as error:
            raise self._map_error(error, resource='container') from None
        if not exists:
            raise StorageContainerNotFoundError('Storage container not found')
        return True

    @runtime_guard(
        operation='storage.exists',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        error_mapper=_safe_error,
    )
    def exists(self, *, container_name: str, blob_name: str) -> bool:
        """Indica si un blob existe sin descargar su contenido."""

        blob = self._blob_client(container_name=container_name, blob_name=blob_name)
        try:
            return bool(blob.exists())
        except Exception as error:
            raise self._map_error(error, resource='blob') from None

    @runtime_guard(
        operation='storage.download',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        result_mapper=_bytes_result,
        error_mapper=_safe_error,
    )
    def download(self, *, container_name: str, blob_name: str) -> bytes:
        """Descarga un blob completo como bytes para cargas acotadas."""

        blob = self._blob_client(container_name=container_name, blob_name=blob_name)
        try:
            return bytes(blob.download_blob().readall())
        except Exception as error:
            raise self._map_error(error, resource='blob') from None

    @runtime_guard(
        operation='storage.download_to',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        error_mapper=_safe_error,
    )
    def download_to(self, *, container_name: str, blob_name: str, target: BinaryIO) -> int:
        """Transfiere un blob hacia un stream binario sin materializarlo completo."""

        if not hasattr(target, 'write'):
            raise TypeError('target must be a writable binary stream')
        blob = self._blob_client(container_name=container_name, blob_name=blob_name)
        try:
            return int(blob.download_blob().readinto(target))
        except Exception as error:
            raise self._map_error(error, resource='blob') from None

    @runtime_guard(
        operation='storage.upload',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        error_mapper=_safe_error,
    )
    def upload(
        self,
        *,
        container_name: str,
        blob_name: str,
        data: bytes | bytearray | BinaryIO,
        overwrite: bool = True,
        metadata: Mapping[str, str] | None = None,
        content_type: str | None = None,
    ) -> None:
        """Sube bytes o un stream binario sin imponer formato de archivo."""

        if not isinstance(overwrite, bool):
            raise TypeError('overwrite must be a boolean')
        normalized_metadata = _normalize_metadata(metadata)
        normalized_content_type = _normalize_content_type(content_type)
        sdk = self._get_sdk()
        blob = self._blob_client(container_name=container_name, blob_name=blob_name)
        kwargs: dict[str, Any] = {
            'overwrite': overwrite,
            'metadata': normalized_metadata,
        }
        if normalized_content_type is not None:
            kwargs['content_settings'] = sdk.ContentSettings(content_type=normalized_content_type)
        try:
            blob.upload_blob(data, **kwargs)
        except Exception as error:
            raise self._map_error(error, resource='blob') from None

    @runtime_guard(
        operation='storage.delete',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        error_mapper=_safe_error,
    )
    def delete(self, *, container_name: str, blob_name: str) -> None:
        """Elimina un blob existente y falla explícitamente si no existe."""

        blob = self._blob_client(container_name=container_name, blob_name=blob_name)
        try:
            blob.delete_blob()
        except Exception as error:
            raise self._map_error(error, resource='blob') from None

    @runtime_guard(
        operation='storage.list_blobs',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        result_mapper=_list_result,
        error_mapper=_safe_error,
    )
    def list_blobs(
        self,
        *,
        container_name: str,
        prefix: str | None = None,
        max_items: int | None = None,
    ) -> tuple[StorageBlobProperties, ...]:
        """Lista blobs hasta un límite estricto sin cargar el container completo."""

        container_name = _require_container_name(container_name)
        normalized_prefix = _normalize_prefix(prefix)
        limit = (
            self.settings.max_list_items
            if max_items is None
            else _require_positive_integer(max_items, 'max_items')
        )
        client = self._get_client().get_container_client(container_name)
        try:
            items = tuple(islice(client.list_blobs(name_starts_with=normalized_prefix), limit + 1))
        except Exception as error:
            raise self._map_error(error, resource='container') from None
        if len(items) > limit:
            raise StorageResultLimitError(max_items=limit)
        return tuple(_to_properties(item, name=str(item.name)) for item in items)

    @runtime_guard(
        operation='storage.get_properties',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        result_mapper=_properties_result,
        error_mapper=_safe_error,
    )
    def get_properties(self, *, container_name: str, blob_name: str) -> StorageBlobProperties:
        """Obtiene propiedades y metadata sin descargar el cuerpo del blob."""

        normalized_blob_name = _require_blob_name(blob_name)
        blob = self._blob_client(container_name=container_name, blob_name=normalized_blob_name)
        try:
            properties = blob.get_blob_properties()
        except Exception as error:
            raise self._map_error(error, resource='blob') from None
        return _to_properties(properties, name=normalized_blob_name)

    def _blob_client(self, *, container_name: str, blob_name: str) -> Any:
        return self._get_client().get_blob_client(
            container=_require_container_name(container_name),
            blob=_require_blob_name(blob_name),
        )

    def _get_sdk(self) -> _StorageSdk:
        if self._sdk is None:
            self._sdk = _load_sdk()
        return self._sdk

    def _get_client(self) -> Any:
        if self._closed:
            raise StorageClosedError('Storage client is closed')
        if self._client is not None:
            return self._client
        sdk = self._get_sdk()
        common = {
            'connection_timeout': self.settings.connection_timeout_seconds,
            'read_timeout': self.settings.read_timeout_seconds,
            'logging_enable': False,
        }
        credential = self.settings.credential
        try:
            if isinstance(credential, StorageConnectionStringCredential):
                client = sdk.BlobServiceClient.from_connection_string(
                    credential.connection_string,
                    **common,
                )
            elif isinstance(credential, StorageSasCredential):
                client = sdk.BlobServiceClient(
                    account_url=credential.account_url,
                    credential=credential.sas_token,
                    **common,
                )
            else:
                raise StorageConfigurationError('Unsupported Storage credential')
        except StorageConfigurationError:
            raise
        except Exception as error:
            raise self._map_error(error, resource=None) from None
        self._client = client
        return client

    def _map_error(self, error: BaseException, *, resource: str | None) -> StorageError:
        sdk = self._get_sdk()
        if isinstance(error, sdk.ServiceRequestError | sdk.ServiceResponseError):
            return StorageConnectionError('Storage connection failed')
        if isinstance(error, sdk.HttpResponseError):
            status = getattr(error, 'status_code', None)
            if status == 401:
                return StorageAuthenticationError('Storage authentication failed')
            if status == 403:
                return StorageAuthorizationError('Storage authorization failed')
            if status == 404 and resource == 'container':
                return StorageContainerNotFoundError('Storage container not found')
            if status == 404 and resource == 'blob':
                return StorageBlobNotFoundError('Storage blob not found')
            if status in {409, 412}:
                return StorageConflictError('Storage operation conflict')
        return StorageOperationError('Storage operation failed')


def _require_container_name(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError('container_name must be text')
    if not value or value != value.strip():
        raise TypeError('container_name must be non-empty text without surrounding whitespace')
    if any(character in value for character in '\x00\r\n'):
        raise TypeError('container_name contains unsupported characters')
    return value


def _require_blob_name(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError('blob_name must be text')
    if not value:
        raise TypeError('blob_name must not be empty')
    if any(character in value for character in '\x00\r\n'):
        raise TypeError('blob_name contains unsupported characters')
    return value


def _normalize_prefix(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError('prefix must be text or None')
    if any(character in value for character in '\x00\r\n'):
        raise TypeError('prefix contains unsupported characters')
    return value


def _normalize_metadata(value: Mapping[str, str] | None) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise TypeError('metadata must be a mapping or None')
    normalized: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise TypeError('metadata keys and values must be text')
        normalized[key] = item
    return normalized


def _normalize_content_type(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or value != value.strip():
        raise TypeError('content_type must be non-empty text without surrounding whitespace')
    if any(character in value for character in '\x00\r\n'):
        raise TypeError('content_type contains unsupported characters')
    return value


def _to_properties(value: Any, *, name: str) -> StorageBlobProperties:
    content_settings = getattr(value, 'content_settings', None)
    return StorageBlobProperties(
        name=name,
        size=int(getattr(value, 'size', 0) or 0),
        etag=None if getattr(value, 'etag', None) is None else str(value.etag),
        last_modified=getattr(value, 'last_modified', None),
        content_type=(
            None
            if content_settings is None or getattr(content_settings, 'content_type', None) is None
            else str(content_settings.content_type)
        ),
        metadata=getattr(value, 'metadata', None) or {},
    )
