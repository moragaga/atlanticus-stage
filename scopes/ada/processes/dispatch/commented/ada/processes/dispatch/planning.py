# Fija el adaptador de turnos ADA sobre el planner común.
from ada.processes.dispatch.scope import DispatchShiftScopeProvider
from atlanticus.data_producers.sql import SqlDataProducerPlanner


class DispatchPlanner(SqlDataProducerPlanner):
    def __init__(self, *, reader, producer_state) -> None:
        super().__init__(
            reader=reader,
            producer_state=producer_state,
            scope_provider=DispatchShiftScopeProvider(),
        )
