from __future__ import annotations

import json
import sys
import tokenize
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ada.data.core import DataPartition, DataSource
from ada.data.sources import LoadedDataSources, PiSourceProvider, build_current_source_registry
from performance.baseline import (
    _F010_PHYSICAL_INTEGRATED,
    BaselineScenario,
    MixedDeactivationInputSource,
    _build_contracts,
    _historical_columns,
    build_baseline_runtime,
)
from performance.docker_stress import (
    _F010_IMAGE_TAG,
    _IMAGE_TAG,
    DockerInspection,
    ExecutionResources,
    PhysicalAudit,
    ResourceProbe,
    _adjudicate_f010,
    _audit_work_root,
    _docker_create_f010_command,
    _parse_args,
)
from performance.f007_physical import (
    F007DatasetBank,
    F007PhysicalDataSourceLoader,
    F007Window,
    build_f007_latest_plan,
    f007_daily_signal_column_for_alarm,
)
from performance.run import _select_mixed_deactivation_started_effect


def _scenario(**updates) -> BaselineScenario:
    values = {
        'test_id': 'F-010',
        'alarm_count': 1000,
        'duration_seconds': 1800,
        'iteration_period_seconds': 5,
        'data_refresh_seconds': 10,
        'data_profile': _F010_PHYSICAL_INTEGRATED,
        'columns_per_alarm': 1,
        'physical_partition_count': 1,
        'physical_partition_layout': 'balanced',
        'historical_series_per_alarm': 1,
        'historical_window_minutes': 60,
        'historical_step_seconds': 10,
        'priority_group_size': 10,
        'initial_active_percent': 100,
        'management_action_at_seconds': 300,
        'management_action_count': 480,
        'management_action_interval_seconds': 1,
        'deactivation_decision_at_seconds': 330,
        'deactivation_decision_count': 480,
        'deactivation_decision_interval_seconds': 1,
        'deactivation_window_seconds': 900,
        'parameter_adoption_at_seconds': 900,
        'parameter_target_threshold': 0.75,
    }
    values.update(updates)
    return BaselineScenario(**values)


def _bank(tmp_path: Path) -> F007DatasetBank:
    as_of = datetime(2026, 8, 30, 16, tzinfo=UTC)
    daily = 'datasets/pi/not_pii/interpolated/daily/year=2026/month=08/day=30/data.parquet'
    return F007DatasetBank(
        manifest_path=tmp_path / 'manifest.json',
        conformance_path=tmp_path / 'conformance.json',
        input_root=tmp_path / 'input',
        dataset_bank_id='f007-controlled-physical-bank-v1',
        aggregate_sha256='a' * 64,
        bank_sha256='b' * 64,
        latest_path='datasets/pi/not_pii/interpolated/latest/data.parquet',
        windows=(
            F007Window(
                window_index=0,
                as_of_utc=as_of,
                target_paths=(daily, 'datasets/dispatch/placeholder/data.parquet'),
                window_fingerprint_sha256='c' * 64,
            ),
        ),
        physical_signal_pool_size=1000,
        partitioned_working_set_bytes=147059328,
    )


def _f010_result(*, iterations: int = 361, overrun_ratio: float = 0.0) -> dict[str, object]:
    return {
        'result': 'PASS',
        'integrity_ok': True,
        'journal_aligned': True,
        'iterations': iterations,
        'p50_iteration_ms': 2000.0,
        'p95_iteration_ms': 3000.0,
        'p99_iteration_ms': 4000.0,
        'overrun_count': 0 if overrun_ratio == 0 else 30,
        'overrun_ratio': overrun_ratio,
        'durable_record_count': 1000,
        'snapshot_count': 100,
        'snapshot_alarm_count': 1000,
        'expected_snapshot_alarm_count': 1000,
        'source_load_p95_ms': 700.0,
        'source_view_count': 2,
        'latest_source_column_count': 1000,
        'historical_source_column_count': 2,
        'historical_source_row_count': 361,
        'deactivation_decision_pressure': {
            'functional_integrity_ok': True,
            'request_receipt_count': 480,
            'decision_receipt_count': 480,
            'management_effect_started_count': 480,
            'deactivation_effect_started_count': 480,
            'deactivation_effect_cleared_count': 480,
            'final_management_pending_count': 0,
            'final_decision_pending_count': 0,
            'final_pending_request_count': 0,
        },
        'parameter_adoption_pressure': {
            'functional_integrity_ok': True,
            'compatible_change_count': 1000,
            'unchanged_change_count': 0,
            'structural_reset_change_count': 0,
            'disabled_change_count': 0,
            'removed_change_count': 0,
            'rejected_change_count': 0,
            'target_threshold_alarm_count': 1000,
            'effective_cache_revision': 'PERF-AC-2',
            'adoption_iteration_count': 1,
        },
    }


