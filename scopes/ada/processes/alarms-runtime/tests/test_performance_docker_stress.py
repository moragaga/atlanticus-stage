from __future__ import annotations

import io
import sys
import tokenize
from pathlib import Path

import pytest

from performance import docker_stress
from performance.docker_stress import (
    DockerInspection,
    DockerStressStage,
    ExecutionResources,
    PhysicalAudit,
    ResourceEnvelope,
    ResourceProbe,
    StageSummary,
    _adjudicate_stage,
    _docker_context_ignore,
    _docker_create_command,
    _docker_create_physical_gate_command,
    _parse_execution_resources,
    _parse_resource_probe,
    _phase_b_refinement,
    _phase_b_refinement_complete,
    _read_execution_resources_from_work,
    _run_phase_a,
    _run_phase_b,
    _run_phase_c,
)


def _result(
    *, alarm_count: int = 1000, p95: float = 1000.0, p99: float = 1500.0, overruns: int = 0
) -> dict[str, object]:
    return {
        'result': 'PASS',
        'integrity_ok': True,
        'journal_aligned': True,
        'iterations': 61,
        'p95_iteration_ms': p95,
        'p99_iteration_ms': p99,
        'overrun_count': overruns,
        'overrun_ratio': overruns / 61,
        'snapshot_alarm_count': alarm_count,
    }


def _resources(
    *, memory_peak: float = 20.0, stop_reason: str = 'insufficient_remaining_time'
) -> ExecutionResources:
    return ExecutionResources(
        cpu_limit_cores=2.0,
        memory_limit_bytes=4 * 1024**3,
        cpu_peak_percent=95.0,
        memory_peak_percent=memory_peak,
        cpu_throttled_seconds=3.0,
        work_iterations=2,
        empty_iterations=59,
        stop_reason=stop_reason,
    )


def _audit(*, alarm_count: int = 1000) -> PhysicalAudit:
    return PhysicalAudit(
        durable_record_count=2,
        journal_bytes=1000,
        duplicate_commit_id_count=0,
        commit_chain_mismatch_count=0,
        snapshot_count=1,
        snapshot_alarm_count=alarm_count,
        snapshot_last_commit_mismatch_count=0,
        journal_aligned=True,
    )


def _inspection() -> DockerInspection:
    return DockerInspection(
        exit_code=0,
        oom_killed=False,
        cpu_cores=2.0,
        memory_bytes=4 * 1024**3,
    )


def _probe() -> ResourceProbe:
    return ResourceProbe(cpu_limit_cores=2.0, memory_limit_bytes=4 * 1024**3)


def _samples() -> list[dict[str, float | None]]:
    return [{'start_interval_ms': None}] + [{'start_interval_ms': 5000.0}] * 60


def _stage_summary(
    *,
    alarm_count: int,
    memory_gib: int,
    classification: str,
) -> StageSummary:
    envelope = ResourceEnvelope.from_memory_gib(memory_gib)
    return StageSummary(
        stage_id=f'e{memory_gib}-a{alarm_count}',
        alarm_count=alarm_count,
        memory_gib=memory_gib,
        cpu_cores=envelope.cpu_cores,
        classification=classification,
        reasons=('test',),
        resource_contract_ok=True,
        container_exit_code=0,
        oom_killed=False,
        iterations=61,
        p95_iteration_ms=1000.0,
        p99_iteration_ms=1500.0,
        p95_start_interval_ms=5000.0,
        overrun_count=0,
        overrun_ratio=0.0,
        cpu_peak_percent=95.0,
        memory_peak_percent=50.0,
        cpu_throttled_seconds=1.0,
        work_iterations=2,
        empty_iterations=59,
        durable_record_count=2,
        journal_bytes=1000,
        duplicate_commit_id_count=0,
        commit_chain_mismatch_count=0,
        snapshot_count=1,
        snapshot_alarm_count=alarm_count,
        snapshot_last_commit_mismatch_count=0,
        journal_aligned=True,
        stop_reason='insufficient_remaining_time',
    )


