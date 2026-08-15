from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any

from atlanticus.datasets import DatasetPublicationResult
from atlanticus.integrations.pi.contracts import PiExtractionMode, PiTagDefinition


@dataclass(frozen=True, slots=True)
class ResolvedPiTag:
    definition: PiTagDefinition
    web_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.definition, PiTagDefinition):
            raise TypeError('definition must be a PiTagDefinition')
        if not isinstance(self.web_id, str) or not self.web_id:
            raise ValueError('web_id must be non-empty text')
        if self.web_id != self.web_id.strip():
            raise ValueError('web_id must not contain surrounding whitespace')

    @property
    def tag_name(self) -> str:
        return self.definition.tag_name

    @property
    def alias(self) -> str:
        return self.definition.alias

    @property
    def extraction_mode(self) -> PiExtractionMode:
        return self.definition.extraction_mode


@dataclass(frozen=True, slots=True)
class PiExecutionPlan:
    interpolated: tuple[ResolvedPiTag, ...]
    recorded: tuple[ResolvedPiTag, ...]
    unresolved_tag_names: tuple[str, ...] = ()
    _by_name: Mapping[str, ResolvedPiTag] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.interpolated, tuple) or not isinstance(self.recorded, tuple):
            raise TypeError('execution plan tag groups must be tuples')
        if any(
            not isinstance(item, ResolvedPiTag) for item in (*self.interpolated, *self.recorded)
        ):
            raise TypeError('execution plan tag groups must contain ResolvedPiTag values')
        if not isinstance(self.unresolved_tag_names, tuple) or any(
            not isinstance(item, str) or not item for item in self.unresolved_tag_names
        ):
            raise TypeError('unresolved_tag_names must be a tuple of non-empty text values')
        tag_names = [item.tag_name.casefold() for item in (*self.interpolated, *self.recorded)]
        if len(set(tag_names)) != len(tag_names):
            raise ValueError('execution plan must not contain duplicate tag names')
        unresolved = [item.casefold() for item in self.unresolved_tag_names]
        if len(set(unresolved)) != len(unresolved):
            raise ValueError('unresolved_tag_names must not contain duplicates')
        if set(tag_names).intersection(unresolved):
            raise ValueError('resolved and unresolved tag names must not overlap')
        object.__setattr__(
            self,
            '_by_name',
            MappingProxyType(
                {item.tag_name: item for item in (*self.interpolated, *self.recorded)}
            ),
        )

    @property
    def resolved(self) -> tuple[ResolvedPiTag, ...]:
        return (*self.interpolated, *self.recorded)

    @property
    def by_name(self) -> Mapping[str, ResolvedPiTag]:
        return self._by_name


@dataclass(frozen=True, slots=True)
class PiPreparationResult:
    plan: PiExecutionPlan
    cache_hit_count: int
    resolved_count: int
    unresolved_count: int
    point_request_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.plan, PiExecutionPlan):
            raise TypeError('plan must be a PiExecutionPlan')
        for field_name in (
            'cache_hit_count',
            'resolved_count',
            'unresolved_count',
            'point_request_count',
        ):
            value = getattr(self, field_name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f'{field_name} must be a non-negative integer')


@dataclass(frozen=True, slots=True)
class PiAcquisitionWindow:
    first_slot_utc: datetime
    last_slot_utc: datetime
    interpolation_seconds: int
    recovery_truncated: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.first_slot_utc, datetime) or not isinstance(
            self.last_slot_utc, datetime
        ):
            raise TypeError('acquisition window slots must be datetime values')
        if self.first_slot_utc.tzinfo is None or self.last_slot_utc.tzinfo is None:
            raise ValueError('acquisition window slots must be timezone-aware')
        if self.first_slot_utc.utcoffset() != timedelta(
            0
        ) or self.last_slot_utc.utcoffset() != timedelta(0):
            raise ValueError('acquisition window slots must use UTC')
        if self.first_slot_utc.microsecond or self.last_slot_utc.microsecond:
            raise ValueError('acquisition window slots must not contain microseconds')
        if self.first_slot_utc > self.last_slot_utc:
            raise ValueError('first_slot_utc must not be after last_slot_utc')
        if (
            not isinstance(self.interpolation_seconds, int)
            or isinstance(self.interpolation_seconds, bool)
            or self.interpolation_seconds <= 0
        ):
            raise ValueError('interpolation_seconds must be a positive integer')
        if not isinstance(self.recovery_truncated, bool):
            raise TypeError('recovery_truncated must be a bool')

    @property
    def slot_count(self) -> int:
        seconds = int((self.last_slot_utc - self.first_slot_utc).total_seconds())
        return (seconds // self.interpolation_seconds) + 1


@dataclass(frozen=True, slots=True)
class PiSample:
    tag_name: str
    timestamp_utc: datetime
    value: Any

    def __post_init__(self) -> None:
        if not isinstance(self.tag_name, str) or not self.tag_name:
            raise TypeError('tag_name must be non-empty text')
        if self.tag_name != self.tag_name.strip():
            raise ValueError('tag_name must not contain surrounding whitespace')
        if not isinstance(self.timestamp_utc, datetime) or self.timestamp_utc.tzinfo is None:
            raise TypeError('timestamp_utc must be a timezone-aware datetime')
        if self.timestamp_utc.utcoffset() != timedelta(0):
            raise ValueError('timestamp_utc must use UTC')
        object.__setattr__(self, 'timestamp_utc', self.timestamp_utc.astimezone(UTC))


@dataclass(frozen=True, slots=True)
class PiAcquisitionResult:
    interpolated: tuple[PiSample, ...]
    recorded: tuple[PiSample, ...]
    interpolated_request_count: int = 0
    recorded_request_count: int = 0
    split_count: int = 0
    interpolated_conflict_count: int = 0
    recorded_conflict_count: int = 0
    unexpected_record_count: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.interpolated, tuple) or not all(
            isinstance(item, PiSample) for item in self.interpolated
        ):
            raise TypeError('interpolated must be a tuple of PiSample values')
        if not isinstance(self.recorded, tuple) or not all(
            isinstance(item, PiSample) for item in self.recorded
        ):
            raise TypeError('recorded must be a tuple of PiSample values')
        for field_name in (
            'interpolated_request_count',
            'recorded_request_count',
            'split_count',
            'interpolated_conflict_count',
            'recorded_conflict_count',
            'unexpected_record_count',
        ):
            value = getattr(self, field_name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f'{field_name} must be a non-negative integer')

    @property
    def request_count(self) -> int:
        return self.interpolated_request_count + self.recorded_request_count


@dataclass(frozen=True, slots=True)
class PiMaterializationResult:
    publications: tuple[DatasetPublicationResult, ...]
    recorded_bucket_conflict_count: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.publications, tuple) or not all(
            isinstance(item, DatasetPublicationResult) for item in self.publications
        ):
            raise TypeError('publications must be a tuple of DatasetPublicationResult values')
        if (
            not isinstance(self.recorded_bucket_conflict_count, int)
            or isinstance(self.recorded_bucket_conflict_count, bool)
            or self.recorded_bucket_conflict_count < 0
        ):
            raise ValueError('recorded_bucket_conflict_count must be a non-negative integer')