def _execution() -> ExecutionResources:
    return ExecutionResources(
        cpu_limit_cores=1.0,
        memory_limit_bytes=2 * 1024**3,
        cpu_peak_percent=99.0,
        memory_peak_percent=30.0,
        cpu_throttled_seconds=4.0,
        work_iterations=100,
        empty_iterations=261,
        stop_reason='insufficient_remaining_time',
    )


def _audit() -> PhysicalAudit:
    return PhysicalAudit(
        durable_record_count=1000,
        journal_bytes=10_000_000,
        duplicate_commit_id_count=0,
        commit_chain_mismatch_count=0,
        snapshot_count=100,
        snapshot_alarm_count=1000,
        snapshot_last_commit_mismatch_count=0,
        journal_aligned=True,
    )


def _binding(bank: F007DatasetBank) -> dict[str, object]:
    return {
        'dataset_bank_id': bank.dataset_bank_id,
        'aggregate_sha256': bank.aggregate_sha256,
        'bank_sha256': bank.bank_sha256,
        'data_profile': 'f010-physical-integrated',
        'fixed_as_of_utc': '2026-08-30T16:00:00Z',
        'prewarm_paths': [bank.latest_path, bank.pi_daily_path(0)],
    }


def _samples(count: int = 361, interval_ms: float = 5000.0) -> list[dict[str, float | None]]:
    return [{'start_interval_ms': None}] + [
        {'start_interval_ms': interval_ms} for _ in range(count - 1)
    ]


def test_f010_scenario_freezes_the_accepted_contract() -> None:
    scenario = _scenario()

    assert scenario.data_profile == 'f010-physical-integrated'
    assert scenario.historical_points_per_series == 360
    assert scenario.has_mixed_deactivation_pressure
    assert scenario.has_parameter_adoption_pressure

    with pytest.raises(ValueError, match='F-010 requires duration_seconds=1800'):
        _scenario(duration_seconds=1795)


def test_f010_physical_management_profile_is_reserved_for_f010() -> None:
    with pytest.raises(
        ValueError, match='f010-physical-integrated management pressure is reserved for F-010'
    ):
        _scenario(test_id='F-010-OTHER')


def test_f010_contracts_merge_to_1000_latest_and_two_daily_columns(tmp_path: Path) -> None:
    scenario = _scenario()
    loader = F007PhysicalDataSourceLoader(loader=None, reader=None)

    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
        source_loader_override=loader,
    )

    views = {view.partition: view for view in runtime.revision.session.data_plan.views}
    assert len(views[DataPartition.LATEST].column_names) == 1000
    assert views[DataPartition.DAILY].column_names == ('signal_000001', 'signal_000002')
    assert isinstance(runtime.input_source, MixedDeactivationInputSource)
    assert runtime.target_revision is not None


def test_f010_alarm_context_maps_historical_signals_over_fixed_two_signal_pool() -> None:
    scenario = _scenario()
    contracts = _build_contracts(scenario)

    assert len(contracts) == 1000
    assert contracts[0].requirements[0].source is DataSource.PI_INTERPOLATED
    assert contracts[0].requirements[0].partition is DataPartition.LATEST
    assert contracts[0].requirements[1].partition is DataPartition.DAILY
    assert contracts[0].requirements[1].column_names == ('signal_000001',)
    assert contracts[1].requirements[1].column_names == ('signal_000002',)
    assert contracts[2].requirements[1].column_names == ('signal_000001',)
    assert _historical_columns(999, scenario=scenario) == ('signal_000002',)
    assert f007_daily_signal_column_for_alarm(1000) == 'signal_000001'


def test_f010_bank_resolves_same_day_daily_target(tmp_path: Path) -> None:
    bank = _bank(tmp_path)

    assert bank.pi_daily_path(0).endswith('year=2026/month=08/day=30/data.parquet')


