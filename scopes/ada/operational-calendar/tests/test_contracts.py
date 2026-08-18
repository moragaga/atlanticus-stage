from __future__ import annotations

from ada.operational_calendar import MINE_CALENDAR, OperationalCalendarResolver


def test_operational_calendar_implements_resolver_contract() -> None:
    assert isinstance(MINE_CALENDAR, OperationalCalendarResolver)
