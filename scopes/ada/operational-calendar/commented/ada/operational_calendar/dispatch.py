from __future__ import annotations

# Dispatch adapta exclusivamente el calendario Mina a ShiftId; la ventana por defecto actual es turno actual + anterior.
# Los comentarios explican intención y fronteras sin modificar estructura ni comportamiento.

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from ada.operational_calendar.calendar import (
    DAY_TURN,
    DEFAULT_TIMEZONE_NAME,
    MINE_CALENDAR,
    NIGHT_TURN,
)
from ada.operational_calendar.models import ShiftIdTurn, WorkShiftWindow

SHIFT_ID_TURN_WINDOW_SIZE = 2


def get_current_shift_id_turn(*, value: datetime | None = None) -> ShiftIdTurn:
    return _from_work_shift(MINE_CALENDAR.resolve_work_shift(value))


def get_previous_shift_id_turn(*, value: datetime | None = None) -> ShiftIdTurn:
    return _from_work_shift(MINE_CALENDAR.resolve_previous_work_shift(value))


# La ventana puede ampliarse explícitamente, pero Dispatch usa dos turnos por defecto.
def get_shift_id_turn_window(
    *,
    value: datetime | None = None,
    window_size: int = SHIFT_ID_TURN_WINDOW_SIZE,
) -> tuple[ShiftIdTurn, ...]:
    if not isinstance(window_size, int) or isinstance(window_size, bool) or window_size < 1:
        raise ValueError('window_size must be an integer greater than zero')
    turns = [get_current_shift_id_turn(value=value)]
    while len(turns) < window_size:
        turns.append(
            get_current_shift_id_turn(
                value=turns[-1].shift_start_utc - timedelta(microseconds=1),
            )
        )
    return tuple(turns)


def parse_shift_id_turn(value: int | str) -> ShiftIdTurn:
    normalized = str(value).strip()
    if not normalized.isdigit() or len(normalized) != 9:
        raise ValueError('shift identifier must contain exactly nine digits')
    operational_date = date(
        2000 + int(normalized[0:2]),
        int(normalized[2:4]),
        int(normalized[4:6]),
    )
    turn = normalized[6:9]
    timezone_value = ZoneInfo(DEFAULT_TIMEZONE_NAME)
    if turn == NIGHT_TURN:
        start_local = datetime.combine(
            operational_date - timedelta(days=1),
            time(hour=MINE_CALENDAR.operational_day_start_hour),
            tzinfo=timezone_value,
        )
        end_local = datetime.combine(
            operational_date,
            time(hour=MINE_CALENDAR.day_shift_start_hour),
            tzinfo=timezone_value,
        )
    elif turn == DAY_TURN:
        start_local = datetime.combine(
            operational_date,
            time(hour=MINE_CALENDAR.day_shift_start_hour),
            tzinfo=timezone_value,
        )
        end_local = datetime.combine(
            operational_date,
            time(hour=MINE_CALENDAR.operational_day_start_hour),
            tzinfo=timezone_value,
        )
    else:
        raise ValueError('shift identifier turn must be 001 or 002')
    return ShiftIdTurn(
        shift_id=int(normalized),
        shift_suffix=turn,
        nominal_date=operational_date,
        shift_start_local=start_local,
        shift_end_local=end_local,
        shift_start_utc=start_local.astimezone(UTC),
        shift_end_utc=end_local.astimezone(UTC),
    )


def _from_work_shift(window: WorkShiftWindow) -> ShiftIdTurn:
    shift_id = int(f'{window.operational_date:%y%m%d}{window.turn}')
    return ShiftIdTurn(
        shift_id=shift_id,
        shift_suffix=window.turn,
        nominal_date=window.operational_date,
        shift_start_local=window.start_local,
        shift_end_local=window.end_local,
        shift_start_utc=window.start_local.astimezone(UTC),
        shift_end_utc=window.end_local.astimezone(UTC),
    )
