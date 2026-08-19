from __future__ import annotations

import json

import pytest

from ada.kpis.persistence import (
    KpiEvaluationConflictError,
    KpiEvaluationRepository,
    KpiEvaluationWriteStatus,
    KpiPersistenceCorruptionError,
)
from tests.support import evaluation, watermark


def test_evaluation_repository_write_once_round_trips(json_store, paths) -> None:
    repository = KpiEvaluationRepository(store=json_store, paths=paths)
    value = evaluation(20)

    assert repository.write_once(value) is KpiEvaluationWriteStatus.CREATED
    assert repository.read(value.watermark) == value


def test_evaluation_repository_same_content_is_idempotent(json_store, paths) -> None:
    repository = KpiEvaluationRepository(store=json_store, paths=paths)
    value = evaluation(20)

    repository.write_once(value)

    assert repository.write_once(value) is KpiEvaluationWriteStatus.UNCHANGED


def test_evaluation_repository_rejects_different_content_for_same_watermark(
    json_store,
    paths,
) -> None:
    repository = KpiEvaluationRepository(store=json_store, paths=paths)
    repository.write_once(evaluation(20, value=10.0))

    with pytest.raises(KpiEvaluationConflictError, match='different content'):
        repository.write_once(evaluation(20, value=11.0))


def test_evaluation_repository_missing_read_returns_none(json_store, paths) -> None:
    repository = KpiEvaluationRepository(store=json_store, paths=paths)

    assert repository.read(watermark(20)) is None


def test_evaluation_repository_iterates_actual_snapshots_in_watermark_order(
    json_store,
    paths,
) -> None:
    repository = KpiEvaluationRepository(store=json_store, paths=paths)
    repository.write_once(evaluation(40))
    repository.write_once(evaluation(10))
    repository.write_once(evaluation(30))

    values = tuple(repository.iter_between())

    assert [item.watermark for item in values] == [watermark(10), watermark(30), watermark(40)]


def test_evaluation_repository_iter_between_uses_exclusive_after_and_inclusive_through(
    json_store,
    paths,
) -> None:
    repository = KpiEvaluationRepository(store=json_store, paths=paths)
    for second in (10, 20, 30, 40):
        repository.write_once(evaluation(second))

    values = tuple(repository.iter_between(after=watermark(10), through=watermark(30)))

    assert [item.watermark for item in values] == [watermark(20), watermark(30)]


def test_evaluation_repository_does_not_infer_missing_ticks(json_store, paths) -> None:
    repository = KpiEvaluationRepository(store=json_store, paths=paths)
    repository.write_once(evaluation(10))
    repository.write_once(evaluation(40))

    values = tuple(repository.iter_between())

    assert [item.watermark for item in values] == [watermark(10), watermark(40)]


def test_evaluation_repository_detects_document_watermark_mismatch(json_store, paths) -> None:
    repository = KpiEvaluationRepository(store=json_store, paths=paths)
    expected = watermark(20)
    path = paths.evaluation_path(expected)
    json_store.replace(path, evaluation(30).as_document())

    with pytest.raises(KpiPersistenceCorruptionError, match='does not match its path'):
        repository.read(expected)


def test_evaluation_repository_detects_invalid_document(json_store, paths) -> None:
    repository = KpiEvaluationRepository(store=json_store, paths=paths)
    path = paths.evaluation_path(watermark(20))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'watermark_utc': 'bad'}), encoding='utf-8')

    with pytest.raises(KpiPersistenceCorruptionError, match='persisted KPI evaluation is invalid'):
        repository.read(watermark(20))


def test_iter_between_rejects_inverted_bounds(json_store, paths) -> None:
    repository = KpiEvaluationRepository(store=json_store, paths=paths)

    with pytest.raises(ValueError, match='after must not be greater'):
        tuple(repository.iter_between(after=watermark(30), through=watermark(20)))