def test_f007_envelope_ladder_matches_azure_pairs() -> None:
    assert [
        (memory, ResourceEnvelope.from_memory_gib(memory).cpu_cores) for memory in range(2, 9)
    ] == [
        (2, 1.0),
        (3, 1.5),
        (4, 2.0),
        (5, 2.5),
        (6, 3.0),
        (7, 3.5),
        (8, 4.0),
    ]
    with pytest.raises(ValueError, match='memory_gib must be one of'):
        ResourceEnvelope.from_memory_gib(1)


def test_f007_stage_contract_derives_exact_geometry() -> None:
    stage = DockerStressStage(alarm_count=4000, envelope=ResourceEnvelope.from_memory_gib(4))
    assert stage.stage_id == 'e4-a4000'
    assert stage.expected_iterations == 61
    assert stage.expected_durable_records == 2
    with pytest.raises(ValueError, match='duration_seconds=300'):
        DockerStressStage(alarm_count=4000, envelope=stage.envelope, duration_seconds=600)


def test_f007_docker_create_applies_exact_cpu_memory_and_no_swap(tmp_path: Path) -> None:
    stage = DockerStressStage(alarm_count=2000, envelope=ResourceEnvelope.from_memory_gib(3))
    bank_root = tmp_path / 'bank'
    command = _docker_create_command(
        stage=stage,
        container_name='f007-test',
        stage_output=tmp_path / 'results',
        stage_work=tmp_path / 'work',
        bank_root=bank_root,
    )
    joined = ' '.join(command)
    assert '--cpus 1.5' in joined
    assert '--memory 3g' in joined
    assert '--memory-swap 3g' in joined
    assert 'container-stage --alarm-count 2000 --expected-memory-gib 3' in joined
    assert 'type=bind,src=' in joined and 'dst=/f007/work' in joined
    assert f'src={bank_root / "input"},dst=/f007/input,readonly' in joined
    assert 'dst=/f007/manifest.json,readonly' in joined
    assert 'dst=/f007/conformance.json,readonly' in joined
    assert '--work-dir /f007/work/run' in joined
    assert '--dataset-root /f007/input' in joined
    assert '--manifest /f007/manifest.json' in joined
    assert '--conformance /f007/conformance.json' in joined


def test_f007_physical_gate_runs_both_partitioned_passes_in_one_container(
    tmp_path: Path,
) -> None:
    command = _docker_create_physical_gate_command(
        envelope=ResourceEnvelope.from_memory_gib(2),
        container_name='f007-physical-gate',
        output_dir=tmp_path / 'results',
        bank_root=tmp_path / 'bank',
    )
    joined = ' '.join(command)

    assert '--cpus 1.0' in joined
    assert '--memory 2g' in joined
    assert '--memory-swap 2g' in joined
    assert 'container-physical-gate --expected-memory-gib 2' in joined
    assert 'dst=/f007/input,readonly' in joined
    assert 'dst=/f007/manifest.json,readonly' in joined
    assert 'dst=/f007/conformance.json,readonly' in joined


def test_f007_phase_b_requires_authorized_phase_a_before_image_build(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        sys,
        'argv',
        [
            'performance.docker_stress',
            '--output-root',
            str(tmp_path / 'results'),
            'phase-b',
        ],
    )

    def fail_build(**_kwargs) -> None:
        raise AssertionError('phase-b prerequisite must be checked before Docker build')

    monkeypatch.setattr(docker_stress, '_repository_root', lambda: tmp_path)
    monkeypatch.setattr(docker_stress, '_build_image', fail_build)
    with pytest.raises(RuntimeError, match='requires an accepted Phase A-P summary'):
        docker_stress.main()


def test_f007_docker_context_excludes_the_external_dataset_bank() -> None:
    ignored = _docker_context_ignore('/tmp/repository', ['.performance-work', 'src'])

    assert '.performance-work' in ignored
    assert 'src' not in ignored


