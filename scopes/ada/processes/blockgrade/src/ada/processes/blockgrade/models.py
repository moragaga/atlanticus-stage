from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from atlanticus.connectivity.sql.models import SqlTableChangeMarker
from atlanticus.datasets.results import DatasetPublicationResult, PublicationStatus


class BlockgradeValueKind(StrEnum):
    TEXT = 'text'
    INTEGER = 'integer'
    FLOAT = 'float'
    BOOLEAN = 'boolean'
    DATE = 'date'
    DATETIME = 'datetime'


class BlockgradeStorageMode(StrEnum):
    SHIFT = 'shift'
    LATEST = 'latest'


class BlockgradeLoadStrategy(StrEnum):
    SHIFT_WINDOW = 'shift_window'
    FULL_SNAPSHOT = 'full_snapshot'


@dataclass(frozen=True, slots=True)
class BlockgradeColumnDefinition:
    source_name: str
    output_name: str
    value_kind: BlockgradeValueKind
    required: bool = False
    source_timezone: str | None = None

    def __post_init__(self) -> None:
        source_name = _required_text(self.source_name, 'source_name')
        output_name = _required_text(self.output_name, 'output_name')
        if not isinstance(self.value_kind, BlockgradeValueKind):
            raise ValueError('value_kind must be a BlockgradeValueKind')
        if not isinstance(self.required, bool):
            raise ValueError('required must be a boolean')
        timezone_name = _optional_text(self.source_timezone)
        if timezone_name is not None and self.value_kind is not BlockgradeValueKind.DATETIME:
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
class BlockgradeSourceDefinition:
    source_key: str
    source_table: str
    storage_mode: BlockgradeStorageMode
    load_strategy: BlockgradeLoadStrategy
    columns: tuple[BlockgradeColumnDefinition, ...]
    enabled: bool = True
    shift_id_column: str | None = None
    shift_id_output_column: str = 'shift_id'
    source_last_update_output_column: str | None = None

    def __post_init__(self) -> None:
        source_key = _required_text(self.source_key, 'source_key')
        source_table = _required_text(self.source_table, 'source_table')
        if not isinstance(self.storage_mode, BlockgradeStorageMode):
            raise ValueError('storage_mode must be a BlockgradeStorageMode')
        if not isinstance(self.load_strategy, BlockgradeLoadStrategy):
            raise ValueError('load_strategy must be a BlockgradeLoadStrategy')
        if not isinstance(self.enabled, bool):
            raise ValueError('enabled must be a boolean')
        columns = tuple(self.columns)
        if not columns or not all(isinstance(item, BlockgradeColumnDefinition) for item in columns):
            raise ValueError('columns must contain BlockgradeColumnDefinition values')
        source_names = tuple(item.source_name for item in columns)
        if len({item.lower() for item in source_names}) != len(source_names):
            raise ValueError('source column names must be unique')
        output_names = tuple(item.output_name for item in columns)
        if len({item.lower() for item in output_names}) != len(output_names):
            raise ValueError('output column names must be unique')
        shift_id_column = _optional_text(self.shift_id_column)
        shift_id_output_column = _required_text(
            self.shift_id_output_column, 'shift_id_output_column'
        )
        if self.load_strategy is BlockgradeLoadStrategy.SHIFT_WINDOW and shift_id_column is None:
            raise ValueError('shift_id_column is required for shift_window sources')
        if (
            self.load_strategy is BlockgradeLoadStrategy.FULL_SNAPSHOT
            and shift_id_column is not None
        ):
            raise ValueError('shift_id_column must be omitted for full_snapshot sources')
        if self.storage_mode is BlockgradeStorageMode.SHIFT:
            if self.load_strategy is not BlockgradeLoadStrategy.SHIFT_WINDOW:
                raise ValueError('shift storage requires shift_window load strategy')
            matching = tuple(item for item in columns if item.source_name == shift_id_column)
            if not matching or matching[0].output_name != shift_id_output_column:
                raise ValueError('shift_id_column must map to shift_id_output_column')
            if matching[0].value_kind is not BlockgradeValueKind.INTEGER:
                raise ValueError('shift_id output column must be integer')
        if (
            self.storage_mode is BlockgradeStorageMode.LATEST
            and self.load_strategy is not BlockgradeLoadStrategy.FULL_SNAPSHOT
        ):
            raise ValueError('latest storage requires full_snapshot load strategy')
        last_update = _optional_text(self.source_last_update_output_column)
        if last_update is not None:
            matching = tuple(item for item in columns if item.output_name == last_update)
            if not matching:
                raise ValueError('source_last_update_output_column must reference an output column')
            if matching[0].value_kind is not BlockgradeValueKind.DATETIME:
                raise ValueError(
                    'source_last_update_output_column must reference a datetime column'
                )
        object.__setattr__(self, 'source_key', source_key)
        object.__setattr__(self, 'source_table', source_table)
        object.__setattr__(self, 'columns', columns)
        object.__setattr__(self, 'shift_id_column', shift_id_column)
        object.__setattr__(self, 'shift_id_output_column', shift_id_output_column)
        object.__setattr__(self, 'source_last_update_output_column', last_update)

    @property
    def expected_output_columns(self) -> tuple[str, ...]:
        return tuple(item.output_name for item in self.columns)

    @property
    def required_output_columns(self) -> tuple[str, ...]:
        return tuple(item.output_name for item in self.columns if item.required)


