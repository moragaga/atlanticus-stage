from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from test_poc_dispatch_operational_day import _part, _target

from atlanticus.datasets import DatasetDefinition
from atlanticus.datasets.parquet import ParquetDatasetStore


def test_cleanup_removes_only_owned_artifacts_older_than_grace(
    tmp_path: Path,
    clock: datetime,
    dispatch_definition: DatasetDefinition,
) -> None:
    store = ParquetDatasetStore(
        root=tmp_path,
        clock=lambda: clock,
        orphan_grace=timedelta(minutes=10),
    )
    target = _target(dispatch_definition)
    store.publish_parts(
        definition=dispatch_definition,
        target=target,
        incoming_parts=(
            _part(
                dispatch_definition,
                target,
                shift_id='26199001',
                tonnage=(100.0,),
            ),
        ),
    )
    target_path = store.path_for(definition=dispatch_definition, target=target)
    first_manifest = json.loads((target_path / 'current.json').read_text(encoding='utf-8'))
    orphan = target_path / first_manifest['parts'][0]['path']
    store.publish_parts(
        definition=dispatch_definition,
        target=target,
        incoming_parts=(
            _part(
                dispatch_definition,
                target,
                shift_id='26199001',
                tonnage=(150.0,),
            ),
        ),
    )
    temporary = target_path / f'.current.json.{"a" * 32}.tmp'
    temporary.write_bytes(b'incomplete')
    unrelated = target_path / '.manual.tmp'
    unrelated.write_bytes(b'must remain')
    old_timestamp = (clock - timedelta(minutes=20)).timestamp()
    os.utime(orphan, (old_timestamp, old_timestamp))
    os.utime(temporary, (old_timestamp, old_timestamp))

    result = store.cleanup(definition=dispatch_definition, target=target)

    assert result.orphan_part_count == 1
    assert result.temporary_count == 1
    assert result.reclaimed_bytes > 0
    assert not orphan.exists()
    assert not temporary.exists()
    assert unrelated.read_bytes() == b'must remain'
    current = json.loads((target_path / 'current.json').read_text(encoding='utf-8'))
    assert all((target_path / part['path']).exists() for part in current['parts'])