def test_f007_parses_cgroup_probe_and_execution_resource_summary(tmp_path: Path) -> None:
    log = (
        'F007_RESOURCE_PROBE {"cpu_limit_cores": 2.0, "memory_limit_bytes": 4294967296}\n'
        '22:00:00 INFO  alarms-runtime-performance completed | run=abc | duration=301.0s | '
        'iterations=61 | work=2 | empty=59 | cpu_limit=2.0 | memory_limit=4096.0MiB | '
        'cpu_peak=97.5% | memory_peak=42.25% | cpu_throttled_seconds=8.5 | stop=insufficient_remaining_time\n'
    )
    probe = _parse_resource_probe(log)
    execution = _parse_execution_resources(log)
    assert probe == ResourceProbe(cpu_limit_cores=2.0, memory_limit_bytes=4 * 1024**3)
    assert execution is not None
    assert execution.cpu_limit_cores == 2.0
    assert execution.memory_limit_bytes == 4 * 1024**3
    assert execution.cpu_peak_percent == 97.5
    assert execution.memory_peak_percent == 42.25
    assert execution.work_iterations == 2
    assert execution.stop_reason == 'insufficient_remaining_time'

    work = tmp_path / 'work'
    log_path = (
        work / 'volume' / 'ada-alarms-runtime-performance' / 'logs' / 'service' / 'executions.jsonl'
    )
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        '{"event":"execution.completed","cpu_limit_cores":2.0,"memory_limit_bytes":4294967296,'
        '"cpu_peak_percent":97.5,"memory_peak_percent":42.25,"work_iterations":2,'
        '"empty_iterations":59,"stop_reason":"insufficient_remaining_time"}\n',
        encoding='utf-8',
    )
    persisted = _read_execution_resources_from_work(work)
    assert persisted is not None
    assert persisted.memory_limit_bytes == 4 * 1024**3


def test_f007_green_margin_accepts_high_cpu_when_cadence_is_healthy() -> None:
    summary = _adjudicate_stage(
        stage=DockerStressStage(alarm_count=1000, envelope=ResourceEnvelope.from_memory_gib(4)),
        inspection=_inspection(),
        probe=_probe(),
        execution=_resources(memory_peak=50.0),
        result=_result(),
        samples=_samples(),
        audit=_audit(),
    )
    assert summary.classification == 'GREEN-MARGIN'
    assert summary.resource_contract_ok is True


def test_f007_boundary_is_non_product_capacity_evidence() -> None:
    summary = _adjudicate_stage(
        stage=DockerStressStage(alarm_count=1000, envelope=ResourceEnvelope.from_memory_gib(4)),
        inspection=_inspection(),
        probe=_probe(),
        execution=_resources(memory_peak=85.0),
        result=_result(p95=4200.0, p99=4800.0),
        samples=_samples(),
        audit=_audit(),
    )
    assert summary.classification == 'BOUNDARY/REVIEW'


def test_f007_saturated_on_period_boundary_or_oom() -> None:
    stage = DockerStressStage(alarm_count=1000, envelope=ResourceEnvelope.from_memory_gib(4))
    latency = _adjudicate_stage(
        stage=stage,
        inspection=_inspection(),
        probe=_probe(),
        execution=_resources(),
        result=_result(p95=5000.0, p99=5500.0),
        samples=_samples(),
        audit=_audit(),
    )
    oom = _adjudicate_stage(
        stage=stage,
        inspection=DockerInspection(
            exit_code=137, oom_killed=True, cpu_cores=2.0, memory_bytes=4 * 1024**3
        ),
        probe=_probe(),
        execution=None,
        result=None,
        samples=[],
        audit=None,
    )
    assert latency.classification == 'SATURATED'
    assert oom.classification == 'SATURATED'


