from atlanticus.data_producers.sql import SqlDataProducerMaterializer


class DispatchMaterializer(SqlDataProducerMaterializer):
    def __init__(self, *, runtime, definitions) -> None:
        super().__init__(
            runtime=runtime,
            definitions=definitions,
            dataset_namespace=('dispatch',),
        )
