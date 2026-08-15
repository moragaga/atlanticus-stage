from __future__ import annotations

from datetime import UTC, datetime
from threading import Lock
from time import sleep
from types import SimpleNamespace

from ada.processes.pi_web_api.models import PiAcquisitionWindow, ResolvedPiTag
from ada.processes.pi_web_api.stress_io_benchmark import (
    _build_io_windows,
    _select_evenly_spaced,
    run_io_case,
)
from atlanticus.integrations.pi.contracts import (
    PiExtractionMode,
    PiMaterialization,
    PiTagDefinition,
    PiValueKind,
)


class _StreamSets:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.windows: list[tuple[datetime, datetime, int]] = []
        self.active = 0
        self.max_active = 0
        self._lock = Lock()

    def get_interpolated(
        self,
        web_ids,
        *,
        start_time_utc,
        end_time_utc,
        interpolation_seconds,
    ):
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.calls.append(tuple(web_ids))
            self.windows.append((start_time_utc, end_time_utc, interpolation_seconds))
        sleep(0.01)
        with self._lock:
            self.active -= 1
        return tuple({'WebId': web_id} for web_id in web_ids)


class _Client:
    def __init__(self) -> None:
        self.streamsets = _StreamSets()
        self.settings = SimpleNamespace(limits=SimpleNamespace(interpolated_max_web_ids=200))


def _tags(count: int) -> tuple[ResolvedPiTag, ...]:
    return tuple(
        ResolvedPiTag(
            definition=PiTagDefinition(
                tag_name=f'TAG_{index:03d}',
                alias=f'tag_{index:03d}',
                value_kind=PiValueKind.NUMBER,
                extraction_mode=PiExtractionMode.INTERPOLATED,
                materializations=(PiMaterialization.MONTHLY,),
            ),
            web_id=f'WEB_{index:03d}',
        )
        for index in range(count)
    )


def test_io_windows_compare_one_slot_against_one_hour() -> None:
    slot, hour = _build_io_windows(
        end_utc=datetime(2026, 8, 14, 23, 59, 50, tzinfo=UTC),
        interpolation_seconds=10,
    )

    assert slot.slot_count == 1
    assert slot.first_slot_utc == slot.last_slot_utc
    assert hour.slot_count == 360
    assert hour.first_slot_utc == datetime(2026, 8, 14, 23, 0, 0, tzinfo=UTC)
    assert hour.last_slot_utc == datetime(2026, 8, 14, 23, 59, 50, tzinfo=UTC)


def test_evenly_spaced_sample_covers_the_full_physical_catalog() -> None:
    tags = _tags(185)

    selected = _select_evenly_spaced(tags, 40)

    assert len(selected) == 40
    assert len({item.tag_name for item in selected}) == 40
    assert selected[0] is tags[0]
    assert selected[-1] is tags[-1]


def test_io_case_uses_five_chunks_and_up_to_three_workers() -> None:
    client = _Client()
    tags = _tags(185)
    window = PiAcquisitionWindow(
        first_slot_utc=datetime(2026, 8, 14, 23, 59, 50, tzinfo=UTC),
        last_slot_utc=datetime(2026, 8, 14, 23, 59, 50, tzinfo=UTC),
        interpolation_seconds=10,
    )

    result = run_io_case(
        client=client,
        tags=tags,
        window=window,
        axis='concurrency',
        window_kind='slot',
        chunk_limit=40,
        workers=3,
    )

    assert result.chunk_count == 5
    assert result.request_count == 5
    assert result.point_count == 185
    assert result.workers == 3
    assert result.timed_out_chunks == 0
    assert sorted(len(call) for call in client.streamsets.calls) == [25, 40, 40, 40, 40]
    assert 1 < client.streamsets.max_active <= 3
    assert set(client.streamsets.windows) == {
        (
            datetime(2026, 8, 14, 23, 59, 50, tzinfo=UTC),
            datetime(2026, 8, 15, 0, 0, 0, tzinfo=UTC),
            10,
        )
    }


def test_io_case_single_request_preserves_requested_width() -> None:
    client = _Client()
    tags = _tags(120)
    window = PiAcquisitionWindow(
        first_slot_utc=datetime(2026, 8, 14, 23, 0, 0, tzinfo=UTC),
        last_slot_utc=datetime(2026, 8, 14, 23, 59, 50, tzinfo=UTC),
        interpolation_seconds=10,
    )

    result = run_io_case(
        client=client,
        tags=tags,
        window=window,
        axis='width',
        window_kind='hour',
        chunk_limit=120,
        workers=1,
    )

    assert result.chunk_count == 1
    assert result.request_count == 1
    assert len(client.streamsets.calls) == 1
    assert len(client.streamsets.calls[0]) == 120
    assert client.streamsets.windows == [
        (
            datetime(2026, 8, 14, 23, 0, 0, tzinfo=UTC),
            datetime(2026, 8, 15, 0, 0, 0, tzinfo=UTC),
            10,
        )
    ]
