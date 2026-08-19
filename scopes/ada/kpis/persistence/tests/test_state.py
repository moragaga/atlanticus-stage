import pytest

from ada.kpis.persistence import (
    KpiCommitStore,
    KpiPersistenceCorruptionError,
    KpiWatermarkRegressionError,
)
from atlanticus.state import StateKey
from tests.support import watermark


def test_commit_store_starts_without_watermark(state_store) -> None:
    store = KpiCommitStore(store=state_store)

    assert store.read_watermark() is None


def test_commit_store_commits_and_reads_watermark(state_store) -> None:
    store = KpiCommitStore(store=state_store)

    assert store.commit_watermark(watermark(20)) == watermark(20)
    assert store.read_watermark() == watermark(20)


def test_commit_store_same_watermark_is_idempotent(state_store) -> None:
    store = KpiCommitStore(store=state_store)
    store.commit_watermark(watermark(20))

    assert store.commit_watermark(watermark(20)) == watermark(20)


def test_commit_store_rejects_regression(state_store) -> None:
    store = KpiCommitStore(store=state_store)
    store.commit_watermark(watermark(30))

    with pytest.raises(KpiWatermarkRegressionError, match='must not move backwards'):
        store.commit_watermark(watermark(20))


def test_commit_store_rejects_unexpected_state_fields(state_store) -> None:
    state_store.replace(
        StateKey(namespace=('kpis',), name='committed-watermark'),
        {'watermark_utc': watermark(20).text, 'unexpected': True},
    )
    store = KpiCommitStore(store=state_store)

    with pytest.raises(KpiPersistenceCorruptionError, match='unexpected or missing fields'):
        store.read_watermark()
