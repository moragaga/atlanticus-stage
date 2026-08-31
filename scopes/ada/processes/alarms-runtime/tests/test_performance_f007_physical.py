from __future__ import annotations

import hashlib
import io
import json
import tokenize
from pathlib import Path

import pytest

from ada.data.core import DataPartition, DataSource
from performance import f007_physical
from performance.baseline import (
    _F007_PHYSICAL_WARM,
    BaselineScenario,
    _build_contracts,
    _signal_columns,
    build_baseline_runtime,
)
from performance.f007_physical import (
    CgroupIoCacheSnapshot,
    F007DatasetBank,
    F007PhysicalDataSourceLoader,
    build_f007_latest_plan,
    build_f007_partitioned_plan,
    f007_latest_signal_columns,
    f007_signal_column_for_alarm,
)


def test_f007_physical_alarm_mapping_reuses_the_fixed_signal_pool() -> None:
    assert f007_signal_column_for_alarm(0) == 'signal_000001'
    assert f007_signal_column_for_alarm(999) == 'signal_001000'
    assert f007_signal_column_for_alarm(1000) == 'signal_000001'
    assert f007_signal_column_for_alarm(2499) == 'signal_000500'
    assert len(f007_latest_signal_columns()) == 1000


def test_f007_physical_baseline_contract_keeps_dataset_width_constant() -> None:
    scenario = BaselineScenario(
        test_id='F-007',
        alarm_count=2500,
        data_profile=_F007_PHYSICAL_WARM,
    )

    contracts = _build_contracts(scenario)

    assert len(contracts) == 1000
    assert contracts[0].requirements[0].source is DataSource.PI_INTERPOLATED
    assert contracts[0].requirements[0].partition is DataPartition.LATEST
    assert contracts[0].requirements[0].column_names == ('signal_000001',)
    assert contracts[-1].requirements[0].column_names == ('signal_001000',)
    assert _signal_columns(0, scenario=scenario) == ('signal_000001',)
    assert _signal_columns(1000, scenario=scenario) == ('signal_000001',)


def test_f007_physical_session_merges_reused_alarm_signals_to_1000_columns(
    tmp_path: Path,
) -> None:
    scenario = BaselineScenario(
        test_id='F-007',
        alarm_count=1001,
        data_profile=_F007_PHYSICAL_WARM,
    )
    source_loader = F007PhysicalDataSourceLoader(loader=None, reader=None)

    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
        source_loader_override=source_loader,
    )

    view = runtime.revision.session.data_plan.views[0]
    assert len(view.column_names) == 1000
    assert runtime.revision.session.entries[0].requirements[0].column_names == ('signal_000001',)
    assert runtime.revision.session.entries[999].requirements[0].column_names == ('signal_001000',)
    assert runtime.revision.session.entries[1000].requirements[0].column_names == ('signal_000001',)


def test_f007_physical_plans_match_the_frozen_profiles() -> None:
    latest = build_f007_latest_plan()
    partitioned = build_f007_partitioned_plan()

    assert len(latest.views) == 1
    assert latest.views[0].source is DataSource.PI_INTERPOLATED
    assert latest.views[0].partition is DataPartition.LATEST
    assert len(latest.views[0].column_names) == 1000

    assert tuple((view.source, view.partition) for view in partitioned.views) == (
        (DataSource.PI_INTERPOLATED, DataPartition.DAILY),
        (DataSource.DISPATCH_STD_SHIFT_STATE, DataPartition.SHIFT),
    )
    assert partitioned.views[0].column_names == ('signal_000001', 'signal_000002')
    assert len(partitioned.views[0].time_windows) == 1
    assert len(partitioned.views[1].column_names) == 24
    assert len(partitioned.views[1].shifts) == 1


