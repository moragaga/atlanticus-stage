# Espejo pedagógico de los contratos puros compartidos de datos operacionales.
from ada.data.core.contracts import (
    DataColumn,
    DataColumnType,
    DataPartition,
    DataRequirement,
    DataSource,
    DataSourceView,
    OperationalScope,
    ShiftScope,
    ShiftSelection,
    TimeWindow,
    TimeWindowUnit,
    normalize_utc_second,
)
from ada.data.core.errors import DataColumnNotRequestedError, DataSourceNotRequestedError
from ada.data.core.runtime import DataRuntimeContext, RuntimeFrameContext

__version__ = '0.1.0'

__all__ = [
    'DataColumn',
    'DataColumnNotRequestedError',
    'DataColumnType',
    'DataPartition',
    'DataRequirement',
    'DataRuntimeContext',
    'DataSource',
    'DataSourceNotRequestedError',
    'DataSourceView',
    'OperationalScope',
    'RuntimeFrameContext',
    'ShiftScope',
    'ShiftSelection',
    'TimeWindow',
    'TimeWindowUnit',
    '__version__',
    'normalize_utc_second',
]
