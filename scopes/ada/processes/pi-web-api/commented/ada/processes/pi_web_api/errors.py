# Errores propios del proceso. Mantenerlos separados evita filtrar excepciones internas de otras capas.

class PiWebApiProcessError(RuntimeError):
    pass


class PiWebApiProcessConfigurationError(PiWebApiProcessError):
    pass


class PiWebApiCatalogError(PiWebApiProcessError):
    pass


class PiWebApiWebIdRegistryError(PiWebApiProcessError):
    pass


class PiWebApiWatermarkError(PiWebApiProcessError):
    pass


class PiWebApiPlannerError(PiWebApiProcessError):
    pass
