# Espejo comentado de la jerarquía de errores públicos.
# Los mensajes son deliberadamente sanitizados y no contienen secretos.
"""Errores públicos y seguros del conector SQL."""

from __future__ import annotations

from atlanticus.connectivity.sql.models import SqlTimeoutPhase


class SqlError(Exception):
    """Error base de ``atlanticus-sql``."""


class SqlConfigurationError(SqlError):
    """Indica una configuración ausente o inválida."""


class SqlQueryContractError(SqlError):
    """Indica una consulta o parámetros incompatibles con el contrato local."""


class SqlConnectionError(SqlError):
    """Representa un fallo al abrir o cerrar la conexión sin exponer su destino."""


class SqlQueryError(SqlError):
    """Representa un fallo de ejecución o lectura sin exponer SQL ni parámetros."""


class SqlTimeoutError(SqlError):
    """Representa un timeout de conexión o consulta sin reintento implícito."""

    def __init__(self, *, phase: SqlTimeoutPhase) -> None:
        self.phase = phase
        super().__init__(f'SQL {phase.value} timeout')


class SqlResultLimitError(SqlError):
    """Indica que ``query()`` debe reemplazarse por una lectura en lotes."""

    def __init__(self, *, max_rows: int) -> None:
        self.max_rows = max_rows
        super().__init__(f'SQL result exceeds the query limit of {max_rows} rows')
