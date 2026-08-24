from pathlib import Path

import pytest

from ada.alarms.persistence import AlarmPersistencePaths


def test_paths_follow_operational_volume_contract(tmp_path: Path) -> None:
    paths = AlarmPersistencePaths(shared_volume_path=tmp_path)

    assert paths.alarms_root == tmp_path / 'ada' / 'alarms'
    assert paths.journal_head_relative == Path('runtime/state/journal-head.json')
    assert paths.group_snapshot_relative('crusher_pressure') == Path(
        'runtime/state/groups/crusher_pressure.json'
    )
    assert (
        paths.journal_segment_path('2026-08-23T20Z#0000', sealed=False)
        == tmp_path
        / 'ada/alarms/runtime/journal/open/year=2026/month=08/day=23/hour=20/part-0000.jsonl'
    )


def test_paths_require_absolute_shared_volume() -> None:
    with pytest.raises(ValueError, match='absolute'):
        AlarmPersistencePaths(shared_volume_path='relative/path')


def test_snapshot_path_rejects_traversal(tmp_path: Path) -> None:
    paths = AlarmPersistencePaths(shared_volume_path=tmp_path)

    with pytest.raises(ValueError, match='safe path'):
        paths.group_snapshot_relative('../escape')
