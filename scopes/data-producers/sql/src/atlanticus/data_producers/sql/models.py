from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from atlanticus.connectivity.sql.models import SqlTableChangeMarker
from atlanticus.data_producers.core import ScopeValue, SourceScope
from atlanticus.datasets.results import DatasetPublicationResult, PublicationStatus


class DataValueKind(StrEnum):
    TEXT = 'text'
    INTEGER = 'integer'
    FLOAT = 'float'
    BOOLEAN = 'boolean'
    DATE = 'date'
    DATETIME = 'datetime'


class SqlStorageMode(StrEnum):
    PARTITIONED = 'partitioned'
    LATEST = 'latest'


class SqlLoadStrategy(StrEnum):
    SCOPED = 'scoped'
    FULL_SNAPSHOT = 'full_snapshot'


@dataclass(frozen=True, slots=True)
class SqlColumnDefinition:
    source_name: str
    output_name: str
    value_kind: DataValueKind
    required: bool = False
    source_timezone: str | None = None

    def __post_init__(self) -> None:
        source_name = _required_text(self.source_name, 'source_name')
        output_name = _required_text(self.output_name, 'output_name')
        if not isinstance(self.value_kind, DataValueKind):
            raise ValueError('value_kind must be a DataValueKind')
        if not isinstance(self.required, bool):
            raise ValueError('required must be a boolean')
        timezone_name = _optional_text(self.source_timezone)
        if timezone_name is not None and self.value_kind is not DataValueKind.DATETIME:
            raise ValueError('source_timezone is only valid for datetime columns')
        if timezone_name is not None:
            try:
                ZoneInfo(timezone_name)
            except ZoneInfoNotFoundError as error:
                raise ValueError('source_timezone is not available') from error
        object.__setattr__(self, 'source_name', source_name)
        object.__setattr__(self, 'output_name', output_name)
        object.__setattr__(self, 'source_timezone', timezone_name)


@dataclass(frozen=True, slots=True)
class SqlSourceDefinition:
    source_key: str
    source_table: str
    storage_mode: SqlStorageMode
    load_strategy: SqlLoadStrategy
    columns: tuple[SqlColumnDefinition, ...]
    enabled: bool = True
    scope_column: str | None = None
    scope_output_column: str | None = None
    materialization_name: str = 'latest'
    partition_dimensions: tuple[str, ...] = ()
    source_last_update_output_column: str | None = None

    def __post_init__(self) -> None:
        source_key = _required_text(self.source_key, 'source_key')
        source_table = _required_text(self.source_table, 'source_table')
        if not isinstance(self.storage_mode, SqlStorageMode):
            raise ValueError('storage_mode must be a SqlStorageMode')
        if not isinstance(self.load_strategy, SqlLoadStrategy):
            raise ValueError('load_strategy must be a SqlLoadStrategy')
        if not isinstance(self.enabled, bool):
            raise ValueError('enabled must be a boolean')
        columns = tuple(self.columns)
        if not columns or not all(isinstance(item, SqlColumnDefinition) for item in columns):
            raise ValueError('columns must contain SqlColumnDefinition values')
        source_names = tuple(item.source_name for item in columns)
        if len({item.lower() for item in source_names}) != len(source_names):
            raise ValueError('source column names must be unique')
        output_names = tuple(item.output_name for item in columns)
        if len({item.lower() for item in output_names}) != len(output_names):
            raise ValueError('output column names must be unique')
        scope_column = _optional_text(self.scope_column)
        scope_output_column = _optional_text(self.scope_output_column)
        materialization_name = _required_text(self.materialization_name, 'materialization_name')
        partition_dimensions = tuple(
            _required_text(item, 'partition dimension') for item in self.partition_dimensions
        )
        if len(set(partition_dimensions)) != len(partition_dimensions):
            raise ValueError('partition_dimensions must be unique')
        if self.load_strategy is SqlLoadStrategy.SCOPED:
            if scope_column is None or scope_output_column is None:
                raise ValueError('scoped sources require scope_column and scope_output_column')
        elif scope_column is not None or scope_output_column is not None:
            raise ValueError('full_snapshot sources must omit scope columns')
        if self.storage_mode is SqlStorageMode.PARTITIONED:
            if self.load_strategy is not SqlLoadStrategy.SCOPED:
                raise ValueError('partitioned storage requires scoped load strategy')
            if not partition_dimensions:
                raise ValueError('partitioned storage requires partition_dimensions')
            matching = tuple(item for item in columns if item.source_name == scope_column)
            if not matching or matching[0].output_name != scope_output_column:
                raise ValueError('scope_column must map to scope_output_column')
            if matching[0].value_kind not in {DataValueKind.INTEGER, DataValueKind.TEXT}:
                raise ValueError('scope output column must be integer or text')
        elif self.load_strategy is not SqlLoadStrategy.FULL_SNAPSHOT:
            raise ValueError('latest storage requires full_snapshot load strategy')
        last_update = _optional_text(self.source_last_update_output_column)
        if last_update is not None:
            matching = tuple(item for item in columns if item.output_name == last_update)
            if not matching:
                raise ValueError('source_last_update_output_column must reference an output column')
            if matching[0].value_kind is not DataValueKind.DATETIME:
                raise ValueError(
                    'source_last_update_output_column must reference a datetime column'
                )
        object.__setattr__(self, 'source_key', source_key)
        object.__setattr__(self, 'source_table', source_table)
        object.__setattr__(self, 'columns', columns)
        object.__setattr__(self, 'scope_column', scope_column)
        object.__setattr__(self, 'scope_output_column', scope_output_column)
        object.__setattr__(self, 'materialization_name', materialization_name)
        object.__setattr__(self, 'partition_dimensions', partition_dimensions)
        object.__setattr__(self, 'source_last_update_output_column', last_update)

    @property
    def expected_output_columns(self) -> tuple[str, ...]:
        return tuple(item.output_name for item in self.columns)

    @property
    def required_output_columns(self) -> tuple[str, ...]:
        return tuple(item.output_name for item in self.columns if item.required)


