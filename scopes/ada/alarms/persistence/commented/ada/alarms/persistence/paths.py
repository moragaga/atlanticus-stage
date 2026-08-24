# Layout físico acordado bajo <shared-volume>/ada/alarms sin decidir la ruta absoluta desde ADA.
from __future__ import annotations

from pathlib import Path

from ada.alarms.persistence.models import parse_segment_id


class AlarmPersistencePaths:
    def __init__(self, *, shared_volume_path: str | Path) -> None:
        path = Path(shared_volume_path)
        if not path.is_absolute():
            raise ValueError('shared_volume_path must be an absolute path')
        self._shared_volume_path = path
        self._alarms_root = path / 'ada' / 'alarms'

    @property
    def shared_volume_path(self) -> Path:
        return self._shared_volume_path

    @property
    def alarms_root(self) -> Path:
        return self._alarms_root

    @property
    def journal_head_relative(self) -> Path:
        return Path('runtime/state/journal-head.json')

    def group_snapshot_relative(self, priority_group: str) -> Path:
        return Path('runtime/state/groups') / f'{_require_priority_group(priority_group)}.json'

    @property
    def journal_open_root(self) -> Path:
        return self._alarms_root / 'runtime' / 'journal' / 'open'

    @property
    def journal_sealed_root(self) -> Path:
        return self._alarms_root / 'runtime' / 'journal' / 'sealed'

    def journal_segment_path(self, segment_id: str, *, sealed: bool) -> Path:
        year, month, day, hour, part = parse_segment_id(segment_id)
        root = self.journal_sealed_root if sealed else self.journal_open_root
        return (
            root
            / f'year={year:04d}'
            / f'month={month:02d}'
            / f'day={day:02d}'
            / f'hour={hour:02d}'
            / f'part-{part:04d}.jsonl'
        )


def _require_priority_group(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError('priority_group must be a string')
    normalized = value.strip()
    if not normalized:
        raise ValueError('priority_group must not be empty')
    if normalized in {'.', '..'} or '/' in normalized or '\\' in normalized or '\x00' in normalized:
        raise ValueError('priority_group must be a safe path segment')
    return normalized
