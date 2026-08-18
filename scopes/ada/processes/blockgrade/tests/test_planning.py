from datetime import UTC, datetime

from ada.processes.blockgrade.extraction import BlockgradeSqlReader
from ada.processes.blockgrade.planning import BlockgradePlanner
from ada.processes.blockgrade.producer_state import BlockgradeProducerState, BlockgradeSourceState
from atlanticus.connectivity.sql import SqlTableChangeMarker


class _Reader(BlockgradeSqlReader):
    def __init__(self, markers):
        self.markers = markers
        self.calls = 0

    def read_change_markers(self, definitions, *, context=None):
        self.calls += 1
        return dict(self.markers)


class _State(BlockgradeProducerState):
    def __init__(self, values):
        self.values = values

    def source_state(self, source_key):
        return self.values.get(source_key, BlockgradeSourceState(source_key=source_key))


def _marker(table: str, token: str) -> SqlTableChangeMarker:
    return SqlTableChangeMarker(
        source_table=table,
        generation_token='generation',
        last_user_update_token=token,
        user_updates=1,
    )


def test_plan_captures_markers_once_and_uses_current_previous_shift(shift_definition) -> None:
    reader = _Reader({'source_shift': _marker('dbo.source_shift', 'new')})
    planner = BlockgradePlanner(reader=reader, producer_state=_State({}))

    plan = planner.capture(
        (shift_definition,),
        captured_at_utc=datetime(2026, 8, 17, 22, 0, tzinfo=UTC),
    )

    assert reader.calls == 1
    assert plan.sources[0].scope is not None
    assert plan.sources[0].scope.values == (260817002, 260817001)
    assert plan.sources[0].scope_token == '260817002|260817001'
    assert tuple(item.partition for item in plan.sources[0].scope.items) == (
        {'year': '2026', 'month': '08', 'day': '17', 'turn': '002'},
        {'year': '2026', 'month': '08', 'day': '17', 'turn': '001'},
    )


def test_plan_skips_source_when_marker_and_scope_are_committed(shift_definition) -> None:
    marker = _marker('dbo.source_shift', 'same')
    state = BlockgradeSourceState(
        source_key='source_shift',
        source_change_marker=marker,
        source_scope_token='260817002|260817001',
    )
    planner = BlockgradePlanner(
        reader=_Reader({'source_shift': marker}),
        producer_state=_State({'source_shift': state}),
    )

    plan = planner.capture(
        (shift_definition,),
        captured_at_utc=datetime(2026, 8, 17, 22, 0, tzinfo=UTC),
    )

    assert plan.sources == ()


def test_plan_prioritizes_sources_that_have_not_been_synchronized_recently(
    snapshot_definition,
) -> None:
    source_b = type(snapshot_definition)(
        source_key='source_b',
        source_table='dbo.source_b',
        storage_mode=snapshot_definition.storage_mode,
        load_strategy=snapshot_definition.load_strategy,
        materialization_name=snapshot_definition.materialization_name,
        columns=snapshot_definition.columns,
    )
    recently_synced = BlockgradeSourceState(
        source_key=snapshot_definition.source_key,
        source_change_marker=_marker(snapshot_definition.source_table, 'old-a'),
        last_synced_at_utc=datetime(2026, 8, 17, 21, 59, tzinfo=UTC),
    )
    never_synced = BlockgradeSourceState(
        source_key='source_b',
        source_change_marker=_marker('dbo.source_b', 'old-b'),
    )
    planner = BlockgradePlanner(
        reader=_Reader(
            {
                snapshot_definition.source_key: _marker(snapshot_definition.source_table, 'new-a'),
                'source_b': _marker('dbo.source_b', 'new-b'),
            }
        ),
        producer_state=_State(
            {
                snapshot_definition.source_key: recently_synced,
                'source_b': never_synced,
            }
        ),
    )

    plan = planner.capture(
        (snapshot_definition, source_b),
        captured_at_utc=datetime(2026, 8, 17, 22, 0, tzinfo=UTC),
    )

    assert tuple(item.definition.source_key for item in plan.sources) == (
        'source_b',
        snapshot_definition.source_key,
    )
