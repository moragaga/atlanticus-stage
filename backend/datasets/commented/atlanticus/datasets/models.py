"""Identidad, definiciones y destinos lógicos de datasets."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from atlanticus.datasets.errors import DatasetDefinitionError, DatasetTargetError
from atlanticus.datasets.layouts import DatasetLayout, FileSetLayout, SingleArtifactLayout
from atlanticus.datasets.validation import (
    validate_dimension_name,
    validate_dimension_value,
    validate_identity_segment,
)


@dataclass(frozen=True, slots=True)
class DatasetKey:
    """Identidad extensible de un dataset que no conoce aplicaciones ni formatos."""

    namespace: tuple[str, ...]
    name: str

    def __post_init__(self) -> None:
        if isinstance(self.namespace, str | bytes):
            raise DatasetDefinitionError('dataset namespace must be an iterable of path segments')
        try:
            namespace = tuple(self.namespace)
        except TypeError as error:
            raise DatasetDefinitionError(
                'dataset namespace must be an iterable of path segments'
            ) from error
        object.__setattr__(self, 'namespace', namespace)
        if not namespace:
            raise DatasetDefinitionError('dataset namespace must not be empty')
        for index, segment in enumerate(namespace):
            validate_identity_segment(
                segment,
                field=f'dataset namespace segment {index}',
                error_type=DatasetDefinitionError,
            )
        validate_identity_segment(
            self.name,
            field='dataset name',
            error_type=DatasetDefinitionError,
        )

    @property
    def identifier(self) -> str:
        """Representación estable de la identidad, independiente de un volumen físico."""

        return '/'.join((*self.namespace, self.name))


@dataclass(frozen=True, slots=True)
class DatasetPartition:
    """Valores ordenados que identifican una partición histórica concreta."""

    values: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if isinstance(self.values, str | bytes | Mapping):
            raise DatasetTargetError('partition values must be an iterable of name-value pairs')
        try:
            source_values = tuple(self.values)
        except TypeError as error:
            raise DatasetTargetError(
                'partition values must be an iterable of name-value pairs'
            ) from error
        values: list[tuple[str, ...]] = []
        for item in source_values:
            if isinstance(item, str | bytes):
                raise DatasetTargetError('each partition value must contain a name and a value')
            try:
                values.append(tuple(item))
            except TypeError as error:
                raise DatasetTargetError(
                    'each partition value must contain a name and a value'
                ) from error
        normalized_values = tuple(values)
        object.__setattr__(self, 'values', normalized_values)
        if not normalized_values:
            raise DatasetTargetError('partition values must not be empty')
        dimensions: set[str] = set()
        for item in normalized_values:
            if len(item) != 2:
                raise DatasetTargetError('each partition value must contain a name and a value')
            dimension, value = item
            validate_dimension_name(
                dimension,
                field='partition dimension',
                error_type=DatasetTargetError,
            )
            validate_dimension_value(
                value,
                field=f'partition value for {dimension}',
                error_type=DatasetTargetError,
            )
            if dimension in dimensions:
                raise DatasetTargetError(f'duplicate partition dimension: {dimension}')
            dimensions.add(dimension)

    @classmethod
    def from_mapping(
        cls,
        values: Mapping[str, str],
        *,
        dimensions: tuple[str, ...] | None = None,
    ) -> DatasetPartition:
        """Crea una partición y, si se indica, aplica el orden canónico esperado."""

        if not isinstance(values, Mapping):
            raise DatasetTargetError('partition must be a mapping')
        if dimensions is None:
            return cls(values=tuple(values.items()))
        provided = set(values)
        expected = set(dimensions)
        if provided != expected:
            missing = sorted(expected - provided)
            additional = sorted(provided - expected)
            raise DatasetTargetError(
                f'partition dimensions do not match; missing={missing}, additional={additional}'
            )
        return cls(values=tuple((dimension, values[dimension]) for dimension in dimensions))

    @property
    def dimensions(self) -> tuple[str, ...]:
        """Nombres de dimensión conservados en orden canónico."""

        return tuple(dimension for dimension, _ in self.values)

    @property
    def logical_segments(self) -> tuple[str, ...]:
        """Pares ordenados que un adaptador podrá convertir en una ruta física."""

        return tuple(f'{dimension}={value}' for dimension, value in self.values)

    def as_dict(self) -> dict[str, str]:
        """Entrega una copia mutable para integraciones que requieren un mapping."""

        return dict(self.values)


@dataclass(frozen=True, slots=True)
class MaterializationDefinition:
    """Representación publicable de un dataset y sus dimensiones históricas."""

    name: str
    layout: DatasetLayout
    partition_dimensions: tuple[str, ...] = ()
    # None conserva la ruta derivada del nombre; una tupla vacía omite el segmento.
    route_segments: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        validate_identity_segment(
            self.name,
            field='materialization name',
            error_type=DatasetDefinitionError,
        )
        if not isinstance(self.layout, SingleArtifactLayout | FileSetLayout):
            raise DatasetDefinitionError('materialization layout is not supported')
        if isinstance(self.partition_dimensions, str | bytes):
            raise DatasetDefinitionError('partition_dimensions must be an iterable of names')
        try:
            dimensions = tuple(self.partition_dimensions)
        except TypeError as error:
            raise DatasetDefinitionError(
                'partition_dimensions must be an iterable of names'
            ) from error
        object.__setattr__(self, 'partition_dimensions', dimensions)
        for dimension in dimensions:
            validate_dimension_name(
                dimension,
                field='partition dimension',
                error_type=DatasetDefinitionError,
            )
        if len(set(dimensions)) != len(dimensions):
            raise DatasetDefinitionError('partition dimensions must not contain duplicates')
        if isinstance(self.layout, FileSetLayout) and self.layout.part_dimension in dimensions:
            raise DatasetDefinitionError(
                'part_dimension must be different from partition dimensions'
            )
        if self.route_segments is not None:
            route_segments = _normalize_route_segments(
                self.route_segments,
                field='materialization route segments',
                allow_empty=True,
            )
            object.__setattr__(self, 'route_segments', route_segments)

    # El adaptador consulta esta propiedad y no interpreta nombres por su cuenta.
    @property
    def resolved_route_segments(self) -> tuple[str, ...]:
        """Segmentos relativos usados por adaptadores físicos para esta materialización."""

        return (self.name,) if self.route_segments is None else self.route_segments


@dataclass(frozen=True, slots=True)
class DatasetTarget:
    """Unidad independiente que un adaptador deberá confirmar o conservar intacta."""

    dataset: DatasetKey
    materialization: str
    partition: DatasetPartition | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.dataset, DatasetKey):
            raise DatasetTargetError('dataset target must reference a DatasetKey')
        validate_identity_segment(
            self.materialization,
            field='materialization name',
            error_type=DatasetTargetError,
        )
        if self.partition is not None and not isinstance(self.partition, DatasetPartition):
            raise DatasetTargetError('partition must be a DatasetPartition or None')

    @property
    def logical_segments(self) -> tuple[str, ...]:
        """Identidad ordenada estable, independiente de la ruta física configurada."""

        partition_segments = () if self.partition is None else self.partition.logical_segments
        return (
            'datasets',
            *self.dataset.namespace,
            self.dataset.name,
            self.materialization,
            *partition_segments,
        )

    @property
    def identifier(self) -> str:
        """Representación estable para resultados, diagnósticos y manifiestos."""

        return '/'.join(self.logical_segments)


@dataclass(frozen=True, slots=True)
class DatasetPartKey:
    """Identidad lógica de una parte dentro de un `FileSetLayout`."""

    target: DatasetTarget
    dimension: str
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.target, DatasetTarget):
            raise DatasetTargetError('dataset part must reference a DatasetTarget')
        validate_dimension_name(
            self.dimension,
            field='part dimension',
            error_type=DatasetTargetError,
        )
        validate_dimension_value(
            self.value,
            field=f'part value for {self.dimension}',
            error_type=DatasetTargetError,
        )

    @property
    def identifier(self) -> str:
        """Identidad estable sin imponer el nombre físico de la parte."""

        return f'{self.target.identifier}#{self.dimension}={self.value}'


@dataclass(frozen=True, slots=True)
class DatasetDefinition:
    """Catálogo neutral de materializaciones disponibles para un dataset lógico."""

    key: DatasetKey
    materializations: tuple[MaterializationDefinition, ...]
    # Permite separar la identidad estable de la ubicación relativa de almacenamiento.
    route_segments: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.key, DatasetKey):
            raise DatasetDefinitionError('dataset definition must reference a DatasetKey')
        if isinstance(self.materializations, str | bytes):
            raise DatasetDefinitionError('materializations must be an iterable of definitions')
        try:
            materializations = tuple(self.materializations)
        except TypeError as error:
            raise DatasetDefinitionError(
                'materializations must be an iterable of definitions'
            ) from error
        object.__setattr__(self, 'materializations', materializations)
        if not materializations:
            raise DatasetDefinitionError('dataset must define at least one materialization')
        if not all(isinstance(item, MaterializationDefinition) for item in materializations):
            raise DatasetDefinitionError(
                'materializations must contain only MaterializationDefinition values'
            )
        names = tuple(item.name for item in materializations)
        if len(set(names)) != len(names):
            raise DatasetDefinitionError('materialization names must not contain duplicates')
        routes = tuple(item.resolved_route_segments for item in materializations)
        if len(set(routes)) != len(routes):
            raise DatasetDefinitionError('materialization routes must not contain duplicates')
        if self.route_segments is not None:
            route_segments = _normalize_route_segments(
                self.route_segments,
                field='dataset route segments',
                allow_empty=False,
            )
            object.__setattr__(self, 'route_segments', route_segments)

    @property
    def resolved_route_segments(self) -> tuple[str, ...]:
        """Ruta relativa base sin imponer raíz física ni formato de archivo."""

        if self.route_segments is not None:
            return self.route_segments
        return (*self.key.namespace, self.key.name)

    # La partición siempre se agrega al final para conservar su orden canónico.
    def resolve_route_segments(self, target: DatasetTarget) -> tuple[str, ...]:
        """Resuelve la ruta relativa validada de un target para un adaptador físico."""

        self.validate_target(target)
        materialization = self.get_materialization(target.materialization)
        partition_segments = () if target.partition is None else target.partition.logical_segments
        return (
            *self.resolved_route_segments,
            *materialization.resolved_route_segments,
            *partition_segments,
        )

    def get_materialization(self, name: str) -> MaterializationDefinition:
        """Resuelve una materialización declarada sin crear defaults implícitos."""

        validate_identity_segment(
            name,
            field='materialization name',
            error_type=DatasetTargetError,
        )
        for materialization in self.materializations:
            if materialization.name == name:
                return materialization
        raise DatasetTargetError(f'unknown materialization: {name}')

    def resolve_target(
        self,
        *,
        materialization: str,
        partition: DatasetPartition | Mapping[str, str] | None = None,
    ) -> DatasetTarget:
        """Construye un destino sólo cuando su partición coincide con la definición."""

        definition = self.get_materialization(materialization)
        resolved_partition = _resolve_partition(definition=definition, partition=partition)
        return DatasetTarget(
            dataset=self.key,
            materialization=definition.name,
            partition=resolved_partition,
        )

    def validate_target(self, target: DatasetTarget) -> DatasetTarget:
        """Confirma que un destino existente pertenece exactamente a esta definición."""

        if not isinstance(target, DatasetTarget):
            raise DatasetTargetError('target must be a DatasetTarget')
        if target.dataset != self.key:
            raise DatasetTargetError('target references a different dataset')
        definition = self.get_materialization(target.materialization)
        _resolve_partition(definition=definition, partition=target.partition)
        return target

    def resolve_part(self, *, target: DatasetTarget, value: str) -> DatasetPartKey:
        """Crea una parte únicamente para un destino declarado como file set."""

        self.validate_target(target)
        definition = self.get_materialization(target.materialization)
        if not isinstance(definition.layout, FileSetLayout):
            raise DatasetTargetError('single-artifact materializations do not accept parts')
        return DatasetPartKey(
            target=target,
            dimension=definition.layout.part_dimension,
            value=value,
        )


# Reutiliza las mismas reglas de seguridad que los segmentos de identidad.
def _normalize_route_segments(
    values: tuple[str, ...],
    *,
    field: str,
    allow_empty: bool,
) -> tuple[str, ...]:
    if isinstance(values, str | bytes):
        raise DatasetDefinitionError(f'{field} must be an iterable of path segments')
    try:
        segments = tuple(values)
    except TypeError as error:
        raise DatasetDefinitionError(
            f'{field} must be an iterable of path segments'
        ) from error
    if not segments and not allow_empty:
        raise DatasetDefinitionError(f'{field} must not be empty')
    for index, segment in enumerate(segments):
        validate_identity_segment(
            segment,
            field=f'{field} segment {index}',
            error_type=DatasetDefinitionError,
        )
    return segments


def _resolve_partition(
    *,
    definition: MaterializationDefinition,
    partition: DatasetPartition | Mapping[str, str] | None,
) -> DatasetPartition | None:
    dimensions = definition.partition_dimensions
    if not dimensions:
        if partition is not None:
            raise DatasetTargetError('unpartitioned materialization does not accept a partition')
        return None
    if partition is None:
        raise DatasetTargetError('partitioned materialization requires a partition')
    if isinstance(partition, DatasetPartition):
        values = partition.as_dict()
    elif isinstance(partition, Mapping):
        values = partition
    else:
        raise DatasetTargetError('partition must be a mapping or DatasetPartition')
    return DatasetPartition.from_mapping(values, dimensions=dimensions)
