from __future__ import annotations

from datetime import datetime

from ada.operational_calendar import get_shift_id_turn_window
from atlanticus.data_producers.core import SourceScope, SourceScopeItem

_SHIFT_WINDOW_SIZE = 2


class BlockgradeShiftScopeProvider:
    def capture(self, *, captured_at_utc: datetime) -> SourceScope:
        turns = get_shift_id_turn_window(
            value=captured_at_utc,
            window_size=_SHIFT_WINDOW_SIZE,
        )
        return SourceScope(
            token='|'.join(str(turn.shift_id) for turn in turns),
            items=tuple(
                SourceScopeItem(value=turn.shift_id, partition=turn.partition) for turn in turns
            ),
        )
