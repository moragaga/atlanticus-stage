"""Cron numérico de cinco campos para resolver slots de ejecución."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from atlanticus.runtime.errors import RuntimeConfigurationError

_MAX_SEARCH_MINUTES = 5 * 366 * 24 * 60
_FIELD_PATTERN = re.compile(r'^[0-9*/,-]+$')


@dataclass(frozen=True, slots=True)
class ScheduleSlot:
    scheduled_at_utc: datetime
    next_scheduled_at_utc: datetime

    def __post_init__(self) -> None:
        _validate_utc_datetime(self.scheduled_at_utc, 'scheduled_at_utc')
        _validate_utc_datetime(self.next_scheduled_at_utc, 'next_scheduled_at_utc')
        if self.next_scheduled_at_utc <= self.scheduled_at_utc:
            raise ValueError('next_scheduled_at_utc must be later than scheduled_at_utc')


@dataclass(frozen=True, slots=True)
class _CronField:
    values: frozenset[int]
    wildcard: bool


@dataclass(frozen=True, slots=True)
class _CronExpression:
    minute: _CronField
    hour: _CronField
    day_of_month: _CronField
    month: _CronField
    day_of_week: _CronField

    def matches(self, value: datetime) -> bool:
        if value.minute not in self.minute.values:
            return False
        if value.hour not in self.hour.values:
            return False
        if value.month not in self.month.values:
            return False
        day_of_month_match = value.day in self.day_of_month.values
        cron_weekday = (value.weekday() + 1) % 7
        day_of_week_match = cron_weekday in self.day_of_week.values
        if self.day_of_month.wildcard and self.day_of_week.wildcard:
            return True
        if self.day_of_month.wildcard:
            return day_of_week_match
        if self.day_of_week.wildcard:
            return day_of_month_match
        return day_of_month_match or day_of_week_match


def validate_cron_expression(expression: str) -> None:
    _parse_cron_expression(expression)


def validate_timezone_name(name: str) -> None:
    _resolve_timezone(name)


def resolve_schedule_slot(
    *,
    expression: str,
    timezone_name: str,
    now_utc: datetime,
) -> ScheduleSlot:
    cron = _parse_cron_expression(expression)
    timezone = _resolve_timezone(timezone_name)
    normalized_now = _normalize_utc_datetime(now_utc, 'now_utc')
    current_minute = normalized_now.replace(second=0, microsecond=0)

    scheduled_at = _find_matching_minute(
        cron=cron,
        timezone=timezone,
        start_utc=current_minute,
        step_minutes=-1,
    )
    next_scheduled_at = _find_matching_minute(
        cron=cron,
        timezone=timezone,
        start_utc=scheduled_at + timedelta(minutes=1),
        step_minutes=1,
    )
    return ScheduleSlot(
        scheduled_at_utc=scheduled_at,
        next_scheduled_at_utc=next_scheduled_at,
    )


def _find_matching_minute(
    *,
    cron: _CronExpression,
    timezone: ZoneInfo,
    start_utc: datetime,
    step_minutes: int,
) -> datetime:
    candidate = start_utc
    step = timedelta(minutes=step_minutes)
    for _ in range(_MAX_SEARCH_MINUTES):
        if cron.matches(candidate.astimezone(timezone)):
            return candidate
        candidate += step
    raise RuntimeConfigurationError('cron expression did not produce a slot within five years')


def _parse_cron_expression(expression: str) -> _CronExpression:
    if not isinstance(expression, str):
        raise TypeError('cron expression must be a string')
    if expression != expression.strip() or not expression:
        raise RuntimeConfigurationError(
            'cron expression must not be empty or padded with whitespace'
        )
    fields = expression.split(' ')
    if len(fields) != 5 or any(not field for field in fields):
        raise RuntimeConfigurationError('cron expression must contain exactly five fields')
    return _CronExpression(
        minute=_parse_field(fields[0], minimum=0, maximum=59, name='minute'),
        hour=_parse_field(fields[1], minimum=0, maximum=23, name='hour'),
        day_of_month=_parse_field(fields[2], minimum=1, maximum=31, name='day_of_month'),
        month=_parse_field(fields[3], minimum=1, maximum=12, name='month'),
        day_of_week=_parse_field(
            fields[4], minimum=0, maximum=7, name='day_of_week', normalize_sunday=True
        ),
    )


def _parse_field(
    raw: str,
    *,
    minimum: int,
    maximum: int,
    name: str,
    normalize_sunday: bool = False,
) -> _CronField:
    if not _FIELD_PATTERN.fullmatch(raw):
        raise RuntimeConfigurationError(f'cron {name} field contains unsupported characters')
    values: set[int] = set()
    wildcard = raw == '*'
    for part in raw.split(','):
        if not part:
            raise RuntimeConfigurationError(f'cron {name} field contains an empty list item')
        values.update(
            _parse_field_part(
                part,
                minimum=minimum,
                maximum=maximum,
                name=name,
            )
        )
    if normalize_sunday and 7 in values:
        values.remove(7)
        values.add(0)
    return _CronField(values=frozenset(values), wildcard=wildcard)


def _parse_field_part(part: str, *, minimum: int, maximum: int, name: str) -> set[int]:
    base, step = _split_step(part, name=name)
    if base == '*':
        start = minimum
        end = maximum
    elif '-' in base:
        pieces = base.split('-')
        if len(pieces) != 2 or not all(piece.isdigit() for piece in pieces):
            raise RuntimeConfigurationError(f'cron {name} range is invalid')
        start, end = (int(piece) for piece in pieces)
    elif base.isdigit():
        start = int(base)
        end = start
    else:
        raise RuntimeConfigurationError(f'cron {name} field is invalid')

    if start < minimum or start > maximum or end < minimum or end > maximum:
        raise RuntimeConfigurationError(
            f'cron {name} field must stay between {minimum} and {maximum}'
        )
    if start > end:
        raise RuntimeConfigurationError(f'cron {name} range start must not exceed range end')
    return set(range(start, end + 1, step))


def _split_step(part: str, *, name: str) -> tuple[str, int]:
    pieces = part.split('/')
    if len(pieces) == 1:
        return pieces[0], 1
    if len(pieces) != 2 or not pieces[0] or not pieces[1].isdigit():
        raise RuntimeConfigurationError(f'cron {name} step is invalid')
    step = int(pieces[1])
    if step <= 0:
        raise RuntimeConfigurationError(f'cron {name} step must be greater than zero')
    return pieces[0], step


def _resolve_timezone(name: str) -> ZoneInfo:
    if not isinstance(name, str):
        raise TypeError('schedule timezone must be a string')
    if name != name.strip() or not name:
        raise RuntimeConfigurationError(
            'schedule timezone must not be empty or padded with whitespace'
        )
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as error:
        raise RuntimeConfigurationError(f'unknown schedule timezone: {name}') from error


def _normalize_utc_datetime(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f'{name} must be a datetime')
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f'{name} must be timezone-aware')
    return value.astimezone(UTC)


def _validate_utc_datetime(value: datetime, name: str) -> None:
    normalized = _normalize_utc_datetime(value, name)
    if normalized != value:
        raise ValueError(f'{name} must use UTC timezone')
