# Espejo pedagógico de los contratos puros compartidos de datos operacionales.
from __future__ import annotations


class DataSourceNotRequestedError(KeyError):
    pass


class DataColumnNotRequestedError(KeyError):
    pass
