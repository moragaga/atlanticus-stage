class FabricaDataProducerError(RuntimeError):
    pass


class FabricaContractError(FabricaDataProducerError):
    pass


class FabricaSourceError(FabricaDataProducerError):
    pass


class FabricaSchemaError(FabricaDataProducerError):
    pass
