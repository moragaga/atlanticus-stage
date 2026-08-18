# Espejo comentado del proceso ADA Fábrica. La lógica ejecutable es idéntica al source productivo; este archivo existe para revisión en español.
class FabricaProcessError(RuntimeError):
    pass


class FabricaProcessConfigurationError(FabricaProcessError):
    pass
