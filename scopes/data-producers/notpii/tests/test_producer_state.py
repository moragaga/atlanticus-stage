from datetime import UTC, datetime

from atlanticus.data_producers.notpii.producer_state import (
    NotPiiProducerState,
    NotPiiStreamObservation,
)
from atlanticus.integrations.pi.contracts import PiExtractionMode
from atlanticus.state import AtomicStateStore


def _state(tmp_path, now: datetime) -> NotPiiProducerState:
    return NotPiiProducerState(
        store=AtomicStateStore(volume_path=tmp_path, application='ada'),
        clock=lambda: now,
    )


def test_stream_watermarks_are_independent_and_global_watermark_never_recedes(tmp_path) -> None:
    now = datetime(2026, 8, 15, 12, 30, tzinfo=UTC)
    state = _state(tmp_path, now)
    later = datetime(2026, 8, 15, 12, 20, tzinfo=UTC)
    earlier = datetime(2026, 8, 15, 11, 0, tzinfo=UTC)

    state.advance(
        {
            PiExtractionMode.INTERPOLATED: NotPiiStreamObservation(
                source_last_updated_at_utc=later,
                changed=True,
            )
        }
    )
    current = state.advance(
        {
            PiExtractionMode.RECORDED: NotPiiStreamObservation(
                source_last_updated_at_utc=earlier,
                changed=False,
            )
        }
    )

    assert current.source_watermark_utc == later
    assert current.streams[PiExtractionMode.INTERPOLATED].source_watermark_utc == later
    assert current.streams[PiExtractionMode.RECORDED].source_watermark_utc == earlier
    assert current.revision == 1


def test_state_is_recovered_after_process_restart(tmp_path) -> None:
    now = datetime(2026, 8, 15, 12, 30, tzinfo=UTC)
    watermark = datetime(2026, 8, 15, 12, 20, tzinfo=UTC)
    first = _state(tmp_path, now)
    first.advance(
        {
            PiExtractionMode.INTERPOLATED: NotPiiStreamObservation(
                source_last_updated_at_utc=watermark,
                changed=True,
            )
        }
    )

    recovered = _state(tmp_path, datetime(2026, 8, 15, 13, 0, tzinfo=UTC)).current()

    assert recovered.revision == 1
    assert recovered.source_watermark_utc == watermark
    assert recovered.streams[PiExtractionMode.INTERPOLATED].revision == 1