def test_f007_resource_mismatch_invalidates_stage() -> None:
    summary = _adjudicate_stage(
        stage=DockerStressStage(alarm_count=1000, envelope=ResourceEnvelope.from_memory_gib(4)),
        inspection=_inspection(),
        probe=ResourceProbe(cpu_limit_cores=1.5, memory_limit_bytes=3 * 1024**3),
        execution=_resources(),
        result=_result(),
        samples=_samples(),
        audit=_audit(),
    )
    assert summary.classification == 'INVALID'
    assert summary.resource_contract_ok is False


def test_f007_safe_execution_window_elapsed_is_natural_boundary_completion() -> None:
    summary = _adjudicate_stage(
        stage=DockerStressStage(alarm_count=2500, envelope=ResourceEnvelope.from_memory_gib(4)),
        inspection=_inspection(),
        probe=_probe(),
        execution=_resources(
            memory_peak=4.289,
            stop_reason='safe_execution_window_elapsed',
        ),
        result=_result(
            alarm_count=2500,
            p95=3856.2170730001526,
            p99=4906.686212000932,
        ),
        samples=_samples(),
        audit=_audit(alarm_count=2500),
    )
    assert summary.classification == 'BOUNDARY/REVIEW'
    assert summary.stop_reason == 'safe_execution_window_elapsed'


def test_f007_unknown_completed_stop_reason_remains_product_fail() -> None:
    summary = _adjudicate_stage(
        stage=DockerStressStage(alarm_count=2500, envelope=ResourceEnvelope.from_memory_gib(4)),
        inspection=_inspection(),
        probe=_probe(),
        execution=_resources(stop_reason='unexpected_stop_reason'),
        result=_result(alarm_count=2500),
        samples=_samples(),
        audit=_audit(alarm_count=2500),
    )
    assert summary.classification == 'PRODUCT FAIL'


def test_f007_integrity_mismatch_is_product_fail_not_saturation() -> None:
    bad_audit = PhysicalAudit(
        durable_record_count=2,
        journal_bytes=1000,
        duplicate_commit_id_count=1,
        commit_chain_mismatch_count=0,
        snapshot_count=1,
        snapshot_alarm_count=1000,
        snapshot_last_commit_mismatch_count=0,
        journal_aligned=True,
    )
    summary = _adjudicate_stage(
        stage=DockerStressStage(alarm_count=1000, envelope=ResourceEnvelope.from_memory_gib(4)),
        inspection=_inspection(),
        probe=_probe(),
        execution=_resources(),
        result=_result(),
        samples=_samples(),
        audit=bad_audit,
    )
    assert summary.classification == 'PRODUCT FAIL'


def test_f007_phase_b_refinement_uses_binary_midpoint_and_contract_stop_rules() -> None:
    assert _phase_b_refinement(lower_green=2000, upper_non_green=4000) == 3000
    assert _phase_b_refinement(lower_green=3100, upper_non_green=5000) == 4100
    assert not _phase_b_refinement_complete(
        lower_green=2000,
        upper_non_green=4000,
        refinement_stages=0,
    )
    assert _phase_b_refinement_complete(
        lower_green=3000,
        upper_non_green=3500,
        refinement_stages=2,
    )
    assert _phase_b_refinement_complete(
        lower_green=2000,
        upper_non_green=4000,
        refinement_stages=3,
    )


