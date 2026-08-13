# Espejo pedagógico: conserva exactamente el código productivo y agrega sólo comentarios.
# La composición entrega settings ya resueltos; este módulo no interpreta variables de entorno.
"""Aprovisionamiento explícito e idempotente de base y contenedores Cosmos."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from atlanticus.connectivity.cosmos.client import CosmosClient
from atlanticus.connectivity.cosmos.errors import (
    CosmosContainerDefinitionMismatchError,
    CosmosContainerNotFoundError,
    CosmosDatabaseNotFoundError,
    CosmosError,
    CosmosProvisioningError,
)
from atlanticus.connectivity.cosmos.models import CosmosContainerSpec
from atlanticus.observability import ErrorInfo, ResultSummary, runtime_guard

_COMPONENT = 'atlanticus.connectivity.cosmos'


# Helper interno _safe_parameters: valida o adapta datos antes de tocar el SDK.
def _safe_parameters(_: tuple[Any, ...], values: Mapping[str, Any]) -> Mapping[str, Any]:
    safe_names = ('container_name',)
    return {name: values[name] for name in safe_names if values.get(name) is not None}


# Helper interno _safe_error: valida o adapta datos antes de tocar el SDK.
def _safe_error(error: BaseException) -> ErrorInfo:
    message = (
        str(error) if isinstance(error, CosmosError | TypeError) else 'Cosmos provisioning failed'
    )
    return ErrorInfo(error_type=type(error).__name__, message=message)


# Helper interno _boolean_result: valida o adapta datos antes de tocar el SDK.
def _boolean_result(value: Any) -> ResultSummary:
    if isinstance(value, bool):
        return ResultSummary(attributes={'created': value})
    return ResultSummary()


# Helper interno _created_result: valida o adapta datos antes de tocar el SDK.
def _created_result(value: Any) -> ResultSummary:
    if isinstance(value, tuple):
        return ResultSummary(metrics={'created_count': len(value)})
    return ResultSummary()


# Contrato/clase CosmosProvisioner: agrupa una responsabilidad concreta sin acoplarla a ADA.
class CosmosProvisioner:
    """Crea y valida recursos sólo cuando la composición lo invoca."""

    # Helper interno __init__: valida o adapta datos antes de tocar el SDK.
    def __init__(self, *, client: CosmosClient) -> None:
        if not isinstance(client, CosmosClient):
            raise CosmosProvisioningError('client must be CosmosClient')
        self.client = client

    @runtime_guard(
        operation='cosmos.database.ensure',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        result_mapper=_boolean_result,
        error_mapper=_safe_error,
    )
    # Operación ensure_database: expone una frontera explícita y sanitizada del conector.
    def ensure_database(self) -> bool:
        """Crea la base configurada cuando falta y retorna si fue creada."""

        sdk_client = self.client._get_client()
        database = self.client._get_database()
        try:
            database.read()
            return False
        except Exception as error:
            if _status_code(error) != 404:
                self.client._raise_sdk_error(
                    error,
                    not_found_error=CosmosDatabaseNotFoundError,
                    not_found_message='Cosmos database was not found',
                    operation_message='Could not inspect Cosmos database',
                )
        try:
            created_database = sdk_client.create_database(self.client.settings.database_name)
        except Exception as error:
            if _status_code(error) == 409:
                self.client._database = sdk_client.get_database_client(
                    self.client.settings.database_name
                )
                return False
            self.client._raise_sdk_error(
                error,
                not_found_error=CosmosDatabaseNotFoundError,
                not_found_message='Cosmos database was not found',
                operation_message='Could not create Cosmos database',
            )
        self.client._database = created_database
        self.client._containers.clear()
        return True

    @runtime_guard(
        operation='cosmos.containers.ensure',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        result_mapper=_created_result,
        error_mapper=_safe_error,
    )
    # Operación ensure_containers: expone una frontera explícita y sanitizada del conector.
    def ensure_containers(
        self,
        specs: Sequence[CosmosContainerSpec],
    ) -> tuple[str, ...]:
        """Crea contenedores ausentes y valida los que ya existen."""

        normalized_specs = _normalize_specs(specs)
        database = self._read_existing_database()
        created: list[str] = []
        for spec in normalized_specs:
            container = database.get_container_client(spec.name)
            try:
                properties = container.read()
            except Exception as error:
                if _status_code(error) != 404:
                    self.client._raise_sdk_error(
                        error,
                        not_found_error=CosmosContainerNotFoundError,
                        not_found_message='Cosmos container was not found',
                        operation_message='Could not inspect Cosmos container',
                    )
                container, was_created = self._create_container(database=database, spec=spec)
                if was_created:
                    created.append(spec.name)
                    self.client._containers[spec.name] = container
                    continue
                properties = container.read()
            _validate_container_properties(spec=spec, properties=properties)
            self.client._containers[spec.name] = container
        return tuple(created)

    @runtime_guard(
        operation='cosmos.containers.validate',
        component=_COMPONENT,
        parameter_mapper=_safe_parameters,
        error_mapper=_safe_error,
        emit_started=False,
    )
    # Operación validate_containers: expone una frontera explícita y sanitizada del conector.
    def validate_containers(self, specs: Sequence[CosmosContainerSpec]) -> None:
        """Comprueba existencia, partición y TTL sin crear ni modificar recursos."""

        normalized_specs = _normalize_specs(specs)
        database = self._read_existing_database()
        for spec in normalized_specs:
            container = database.get_container_client(spec.name)
            try:
                properties = container.read()
            except Exception as error:
                self.client._raise_sdk_error(
                    error,
                    not_found_error=CosmosContainerNotFoundError,
                    not_found_message='Cosmos container was not found',
                    operation_message='Could not validate Cosmos container',
                )
            _validate_container_properties(spec=spec, properties=properties)
            self.client._containers[spec.name] = container

    # Helper interno _read_existing_database: valida o adapta datos antes de tocar el SDK.
    def _read_existing_database(self) -> Any:
        database = self.client._get_database()
        try:
            database.read()
        except Exception as error:
            self.client._raise_sdk_error(
                error,
                not_found_error=CosmosDatabaseNotFoundError,
                not_found_message='Cosmos database was not found',
                operation_message='Could not read Cosmos database',
            )
        return database

    # Helper interno _create_container: valida o adapta datos antes de tocar el SDK.
    def _create_container(
        self,
        *,
        database: Any,
        spec: CosmosContainerSpec,
    ) -> tuple[Any, bool]:
        sdk = self.client._get_sdk()
        try:
            container = database.create_container(
                id=spec.name,
                partition_key=sdk.PartitionKey(path=spec.partition_key_path),
                default_ttl=spec.default_ttl_seconds,
            )
            return container, True
        except Exception as error:
            if _status_code(error) == 409:
                return database.get_container_client(spec.name), False
            self.client._raise_sdk_error(
                error,
                not_found_error=CosmosContainerNotFoundError,
                not_found_message='Cosmos container was not found',
                operation_message='Could not create Cosmos container',
            )
        raise CosmosProvisioningError('Could not create Cosmos container')


# Helper interno _normalize_specs: valida o adapta datos antes de tocar el SDK.
def _normalize_specs(
    specs: Sequence[CosmosContainerSpec],
) -> tuple[CosmosContainerSpec, ...]:
    if isinstance(specs, str | bytes | bytearray | Mapping):
        raise CosmosProvisioningError('container specs must be a sequence')
    try:
        normalized = tuple(specs)
    except TypeError:
        raise CosmosProvisioningError('container specs must be a sequence') from None
    if not normalized:
        raise CosmosProvisioningError('container specs must not be empty')
    if any(not isinstance(spec, CosmosContainerSpec) for spec in normalized):
        raise CosmosProvisioningError('container specs must contain CosmosContainerSpec values')
    names = [spec.name for spec in normalized]
    if len(names) != len(set(names)):
        raise CosmosProvisioningError('container specs must have unique names')
    return normalized


# Helper interno _validate_container_properties: valida o adapta datos antes de tocar el SDK.
def _validate_container_properties(
    *,
    spec: CosmosContainerSpec,
    properties: Mapping[str, Any],
) -> None:
    actual_paths = _partition_paths(properties)
    expected_paths = (spec.partition_key_path,)
    if actual_paths != expected_paths:
        raise CosmosContainerDefinitionMismatchError(
            container_name=spec.name,
            property_name='partition_key_path',
            expected=expected_paths,
            actual=actual_paths,
        )
    actual_ttl = properties.get('defaultTtl')
    if actual_ttl != spec.default_ttl_seconds:
        raise CosmosContainerDefinitionMismatchError(
            container_name=spec.name,
            property_name='default_ttl_seconds',
            expected=spec.default_ttl_seconds,
            actual=actual_ttl,
        )


# Helper interno _partition_paths: valida o adapta datos antes de tocar el SDK.
def _partition_paths(properties: Mapping[str, Any]) -> tuple[str, ...]:
    partition_key = properties.get('partitionKey')
    if not isinstance(partition_key, Mapping):
        raise CosmosProvisioningError('Cosmos container has no partitionKey properties')
    paths = partition_key.get('paths')
    if not isinstance(paths, list | tuple) or not paths:
        raise CosmosProvisioningError('Cosmos container has no partition key paths')
    return tuple(str(path) for path in paths)


# Helper interno _status_code: valida o adapta datos antes de tocar el SDK.
def _status_code(error: BaseException) -> int | None:
    value = getattr(error, 'status_code', None)
    return value if isinstance(value, int) else None
