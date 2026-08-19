from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from ada.kpis.core import KpiWatermark, ShiftScope, ShiftSelection
from ada.operational_calendar import (
    MINE_CALENDAR,
    ShiftIdTurn,
    get_current_shift_id_turn,
    get_previous_shift_id_turn,
    parse_shift_id_turn,
)


@dataclass(frozen=True, slots=True)
class MineShiftResolver:
    def resolve(
        self,
        *,
        selection: ShiftSelection,
        watermark: KpiWatermark,
    ) -> tuple[ShiftIdTurn, ...]:
        if not isinstance(selection, ShiftSelection):
            raise TypeError('selection must be ShiftSelection')
        if not isinstance(watermark, KpiWatermark):
            raise TypeError('watermark must be KpiWatermark')
        current = get_current_shift_id_turn(value=watermark.timestamp_utc)
        if selection.scope is ShiftScope.CURRENT:
            return (current,)
        if selection.scope is ShiftScope.PREVIOUS:
            return (get_previous_shift_id_turn(value=watermark.timestamp_utc),)
        if selection.scope is ShiftScope.CURRENT_TURN:
            return _current_operational_day(current)
        if selection.scope is ShiftScope.PREVIOUS_TURN:
            return _full_operational_day(current.nominal_date - timedelta(days=1))
        if selection.scope is ShiftScope.CURRENT_WEEK:
            days_since_start = (
                current.nominal_date.weekday() - MINE_CALENDAR.week_start_weekday
            ) % 7
            start = current.nominal_date - timedelta(days=days_since_start)
            return _operational_days_through_current(start=start, current=current)
        if selection.scope is ShiftScope.DAYS:
            assert selection.days is not None
            start = current.nominal_date - timedelta(days=selection.days - 1)
            return _operational_days_through_current(start=start, current=current)
        raise ValueError(f'unsupported shift scope: {selection.scope.value}')


def _operational_days_through_current(
    *,
    start: date,
    current: ShiftIdTurn,
) -> tuple[ShiftIdTurn, ...]:
    turns: list[ShiftIdTurn] = []
    day = start
    while day < current.nominal_date:
        turns.extend(_full_operational_day(day))
        day += timedelta(days=1)
    turns.extend(_current_operational_day(current))
    return tuple(turns)


def _current_operational_day(current: ShiftIdTurn) -> tuple[ShiftIdTurn, ...]:
    if current.shift_suffix == '001':
        return (current,)
    if current.shift_suffix == '002':
        return (_turn(current.nominal_date, '001'), current)
    raise ValueError('current mine shift must use turn 001 or 002')


def _full_operational_day(value: date) -> tuple[ShiftIdTurn, ShiftIdTurn]:
    return (_turn(value, '001'), _turn(value, '002'))


def _turn(value: date, suffix: str) -> ShiftIdTurn:
    return parse_shift_id_turn(int(f'{value:%y%m%d}{suffix}'))
