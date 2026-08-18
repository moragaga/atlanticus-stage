from datetime import UTC, datetime

from atlanticus.connectivity.sql import SqlTableChangeMarker
from atlanticus.data_producers.core import SourceScope, SourceScopeItem
from atlanticus.data_producers.sql import (
    SqlDataProducerPlanner,
    SqlDataProducerReader,
    SqlProducerState,
    SqlSourceState,
)


class _Reader(SqlDataProducerReader):
    def __init__(self, markers):
        self.markers = markers
        self.calls = 0

    def read_change_markers(self, definitions, *, context=None):
        self.calls += 1
        return dict(self.markers)


class _State(SqlProducerState):
    def __init__(self, values):
        self.values = values

    def source_state(self, source_key):
        return self.values.get(source_key, SqlSourceState(source_key=source_key))


class _ScopeProvider:
    def __init__(self):
        self.calls = 0

    def capture(self, *, captured_at_utc):
        self.calls += 1
        return SourceScope(
            token='1|2',
            items=(
                SourceScopeItem(value=1, partition={'year': '2026', 'window': '1'}),
                SourceScopeItem(value=2, partition={'year': '2026', 'window': '2'}),
            ),
        )


def _marker(table: str, token: str) -> SqlTableChangeMarker:
    return SqlTableChangeMarker(
        source_table=table,
        generation_token='generation',
        last_user_update_token=token,
        user_updates=1,
    )


def test_planner_captures_marker_and_scope_once(scoped_definition) -> None:
    reader = _Reader({'source_scoped': _marker('dbo.source_scoped', 'new')})
    scope_provider = _ScopeProvider()
    planner = SqlDataProducerPlanner(
        reader=reader,
        producer_state=_State({}),
        scope_provider=scope_provider,
    )

    plan = planner.capture(
        (scoped_definition,),
        captured_at_utc=datetime(2026, 8, 18, 12, 0, tzinfo=UTC),
    )

    assert reader.calls == 1
    assert scope_provider.calls == 1
    assert plan.sources[0].scope_token == '1|2'
    assert plan.sources[0].scope.values == (1, 2)


def test_planner_skips_committed_marker_and_scope(scoped_definition) -> None:
    marker = _marker('dbo.source_scoped', 'same')
    state = SqlSourceState(
        source_key='source_scoped',
        source_change_marker=marker,
        source_scope_token='1|2',
    )
    planner = SqlDataProducerPlanner(
        reader=_Reader({'source_scoped': marker}),
        producer_state=_State({'source_scoped': state}),
        scope_provider=_ScopeProvider(),
    )

    plan = planner.capture(
        (scoped_definition,),
        captured_at_utc=datetime(2026, 8, 18, 12, 0, tzinfo=UTC),
    )

    assert plan.sources == ()


def test_planner_does_not_require_scope_provider_for_snapshot(snapshot_definition) -> None:
    planner = SqlDataProducerPlanner(
        reader=_Reader({'source_latest': _marker('dbo.source_latest', 'new')}),
        producer_state=_State({}),
    )

    plan = planner.capture(
        (snapshot_definition,),
        captured_at_utc=datetime(2026, 8, 18, 12, 0, tzinfo=UTC),
    )

    assert plan.sources[0].scope is None
