import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from ada.processes.pi_web_api import (
    PiProducerState,
    PiSourceState,
    PiWatermarkCoordinator,
    PiWebApiWatermarkError,
)
from atlanticus.state import AtomicStateStore


def _store(tmp_path: Path) -> AtomicStateStore:
    return AtomicStateStore(volume_path=tmp_path, application='ada')


def test_private_and_public_watermarks_use_separate_state_keys(tmp_path: Path) -> None:
    store = _store(tmp_path)
    producer = PiProducerState(store=store)
    source = PiSourceState(store=store)
    coordinator = PiWatermarkCoordinator(producer=producer, source=source)

    coordinator.commit_materialized(datetime(2026, 8, 14, 10, 9, 20, 987654, tzinfo=UTC))

    producer_path = tmp_path / 'ada' / '.runtime' / 'state' / 'producers' / 'pi-web-api.json'
    source_path = tmp_path / 'ada' / '.runtime' / 'state' / 'sources' / 'pi-web-api.json'
    producer_payload = json.loads(producer_path.read_text(encoding='utf-8'))['value']
    source_payload = json.loads(source_path.read_text(encoding='utf-8'))['value']
    assert producer_payload == {
        'producer': 'pi-web-api',
        'committed_watermark_utc': '2026-08-14T10:09:20Z',
    }
    assert source_payload == {
        'source': 'pi-web-api',
        'source_watermark_utc': '2026-08-14T10:09:20Z',
    }


def test_watermarks_reload_from_state_without_microseconds(tmp_path: Path) -> None:
    store = _store(tmp_path)
    PiWatermarkCoordinator(
        producer=PiProducerState(store=store),
        source=PiSourceState(store=store),
    ).commit_materialized(datetime(2026, 8, 14, 10, 9, 20, tzinfo=UTC))

    assert PiProducerState(store=store).current().committed_watermark_utc == datetime(
        2026, 8, 14, 10, 9, 20, tzinfo=UTC
    )
    assert PiSourceState(store=store).current().source_watermark_utc == datetime(
        2026, 8, 14, 10, 9, 20, tzinfo=UTC
    )


def test_watermarks_are_monotonic(tmp_path: Path) -> None:
    store = _store(tmp_path)
    producer = PiProducerState(store=store)
    source = PiSourceState(store=store)
    current = datetime(2026, 8, 14, 10, 9, 20, tzinfo=UTC)
    previous = current - timedelta(seconds=10)
    producer.commit(current)
    source.publish(current)

    with pytest.raises(PiWebApiWatermarkError, match='must not move backwards'):
        producer.commit(previous)
    with pytest.raises(PiWebApiWatermarkError, match='must not move backwards'):
        source.publish(previous)


def test_watermarks_reject_non_utc_values(tmp_path: Path) -> None:
    producer = PiProducerState(store=_store(tmp_path))

    with pytest.raises(PiWebApiWatermarkError, match='must use UTC'):
        producer.commit(datetime(2026, 8, 14, 10, 9, 20, tzinfo=timezone(timedelta(hours=-4))))
