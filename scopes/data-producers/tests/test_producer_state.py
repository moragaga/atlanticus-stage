from datetime import UTC, datetime

from atlanticus.connectivity.sql import SqlTableChangeMarker
from atlanticus.data_producers.sql import SqlProducerState
from atlanticus.state import AtomicStateStore, StateKey


def _marker(token: str) -> SqlTableChangeMarker:
    return SqlTableChangeMarker(
        source_table='dbo.source',
        generation_token='generation',
        last_user_update_token=token,
        user_updates=1,
    )


def test_state_uses_configured_producer_namespace(tmp_path) -> None:
    store = AtomicStateStore(volume_path=tmp_path, application='app')
    state = SqlProducerState(
        store=store,
        producer_key='producer',
        clock=lambda: datetime(2026, 8, 18, 12, 0, tzinfo=UTC),
    )

    committed = state.commit_source(
        source_key='source_a',
        target_change_marker=_marker('target-1'),
        target_scope_token='scope',
        changed=True,
        source_last_update_utc=None,
        publication_signatures={'dataset': 'signature'},
    )

    assert committed.revision == 1
    assert store.path_for(StateKey(namespace=('producers', 'producer'), name='source_a')).exists()


def test_marker_can_advance_without_material_revision(tmp_path) -> None:
    state = SqlProducerState(
        store=AtomicStateStore(volume_path=tmp_path, application='app'),
        producer_key='producer',
    )

    first = state.commit_source(
        source_key='source',
        target_change_marker=_marker('target-1'),
        target_scope_token=None,
        changed=True,
        source_last_update_utc=None,
        publication_signatures={},
    )
    second = state.commit_source(
        source_key='source',
        target_change_marker=_marker('target-2'),
        target_scope_token=None,
        changed=False,
        source_last_update_utc=None,
        publication_signatures={},
    )

    assert first.revision == second.revision == 1
    assert second.source_change_marker.last_user_update_token == 'target-2'


def test_signature_recovers_commit_before_state(tmp_path) -> None:
    state = SqlProducerState(
        store=AtomicStateStore(volume_path=tmp_path, application='app'),
        producer_key='producer',
    )

    recovered = state.commit_source(
        source_key='source',
        target_change_marker=_marker('target-1'),
        target_scope_token=None,
        changed=False,
        source_last_update_utc=None,
        publication_signatures={'dataset': 'already-published'},
    )

    assert recovered.revision == 1
