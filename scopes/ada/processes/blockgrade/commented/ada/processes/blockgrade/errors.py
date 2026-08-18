# Conserva errores propios del proceso y reexporta errores técnicos comunes.
from atlanticus.data_producers.sql import (
    SqlDataProducerMaterializationError,
    SqlDataProducerReadError,
    SqlDataProducerSchemaError,
)


class BlockgradeProcessError(RuntimeError):
    pass


class BlockgradeProcessConfigurationError(BlockgradeProcessError):
    pass


class BlockgradeCatalogError(ValueError):
    pass


BlockgradeSqlReadError = SqlDataProducerReadError
BlockgradeSchemaError = SqlDataProducerSchemaError
BlockgradeMaterializationError = SqlDataProducerMaterializationError
