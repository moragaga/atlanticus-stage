# Espejo pedagógico de la jerarquía mínima de errores del dominio.
# AlarmContractError señala entradas que rompen contratos cerrados.
# AlarmLifecycleError señala una imposibilidad al materializar una transición válida.


class AlarmCoreError(Exception):
    pass


class AlarmContractError(AlarmCoreError):
    pass


class AlarmLifecycleError(AlarmCoreError):
    pass
