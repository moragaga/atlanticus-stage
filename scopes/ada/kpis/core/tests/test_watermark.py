from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from ada.kpis.core import KpiWatermark


def test_watermark_normalizes_aware_timestamp_to_utc() -> None:
    chile = timezone(-timedelta(hours=4))
    watermark = KpiWatermark(datetime(2026, 8, 19, 14, 15, 20, tzinfo=chile))

    assert watermark.timestamp_utc == datetime(2026, 8, 19, 18, 15, 20, tzinfo=UTC)
    assert watermark.text == '2026-08-19T18:15:20Z'
    assert watermark.filename_token == '20260819T181520Z'


def test_watermark_round_trips_document() -> None:
    original = KpiWatermark(datetime(2026, 8, 19, 18, 15, 20, tzinfo=UTC))

    assert KpiWatermark.from_document(original.as_document()) == original
    assert KpiWatermark.parse(original.text) == original


def test_watermark_requires_timezone_and_second_precision() -> None:
    with pytest.raises(ValueError, match='timezone-aware'):
        KpiWatermark(datetime(2026, 8, 19, 18, 15, 20))

    with pytest.raises(ValueError, match='second precision'):
        KpiWatermark(datetime(2026, 8, 19, 18, 15, 20, 1, tzinfo=UTC))
