"""Contratos neutrales para identificar y publicar datasets Atlanticus."""

from pkgutil import extend_path

from atlanticus.datasets.errors import (
    DatasetDefinitionError,
    DatasetError,
    DatasetTargetError,
    DatasetValidationError,
)
from atlanticus.datasets.layouts import DatasetLayout, FileSetLayout, SingleArtifactLayout
from atlanticus.datasets.models import (
    DatasetDefinition,
    DatasetKey,
    DatasetPartition,
    DatasetPartKey,
    DatasetTarget,
    MaterializationDefinition,
)
from atlanticus.datasets.results import (
    DatasetBatchResult,
    DatasetBatchStatus,
    DatasetPublicationFailure,
    DatasetPublicationResult,
    PublicationQuality,
    PublicationSkipReason,
    PublicationStatus,
)

__path__ = extend_path(__path__, __name__)

__version__ = '0.1.0'

__all__ = [
    'DatasetBatchResult',
    'DatasetBatchStatus',
    'DatasetDefinition',
    'DatasetDefinitionError',
    'DatasetError',
    'DatasetKey',
    'DatasetLayout',
    'DatasetPartKey',
    'DatasetPartition',
    'DatasetPublicationFailure',
    'DatasetPublicationResult',
    'DatasetTarget',
    'DatasetTargetError',
    'DatasetValidationError',
    'FileSetLayout',
    'MaterializationDefinition',
    'PublicationQuality',
    'PublicationSkipReason',
    'PublicationStatus',
    'SingleArtifactLayout',
    '__version__',
]
