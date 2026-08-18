from __future__ import annotations

# Los grupos G1–G4 usan una regla independiente 20:00/08:00 y anchors por área; no se mezclan con los horarios Mina/Planta.
# Los comentarios explican intención y fronteras sin modificar estructura ni comportamiento.

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from ada.operational_calendar.calendar import DEFAULT_TIMEZONE_NAME, OperationalCalendar
from ada.operational_calendar.models import WorkShiftCode, WorkShiftWindow


class TurnArea(StrEnum):
    MINA = 'mina'
    PLANTA = 'planta'


@dataclass(frozen=True, slots=True)
class TurnCalendarResult:
    area: TurnArea
    group: str
    turn_code: WorkShiftCode
    turn_date: date
    turn_start_local: datetime
    turn_end_local: datetime
    turn_start_utc: datetime
    turn_end_utc: datetime


@dataclass(frozen=True, slots=True)
class _AreaRotationRule:
    day_anchor: date
    night_anchor: date


_ROTATION_RULES = {
    TurnArea.MINA: _AreaRotationRule(
        day_anchor=date(2022, 1, 5),
        night_anchor=date(2022, 1, 4),
    ),
    TurnArea.PLANTA: _AreaRotationRule(
        day_anchor=date(2022, 1, 6),
        night_anchor=date(2022, 1, 5),
    ),
}

# Esta tercera regla temporal es deliberadamente distinta de Mina y Planta.
_GROUP_CALENDAR = OperationalCalendar(
    key='group_20',
    operational_day_start_hour=20,
    day_shift_start_hour=8,
)


def get_current_turn(
    *,
    area: TurnArea | str,
    value: datetime | None = None,
) -> TurnCalendarResult:
    return _build_result(
        area=_normalize_area(area),
        window=_GROUP_CALENDAR.resolve_work_shift(value),
    )


def get_previous_turn(
    *,
    area: TurnArea | str,
    value: datetime | None = None,
) -> TurnCalendarResult:
    return _build_result(
        area=_normalize_area(area),
        window=_GROUP_CALENDAR.resolve_previous_work_shift(value),
    )


def get_turns_for_incremental_window(
    *,
    area: TurnArea | str,
    value: datetime | None = None,
) -> tuple[TurnCalendarResult, TurnCalendarResult]:
    return (
        get_previous_turn(area=area, value=value),
        get_current_turn(area=area, value=value),
    )


def build_turn_calendar_rows(
    *,
    start_date: date,
    end_date: date,
    area: TurnArea | str,
) -> list[dict[str, str]]:
    if end_date < start_date:
        raise ValueError('end_date must not be earlier than start_date')
    area_value = _normalize_area(area)
    rows: list[dict[str, str]] = []
    current_date = start_date
    while current_date <= end_date:
        for code in (WorkShiftCode.DAY, WorkShiftCode.NIGHT):
            window = _build_group_window(turn_date=current_date, code=code)
            rows.append(_to_row(_build_result(area=area_value, window=window)))
        current_date += timedelta(days=1)
    return rows


def _build_group_window(*, turn_date: date, code: WorkShiftCode) -> WorkShiftWindow:
    timezone_value = ZoneInfo(DEFAULT_TIMEZONE_NAME)
    if code is WorkShiftCode.DAY:
        probe = datetime.combine(
            turn_date,
            datetime.min.time(),
            tzinfo=timezone_value,
        ) + timedelta(hours=_GROUP_CALENDAR.day_shift_start_hour)
    else:
        probe = datetime.combine(
            turn_date,
            datetime.min.time(),
            tzinfo=timezone_value,
        ) + timedelta(hours=_GROUP_CALENDAR.operational_day_start_hour)
    return _GROUP_CALENDAR.resolve_work_shift(probe)


# La paridad semanal respecto del anchor alterna G1/G3 o G2/G4 según el tipo de turno.
def _build_result(*, area: TurnArea, window: WorkShiftWindow) -> TurnCalendarResult:
    turn_date = window.start_local.date()
    rule = _ROTATION_RULES[area]
    if window.code is WorkShiftCode.DAY:
        week_index = (turn_date - rule.day_anchor).days // 7
        group = 'G1' if week_index % 2 == 0 else 'G3'
    else:
        week_index = (turn_date - rule.night_anchor).days // 7
        group = 'G2' if week_index % 2 == 0 else 'G4'
    return TurnCalendarResult(
        area=area,
        group=group,
        turn_code=window.code,
        turn_date=turn_date,
        turn_start_local=window.start_local,
        turn_end_local=window.end_local,
        turn_start_utc=window.start_utc,
        turn_end_utc=window.end_utc,
    )


def _normalize_area(area: TurnArea | str) -> TurnArea:
    if isinstance(area, TurnArea):
        return area
    try:
        return TurnArea(str(area).strip().lower())
    except ValueError as error:
        valid_values = ', '.join(item.value for item in TurnArea)
        raise ValueError(f'area must be one of: {valid_values}') from error


def _to_row(turn: TurnCalendarResult) -> dict[str, str]:
    return {
        'area': turn.area.value,
        'grupo': turn.group,
        'turno': turn.turn_code.value,
        'turn_date': turn.turn_date.isoformat(),
        'turn_start_local': turn.turn_start_local.isoformat(),
        'turn_end_local': turn.turn_end_local.isoformat(),
        'turn_start_utc': turn.turn_start_utc.isoformat(),
        'turn_end_utc': turn.turn_end_utc.isoformat(),
    }
