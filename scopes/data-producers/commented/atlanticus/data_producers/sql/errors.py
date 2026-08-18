# Agrupa errores propios de la implementación SQL del Data Producer.
class SqlDataProducerError(RuntimeError):
    pass


class SqlDataProducerReadError(SqlDataProducerError):
    pass


class SqlDataProducerSchemaError(SqlDataProducerError):
    pass


class SqlDataProducerMaterializationError(SqlDataProducerError):
    pass
