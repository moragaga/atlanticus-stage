from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum


class DataSource(StrEnum):
    PI_INTERPOLATED = 'pi.interpolated'
    PI_RECORDED = 'pi.recorded'

    DISPATCH_TIEMPOS_MLP = 'dispatch.tiempos_mlp'
    DISPATCH_STD_SHIFT_LOADS = 'dispatch.std_shift_loads'
    DISPATCH_STD_SHIFT_STATE = 'dispatch.std_shift_state'
    DISPATCH_STD_TRUCK = 'dispatch.std_truck'
    DISPATCH_STD_SHIFT_GRADE = 'dispatch.std_shift_grade'
    DISPATCH_STD_SHIFT_LOADS_2 = 'dispatch.std_shift_loads_2'
    DISPATCH_STD_SHIFT_DUMPS = 'dispatch.std_shift_dumps'

    BLOCKGRADE_MMS_BLOCKGRADE_DETAILS_BUCKET = 'blockgrade.mms_blockgrade_details_bucket'

    REMANENTES_EXTRAIBLES = 'remanentes.extraibles'
    REMANENTES_NO_EXTRAIBLES = 'remanentes.no_extraibles'
    REMANENTES_STOCKS = 'remanentes.stocks'

    FABRICA_PLANES = 'fabrica.planes'
    FABRICA_KPIS = 'fabrica.kpis'


class DataColumnType(StrEnum):
    TEXT = 'text'
    INTEGER = 'integer'
    FLOAT = 'float'
    BOOLEAN = 'boolean'
    DATE = 'date'
    DATETIME = 'datetime'


@dataclass(frozen=True, slots=True)
class DataColumn:
    name: str
    data_type: DataColumnType

    def __post_init__(self) -> None:
        object.__setattr__(self, 'name', _required_text(self.name, 'column name'))
        if not isinstance(self.data_type, DataColumnType):
            raise TypeError('column data_type must be DataColumnType')


class DataPartition(StrEnum):
    LATEST = 'latest'
    DAILY = 'daily'
    MONTHLY = 'monthly'
    WEEKLY = 'weekly'
    SHIFT = 'shift'


class OperationalScope(StrEnum):
    CURRENT_TURN_MINE = 'current_turn_mine'
    PREVIOUS_TURN_MINE = 'previous_turn_mine'
    CURRENT_TURN_PLANT = 'current_turn_plant'
    PREVIOUS_TURN_PLANT = 'previous_turn_plant'
    CURRENT_OPERATIONAL_DAY_MINE = 'current_operational_day_mine'
    CURRENT_OPERATIONAL_DAY_PLANT = 'current_operational_day_plant'
    CURRENT_OPERATIONAL_MONTH_MINE = 'current_operational_month_mine'
    CURRENT_OPERATIONAL_MONTH_PLANT = 'current_operational_month_plant'


class ShiftScope(StrEnum):
    CURRENT = 'current'
    PREVIOUS = 'previous'
    CURRENT_TURN = 'current_turn'
    PREVIOUS_TURN = 'previous_turn'
    CURRENT_WEEK = 'current_week'
    DAYS = 'days'


class TimeWindowUnit(StrEnum):
    MINUTES = 'minutes'
    HOURS = 'hours'
    DAYS = 'days'
    MONTHS = 'months'


@dataclass(frozen=True, slots=True)
class TimeWindow:
    value: int
    unit: TimeWindowUnit

    def __post_init__(self) -> None:
        if not isinstance(self.value, int) or isinstance(self.value, bool) or self.value <= 0:
            raise ValueError('time window value must be an integer greater than zero')
        if not isinstance(self.unit, TimeWindowUnit):
            raise TypeError('time window unit must be TimeWindowUnit')

    def to_timedelta(self) -> timedelta:
        if self.unit is TimeWindowUnit.MINUTES:
            return timedelta(minutes=self.value)
        if self.unit is TimeWindowUnit.HOURS:
            return timedelta(hours=self.value)
        if self.unit is TimeWindowUnit.DAYS:
            return timedelta(days=self.value)
        raise ValueError('months time window has no fixed timedelta')

    def start_from(self, end: datetime) -> datetime:
        value = normalize_utc_second(end, field_name='end')
        if self.unit is TimeWindowUnit.MINUTES:
            return value - timedelta(minutes=self.value)
        if self.unit is TimeWindowUnit.HOURS:
            return value - timedelta(hours=self.value)
        if self.unit is TimeWindowUnit.DAYS:
            return value - timedelta(days=self.value)
        return _subtract_months(value, self.value)


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
class DataSourceView:
    source: DataSource
    partition: DataPartition

    def __post_init__(self) -> None:
        if not isinstance(self.source, DataSource):
            raise TypeError('source view source must be DataSource')
        if not isinstance(self.partition, DataPartition):
            raise TypeError('source view partition must be DataPartition')


