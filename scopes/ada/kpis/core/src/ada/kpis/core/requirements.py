from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from ada.kpis.core.enums import (
    KpiOperationalScope,
    KpiPartition,
    KpiSource,
    ShiftScope,
)


class KpiTimeWindowUnit(StrEnum):
    MINUTES = 'minutes'
    HOURS = 'hours'
    DAYS = 'days'
    MONTHS = 'months'


@dataclass(frozen=True, slots=True)
class KpiTimeWindow:
    value: int
    unit: KpiTimeWindowUnit

    def __post_init__(self) -> None:
        if not isinstance(self.value, int) or isinstance(self.value, bool) or self.value <= 0:
            raise ValueError('time window value must be an integer greater than zero')
        if not isinstance(self.unit, KpiTimeWindowUnit):
            raise TypeError('time window unit must be KpiTimeWindowUnit')

    def to_timedelta(self) -> timedelta:
        if self.unit is KpiTimeWindowUnit.MINUTES:
            return timedelta(minutes=self.value)
        if self.unit is KpiTimeWindowUnit.HOURS:
            return timedelta(hours=self.value)
        if self.unit is KpiTimeWindowUnit.DAYS:
            return timedelta(days=self.value)
        raise ValueError('months time window has no fixed timedelta')

    def start_from(self, end: datetime) -> datetime:
        if not isinstance(end, datetime):
            raise TypeError('end must be datetime')
        if end.tzinfo is None or end.utcoffset() is None:
            raise ValueError('end must be timezone-aware')
        if self.unit is KpiTimeWindowUnit.MINUTES:
            return end - timedelta(minutes=self.value)
        if self.unit is KpiTimeWindowUnit.HOURS:
            return end - timedelta(hours=self.value)
        if self.unit is KpiTimeWindowUnit.DAYS:
            return end - timedelta(days=self.value)
        return _subtract_months(end, self.value)


@dataclass(frozen=True, slots=True)
class ShiftSelection:
    scope: ShiftScope
    days: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.scope, ShiftScope):
            raise TypeError('shift scope must be ShiftScope')
        if self.scope is ShiftScope.DAYS:
            if not isinstance(self.days, int) or isinstance(self.days, bool):
                raise ValueError('days shift scope requires an integer days value')
            if not 1 <= self.days <= 7:
                raise ValueError('days shift scope requires days between 1 and 7')
            return
        if self.days is not None:
            raise ValueError('days can only be declared with ShiftScope.DAYS')


@dataclass(frozen=True, slots=True)
class KpiSourceView:
    source: KpiSource
    partition: KpiPartition

    def __post_init__(self) -> None:
        if not isinstance(self.source, KpiSource):
            raise TypeError('source view source must be KpiSource')
        if not isinstance(self.partition, KpiPartition):
            raise TypeError('source view partition must be KpiPartition')


@dataclass(frozen=True, slots=True)
class SourceRequirement:
    source: KpiSource
    partition: KpiPartition
    columns: tuple[str, ...]
    time_window: KpiTimeWindow | None = None
    operational_scope: KpiOperationalScope | None = None
    shift: ShiftSelection | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source, KpiSource):
            raise TypeError('source requirement source must be KpiSource')
        if not isinstance(self.partition, KpiPartition):
            raise TypeError('source requirement partition must be KpiPartition')
        columns = tuple(_required_text(column, 'column') for column in self.columns)
        if not columns:
            raise ValueError('source requirement requires at least one column')
        if len(columns) != len(set(columns)):
            raise ValueError('source requirement columns must be unique')
        if self.time_window is not None and not isinstance(self.time_window, KpiTimeWindow):
            raise TypeError('time_window must be KpiTimeWindow')
        if self.operational_scope is not None and not isinstance(
            self.operational_scope, KpiOperationalScope
        ):
            raise TypeError('operational_scope must be KpiOperationalScope')
        if self.shift is not None and not isinstance(self.shift, ShiftSelection):
            raise TypeError('shift must be ShiftSelection')
        selectors = tuple(
            value
            for value in (self.time_window, self.operational_scope, self.shift)
            if value is not None
        )
        if len(selectors) > 1:
            raise ValueError(
                'source requirement cannot mix time_window, operational_scope, and shift'
            )
        _validate_partition_selector(self)
        object.__setattr__(self, 'columns', columns)

    @property
    def view(self) -> KpiSourceView:
        return KpiSourceView(source=self.source, partition=self.partition)


def _validate_partition_selector(requirement: SourceRequirement) -> None:
    partition = requirement.partition
    time_window = requirement.time_window
    operational_scope = requirement.operational_scope
    shift = requirement.shift

    if partition is KpiPartition.LATEST and any(
        value is not None for value in (time_window, operational_scope, shift)
    ):
        raise ValueError('latest partition must not declare a temporal selector')
    if shift is not None and partition is not KpiPartition.SHIFT:
        raise ValueError('shift selection requires shift partition')
    if partition is KpiPartition.SHIFT and shift is None:
        raise ValueError('shift partition requires shift selection')
    if time_window is not None:
        if time_window.unit is KpiTimeWindowUnit.MONTHS:
            if partition is not KpiPartition.MONTHLY:
                raise ValueError('months time window requires monthly partition')
        elif partition is not KpiPartition.DAILY:
            raise ValueError('minutes, hours, and days time windows require daily partition')
    if operational_scope is not None:
        expected = (
            KpiPartition.DAILY
            if operational_scope in _DAILY_OPERATIONAL_SCOPES
            else KpiPartition.MONTHLY
        )
        if partition is not expected:
            raise ValueError(f'{operational_scope.value} requires {expected.value} partition')

    if requirement.source is KpiSource.PI_INTERPOLATED:
        if partition not in {
            KpiPartition.LATEST,
            KpiPartition.DAILY,
            KpiPartition.MONTHLY,
        }:
            raise ValueError(f'{requirement.source.value}: unsupported partition: {partition.value}')
        if partition is not KpiPartition.LATEST and time_window is None and operational_scope is None:
            raise ValueError(
                f'{requirement.source.value}: {partition.value} partition requires a temporal selector'
            )
    elif requirement.source is KpiSource.PI_RECORDED:
        if partition not in {KpiPartition.DAILY, KpiPartition.MONTHLY}:
            raise ValueError(f'{requirement.source.value}: unsupported partition: {partition.value}')
        if time_window is None and operational_scope is None:
            raise ValueError(
                f'{requirement.source.value}: {partition.value} partition requires a temporal selector'
            )


_DAILY_OPERATIONAL_SCOPES = frozenset(
    {
        KpiOperationalScope.CURRENT_TURN_MINE,
        KpiOperationalScope.PREVIOUS_TURN_MINE,
        KpiOperationalScope.CURRENT_TURN_PLANT,
        KpiOperationalScope.PREVIOUS_TURN_PLANT,
        KpiOperationalScope.CURRENT_OPERATIONAL_DAY_MINE,
        KpiOperationalScope.CURRENT_OPERATIONAL_DAY_PLANT,
    }
)


def _subtract_months(value: datetime, months: int) -> datetime:
    total = value.year * 12 + value.month - 1 - months
    year, month_index = divmod(total, 12)
    month = month_index + 1
    next_month = datetime(
        year + (month == 12),
        1 if month == 12 else month + 1,
        1,
        tzinfo=value.tzinfo,
    )
    last_day = (next_month - timedelta(days=1)).day
    return value.replace(year=year, month=month, day=min(value.day, last_day))


def _required_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field_name} must be a non-empty string')
    return value.strip()
