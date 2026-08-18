# Conserva errores propios del proceso y reexporta errores técnicos comunes.
from atlanticus.data_producers.sql import (
    SqlDataProducerMaterializationError,
    SqlDataProducerReadError,
    SqlDataProducerSchemaError,
)


class DispatchProcessError(RuntimeError):
    pass


class DispatchProcessConfigurationError(DispatchProcessError):
    pass


class DispatchCatalogError(ValueError):
    pass


DispatchSqlReadError = SqlDataProducerReadError
DispatchSchemaError = SqlDataProducerSchemaError
DispatchMaterializationError = SqlDataProducerMaterializationError
