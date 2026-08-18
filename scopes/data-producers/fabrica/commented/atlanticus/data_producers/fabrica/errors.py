# Espejo comentado del Data Producer Fábrica. La lógica ejecutable es idéntica al source productivo; este archivo existe para revisión en español.
class FabricaDataProducerError(RuntimeError):
    pass


class FabricaContractError(FabricaDataProducerError):
    pass


class FabricaSourceError(FabricaDataProducerError):
    pass


class FabricaSchemaError(FabricaDataProducerError):
    pass
