from atlanticus.data_producers.sql import SqlProducerState, SqlSourceState, marker_changed

BlockgradeSourceState = SqlSourceState


class BlockgradeProducerState(SqlProducerState):
    def __init__(self, *, store, clock=None) -> None:
        super().__init__(store=store, producer_key='blockgrade', clock=clock)


__all__ = ['BlockgradeProducerState', 'BlockgradeSourceState', 'marker_changed']