def test_f010_physical_loader_pins_reads_and_rebinds_iteration_as_of() -> None:
    plan = build_f007_latest_plan()
    pinned = datetime(2026, 8, 30, 16, tzinfo=UTC)
    requested = datetime(2030, 1, 1, tzinfo=UTC)
    physical_loaded = LoadedDataSources(
        as_of=pinned,
        plan=plan,
        registry=build_current_source_registry(pi_source=PiSourceProvider.NOTPII),
        loaded={},
        failures={},
    )

    class Loader:
        def __init__(self) -> None:
            self.as_of = None

        def load(self, *, plan, as_of):
            self.as_of = as_of
            return physical_loaded

    loader = Loader()
    source = F007PhysicalDataSourceLoader(
        loader=loader,
        reader=None,
        fixed_as_of_utc=pinned,
        load_count=1,
    )

    loaded = source.load(plan=plan, as_of=requested)

    assert loader.as_of == pinned
    assert loaded.as_of == requested
    assert loaded.plan is physical_loaded.plan
    assert loaded.registry is physical_loaded.registry
    assert loaded.loaded == physical_loaded.loaded
    assert loaded.failures == physical_loaded.failures
    assert loaded.shift_resolver is physical_loaded.shift_resolver
    assert loaded.operational_resolver is physical_loaded.operational_resolver


def test_f010_docker_command_uses_e2_and_read_only_bank(tmp_path: Path) -> None:
    bank_root = tmp_path / 'bank'
    output = tmp_path / 'output'
    work = tmp_path / 'work'

    command = _docker_create_f010_command(
        container_name='f010-test',
        output_dir=output,
        work_dir=work,
        bank_root=bank_root,
    )
    joined = ' '.join(command)

    assert '--cpus 1.0' in joined
    assert '--memory 2g' in joined
    assert 'container-f010' in command
    assert 'dst=/f007/input,readonly' in joined
    assert _IMAGE_TAG == 'atlanticus-r35-f007:0.5.2'
    assert _F010_IMAGE_TAG == 'atlanticus-r35-f010:0.1.7'
    assert _F010_IMAGE_TAG in command


def test_f010_mixed_deactivation_audit_accepts_started_then_cleared_lifecycle() -> None:
    started = {
        'effect_id': 'PERF-DE-PERF-REQ-000001',
        'alarm_key': 'perf-alarm-000001',
        'kind': 'STARTED',
        'effective_from': '2026-08-30T16:05:30Z',
        'effective_until': '2026-08-30T16:20:00Z',
    }
    cleared = {
        'effect_id': 'PERF-DE-PERF-REQ-000001',
        'alarm_key': 'perf-alarm-000001',
        'kind': 'CLEARED',
    }

    selected, lifecycle_ok = _select_mixed_deactivation_started_effect(
        [started, cleared],
        expect_cleared=True,
    )

    assert lifecycle_ok is True
    assert selected is started

    _, short_lifecycle_ok = _select_mixed_deactivation_started_effect(
        [started, cleared],
        expect_cleared=False,
    )
    assert short_lifecycle_ok is False


def test_f010_physical_audit_tracks_wal_chain_and_materialization_per_group(
    tmp_path: Path,
) -> None:
    stage_work = tmp_path / 'work'
    runtime_root = stage_work / 'volume' / 'ada' / 'alarms' / 'runtime'
    journal = runtime_root / 'journal' / '2026-08-31T14.jsonl'
    groups = runtime_root / 'state' / 'groups'
    journal.parent.mkdir(parents=True)
    groups.mkdir(parents=True)

    group_a_v1 = {
        'priority_group': 'group-a',
        'last_commit_id': 'A1',
        'alarms': {'alarm-a': {'last_commit_id': 'A1'}},
    }
    group_b_v1 = {
        'priority_group': 'group-b',
        'last_commit_id': 'B1',
        'alarms': {'alarm-b': {'last_commit_id': 'B1'}},
    }
    group_a_v2 = {
        'priority_group': 'group-a',
        'last_commit_id': 'A2',
        'alarms': {'alarm-a': {'last_commit_id': 'A1'}},
    }
    records = [
        {
            'commit': {
                'commit_id': 'A1',
                'priority_group': 'group-a',
                'previous_commit_id': None,
            },
            'snapshot_after': group_a_v1,
        },
        {
            'commit': {
                'commit_id': 'B1',
                'priority_group': 'group-b',
                'previous_commit_id': None,
            },
            'snapshot_after': group_b_v1,
        },
        {
            'commit': {
                'commit_id': 'A2',
                'priority_group': 'group-a',
                'previous_commit_id': 'A1',
            },
            'snapshot_after': group_a_v2,
        },
    ]
    journal.write_text(
        ''.join(json.dumps(record, sort_keys=True) + '\n' for record in records),
        encoding='utf-8',
    )
    (groups / 'group-a.json').write_text(json.dumps(group_a_v2), encoding='utf-8')
    (groups / 'group-b.json').write_text(json.dumps(group_b_v1), encoding='utf-8')
    head = {'segment_key': '2026-08-31T14Z#0000', 'byte_offset': journal.stat().st_size}
    (runtime_root / 'state' / 'journal-head.json').write_text(
        json.dumps({'durable': head, 'materialized': head}),
        encoding='utf-8',
    )

    audit = _audit_work_root(stage_work)

    assert audit is not None
    assert audit.durable_record_count == 3
    assert audit.duplicate_commit_id_count == 0
    assert audit.commit_chain_mismatch_count == 0
    assert audit.snapshot_count == 2
    assert audit.snapshot_alarm_count == 2
    assert audit.snapshot_last_commit_mismatch_count == 0
    assert audit.journal_aligned is True

    corrupted = dict(group_a_v2)
    corrupted['last_commit_id'] = 'WRONG'
    (groups / 'group-a.json').write_text(json.dumps(corrupted), encoding='utf-8')
    corrupted_audit = _audit_work_root(stage_work)
    assert corrupted_audit is not None
    assert corrupted_audit.snapshot_last_commit_mismatch_count == 1


