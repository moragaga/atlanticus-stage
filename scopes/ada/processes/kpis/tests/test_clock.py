from datetime import UTC, datetime

import pytest

from ada.data.core import DataSource
from ada.data.sources import PiSourceProvider
from ada.kpis.core import KpiWatermark
from ada.processes.kpis.clock import StatePiClock
from ada.processes.kpis.errors import KpiProcessWatermarkError
from atlanticus.state import AtomicStateStore, StateKey


def _store(tmp_path) -> AtomicStateStore:
    return AtomicStateStore(volume_path=tmp_path, application='ada')


def test_pi_web_api_clock_is_empty_until_source_watermark_exists(tmp_path) -> None:
    snapshot = StatePiClock(
        store=_store(tmp_path),
        provider=PiSourceProvider.PI_WEB_API,
    ).current()

    assert snapshot.watermark is None
    assert snapshot.source_watermarks[DataSource.PI_INTERPOLATED] is None
    assert snapshot.source_watermarks[DataSource.PI_RECORDED] is None


def test_pi_web_api_clock_uses_source_state_for_both_pi_views(tmp_path) -> None:
    store = _store(tmp_path)
    store.replace(
        StateKey(namespace=('sources',), name='pi-web-api'),
        {
            'source': 'pi-web-api',
            'source_watermark_utc': '2026-08-19T20:10:20Z',
        },
    )

    snapshot = StatePiClock(
        store=store,
        provider=PiSourceProvider.PI_WEB_API,
    ).current()

    expected = KpiWatermark(datetime(2026, 8, 19, 20, 10, 20, tzinfo=UTC))
    assert snapshot.watermark == expected
    assert snapshot.source_watermarks[DataSource.PI_INTERPOLATED] == expected
    assert snapshot.source_watermarks[DataSource.PI_RECORDED] == expected


def test_notpii_clock_is_driven_by_interpolated_stream_not_manifest_max(tmp_path) -> None:
    store = _store(tmp_path)
    store.replace(
        StateKey(namespace=('producers',), name='notpii'),
        {
            'producer': 'notpii',
            'revision': 3,
            'source_watermark_utc': '2026-08-19T20:12:00.000000Z',
            'last_change_at_utc': '2026-08-19T20:12:01.000000Z',
            'streams': {
                'interpolated': {
                    'revision': 2,
                    'source_watermark_utc': '2026-08-19T20:11:30.000000Z',
                    'last_change_at_utc': '2026-08-19T20:11:31.000000Z',
                },
                'recorded': {
                    'revision': 1,
                    'source_watermark_utc': '2026-08-19T20:12:00.000000Z',
                    'last_change_at_utc': '2026-08-19T20:12:01.000000Z',
                },
            },
        },
    )

    snapshot = StatePiClock(
        store=store,
        provider=PiSourceProvider.NOTPII,
    ).current()

    assert snapshot.watermark.text == '2026-08-19T20:11:30Z'
    assert snapshot.source_watermarks[DataSource.PI_INTERPOLATED].text == '2026-08-19T20:11:30Z'
    assert snapshot.source_watermarks[DataSource.PI_RECORDED].text == '2026-08-19T20:12:00Z'


def test_notpii_without_interpolated_stream_does_not_start_kpi_clock(tmp_path) -> None:
    store = _store(tmp_path)
    store.replace(
        StateKey(namespace=('producers',), name='notpii'),
        {
            'producer': 'notpii',
            'revision': 1,
            'source_watermark_utc': '2026-08-19T20:12:00.000000Z',
            'last_change_at_utc': '2026-08-19T20:12:01.000000Z',
            'streams': {
                'recorded': {
                    'revision': 1,
                    'source_watermark_utc': '2026-08-19T20:12:00.000000Z',
                    'last_change_at_utc': '2026-08-19T20:12:01.000000Z',
                }
            },
        },
    )

    snapshot = StatePiClock(
        store=store,
        provider=PiSourceProvider.NOTPII,
    ).current()

    assert snapshot.watermark is None
    assert snapshot.source_watermarks[DataSource.PI_RECORDED].text == '2026-08-19T20:12:00Z'


def test_clock_rejects_subsecond_kpi_watermark(tmp_path) -> None:
    store = _store(tmp_path)
    store.replace(
        StateKey(namespace=('sources',), name='pi-web-api'),
        {
            'source': 'pi-web-api',
            'source_watermark_utc': '2026-08-19T20:10:20.123456Z',
        },
    )

    with pytest.raises(KpiProcessWatermarkError, match='invalid'):
        StatePiClock(store=store, provider=PiSourceProvider.PI_WEB_API).current()