@dataclass(frozen=True, slots=True)
class SqlSourcePlan:
    definition: SqlSourceDefinition
    change_marker: SqlTableChangeMarker
    scope: SourceScope | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.definition, SqlSourceDefinition):
            raise TypeError('definition must be a SqlSourceDefinition')
        if not isinstance(self.change_marker, SqlTableChangeMarker):
            raise TypeError('change_marker must be a SqlTableChangeMarker')
        if self.definition.load_strategy is SqlLoadStrategy.SCOPED:
            if not isinstance(self.scope, SourceScope):
                raise ValueError('scoped plans require a SourceScope')
        elif self.scope is not None:
            raise ValueError('full_snapshot plans do not accept a scope')

    @property
    def scope_token(self) -> str | None:
        return None if self.scope is None else self.scope.token


@dataclass(frozen=True, slots=True)
class SqlExecutionPlan:
    captured_at_utc: datetime
    sources: tuple[SqlSourcePlan, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, 'captured_at_utc', _normalize_utc(self.captured_at_utc))
        if not all(isinstance(source, SqlSourcePlan) for source in self.sources):
            raise TypeError('sources must contain SqlSourcePlan values')


@dataclass(frozen=True, slots=True)
class SqlPublicationResult:
    publication: DatasetPublicationResult
    scope_value: ScopeValue | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.publication, DatasetPublicationResult):
            raise TypeError('publication must be a DatasetPublicationResult')
        if self.scope_value is not None:
            _scope_value(self.scope_value)


@dataclass(frozen=True, slots=True)
class SqlSourceExecutionResult:
    source_key: str
    source_row_count: int
    publications: tuple[SqlPublicationResult, ...]
    missing_scope_values: tuple[ScopeValue, ...] = ()
    source_last_update_utc: datetime | None = None

    def __post_init__(self) -> None:
        source_key = _required_text(self.source_key, 'source_key')
        if (
            not isinstance(self.source_row_count, int)
            or isinstance(self.source_row_count, bool)
            or self.source_row_count < 0
        ):
            raise ValueError('source_row_count must be a non-negative integer')
        publications = tuple(self.publications)
        if not all(isinstance(item, SqlPublicationResult) for item in publications):
            raise TypeError('publications must contain SqlPublicationResult values')
        missing = tuple(_scope_value(value) for value in self.missing_scope_values)
        last_update = (
            None
            if self.source_last_update_utc is None
            else _normalize_utc(self.source_last_update_utc)
        )
        object.__setattr__(self, 'source_key', source_key)
        object.__setattr__(self, 'publications', publications)
        object.__setattr__(self, 'missing_scope_values', missing)
        object.__setattr__(self, 'source_last_update_utc', last_update)

    @property
    def changed(self) -> bool:
        return any(
            item.publication.status is PublicationStatus.COMMITTED for item in self.publications
        )

    @property
    def publication_count(self) -> int:
        return len(self.publications)

    @property
    def publications_committed(self) -> int:
        return sum(
            item.publication.status is PublicationStatus.COMMITTED for item in self.publications
        )

    @property
    def rows_published(self) -> int:
        return sum(item.publication.item_count for item in self.publications)

    @property
    def publication_signatures(self) -> Mapping[str, str]:
        return MappingProxyType(
            {
                item.publication.target.identifier: item.publication.content_signature
                for item in self.publications
                if item.publication.content_signature is not None
            }
        )


def _scope_value(value: object) -> ScopeValue:
    if isinstance(value, bool):
        raise ValueError('scope value must be an integer or non-empty string')
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ValueError('scope value must be an integer or non-empty string')


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field_name} must be a non-empty string')
    return value.strip()


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('optional text values must be strings or None')
    normalized = value.strip()
    return normalized or None


def _normalize_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError('timestamp must be timezone-aware')
    return value.astimezone(UTC)