@dataclass(frozen=True, slots=True)
class BlockgradeSourcePlan:
    definition: BlockgradeSourceDefinition
    change_marker: SqlTableChangeMarker
    scope_token: str | None
    shift_ids: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.definition, BlockgradeSourceDefinition):
            raise TypeError('definition must be a BlockgradeSourceDefinition')
        if not isinstance(self.change_marker, SqlTableChangeMarker):
            raise TypeError('change_marker must be a SqlTableChangeMarker')
        scope_token = _optional_text(self.scope_token)
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value <= 0
            for value in self.shift_ids
        ):
            raise ValueError('shift_ids must contain positive integers')
        if self.definition.load_strategy is BlockgradeLoadStrategy.SHIFT_WINDOW:
            if not self.shift_ids or scope_token is None:
                raise ValueError('shift_window plans require shift_ids and scope_token')
        elif self.shift_ids or scope_token is not None:
            raise ValueError('full_snapshot plans do not accept shift_ids or scope_token')
        object.__setattr__(self, 'scope_token', scope_token)


@dataclass(frozen=True, slots=True)
class BlockgradeExecutionPlan:
    captured_at_utc: datetime
    sources: tuple[BlockgradeSourcePlan, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, 'captured_at_utc', _normalize_utc(self.captured_at_utc))
        if not all(isinstance(source, BlockgradeSourcePlan) for source in self.sources):
            raise TypeError('sources must contain BlockgradeSourcePlan values')


@dataclass(frozen=True, slots=True)
class BlockgradePublicationResult:
    publication: DatasetPublicationResult
    shift_id: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.publication, DatasetPublicationResult):
            raise TypeError('publication must be a DatasetPublicationResult')
        if self.shift_id is not None and (
            not isinstance(self.shift_id, int)
            or isinstance(self.shift_id, bool)
            or self.shift_id <= 0
        ):
            raise ValueError('shift_id must be a positive integer or None')


@dataclass(frozen=True, slots=True)
class BlockgradeSourceExecutionResult:
    source_key: str
    source_row_count: int
    publications: tuple[BlockgradePublicationResult, ...]
    missing_shift_ids: tuple[int, ...] = ()
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
        if not all(isinstance(item, BlockgradePublicationResult) for item in publications):
            raise TypeError('publications must contain BlockgradePublicationResult values')
        missing = tuple(self.missing_shift_ids)
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in missing
        ):
            raise ValueError('missing_shift_ids must contain positive integers')
        last_update = (
            None
            if self.source_last_update_utc is None
            else _normalize_utc(self.source_last_update_utc)
        )
        object.__setattr__(self, 'source_key', source_key)
        object.__setattr__(self, 'publications', publications)
        object.__setattr__(self, 'missing_shift_ids', missing)
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
