from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from atlanticus.datasets import (
    DatasetBatchResult,
    DatasetBatchStatus,
    DatasetDefinition,
    DatasetKey,
    DatasetPublicationFailure,
    DatasetPublicationResult,
    DatasetValidationError,
    MaterializationDefinition,
    PublicationQuality,
    PublicationSkipReason,
    PublicationStatus,
    SingleArtifactLayout,
)


@pytest.fixture
def targets():
    definition = DatasetDefinition(
        key=DatasetKey(namespace=('pi', 'pi-web-api', 'interpolated'), name='process'),
        materializations=(
            MaterializationDefinition(name='latest', layout=SingleArtifactLayout()),
            MaterializationDefinition(name='daily', layout=SingleArtifactLayout()),
            MaterializationDefinition(name='monthly', layout=SingleArtifactLayout()),
        ),
    )
    return tuple(
        definition.resolve_target(materialization=name) for name in ('latest', 'daily', 'monthly')
    )


def _confirmed_result(target, *, status=PublicationStatus.COMMITTED, warning_count=0):
    quality = PublicationQuality.WARNING if warning_count else PublicationQuality.SUCCESS
    return DatasetPublicationResult(
        target=target,
        status=status,
        quality=quality,
        finished_at_utc=datetime(2026, 7, 21, 12, 0, tzinfo=UTC),
        duration_ms=12.5,
        item_count=10,
        artifact_count=1,
        size_bytes=1024,
        content_signature='sha256:abc',
        warning_count=warning_count,
    )


def test_confirmed_result_normalizes_time_to_utc(targets) -> None:
    result = DatasetPublicationResult(
        target=targets[0],
        status=PublicationStatus.COMMITTED,
        quality=PublicationQuality.SUCCESS,
        finished_at_utc=datetime(
            2026,
            7,
            21,
            8,
            0,
            tzinfo=timezone(-timedelta(hours=4)),
        ),
        duration_ms=10,
        item_count=1,
        artifact_count=1,
    )

    assert result.finished_at_utc == datetime(2026, 7, 21, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize('status', [PublicationStatus.COMMITTED, PublicationStatus.UNCHANGED])
def test_empty_content_can_never_be_confirmed(targets, status: PublicationStatus) -> None:
    with pytest.raises(DatasetValidationError):
        DatasetPublicationResult(
            target=targets[0],
            status=status,
            quality=PublicationQuality.SUCCESS,
            finished_at_utc=datetime(2026, 7, 21, 12, 0, tzinfo=UTC),
            duration_ms=0,
            item_count=0,
            artifact_count=1,
        )


def test_empty_content_is_skipped_without_write_metadata(targets) -> None:
    result = DatasetPublicationResult.skipped_empty(
        target=targets[0],
        finished_at_utc=datetime(2026, 7, 21, 12, 0, tzinfo=UTC),
    )

    assert result.status is PublicationStatus.SKIPPED
    assert result.quality is PublicationQuality.WARNING
    assert result.skip_reason is PublicationSkipReason.EMPTY_CONTENT
    assert result.item_count == 0
    assert result.artifact_count == 0
    assert result.size_bytes is None
    assert result.content_signature is None


def test_skipped_result_rejects_any_evidence_of_a_write(targets) -> None:
    with pytest.raises(DatasetValidationError):
        DatasetPublicationResult(
            target=targets[0],
            status=PublicationStatus.SKIPPED,
            quality=PublicationQuality.WARNING,
            finished_at_utc=datetime(2026, 7, 21, 12, 0, tzinfo=UTC),
            duration_ms=0,
            item_count=0,
            artifact_count=0,
            size_bytes=0,
            warning_count=1,
            skip_reason=PublicationSkipReason.EMPTY_CONTENT,
        )


def test_batch_preserves_partial_results_and_summarizes_counts(targets) -> None:
    batch = DatasetBatchResult(
        publications=(
            _confirmed_result(targets[0]),
            _confirmed_result(
                targets[1],
                status=PublicationStatus.UNCHANGED,
                warning_count=1,
            ),
        ),
        failures=(
            DatasetPublicationFailure.from_exception(
                target=targets[2],
                error=OSError('write failed'),
                duration_ms=2,
            ),
        ),
    )

    assert batch.status is DatasetBatchStatus.WARNING
    assert batch.committed_count == 1
    assert batch.unchanged_count == 1
    assert batch.skipped_count == 0
    assert batch.warning_count == 1
    assert batch.failed_count == 1


def test_batch_is_failed_only_when_no_target_produced_a_result(targets) -> None:
    batch = DatasetBatchResult(
        failures=(
            DatasetPublicationFailure.from_exception(
                target=targets[0],
                error=OSError('write failed'),
            ),
        ),
    )

    assert batch.status is DatasetBatchStatus.FAILED


def test_failure_does_not_expose_the_exception_message(targets) -> None:
    failure = DatasetPublicationFailure.from_exception(
        target=targets[0],
        error=OSError('https://storage.example/data?sig=sensitive-token'),
    )

    assert failure.error_type == 'OSError'
    assert failure.message == 'dataset publication failed'
    assert 'sensitive-token' not in failure.message


def test_batch_rejects_duplicate_targets(targets) -> None:
    with pytest.raises(DatasetValidationError):
        DatasetBatchResult(
            publications=(_confirmed_result(targets[0]),),
            failures=(
                DatasetPublicationFailure.from_exception(
                    target=targets[0],
                    error=OSError('duplicate'),
                ),
            ),
        )