def test_f007_bank_preflight_reads_control_files_without_opening_parquet(
    tmp_path: Path, monkeypatch
) -> None:
    manifest = _accepted_manifest_document()
    conformance = _accepted_conformance_document()
    manifest_path = tmp_path / 'manifest.json'
    conformance_path = tmp_path / 'conformance.json'
    input_root = tmp_path / 'input'
    input_root.mkdir()
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding='utf-8')
    conformance_path.write_text(json.dumps(conformance, sort_keys=True), encoding='utf-8')

    monkeypatch.setattr(
        f007_physical,
        '_ACCEPTED_MANIFEST_SHA256',
        hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(
        f007_physical,
        '_ACCEPTED_CONFORMANCE_SHA256',
        hashlib.sha256(conformance_path.read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(f007_physical, '_ACCEPTED_AGGREGATE_SHA256', 'a' * 64)
    monkeypatch.setattr(f007_physical, '_ACCEPTED_BANK_SHA256', 'b' * 64)

    bank = F007DatasetBank.load(
        manifest_path=manifest_path,
        conformance_path=conformance_path,
        input_root=input_root,
        require_read_only=False,
    )

    assert bank.dataset_bank_id == 'f007-controlled-physical-bank-v1'
    assert bank.latest_path == 'datasets/pi/not_pii/interpolated/latest/data.parquet'
    assert len(bank.windows) == 61
    assert not tuple(input_root.rglob('*.parquet'))


def test_f007_cgroup_snapshot_reads_io_cache_and_throttling_metrics(tmp_path: Path) -> None:
    (tmp_path / 'memory.current').write_text('1234\n', encoding='utf-8')
    (tmp_path / 'memory.stat').write_text(
        'anon 100\nfile 200\nactive_file 80\ninactive_file 120\n', encoding='utf-8'
    )
    (tmp_path / 'io.stat').write_text(
        '8:0 rbytes=1000 wbytes=0 rios=10 wios=0\n8:16 rbytes=500 wbytes=0 rios=5 wios=0\n',
        encoding='utf-8',
    )
    (tmp_path / 'cpu.stat').write_text(
        'usage_usec 9000\nnr_throttled 3\nthrottled_usec 250\n', encoding='utf-8'
    )

    sample = CgroupIoCacheSnapshot.read(root=tmp_path)

    assert sample.memory_current == 1234
    assert sample.memory_anon == 100
    assert sample.memory_file == 200
    assert sample.memory_active_file == 80
    assert sample.memory_inactive_file == 120
    assert sample.io_read_bytes == 1500
    assert sample.io_read_operations == 15
    assert sample.cpu_usage_usec == 9000
    assert sample.cpu_nr_throttled == 3
    assert sample.cpu_throttled_usec == 250


def test_f007_cgroup_snapshot_accepts_empty_io_stat_as_zero_baseline(tmp_path: Path) -> None:
    (tmp_path / 'memory.current').write_text('1234\n', encoding='utf-8')
    (tmp_path / 'memory.stat').write_text(
        'anon 100\nfile 200\nactive_file 80\ninactive_file 120\n', encoding='utf-8'
    )
    (tmp_path / 'io.stat').write_text('', encoding='utf-8')
    (tmp_path / 'cpu.stat').write_text(
        'usage_usec 9000\nnr_throttled 0\nthrottled_usec 0\n', encoding='utf-8'
    )

    sample = CgroupIoCacheSnapshot.read(root=tmp_path)

    assert sample.io_read_bytes == 0
    assert sample.io_read_operations == 0


def test_f007_cgroup_snapshot_rejects_nonempty_io_stat_without_read_counters(
    tmp_path: Path,
) -> None:
    (tmp_path / 'memory.current').write_text('1234\n', encoding='utf-8')
    (tmp_path / 'memory.stat').write_text(
        'anon 100\nfile 200\nactive_file 80\ninactive_file 120\n', encoding='utf-8'
    )
    (tmp_path / 'io.stat').write_text('8:0 dbytes=1 dios=1\n', encoding='utf-8')
    (tmp_path / 'cpu.stat').write_text(
        'usage_usec 9000\nnr_throttled 0\nthrottled_usec 0\n', encoding='utf-8'
    )

    with pytest.raises(RuntimeError, match='does not expose rbytes/rios counters'):
        CgroupIoCacheSnapshot.read(root=tmp_path)


def test_f007_physical_commented_mirrors_only_add_comments() -> None:
    root = Path(__file__).resolve().parents[1] / 'performance'
    for relative in ('f007_physical.py', 'baseline.py', 'run.py'):
        assert _python_tokens(root / 'commented' / relative) == _python_tokens(root / relative)


def _accepted_manifest_document() -> dict[str, object]:
    windows = [
        {
            'window_index': index,
            'as_of_utc': f'2026-08-{30 - (index % 20):02d}T16:00:00Z',
            'pi_daily_target_count': 8,
            'dispatch_shift_target_count': 14,
            'target_count': 22,
            'target_paths': [
                f'window-{index:02d}/target-{target:02d}.parquet' for target in range(22)
            ],
            'window_fingerprint_sha256': f'{index + 1:064x}',
        }
        for index in range(61)
    ]
    return {
        'manifest_version': '1.1',
        'dataset_bank_id': 'f007-controlled-physical-bank-v1',
        'origin_class': 'controlled_synthetic_physical',
        'operational_representative': False,
        'mount_contract': {'container_path': '/f007/input', 'read_only': True},
        'aggregate_physical': {
            'file_count': 1343,
            'physical_signal_pool_size': 1000,
            'pi_daily_file_count': 488,
            'dispatch_shift_file_count': 854,
            'pi_latest_file_count': 1,
            'row_group_count_total': 1343,
            'partitioned_working_set_bytes': 147059328,
        },
        'fingerprints': {'aggregate_sha256': 'a' * 64, 'bank_sha256': 'b' * 64},
        'alarm_signal_mapping': {
            'physical_signal_pool_size': 1000,
            'strategy': 'round_robin_modulo',
            'alarm_count_ceiling': None,
            'ladder_frozen': False,
        },
        'profiles': {
            'WARM_FIXED': {'authoritative_for_alarm_capacity': True},
            'FIRST_TOUCH_PARTITIONED': {'window_order': list(range(61))},
            'WARM_REPLAY_PARTITIONED': {
                'window_order': list(range(61)),
                'same_window_order': True,
            },
        },
        'windows': windows,
        'files': [
            {
                'source': 'pi.interpolated',
                'materialization': 'latest',
                'mount_relative_path': 'datasets/pi/not_pii/interpolated/latest/data.parquet',
            }
        ],
    }


def _accepted_conformance_document() -> dict[str, object]:
    required = {
        key: True
        for key in (
            'alarm_count_ladder_remains_unfrozen',
            'alarm_signal_mapping_pool_is_1000',
            'all_files_use_single_row_group',
            'all_files_use_zstd',
            'dispatch_geometry_is_256x25',
            'every_window_has_14_dispatch_shift_targets',
            'every_window_has_22_targets',
            'every_window_has_8_pi_daily_targets',
            'pi_daily_geometry_is_8640x3',
            'pi_latest_geometry_is_1x1001',
            'sample_deterministic_rebuild_verified',
            'total_file_count_is_1343',
            'warm_replay_uses_same_window_order',
            'window_count_is_61',
            'window_target_paths_are_globally_unique',
        )
    }
    return {
        'dataset_bank_id': 'f007-controlled-physical-bank-v1',
        'status': 'PASS',
        'claims': {
            'final_61_window_geometry_frozen': True,
            'cold_cache_guaranteed': False,
        },
        'checks': required,
    }


def _python_tokens(path: Path) -> list[tuple[int, str]]:
    tokens = tokenize.generate_tokens(io.StringIO(path.read_text(encoding='utf-8')).readline)
    return [
        (item.type, item.string)
        for item in tokens
        if item.type
        not in {
            tokenize.COMMENT,
            tokenize.ENCODING,
            tokenize.INDENT,
            tokenize.DEDENT,
            tokenize.NEWLINE,
            tokenize.NL,
            tokenize.ENDMARKER,
        }
    ]
