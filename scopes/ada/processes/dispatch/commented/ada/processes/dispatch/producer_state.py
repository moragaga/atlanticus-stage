# Fija únicamente el producer_key Dispatch sobre el state común.
from atlanticus.data_producers.sql import SqlProducerState, SqlSourceState, marker_changed

DispatchSourceState = SqlSourceState


class DispatchProducerState(SqlProducerState):
    def __init__(self, *, store, clock=None) -> None:
        super().__init__(store=store, producer_key='dispatch', clock=clock)


__all__ = ['DispatchProducerState', 'DispatchSourceState', 'marker_changed']
