from datetime import UTC, datetime

from ada.processes.dispatch.producer_state import DispatchProducerState
from atlanticus.connectivity.sql import SqlTableChangeMarker
from atlanticus.state import AtomicStateStore, StateKey


def _marker(token: str) -> SqlTableChangeMarker:
    return SqlTableChangeMarker(
        source_table='dbo.source',
        generation_token='generation',
        last_user_update_token=token,
        user_updates=1,
    )


def test_state_is_persisted_independently_per_source(tmp_path) -> None:
    store = AtomicStateStore(volume_path=tmp_path, application='ada')
    state = DispatchProducerState(
        store=store,
        clock=lambda: datetime(2026, 8, 17, 22, 0, tzinfo=UTC),
    )

    committed = state.commit_source(
        source_key='source_a',
        target_change_marker=_marker('target-1'),
        target_scope_token='scope',
        changed=True,
        source_last_update_utc=datetime(2026, 8, 17, 21, 59, tzinfo=UTC),
        publication_signatures={'dataset': 'signature'},
    )

    assert committed.revision == 1
    assert store.path_for(StateKey(namespace=('producers', 'dispatch'), name='source_a')).exists()
    assert DispatchProducerState(store=store).source_state('source_b').revision == 0


def test_marker_can_advance_without_material_revision(tmp_path) -> None:
    state = DispatchProducerState(store=AtomicStateStore(volume_path=tmp_path, application='ada'))

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
    assert second.last_synced_at_utc is not None


def test_signature_recovers_publication_committed_before_state(tmp_path) -> None:
    state = DispatchProducerState(store=AtomicStateStore(volume_path=tmp_path, application='ada'))

    recovered = state.commit_source(
        source_key='source',
        target_change_marker=_marker('target-1'),
        target_scope_token=None,
        changed=False,
        source_last_update_utc=None,
        publication_signatures={'dataset': 'already-published'},
    )

    assert recovered.revision == 1