@dataclass(frozen=True, slots=True)
class DataRequirement:
    source: DataSource
    partition: DataPartition
    columns: tuple[DataColumn, ...]
    time_window: TimeWindow | None = None
    operational_scope: OperationalScope | None = None
    shift: ShiftSelection | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source, DataSource):
            raise TypeError('data requirement source must be DataSource')
        if not isinstance(self.partition, DataPartition):
            raise TypeError('data requirement partition must be DataPartition')
        columns = tuple(self.columns)
        if not columns:
            raise ValueError('data requirement requires at least one column')
        if not all(isinstance(column, DataColumn) for column in columns):
            raise TypeError('data requirement columns must contain DataColumn values')
        names = tuple(column.name for column in columns)
        if len(names) != len(set(names)):
            raise ValueError('data requirement column names must be unique')
        if self.time_window is not None and not isinstance(self.time_window, TimeWindow):
            raise TypeError('time_window must be TimeWindow')
        if self.operational_scope is not None and not isinstance(
            self.operational_scope, OperationalScope
        ):
            raise TypeError('operational_scope must be OperationalScope')
        if self.shift is not None and not isinstance(self.shift, ShiftSelection):
            raise TypeError('shift must be ShiftSelection')
        selectors = tuple(
            value
            for value in (self.time_window, self.operational_scope, self.shift)
            if value is not None
        )
        if len(selectors) > 1:
            raise ValueError(
                'data requirement cannot mix time_window, operational_scope, and shift'
            )
        _validate_partition_selector(self)
        object.__setattr__(self, 'columns', columns)

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)

    @property
    def view(self) -> DataSourceView:
        return DataSourceView(source=self.source, partition=self.partition)


def normalize_utc_second(value: datetime, *, field_name: str = 'datetime') -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f'{field_name} must be a datetime')
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f'{field_name} must be timezone-aware')
    normalized = value.astimezone(UTC)
    if normalized.microsecond != 0:
        raise ValueError(f'{field_name} must use second precision')
    return normalized


def _validate_partition_selector(requirement: DataRequirement) -> None:
    partition = requirement.partition
    time_window = requirement.time_window
    operational_scope = requirement.operational_scope
    shift = requirement.shift

    if partition is DataPartition.LATEST and any(
        value is not None for value in (time_window, operational_scope, shift)
    ):
        raise ValueError('latest partition must not declare a temporal selector')
    if shift is not None and partition is not DataPartition.SHIFT:
        raise ValueError('shift selection requires shift partition')
    if partition is DataPartition.SHIFT and shift is None:
        raise ValueError('shift partition requires shift selection')
    if time_window is not None:
        if time_window.unit is TimeWindowUnit.MONTHS:
            if partition is not DataPartition.MONTHLY:
                raise ValueError('months time window requires monthly partition')
        elif partition is not DataPartition.DAILY:
            raise ValueError('minutes, hours, and days time windows require daily partition')
    if operational_scope is not None:
        expected = (
            DataPartition.DAILY
            if operational_scope in _DAILY_OPERATIONAL_SCOPES
            else DataPartition.MONTHLY
        )
        if partition is not expected:
            raise ValueError(f'{operational_scope.value} requires {expected.value} partition')

    supported = _SOURCE_PARTITIONS.get(requirement.source)
    if supported is not None and partition not in supported:
        raise ValueError(f'{requirement.source.value}: unsupported partition: {partition.value}')
    if (
        partition in _SOURCE_TEMPORAL_SELECTOR_REQUIRED.get(requirement.source, frozenset())
        and time_window is None
        and operational_scope is None
    ):
        raise ValueError(
            f'{requirement.source.value}: {partition.value} partition requires a temporal selector'
        )


_SOURCE_PARTITIONS = {
    DataSource.PI_INTERPOLATED: frozenset(
        {DataPartition.LATEST, DataPartition.DAILY, DataPartition.MONTHLY}
    ),
    DataSource.PI_RECORDED: frozenset({DataPartition.DAILY, DataPartition.MONTHLY}),
}

_SOURCE_TEMPORAL_SELECTOR_REQUIRED = {
    DataSource.PI_INTERPOLATED: frozenset({DataPartition.DAILY, DataPartition.MONTHLY}),
    DataSource.PI_RECORDED: frozenset({DataPartition.DAILY, DataPartition.MONTHLY}),
}


_DAILY_OPERATIONAL_SCOPES = frozenset(
    {
        OperationalScope.CURRENT_TURN_MINE,
        OperationalScope.PREVIOUS_TURN_MINE,
        OperationalScope.CURRENT_TURN_PLANT,
        OperationalScope.PREVIOUS_TURN_PLANT,
        OperationalScope.CURRENT_OPERATIONAL_DAY_MINE,
        OperationalScope.CURRENT_OPERATIONAL_DAY_PLANT,
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
