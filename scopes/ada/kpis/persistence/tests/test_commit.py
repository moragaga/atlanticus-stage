import pytest

from ada.kpis.persistence import (
    KpiCommitStore,
    KpiEvaluationCommitter,
    KpiEvaluationRepository,
    KpiEvaluationWriteStatus,
    KpiLatestRepository,
)
from atlanticus.json import JsonDocumentStore, JsonWriteError
from tests.support import evaluation


class FailingLatestJsonStore(JsonDocumentStore):
    def replace(self, path, document) -> None:
        if str(path).endswith('/datasets/kpis/latest/data.json'):
            raise JsonWriteError('latest failed')
        super().replace(path, document)


def test_committer_publishes_evaluation_latest_then_watermark(
    json_store, paths, state_store
) -> None:
    evaluations = KpiEvaluationRepository(store=json_store, paths=paths)
    latest = KpiLatestRepository(store=json_store, paths=paths)
    state = KpiCommitStore(store=state_store)
    committer = KpiEvaluationCommitter(evaluations=evaluations, latest=latest, state=state)
    value = evaluation(20)

    status = committer.commit(value)

    assert status is KpiEvaluationWriteStatus.CREATED
    assert evaluations.read(value.watermark) == value
    assert latest.read() == value
    assert state.read_watermark() == value.watermark


def test_committer_does_not_advance_watermark_when_latest_fails(paths, state_store) -> None:
    evaluations = KpiEvaluationRepository(store=JsonDocumentStore(), paths=paths)
    latest = KpiLatestRepository(store=FailingLatestJsonStore(), paths=paths)
    state = KpiCommitStore(store=state_store)
    committer = KpiEvaluationCommitter(evaluations=evaluations, latest=latest, state=state)
    value = evaluation(20)

    with pytest.raises(JsonWriteError, match='latest failed'):
        committer.commit(value)

    assert evaluations.read(value.watermark) == value
    assert state.read_watermark() is None


def test_committer_retry_reuses_immutable_evaluation_and_finishes_commit(
    json_store,
    paths,
    state_store,
) -> None:
    evaluations = KpiEvaluationRepository(store=json_store, paths=paths)
    state = KpiCommitStore(store=state_store)
    value = evaluation(20)
    evaluations.write_once(value)
    committer = KpiEvaluationCommitter(
        evaluations=evaluations,
        latest=KpiLatestRepository(store=json_store, paths=paths),
        state=state,
    )

    status = committer.commit(value)

    assert status is KpiEvaluationWriteStatus.UNCHANGED
    assert state.read_watermark() == value.watermark