def test_f007_phase_a_uses_e2_anchor_and_only_escalates_envelope_when_needed(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[tuple[int, int]] = []
    classifications = {2: 'SATURATED', 3: 'BOUNDARY/REVIEW', 4: 'GREEN-MARGIN'}

    def fake_run_or_reuse_stage(**kwargs) -> StageSummary:
        stage = kwargs['stage']
        calls.append((stage.envelope.memory_gib, stage.alarm_count))
        return _stage_summary(
            alarm_count=stage.alarm_count,
            memory_gib=stage.envelope.memory_gib,
            classification=classifications[stage.envelope.memory_gib],
        )

    monkeypatch.setattr(docker_stress, '_run_or_reuse_stage', fake_run_or_reuse_stage)

    return_code = _run_phase_a(
        repository_root=tmp_path,
        output_root=tmp_path / 'results',
        work_root=tmp_path / 'work',
        replace=False,
    )

    document = docker_stress._read_json_or_none(tmp_path / 'results' / 'phase-a-summary.json')
    assert return_code == 0
    assert calls == [(2, 1000), (3, 1000), (4, 1000)]
    assert document is not None
    assert document['phase'] == 'A-P'
    assert document['search_authorized'] is True
    assert document['baseline_envelope_memory_gib'] == 3


def test_f007_phase_b_expands_by_two_then_refines_until_gap_is_within_25_percent(
    tmp_path: Path, monkeypatch
) -> None:
    output_root = tmp_path / 'results'
    docker_stress._write_json(
        output_root / 'phase-a-summary.json',
        {
            'contract_version': '1.0.0',
            'phase': 'A-P',
            'search_authorized': True,
        },
    )
    classifications = {
        1000: 'GREEN-MARGIN',
        2000: 'GREEN-MARGIN',
        4000: 'SATURATED',
        3000: 'GREEN-MARGIN',
        3500: 'BOUNDARY/REVIEW',
    }
    calls: list[int] = []

    def fake_run_or_reuse_stage(**kwargs) -> StageSummary:
        stage = kwargs['stage']
        calls.append(stage.alarm_count)
        return _stage_summary(
            alarm_count=stage.alarm_count,
            memory_gib=stage.envelope.memory_gib,
            classification=classifications[stage.alarm_count],
        )

    monkeypatch.setattr(docker_stress, '_run_or_reuse_stage', fake_run_or_reuse_stage)

    return_code = _run_phase_b(
        repository_root=tmp_path,
        output_root=output_root,
        work_root=tmp_path / 'work',
        stop_after_alarm_count=None,
        replace=False,
    )

    document = docker_stress._read_json_or_none(output_root / 'phase-b-summary.json')
    assert return_code == 0
    assert calls == [1000, 2000, 4000, 3000, 3500]
    assert document is not None
    assert document['search_status'] == 'BRACKETED'
    assert document['stress_reference_load'] == 3000
    assert document['lower_green_alarm_count'] == 3000
    assert document['upper_non_green_alarm_count'] == 3500
    assert document['refinement_stage_count'] == 2
    assert document['predetermined_alarm_count_ceiling'] is None


def test_f007_phase_b_stops_when_e4_anchor_has_no_green_lower_bound(
    tmp_path: Path, monkeypatch
) -> None:
    output_root = tmp_path / 'results'
    docker_stress._write_json(
        output_root / 'phase-a-summary.json',
        {'contract_version': '1.0.0', 'search_authorized': True},
    )

    def fake_run_or_reuse_stage(**kwargs) -> StageSummary:
        stage = kwargs['stage']
        return _stage_summary(
            alarm_count=stage.alarm_count,
            memory_gib=4,
            classification='BOUNDARY/REVIEW',
        )

    monkeypatch.setattr(docker_stress, '_run_or_reuse_stage', fake_run_or_reuse_stage)

    _run_phase_b(
        repository_root=tmp_path,
        output_root=output_root,
        work_root=tmp_path / 'work',
        stop_after_alarm_count=None,
        replace=False,
    )

    document = docker_stress._read_json_or_none(output_root / 'phase-b-summary.json')
    assert document is not None
    assert document['search_status'] == 'NO_GREEN_LOWER_BOUND'
    assert document['lower_green_alarm_count'] is None
    assert document['upper_non_green_alarm_count'] == 1000
    assert document['stress_reference_load'] is None


def test_f007_phase_b_operator_stop_reports_open_knee_without_inventing_ceiling(
    tmp_path: Path, monkeypatch
) -> None:
    output_root = tmp_path / 'results'
    docker_stress._write_json(
        output_root / 'phase-a-summary.json',
        {'contract_version': '1.0.0', 'search_authorized': True},
    )
    calls: list[int] = []

    def fake_run_or_reuse_stage(**kwargs) -> StageSummary:
        stage = kwargs['stage']
        calls.append(stage.alarm_count)
        return _stage_summary(
            alarm_count=stage.alarm_count,
            memory_gib=4,
            classification='GREEN-MARGIN',
        )

    monkeypatch.setattr(docker_stress, '_run_or_reuse_stage', fake_run_or_reuse_stage)

    _run_phase_b(
        repository_root=tmp_path,
        output_root=output_root,
        work_root=tmp_path / 'work',
        stop_after_alarm_count=4000,
        replace=False,
    )

    document = docker_stress._read_json_or_none(output_root / 'phase-b-summary.json')
    assert calls == [1000, 2000, 4000]
    assert document is not None
    assert document['search_status'] == 'OPEN_HIGHER_THAN_TESTED'
    assert document['knee_greater_than_tested_alarm_count'] == 4000
    assert document['stress_reference_load'] is None
    assert document['predetermined_alarm_count_ceiling'] is None


def test_f007_phase_c_derives_reference_from_phase_b_and_reuses_e4(
    tmp_path: Path, monkeypatch
) -> None:
    output_root = tmp_path / 'results'
    docker_stress._write_json(
        output_root / 'phase-b-summary.json',
        {
            'contract_version': '1.0.0',
            'search_status': 'BRACKETED',
            'stress_reference_load': 3000,
        },
    )
    e4 = _stage_summary(
        alarm_count=3000,
        memory_gib=4,
        classification='GREEN-MARGIN',
    )
    docker_stress._write_json(
        output_root / 'e4-a3000' / 'stage-summary.json',
        e4.as_document(),
    )
    calls: list[int] = []
    classifications = {2: 'BOUNDARY/REVIEW', 3: 'GREEN-MARGIN'}

    def fake_run_or_reuse_stage(**kwargs) -> StageSummary:
        stage = kwargs['stage']
        calls.append(stage.envelope.memory_gib)
        return _stage_summary(
            alarm_count=stage.alarm_count,
            memory_gib=stage.envelope.memory_gib,
            classification=classifications[stage.envelope.memory_gib],
        )

    monkeypatch.setattr(docker_stress, '_run_or_reuse_stage', fake_run_or_reuse_stage)

    return_code = _run_phase_c(
        repository_root=tmp_path,
        output_root=output_root,
        work_root=tmp_path / 'work',
        headroom_memory_gib=(),
        replace=False,
    )

    document = docker_stress._read_json_or_none(output_root / 'phase-c-summary.json')
    assert return_code == 0
    assert calls == [2, 3]
    assert document is not None
    assert document['stress_reference_load'] == 3000
    assert document['minimum_viable_memory_gib'] == 2
    assert document['recommended_memory_gib'] == 3
    assert document['e4_reused_from_phase_b'] is True


def test_f007_commented_python_mirror_only_adds_comments() -> None:
    root = Path(__file__).resolve().parents[1] / 'performance'
    production = root / 'docker_stress.py'
    commented = root / 'commented' / 'docker_stress.py'
    assert _python_tokens(commented) == _python_tokens(production)


def test_f007_commented_dockerfile_only_adds_comments() -> None:
    root = Path(__file__).resolve().parents[1] / 'performance'
    production = root / 'docker' / 'F007.Dockerfile'
    commented = root / 'commented' / 'docker' / 'F007.Dockerfile'
    production_lines = [
        line for line in production.read_text(encoding='utf-8').splitlines() if line.strip()
    ]
    commented_lines = [
        line
        for line in commented.read_text(encoding='utf-8').splitlines()
        if line.strip() and not line.lstrip().startswith('#')
    ]
    assert commented_lines == production_lines


def _python_tokens(path: Path) -> list[tuple[int, str]]:
    tokens = tokenize.generate_tokens(io.StringIO(path.read_text(encoding='utf-8')).readline)
    return [
        (token.type, token.string)
        for token in tokens
        if token.type
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
