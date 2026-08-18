class SqlDataProducerError(RuntimeError):
    pass


class SqlDataProducerReadError(SqlDataProducerError):
    pass


class SqlDataProducerSchemaError(SqlDataProducerError):
    pass


class SqlDataProducerMaterializationError(SqlDataProducerError):
    pass
