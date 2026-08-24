# Espejo pedagógico del WAL físico del Alarm Engine.
# El journal escribe registros JSONL append-only, fuerza flush y fsync antes de confirmar durable.
# Las posiciones físicas identifican segmento y byte_offset; sólo los bytes alcanzados por JournalHead.durable están confirmados.
# Las colas posteriores al durable pueden descartarse durante recovery porque nunca cruzaron la frontera de confirmación.
# Los segmentos se organizan por hora UTC y sólo se sellan cuando su contenido durable/materialized es consistente.
# Los comentarios se dejan en este bloque para no alterar las decisiones de formato de Ruff dentro de expresiones complejas.

from __future__ import annotations

import os
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path

from ada.alarms.persistence.errors import (
    AlarmPersistenceConflictError,
    AlarmPersistenceCorruptionError,
    AlarmPersistenceValidationError,
    AlarmPersistenceWriteError,
)
from ada.alarms.persistence.models import (
    EngineCommitRecord,
    JournalEntry,
    JournalPosition,
    segment_id_for_evaluated_at,
)
from ada.alarms.persistence.paths import AlarmPersistencePaths
from ada.alarms.persistence.serialization import decode_record_line, encode_record_line


class EngineJournal:
    def __init__(self, *, paths: AlarmPersistencePaths) -> None:
        if not isinstance(paths, AlarmPersistencePaths):
            raise TypeError('paths must be AlarmPersistencePaths')
        self._paths = paths

    def append_batch(self, records: Sequence[EngineCommitRecord]) -> tuple[JournalEntry, ...]:
        ordered = _validate_batch(records)
        segment_id = segment_id_for_evaluated_at(ordered[0].commit.evaluated_at)
        path = self._paths.journal_segment_path(segment_id, sealed=False)
        sealed_path = self._paths.journal_segment_path(segment_id, sealed=True)
        if sealed_path.exists():
            raise AlarmPersistenceConflictError('cannot append to a sealed journal segment')
        payloads = [encode_record_line(record.as_document()) for record in ordered]
        created = not path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open('ab') as file_handle:
                start = file_handle.tell()
                entries: list[JournalEntry] = []
                cursor = start
                for record, payload in zip(ordered, payloads, strict=True):
                    file_handle.write(payload)
                    end_offset = cursor + len(payload)
                    entries.append(
                        JournalEntry(
                            record=record,
                            start_offset=cursor,
                            end=JournalPosition(
                                segment_id=segment_id,
                                byte_offset=end_offset,
                                commit_id=record.commit.commit_id,
                            ),
                        )
                    )
                    cursor = end_offset
                file_handle.flush()
                os.fsync(file_handle.fileno())
            if created:
                _fsync_directory(path.parent)
        except OSError as error:
            raise AlarmPersistenceWriteError('could not append Alarm Engine WAL batch') from error
        return tuple(entries)

    def read_entries(
        self,
        *,
        after: JournalPosition | None,
        through: JournalPosition,
    ) -> tuple[JournalEntry, ...]:
        if after is not None and not isinstance(after, JournalPosition):
            raise TypeError('after must be a JournalPosition or None')
        if not isinstance(through, JournalPosition):
            raise TypeError('through must be a JournalPosition')
        if after is not None and _position_key(after) > _position_key(through):
            raise ValueError('after must not be ahead of through')
        segments = self.discover_segments()
        if through.segment_id not in segments:
            raise AlarmPersistenceCorruptionError('durable journal segment does not exist')
        selected_ids = [
            segment_id
            for segment_id in sorted(segments)
            if (after is None or segment_id >= after.segment_id)
            and segment_id <= through.segment_id
        ]
        entries: list[JournalEntry] = []
        for segment_id in selected_ids:
            path = segments[segment_id]
            start_offset = (
                after.byte_offset if after is not None and segment_id == after.segment_id else 0
            )
            limit = through.byte_offset if segment_id == through.segment_id else path.stat().st_size
            entries.extend(
                self._read_segment(
                    path=path,
                    segment_id=segment_id,
                    start_offset=start_offset,
                    end_offset=limit,
                )
            )
        if not entries:
            if after == through:
                return ()
            raise AlarmPersistenceCorruptionError(
                'journal range does not contain the expected record'
            )
        if entries[-1].end != through:
            raise AlarmPersistenceCorruptionError(
                'durable journal position is not a record boundary'
            )
        return tuple(entries)

    def validate_durable_region(self, durable: JournalPosition | None) -> tuple[JournalEntry, ...]:
        if durable is None:
            if self._discover_sealed_segments():
                raise AlarmPersistenceCorruptionError(
                    'sealed journal segments exist without a durable journal head'
                )
            return ()
        entries = self.read_entries(after=None, through=durable)
        previous_by_group: dict[str, str] = {}
        for entry in entries:
            commit = entry.record.commit
            expected_previous = previous_by_group.get(commit.priority_group)
            if commit.previous_commit_id != expected_previous:
                raise AlarmPersistenceCorruptionError(
                    'journal previous_commit_id chain is discontinuous'
                )
            previous_by_group[commit.priority_group] = commit.commit_id
        return entries

    def discard_unconfirmed_tail(self, durable: JournalPosition | None) -> int:
        open_segments = self._discover_open_segments()
        sealed_segments = self._discover_sealed_segments()
        if durable is None:
            if sealed_segments:
                raise AlarmPersistenceCorruptionError(
                    'sealed journal segments exist without a durable journal head'
                )
            removed = 0
            for path in open_segments.values():
                try:
                    removed += path.stat().st_size
                    path.unlink()
                    _fsync_directory(path.parent)
                except OSError as error:
                    raise AlarmPersistenceWriteError(
                        'could not discard unconfirmed journal tail'
                    ) from error
            self._remove_empty_journal_directories(self._paths.journal_open_root)
            return removed

        if any(segment_id > durable.segment_id for segment_id in sealed_segments):
            raise AlarmPersistenceCorruptionError('sealed journal exists beyond durable position')
        durable_path = self._resolve_segment_path(durable.segment_id)
        if durable_path is None:
            raise AlarmPersistenceCorruptionError('durable journal segment does not exist')
        try:
            durable_size = durable_path.stat().st_size
        except OSError as error:
            raise AlarmPersistenceCorruptionError(
                'could not inspect durable journal segment'
            ) from error
        if durable_size < durable.byte_offset:
            raise AlarmPersistenceCorruptionError('durable journal position exceeds segment size')
        removed = 0
        if durable_size > durable.byte_offset:
            if durable.segment_id in sealed_segments:
                raise AlarmPersistenceCorruptionError(
                    'sealed journal contains bytes beyond durable position'
                )
            try:
                with durable_path.open('r+b') as file_handle:
                    file_handle.truncate(durable.byte_offset)
                    file_handle.flush()
                    os.fsync(file_handle.fileno())
                removed += durable_size - durable.byte_offset
            except OSError as error:
                raise AlarmPersistenceWriteError(
                    'could not truncate unconfirmed journal tail'
                ) from error
        for segment_id, path in open_segments.items():
            if segment_id <= durable.segment_id:
                continue
            try:
                removed += path.stat().st_size
                path.unlink()
                _fsync_directory(path.parent)
            except OSError as error:
                raise AlarmPersistenceWriteError(
                    'could not discard unconfirmed journal segment'
                ) from error
        self._remove_empty_journal_directories(self._paths.journal_open_root)
        return removed

    def seal_before(self, segment_id: str) -> int:
        sealed_count = 0
        for candidate, source in sorted(self._discover_open_segments().items()):
            if candidate >= segment_id:
                continue
            destination = self._paths.journal_segment_path(candidate, sealed=True)
            if destination.exists():
                raise AlarmPersistenceCorruptionError(
                    'journal segment exists in open and sealed trees'
                )
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, destination)
                _fsync_directory(source.parent)
                _fsync_directory(destination.parent)
            except OSError as error:
                raise AlarmPersistenceWriteError('could not seal journal segment') from error
            sealed_count += 1
        self._remove_empty_journal_directories(self._paths.journal_open_root)
        return sealed_count

    def verify_append_position(
        self,
        *,
        durable: JournalPosition | None,
        segment_id: str,
    ) -> None:
        target = self._paths.journal_segment_path(segment_id, sealed=False)
        sealed_target = self._paths.journal_segment_path(segment_id, sealed=True)
        if sealed_target.exists():
            raise AlarmPersistenceCorruptionError('target journal segment is already sealed')
        if durable is None:
            if target.exists() and target.stat().st_size != 0:
                raise AlarmPersistenceCorruptionError('journal contains bytes without durable head')
            return
        if segment_id < durable.segment_id:
            raise AlarmPersistenceValidationError('journal cycle must not move backwards')
        if segment_id == durable.segment_id:
            if not target.exists():
                raise AlarmPersistenceCorruptionError('current durable journal segment is not open')
            if target.stat().st_size != durable.byte_offset:
                raise AlarmPersistenceCorruptionError(
                    'current journal size does not match durable position'
                )
            return
        if target.exists() and target.stat().st_size != 0:
            raise AlarmPersistenceCorruptionError('new journal segment already contains bytes')

    def discover_segments(self) -> dict[str, Path]:
        segments = self._discover_sealed_segments()
        for segment_id, path in self._discover_open_segments().items():
            if segment_id in segments:
                raise AlarmPersistenceCorruptionError(
                    'journal segment exists in open and sealed trees'
                )
            segments[segment_id] = path
        return segments

    def _resolve_segment_path(self, segment_id: str) -> Path | None:
        open_path = self._paths.journal_segment_path(segment_id, sealed=False)
        sealed_path = self._paths.journal_segment_path(segment_id, sealed=True)
        open_exists = open_path.exists()
        sealed_exists = sealed_path.exists()
        if open_exists and sealed_exists:
            raise AlarmPersistenceCorruptionError('journal segment exists in open and sealed trees')
        if open_exists:
            return open_path
        if sealed_exists:
            return sealed_path
        return None

    def _read_segment(
        self,
        *,
        path: Path,
        segment_id: str,
        start_offset: int,
        end_offset: int,
    ) -> list[JournalEntry]:
        if start_offset < 0 or end_offset < start_offset:
            raise AlarmPersistenceCorruptionError('journal byte range is invalid')
        try:
            size = path.stat().st_size
        except OSError as error:
            raise AlarmPersistenceCorruptionError('could not inspect journal segment') from error
        if end_offset > size:
            raise AlarmPersistenceCorruptionError('journal byte range exceeds segment size')
        entries: list[JournalEntry] = []
        try:
            with path.open('rb') as file_handle:
                file_handle.seek(start_offset)
                cursor = start_offset
                while cursor < end_offset:
                    line = file_handle.readline(end_offset - cursor)
                    if not line:
                        raise AlarmPersistenceCorruptionError(
                            'journal ended before the expected durable boundary'
                        )
                    new_cursor = cursor + len(line)
                    if new_cursor > end_offset or not line.endswith(b'\n'):
                        raise AlarmPersistenceCorruptionError(
                            'journal durable boundary splits a record'
                        )
                    payload = decode_record_line(line)
                    record = EngineCommitRecord.from_document(payload)
                    entries.append(
                        JournalEntry(
                            record=record,
                            start_offset=cursor,
                            end=JournalPosition(
                                segment_id=segment_id,
                                byte_offset=new_cursor,
                                commit_id=record.commit.commit_id,
                            ),
                        )
                    )
                    cursor = new_cursor
        except AlarmPersistenceCorruptionError:
            raise
        except OSError as error:
            raise AlarmPersistenceCorruptionError('could not read journal segment') from error
        return entries

    def _discover_open_segments(self) -> dict[str, Path]:
        return self._discover_under(self._paths.journal_open_root)

    def _discover_sealed_segments(self) -> dict[str, Path]:
        return self._discover_under(self._paths.journal_sealed_root)

    def _discover_under(self, root: Path) -> dict[str, Path]:
        if not root.exists():
            return {}
        segments: dict[str, Path] = {}
        for path in root.glob('year=*/month=*/day=*/hour=*/part-*.jsonl'):
            try:
                relative = path.relative_to(root)
                year = int(relative.parts[0].split('=', maxsplit=1)[1])
                month = int(relative.parts[1].split('=', maxsplit=1)[1])
                day = int(relative.parts[2].split('=', maxsplit=1)[1])
                hour = int(relative.parts[3].split('=', maxsplit=1)[1])
                part = int(path.stem.split('-', maxsplit=1)[1])
                segment_id = f'{year:04d}-{month:02d}-{day:02d}T{hour:02d}Z#{part:04d}'
                self._paths.journal_segment_path(segment_id, sealed=False)
            except (IndexError, TypeError, ValueError) as error:
                raise AlarmPersistenceCorruptionError('journal segment path is invalid') from error
            if segment_id in segments:
                raise AlarmPersistenceCorruptionError('duplicate journal segment was discovered')
            segments[segment_id] = path
        return segments

    def _remove_empty_journal_directories(self, root: Path) -> None:
        if not root.exists():
            return
        for directory in sorted(
            (path for path in root.rglob('*') if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            with suppress(OSError):
                directory.rmdir()


def _validate_batch(records: Sequence[EngineCommitRecord]) -> tuple[EngineCommitRecord, ...]:
    if isinstance(records, str | bytes) or not isinstance(records, Sequence):
        raise TypeError('records must be a sequence')
    if not records:
        raise AlarmPersistenceValidationError('records must not be empty')
    cycle_id: str | None = None
    groups: set[str] = set()
    normalized: list[EngineCommitRecord] = []
    segment_id: str | None = None
    for record in records:
        if not isinstance(record, EngineCommitRecord):
            raise TypeError('records must contain only EngineCommitRecord values')
        if cycle_id is None:
            cycle_id = record.commit.cycle_id
        elif record.commit.cycle_id != cycle_id:
            raise AlarmPersistenceValidationError('all records in a batch must share cycle_id')
        if record.commit.priority_group in groups:
            raise AlarmPersistenceValidationError(
                'a batch must contain at most one commit per priority_group'
            )
        groups.add(record.commit.priority_group)
        record_segment = segment_id_for_evaluated_at(record.commit.evaluated_at)
        if segment_id is None:
            segment_id = record_segment
        elif record_segment != segment_id:
            raise AlarmPersistenceValidationError(
                'all records in a batch must belong to the same UTC hour'
            )
        normalized.append(record)
    return tuple(
        sorted(normalized, key=lambda item: (item.commit.priority_group, item.commit.commit_id))
    )


def _position_key(value: JournalPosition) -> tuple[str, int]:
    return value.segment_id, value.byte_offset


def _fsync_directory(directory: Path) -> None:
    if os.name == 'nt':
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