def test_f010_recheck_mode_is_explicit_and_exclusive(monkeypatch) -> None:
    monkeypatch.setattr(sys, 'argv', ['docker_stress', 'final-qualification', '--recheck'])
    args = _parse_args()
    assert args.recheck is True
    assert args.replace is False

    monkeypatch.setattr(
        sys,
        'argv',
        ['docker_stress', 'final-qualification', '--replace', '--recheck'],
    )
    with pytest.raises(SystemExit):
        _parse_args()


def test_f010_adjudication_passes_clean_integrated_evidence(tmp_path: Path) -> None:
    bank = _bank(tmp_path)

    summary = _adjudicate_f010(
        bank=bank,
        inspection=DockerInspection(
            exit_code=0,
            oom_killed=False,
            cpu_cores=1.0,
            memory_bytes=2 * 1024**3,
        ),
        probe=ResourceProbe(cpu_limit_cores=1.0, memory_limit_bytes=2 * 1024**3),
        execution=_execution(),
        result=_f010_result(),
        samples=_samples(),
        audit=_audit(),
        binding=_binding(bank),
    )

    assert summary['qualification_status'] == 'PASS/GREEN'
    assert summary['hard_integrity_ok'] is True
    assert summary['f011_profile_required'] is False


def test_f010_clean_integrity_with_sustained_cadence_loss_routes_to_f011(tmp_path: Path) -> None:
    bank = _bank(tmp_path)

    summary = _adjudicate_f010(
        bank=bank,
        inspection=DockerInspection(
            exit_code=0,
            oom_killed=False,
            cpu_cores=1.0,
            memory_bytes=2 * 1024**3,
        ),
        probe=ResourceProbe(cpu_limit_cores=1.0, memory_limit_bytes=2 * 1024**3),
        execution=_execution(),
        result=_f010_result(iterations=350, overrun_ratio=0.08),
        samples=_samples(count=350, interval_ms=5600.0),
        audit=_audit(),
        binding=_binding(bank),
    )

    assert summary['qualification_status'] == 'REVIEW_PROFILE_F011'
    assert summary['hard_integrity_ok'] is True
    assert summary['f011_profile_required'] is True


def test_f010_hard_semantic_failure_is_not_reclassified_as_capacity(tmp_path: Path) -> None:
    bank = _bank(tmp_path)
    result = _f010_result()
    result['deactivation_decision_pressure']['functional_integrity_ok'] = False

    summary = _adjudicate_f010(
        bank=bank,
        inspection=DockerInspection(
            exit_code=0,
            oom_killed=False,
            cpu_cores=1.0,
            memory_bytes=2 * 1024**3,
        ),
        probe=ResourceProbe(cpu_limit_cores=1.0, memory_limit_bytes=2 * 1024**3),
        execution=_execution(),
        result=result,
        samples=_samples(),
        audit=_audit(),
        binding=_binding(bank),
    )

    assert summary['qualification_status'] == 'FAIL'
    assert summary['f011_profile_required'] is False


def test_f010_commented_mirrors_are_token_equivalent() -> None:
    root = Path(__file__).resolve().parents[1] / 'performance'
    for relative in ('baseline.py', 'run.py', 'f007_physical.py', 'docker_stress.py'):
        productive = root / relative
        commented = root / 'commented' / relative
        assert _tokens(productive) == _tokens(commented)


def _tokens(path: Path) -> tuple[tuple[int, str], ...]:
    result: list[tuple[int, str]] = []
    with path.open('rb') as handle:
        for token in tokenize.tokenize(handle.readline):
            if token.type in {
                tokenize.ENCODING,
                tokenize.COMMENT,
                tokenize.NL,
                tokenize.NEWLINE,
                tokenize.INDENT,
                tokenize.DEDENT,
                tokenize.ENDMARKER,
            }:
                continue
            result.append((token.type, token.string))
    return tuple(result)
