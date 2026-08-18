# Fija el adaptador de turnos ADA sobre el planner común.
from ada.processes.blockgrade.scope import BlockgradeShiftScopeProvider
from atlanticus.data_producers.sql import SqlDataProducerPlanner


class BlockgradePlanner(SqlDataProducerPlanner):
    def __init__(self, *, reader, producer_state) -> None:
        super().__init__(
            reader=reader,
            producer_state=producer_state,
            scope_provider=BlockgradeShiftScopeProvider(),
        )
