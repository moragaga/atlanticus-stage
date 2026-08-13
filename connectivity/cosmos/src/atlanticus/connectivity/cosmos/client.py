"""Cliente síncrono y reutilizable para operaciones documentales de Cosmos."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from importlib import import_module
from itertools import islice
from types import TracebackType
from typing import Any

from atlanticus.connectivity.cosmos.errors import (
    CosmosAuthenticationError,
    CosmosAuthorizationError,
    CosmosClosedError,
    CosmosConfigurationError,
    CosmosConflictError,
    CosmosContainerNotFoundError,
    CosmosDatabaseNotFoundError,
    CosmosError,
    CosmosItemNotFoundError,
    CosmosOperationError,
    CosmosPreconditionFailedError,
    CosmosQueryContractError,
    CosmosResultLimitError,
    CosmosThrottledError,
)
from atlanticus.connectivity.cosmos.models import (
    CosmosPage,
    CosmosPatchOperation,
    CosmosQueryParameter,
    normalize_patch_operations,
)
from atlanticus.connectivity.cosmos.settings import CosmosSettings
from atlanticus.observability import ErrorInfo, ResultSummary, runtime_guard

_COMPONENT = 'atlanticus.connectivity.cosmos'
_PARTITION_KEY_UNSET = object()
_SYSTEM_METADATA_KEYS = frozenset({'_rid', '_self', '_etag', '_attachments', '_ts'})


@dataclass(frozen=True, slots=True)
class _CosmosSdk:
    CosmosClient: Any
    PartitionKey: Any
    MatchConditions: Any
    CosmosHttpResponseError: type[BaseException]


def _safe_parameters(_: tuple[Any, ...], values: Mapping[str, Any]) -> Mapping[str, Any]:
    safe_names = (
        'container_name',
        'item_id',
        'cross_partition',
        'max_items',
        'page_size',
        'include_metadata',
    )
    return {name: values[name] for name in safe_names if values.get(name) is not None}


def _safe_error(error: BaseException) -> ErrorInfo:
    message = (
        str(error) if isinstance(error, CosmosError | TypeError) else 'Cosmos operation failed'
    )
    return ErrorInfo(error_type=type(error).__name__, message=message)


def _document_result(value: Any) -> ResultSummary:
    return ResultSummary(attributes={'document': isinstance(value, Mapping)})


def _items_result(value: Any) -> ResultSummary:
    if isinstance(value, tuple):
        return ResultSummary(metrics={'item_count': len(value)})
    if isinstance(value, CosmosPage):
        return ResultSummary(metrics={'item_count': value.item_count})
    return ResultSummary()


class CosmosClient:
    """Expone operaciones documentales, consultas, páginas e iteradores neutrales."""

    def __init__(self, *, settings: CosmosSettings) -> None:
        if not isinstance(settings, CosmosSettings):
            raise CosmosConfigurationError('settings must be CosmosSettings')
        self.settings = settings
        self._sdk: _CosmosSdk | None = None
        self._client: Any | None = None
        self._database: Any | None = None
        self._containers: dict[str, Any] = {}
        self._closed = False

    def __enter__(self) -> CosmosClient:
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
        except CosmosOperationError:
            if exc_value is None:
                raise

    def open(self) -> None:
        """Crea una única instancia del SDK reutilizable por proceso y conexión."""

        if self._closed:
            raise CosmosClosedError('Cosmos client is closed')
        if self._client is not None:
            return
        sdk = self._get_sdk()
        try:
            self._client = sdk.CosmosClient(
                self.settings.endpoint,
                credential=self.settings.key,
                connection_timeout=self.settings.connection_timeout_seconds,
                timeout=self.settings.request_timeout_seconds,
                connection_mode='Gateway',
                retry_write=0,
            )
        except Exception as error:
            self._raise_sdk_error(
                error,
                not_found_error=CosmosOperationError,
                not_found_message='Could not create Cosmos client',
                operation_message='Could not create Cosmos client',
            )

    def close(self) -> None:
        """Cierra el SDK de forma idempotente y evita reutilizar la instancia."""

        sdk_client = self._client
        self._client = None
        self._database = None
        self._containers.clear()
        self._closed = True
        if sdk_client is None:
            return
        try:
            sdk_client.close()
        except Exception:
            raise CosmosOperationError('Could not close Cosmos client') from None

    @runtime_guard(
        operation='cosmos.health_check',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        error_mapper=_safe_error,
        emit_started=False,
    )
    def health_check(self) -> bool:
        """Valida credenciales y existencia de la base configurada."""

        try:
            self._get_database().read()
        except Exception as error:
            self._raise_sdk_error(
                error,
                not_found_error=CosmosDatabaseNotFoundError,
                not_found_message='Cosmos database was not found',
                operation_message='Could not read Cosmos database',
            )
        return True

    @runtime_guard(
        operation='cosmos.item.create',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        result_mapper=_document_result,
        error_mapper=_safe_error,
    )
    def create_item(
        self,
        *,
        container_name: str,
        item: Mapping[str, Any],
        include_metadata: bool = False,
    ) -> dict[str, Any]:
        """Crea un documento nuevo y falla ante un identificador existente."""

        document = _normalize_input_document(item, require_id=True)
        try:
            result = self._get_container(container_name).create_item(body=document)
        except Exception as error:
            self._raise_sdk_error(
                error,
                not_found_error=CosmosContainerNotFoundError,
                not_found_message='Cosmos container was not found',
                operation_message='Could not create Cosmos item',
            )
        return _normalize_output_document(result, include_metadata=include_metadata)

    @runtime_guard(
        operation='cosmos.item.read',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        result_mapper=_document_result,
        error_mapper=_safe_error,
        emit_started=False,
    )
    def read_item(
        self,
        *,
        container_name: str,
        item_id: str,
        partition_key: Any,
        include_metadata: bool = False,
    ) -> dict[str, Any]:
        """Lee estrictamente un documento o lanza ``CosmosItemNotFoundError``."""

        normalized_id = _require_identifier(item_id, 'item_id')
        try:
            result = self._get_container(container_name).read_item(
                item=normalized_id,
                partition_key=partition_key,
            )
        except Exception as error:
            self._raise_sdk_error(
                error,
                not_found_error=CosmosItemNotFoundError,
                not_found_message='Cosmos item was not found',
                operation_message='Could not read Cosmos item',
            )
        return _normalize_output_document(result, include_metadata=include_metadata)

    def find_item(
        self,
        *,
        container_name: str,
        item_id: str,
        partition_key: Any,
        include_metadata: bool = False,
    ) -> dict[str, Any] | None:
        """Retorna ``None`` exclusivamente cuando el documento no existe."""

        try:
            return self.read_item(
                container_name=container_name,
                item_id=item_id,
                partition_key=partition_key,
                include_metadata=include_metadata,
            )
        except CosmosItemNotFoundError:
            return None

    @runtime_guard(
        operation='cosmos.item.upsert',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        result_mapper=_document_result,
        error_mapper=_safe_error,
    )
    def upsert_item(
        self,
        *,
        container_name: str,
        item: Mapping[str, Any],
        include_metadata: bool = False,
    ) -> dict[str, Any]:
        """Inserta o reemplaza un documento mediante la semántica nativa de Cosmos."""

        document = _normalize_input_document(item, require_id=True)
        try:
            result = self._get_container(container_name).upsert_item(body=document)
        except Exception as error:
            self._raise_sdk_error(
                error,
                not_found_error=CosmosContainerNotFoundError,
                not_found_message='Cosmos container was not found',
                operation_message='Could not upsert Cosmos item',
            )
        return _normalize_output_document(result, include_metadata=include_metadata)

    @runtime_guard(
        operation='cosmos.item.patch',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        result_mapper=_document_result,
        error_mapper=_safe_error,
    )
    def patch_item(
        self,
        *,
        container_name: str,
        item_id: str,
        partition_key: Any,
        operations: Sequence[CosmosPatchOperation],
        if_match_etag: str | None = None,
        filter_predicate: str | None = None,
        include_metadata: bool = False,
    ) -> dict[str, Any]:
        """Aplica un patch neutral sin exponer objetos del SDK."""

        normalized_id = _require_identifier(item_id, 'item_id')
        normalized_operations = normalize_patch_operations(operations)
        normalized_filter = _optional_text(filter_predicate, 'filter_predicate')
        arguments: dict[str, Any] = self._etag_arguments(if_match_etag)
        if normalized_filter is not None:
            arguments['filter_predicate'] = normalized_filter
        try:
            result = self._get_container(container_name).patch_item(
                item=normalized_id,
                partition_key=partition_key,
                patch_operations=list(normalized_operations),
                **arguments,
            )
        except Exception as error:
            self._raise_sdk_error(
                error,
                not_found_error=CosmosItemNotFoundError,
                not_found_message='Cosmos item was not found',
                operation_message='Could not patch Cosmos item',
            )
        return _normalize_output_document(result, include_metadata=include_metadata)

    @runtime_guard(
        operation='cosmos.item.delete',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        error_mapper=_safe_error,
    )
    def delete_item(
        self,
        *,
        container_name: str,
        item_id: str,
        partition_key: Any,
        if_match_etag: str | None = None,
    ) -> None:
        """Elimina un documento y rechaza un ETag obsoleto cuando fue entregado."""

        normalized_id = _require_identifier(item_id, 'item_id')
        try:
            self._get_container(container_name).delete_item(
                item=normalized_id,
                partition_key=partition_key,
                **self._etag_arguments(if_match_etag),
            )
        except Exception as error:
            self._raise_sdk_error(
                error,
                not_found_error=CosmosItemNotFoundError,
                not_found_message='Cosmos item was not found',
                operation_message='Could not delete Cosmos item',
            )

    @runtime_guard(
        operation='cosmos.query.items',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        result_mapper=_items_result,
        error_mapper=_safe_error,
    )
    def query_items(
        self,
        *,
        container_name: str,
        query: str,
        parameters: Sequence[CosmosQueryParameter | Mapping[str, Any]] | None = None,
        partition_key: Any = _PARTITION_KEY_UNSET,
        cross_partition: bool = False,
        max_items: int | None = None,
        page_size: int | None = None,
        include_metadata: bool = False,
    ) -> tuple[dict[str, Any], ...]:
        """Materializa documentos y falla si supera el máximo autorizado."""

        limit = (
            self.settings.max_query_items
            if max_items is None
            else _positive_integer(max_items, 'max_items')
        )
        iterator = self.iter_items(
            container_name=container_name,
            query=query,
            parameters=parameters,
            partition_key=partition_key,
            cross_partition=cross_partition,
            page_size=page_size,
            include_metadata=include_metadata,
        )
        values = tuple(islice(iterator, limit + 1))
        if len(values) > limit:
            raise CosmosResultLimitError(max_items=limit)
        return values

    @runtime_guard(
        operation='cosmos.query.values',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        result_mapper=_items_result,
        error_mapper=_safe_error,
    )
    def query_values(
        self,
        *,
        container_name: str,
        query: str,
        parameters: Sequence[CosmosQueryParameter | Mapping[str, Any]] | None = None,
        partition_key: Any = _PARTITION_KEY_UNSET,
        cross_partition: bool = False,
        max_items: int | None = None,
        page_size: int | None = None,
        include_metadata: bool = False,
    ) -> tuple[Any, ...]:
        """Materializa resultados ``SELECT VALUE`` sin asumir que son documentos."""

        limit = (
            self.settings.max_query_items
            if max_items is None
            else _positive_integer(max_items, 'max_items')
        )
        iterator = self.iter_values(
            container_name=container_name,
            query=query,
            parameters=parameters,
            partition_key=partition_key,
            cross_partition=cross_partition,
            page_size=page_size,
            include_metadata=include_metadata,
        )
        values = tuple(islice(iterator, limit + 1))
        if len(values) > limit:
            raise CosmosResultLimitError(max_items=limit)
        return values

    @runtime_guard(
        operation='cosmos.query.page',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        result_mapper=_items_result,
        error_mapper=_safe_error,
    )
    def query_page(
        self,
        *,
        container_name: str,
        query: str,
        parameters: Sequence[CosmosQueryParameter | Mapping[str, Any]] | None = None,
        partition_key: Any = _PARTITION_KEY_UNSET,
        cross_partition: bool = False,
        page_size: int | None = None,
        continuation_token: str | None = None,
        include_metadata: bool = False,
    ) -> CosmosPage[dict[str, Any]]:
        """Obtiene exactamente una página y conserva el token como valor opaco."""

        normalized = self._normalize_query_call(
            container_name=container_name,
            query=query,
            parameters=parameters,
            partition_key=partition_key,
            cross_partition=cross_partition,
            page_size=page_size,
        )
        token = _optional_text(continuation_token, 'continuation_token')
        try:
            paged = normalized.container.query_items(**normalized.arguments)
            page_iterator = paged.by_page(continuation_token=token)
            raw_page = next(page_iterator, ())
            items = tuple(
                _normalize_query_document(value, include_metadata=include_metadata)
                for value in raw_page
            )
            next_token = getattr(page_iterator, 'continuation_token', None) or None
        except Exception as error:
            self._raise_sdk_error(
                error,
                not_found_error=CosmosContainerNotFoundError,
                not_found_message='Cosmos container was not found',
                operation_message='Could not query Cosmos items',
            )
        return CosmosPage(items=items, continuation_token=next_token)

    def iter_items(
        self,
        *,
        container_name: str,
        query: str,
        parameters: Sequence[CosmosQueryParameter | Mapping[str, Any]] | None = None,
        partition_key: Any = _PARTITION_KEY_UNSET,
        cross_partition: bool = False,
        page_size: int | None = None,
        include_metadata: bool = False,
    ) -> Iterator[dict[str, Any]]:
        """Recorre consultas grandes sin un límite global de documentos."""

        normalized = self._normalize_query_call(
            container_name=container_name,
            query=query,
            parameters=parameters,
            partition_key=partition_key,
            cross_partition=cross_partition,
            page_size=page_size,
        )
        return self._iter_query(
            normalized=normalized,
            documents=True,
            include_metadata=include_metadata,
        )

    def iter_values(
        self,
        *,
        container_name: str,
        query: str,
        parameters: Sequence[CosmosQueryParameter | Mapping[str, Any]] | None = None,
        partition_key: Any = _PARTITION_KEY_UNSET,
        cross_partition: bool = False,
        page_size: int | None = None,
        include_metadata: bool = False,
    ) -> Iterator[Any]:
        """Recorre resultados escalares u objetos ``SELECT VALUE`` por páginas del SDK."""

        normalized = self._normalize_query_call(
            container_name=container_name,
            query=query,
            parameters=parameters,
            partition_key=partition_key,
            cross_partition=cross_partition,
            page_size=page_size,
        )
        return self._iter_query(
            normalized=normalized,
            documents=False,
            include_metadata=include_metadata,
        )

    def _normalize_query_call(
        self,
        *,
        container_name: str,
        query: str,
        parameters: Sequence[CosmosQueryParameter | Mapping[str, Any]] | None,
        partition_key: Any,
        cross_partition: bool,
        page_size: int | None,
    ) -> _NormalizedQuery:
        normalized_query = _require_query(query)
        normalized_parameters = _normalize_query_parameters(parameters)
        normalized_scope = _normalize_query_scope(
            partition_key=partition_key,
            cross_partition=cross_partition,
        )
        normalized_page_size = (
            self.settings.page_size
            if page_size is None
            else _positive_integer(page_size, 'page_size')
        )
        arguments: dict[str, Any] = {
            'query': normalized_query,
            'parameters': [parameter.as_sdk_value() for parameter in normalized_parameters],
            'max_item_count': normalized_page_size,
            'enable_cross_partition_query': normalized_scope.cross_partition,
        }
        if normalized_scope.partition_key is not _PARTITION_KEY_UNSET:
            arguments['partition_key'] = normalized_scope.partition_key
        return _NormalizedQuery(
            container=self._get_container(container_name),
            arguments=arguments,
        )

    def _iter_query(
        self,
        *,
        normalized: _NormalizedQuery,
        documents: bool,
        include_metadata: bool,
    ) -> Iterator[Any]:
        try:
            iterator = iter(normalized.container.query_items(**normalized.arguments))
            for value in iterator:
                if documents:
                    yield _normalize_query_document(
                        value,
                        include_metadata=include_metadata,
                    )
                else:
                    yield _normalize_query_value(
                        value,
                        include_metadata=include_metadata,
                    )
        except Exception as error:
            self._raise_sdk_error(
                error,
                not_found_error=CosmosContainerNotFoundError,
                not_found_message='Cosmos container was not found',
                operation_message='Could not query Cosmos items',
            )

    def _etag_arguments(self, value: str | None) -> dict[str, Any]:
        etag = _optional_text(value, 'if_match_etag')
        if etag is None:
            return {}
        return {
            'etag': etag,
            'match_condition': self._get_sdk().MatchConditions.IfNotModified,
        }

    def _get_sdk(self) -> _CosmosSdk:
        if self._sdk is None:
            self._sdk = _load_cosmos_sdk()
        return self._sdk

    def _get_client(self) -> Any:
        self.open()
        if self._client is None:
            raise CosmosOperationError('Cosmos client is not open')
        return self._client

    def _get_database(self) -> Any:
        if self._database is None:
            try:
                self._database = self._get_client().get_database_client(self.settings.database_name)
            except Exception as error:
                self._raise_sdk_error(
                    error,
                    not_found_error=CosmosDatabaseNotFoundError,
                    not_found_message='Cosmos database was not found',
                    operation_message='Could not get Cosmos database client',
                )
        return self._database

    def _get_container(self, container_name: str) -> Any:
        normalized_name = _require_resource_identifier(container_name, 'container_name')
        if normalized_name not in self._containers:
            try:
                self._containers[normalized_name] = self._get_database().get_container_client(
                    normalized_name
                )
            except Exception as error:
                self._raise_sdk_error(
                    error,
                    not_found_error=CosmosContainerNotFoundError,
                    not_found_message='Cosmos container was not found',
                    operation_message='Could not get Cosmos container client',
                )
        return self._containers[normalized_name]

    def _raise_sdk_error(
        self,
        error: BaseException,
        *,
        not_found_error: type[CosmosOperationError],
        not_found_message: str,
        operation_message: str,
    ) -> None:
        if isinstance(error, CosmosError):
            raise error
        status_code = getattr(error, 'status_code', None)
        if status_code == 401:
            raise CosmosAuthenticationError('Cosmos authentication failed') from None
        if status_code == 403:
            raise CosmosAuthorizationError('Cosmos authorization failed') from None
        if status_code == 404:
            raise not_found_error(not_found_message) from None
        if status_code == 409:
            raise CosmosConflictError('Cosmos resource already exists') from None
        if status_code == 412:
            raise CosmosPreconditionFailedError('Cosmos ETag precondition failed') from None
        if status_code == 429:
            raise CosmosThrottledError(
                'Cosmos request remained throttled after SDK retries'
            ) from None
        sdk = self._sdk
        if sdk is not None and isinstance(error, sdk.CosmosHttpResponseError):
            raise CosmosOperationError(operation_message) from None
        if isinstance(error, ImportError | OSError):
            raise CosmosOperationError('Cosmos SDK runtime is unavailable') from None
        raise CosmosOperationError(operation_message) from None


@dataclass(frozen=True, slots=True)
class _NormalizedQuery:
    container: Any
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _QueryScope:
    partition_key: Any
    cross_partition: bool


def _load_cosmos_sdk() -> _CosmosSdk:
    try:
        cosmos = import_module('azure.cosmos')
        cosmos_exceptions = import_module('azure.cosmos.exceptions')
        azure_core = import_module('azure.core')
    except ImportError, OSError:
        raise CosmosOperationError('Cosmos SDK runtime is unavailable') from None
    return _CosmosSdk(
        CosmosClient=cosmos.CosmosClient,
        PartitionKey=cosmos.PartitionKey,
        MatchConditions=azure_core.MatchConditions,
        CosmosHttpResponseError=cosmos_exceptions.CosmosHttpResponseError,
    )


def _normalize_input_document(
    value: Mapping[str, Any],
    *,
    require_id: bool,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CosmosConfigurationError('item must be a mapping')
    document = dict(value)
    if require_id:
        document['id'] = _require_identifier(document.get('id'), 'item.id')
    elif 'id' in document:
        document['id'] = _require_identifier(document['id'], 'item.id')
    return document


def _normalize_output_document(value: Any, *, include_metadata: bool) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CosmosOperationError('Cosmos item response was not a document')
    document = dict(value)
    if include_metadata:
        return document
    return {key: item for key, item in document.items() if key not in _SYSTEM_METADATA_KEYS}


def _normalize_query_document(value: Any, *, include_metadata: bool) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CosmosQueryContractError(
            'query_items requires document results; use query_values for SELECT VALUE'
        )
    return _normalize_output_document(value, include_metadata=include_metadata)


def _normalize_query_value(value: Any, *, include_metadata: bool) -> Any:
    if isinstance(value, Mapping):
        return _normalize_output_document(value, include_metadata=include_metadata)
    return value


def _require_identifier(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value or not value.strip():
        raise CosmosConfigurationError(f'{field_name} must be a non-empty string')
    if value != value.strip():
        raise CosmosConfigurationError(f'{field_name} must not contain surrounding whitespace')
    if '\x00' in value:
        raise CosmosConfigurationError(f'{field_name} must not contain null characters')
    return value


def _require_resource_identifier(value: Any, field_name: str) -> str:
    identifier = _require_identifier(value, field_name)
    if any(character in identifier for character in '/\\#?\t\r\n'):
        raise CosmosConfigurationError(f'{field_name} contains unsupported Cosmos characters')
    if len(identifier) > 255:
        raise CosmosConfigurationError(f'{field_name} must contain at most 255 characters')
    return identifier


def _require_query(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CosmosQueryContractError('query must be a non-empty string')
    if '\x00' in value:
        raise CosmosQueryContractError('query must not contain null characters')
    return value


def _normalize_query_parameters(
    values: Sequence[CosmosQueryParameter | Mapping[str, Any]] | None,
) -> tuple[CosmosQueryParameter, ...]:
    if values is None:
        return ()
    if isinstance(values, str | bytes | bytearray | Mapping):
        raise CosmosQueryContractError('parameters must be a sequence')
    try:
        raw_values = tuple(values)
    except TypeError:
        raise CosmosQueryContractError('parameters must be a sequence') from None
    normalized: list[CosmosQueryParameter] = []
    names: set[str] = set()
    for value in raw_values:
        if isinstance(value, CosmosQueryParameter):
            parameter = value
        elif isinstance(value, Mapping) and set(value) == {'name', 'value'}:
            parameter = CosmosQueryParameter(name=value['name'], value=value['value'])
        else:
            raise CosmosQueryContractError(
                'parameters must contain CosmosQueryParameter or name/value mappings'
            )
        if parameter.name in names:
            raise CosmosQueryContractError(f'duplicate Cosmos query parameter: {parameter.name}')
        names.add(parameter.name)
        normalized.append(parameter)
    return tuple(normalized)


def _normalize_query_scope(*, partition_key: Any, cross_partition: bool) -> _QueryScope:
    if not isinstance(cross_partition, bool):
        raise CosmosQueryContractError('cross_partition must be a boolean')
    has_partition = partition_key is not _PARTITION_KEY_UNSET
    if has_partition and cross_partition:
        raise CosmosQueryContractError(
            'partition_key and cross_partition=True are mutually exclusive'
        )
    if not has_partition and not cross_partition:
        raise CosmosQueryContractError('query requires partition_key or cross_partition=True')
    return _QueryScope(partition_key=partition_key, cross_partition=cross_partition)


def _positive_integer(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise CosmosConfigurationError(f'{field_name} must be a positive integer')
    return value


def _optional_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise CosmosConfigurationError(f'{field_name} must be a non-empty string or None')
    if '\x00' in value:
        raise CosmosConfigurationError(f'{field_name} must not contain null characters')
    return value
