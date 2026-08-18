# Este archivo define la superficie pública del producer Remanentes.
# Solo reexporta contratos y componentes estables para que ADA no dependa de detalles internos.

from atlanticus.data_producers.remanentes.composition import (
    RemanentesDataProducerComponents,
    RemanentesStorageConnection,
    build_remanentes_data_producer,
)
from atlanticus.data_producers.remanentes.contracts import (
    StockMetricDefinition,
    validate_stock_metrics,
)
from atlanticus.data_producers.remanentes.errors import (
    RemanentesContractError,
    RemanentesSourceError,
)
from atlanticus.data_producers.remanentes.job import RemanentesJob
from atlanticus.data_producers.remanentes.materialization import (
    RemanentesMaterializationResult,
    RemanentesMaterializer,
)
from atlanticus.data_producers.remanentes.models import (
    RemanentesRowsStreamDefinition,
    RemanentesSourceBlob,
    RemanentesStocksStreamDefinition,
    RemanentesStreamDefinition,
    parse_source_timestamp,
)
from atlanticus.data_producers.remanentes.producer_state import (
    RemanentesProducerManifest,
    RemanentesProducerState,
    RemanentesStreamState,
)
from atlanticus.data_producers.remanentes.source import RemanentesStorageSource
from atlanticus.data_producers.remanentes.transform import (
    RemanentesTransformResult,
    merge_snapshot,
    transform_snapshot,
)

__version__ = '0.1.0'

__all__ = [
    'RemanentesContractError',
    'RemanentesDataProducerComponents',
    'RemanentesJob',
    'RemanentesMaterializationResult',
    'RemanentesMaterializer',
    'RemanentesProducerManifest',
    'RemanentesProducerState',
    'RemanentesRowsStreamDefinition',
    'RemanentesSourceBlob',
    'RemanentesSourceError',
    'RemanentesStorageConnection',
    'RemanentesStorageSource',
    'RemanentesStocksStreamDefinition',
    'RemanentesStreamDefinition',
    'RemanentesStreamState',
    'RemanentesTransformResult',
    'StockMetricDefinition',
    '__version__',
    'build_remanentes_data_producer',
    'merge_snapshot',
    'parse_source_timestamp',
    'transform_snapshot',
    'validate_stock_metrics',
]
