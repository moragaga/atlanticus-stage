from __future__ import annotations

# Espejo pedagógico: conserva exactamente el comportamiento productivo y explica los contratos relevantes.
import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# Imagen aislada del harness F-007; el banco físico se monta externamente en modo read-only.
_IMAGE_TAG = 'atlanticus-r35-f007:0.5.2'
_F010_IMAGE_TAG = 'atlanticus-r35-f010:0.1.7'
_PROBE_PREFIX = 'F007_RESOURCE_PROBE '
_ENVELOPES = {
    2: 1.0,
    3: 1.5,
    4: 2.0,
    5: 2.5,
    6: 3.0,
    7: 3.5,
    8: 4.0,
}
_GREEN = 'GREEN-MARGIN'
_BOUNDARY = 'BOUNDARY/REVIEW'
_SATURATED = 'SATURATED'
_PRODUCT_FAIL = 'PRODUCT FAIL'
_INVALID = 'INVALID'
_NATURAL_STOP_REASONS = frozenset({'insufficient_remaining_time', 'safe_execution_window_elapsed'})
# Parámetros congelados por el contrato de búsqueda física de capacidad v1.0.0.
# El factor ×2 es un algoritmo de exploración y no un techo de alarmas.
_CAPACITY_SEARCH_CONTRACT_VERSION = '1.0.0'
_CAPACITY_ANCHOR_ALARM_COUNT = 1000
_CAPACITY_SEARCH_MEMORY_GIB = 4
_CAPACITY_MAX_REFINEMENT_STAGES = 3
_CAPACITY_REFINEMENT_GAP_RATIO = 0.25
_F010_ALARM_COUNT = 1000
_F010_DURATION_SECONDS = 1800
_F010_EXPECTED_ITERATIONS = 361
_F010_MEMORY_GIB = 2
_F010_STATUS_PASS = 'PASS/GREEN'
_F010_STATUS_REVIEW = 'REVIEW_PROFILE_F011'
_F010_STATUS_FAIL = 'FAIL'
_F010_STATUS_INVALID = 'INVALID'


@dataclass(frozen=True, slots=True)
class ResourceEnvelope:
    memory_gib: int
    cpu_cores: float

    @classmethod
    def from_memory_gib(cls, memory_gib: int) -> ResourceEnvelope:
        if memory_gib not in _ENVELOPES:
            raise ValueError('memory_gib must be one of 2, 3, 4, 5, 6, 7, 8')
        return cls(memory_gib=memory_gib, cpu_cores=_ENVELOPES[memory_gib])

    @property
    def memory_bytes(self) -> int:
        return self.memory_gib * 1024**3

    @property
    def key(self) -> str:
        return f'E{self.memory_gib}'


@dataclass(frozen=True, slots=True)
class DockerStressStage:
    alarm_count: int
    envelope: ResourceEnvelope
    duration_seconds: int = 300
    iteration_period_seconds: int = 5
    data_refresh_seconds: int = 10

    def __post_init__(self) -> None:
        if self.alarm_count < 1000:
            raise ValueError('F-007 alarm_count must be at least 1000')
        if self.duration_seconds != 300:
            raise ValueError('F-007 requires duration_seconds=300')
        if self.iteration_period_seconds != 5:
            raise ValueError('F-007 requires iteration_period_seconds=5')
        if self.data_refresh_seconds != 10:
            raise ValueError('F-007 requires data_refresh_seconds=10')

    @property
    def stage_id(self) -> str:
        return f'{self.envelope.key.lower()}-a{self.alarm_count}'

    @property
    def expected_iterations(self) -> int:
        return int(self.duration_seconds / self.iteration_period_seconds) + 1

    @property
    def expected_durable_records(self) -> int:
        return 2


@dataclass(frozen=True, slots=True)
class ResourceProbe:
    cpu_limit_cores: float | None
    memory_limit_bytes: int | None


@dataclass(frozen=True, slots=True)
class ExecutionResources:
    cpu_limit_cores: float | None
    memory_limit_bytes: int | None
    cpu_peak_percent: float | None
    memory_peak_percent: float | None
    cpu_throttled_seconds: float | None
    work_iterations: int | None
    empty_iterations: int | None
    stop_reason: str | None


@dataclass(frozen=True, slots=True)
class PhysicalAudit:
    durable_record_count: int
    journal_bytes: int
    duplicate_commit_id_count: int
    commit_chain_mismatch_count: int
    snapshot_count: int
    snapshot_alarm_count: int
    snapshot_last_commit_mismatch_count: int
    journal_aligned: bool


@dataclass(frozen=True, slots=True)
class StageSummary:
    stage_id: str
    alarm_count: int
    memory_gib: int
    cpu_cores: float
    classification: str
    reasons: tuple[str, ...]
    resource_contract_ok: bool
    container_exit_code: int
    oom_killed: bool
    iterations: int | None
    p95_iteration_ms: float | None
    p99_iteration_ms: float | None
    p95_start_interval_ms: float | None
    overrun_count: int | None
    overrun_ratio: float | None
    cpu_peak_percent: float | None
    memory_peak_percent: float | None
    cpu_throttled_seconds: float | None
    work_iterations: int | None
    empty_iterations: int | None
    durable_record_count: int | None
    journal_bytes: int | None
    duplicate_commit_id_count: int | None
    commit_chain_mismatch_count: int | None
    snapshot_count: int | None
    snapshot_alarm_count: int | None
    snapshot_last_commit_mismatch_count: int | None
    journal_aligned: bool | None
    stop_reason: str | None

    def as_document(self) -> dict[str, Any]:
        document = asdict(self)
        document['reasons'] = list(self.reasons)
        return document


@dataclass(frozen=True, slots=True)
class DockerInspection:
    exit_code: int
    oom_killed: bool
    cpu_cores: float | None
    memory_bytes: int | None


# En el host se validan primero las precondiciones de cada fase para evitar builds o runs inválidos.
def main() -> int:
    args = _parse_args()
    if args.command == 'container-stage':
        return _run_container_stage(args)
    if args.command == 'container-physical-gate':
        return _run_container_physical_gate(args)
    if args.command == 'container-f010':
        return _run_container_f010(args)
    repository_root = _repository_root()
    if args.command == 'build':
        _build_image(repository_root=repository_root, rebuild=args.rebuild)
        return 0
    # F-010 usa una imagen separada para no alterar el contrato histórico del harness F-007.
    if args.command == 'build-f010':
        _build_image(
            repository_root=repository_root,
            rebuild=args.rebuild,
            image_tag=_F010_IMAGE_TAG,
        )
        return 0
    if args.command == 'final-qualification':
        if args.recheck:
            return _run_f010_final_qualification(
                repository_root=repository_root,
                replace=False,
                recheck=True,
            )
        _build_image(
            repository_root=repository_root,
            rebuild=args.rebuild,
            image_tag=_F010_IMAGE_TAG,
        )
        return _run_f010_final_qualification(
            repository_root=repository_root,
            replace=args.replace,
            recheck=False,
        )
    output_root = (repository_root / args.output_root).resolve()
    work_root = (repository_root / args.work_root).resolve()
    if args.command == 'phase-b':
        _require_phase_a_search_authorization(output_root)
    if args.command == 'phase-c':
        _phase_b_stress_reference(output_root)
    _build_image(repository_root=repository_root, rebuild=args.rebuild)
    if args.command == 'physical-gate':
        return _run_physical_gate(
            repository_root=repository_root,
            memory_gib=args.memory_gib,
            replace=args.replace,
        )
    if args.command == 'stage':
        stage = DockerStressStage(
            alarm_count=args.alarm_count,
            envelope=ResourceEnvelope.from_memory_gib(args.memory_gib),
        )
        summary = _run_or_reuse_stage(
            repository_root=repository_root,
            stage=stage,
            output_root=output_root,
            work_root=work_root,
            replace=args.replace,
        )
        print(json.dumps(summary.as_document(), indent=2, sort_keys=True))
        return _summary_exit_code(summary)
    if args.command == 'phase-a':
        return _run_phase_a(
            repository_root=repository_root,
            output_root=output_root,
            work_root=work_root,
            replace=args.replace,
        )
    if args.command == 'phase-b':
        return _run_phase_b(
            repository_root=repository_root,
            output_root=output_root,
            work_root=work_root,
            stop_after_alarm_count=args.stop_after_alarm_count,
            replace=args.replace,
        )
    if args.command == 'phase-c':
        return _run_phase_c(
            repository_root=repository_root,
            output_root=output_root,
            work_root=work_root,
            headroom_memory_gib=tuple(args.headroom_memory_gib),
            replace=args.replace,
        )
    raise AssertionError(f'unsupported command: {args.command}')


def _run_container_stage(args: argparse.Namespace) -> int:
    from atlanticus.runtime._resource_sampler import CgroupResourceSampler
    from performance import run as performance_run

    envelope = ResourceEnvelope.from_memory_gib(args.expected_memory_gib)
    sample = CgroupResourceSampler().sample()
    probe = {
        'cpu_limit_cores': sample.cpu_limit_cores,
        'memory_limit_bytes': sample.memory_limit_bytes,
    }
    print(_PROBE_PREFIX + json.dumps(probe, sort_keys=True), flush=True)
    if not _limits_match(
        envelope=envelope,
        cpu_cores=sample.cpu_limit_cores,
        memory_bytes=sample.memory_limit_bytes,
    ):
        print('F007_RESOURCE_CONTRACT_ERROR requested container limits do not match cgroups')
        return 3
    original = sys.argv
    sys.argv = [
        'performance.run',
        '--test-id',
        'F-007',
        '--alarm-count',
        str(args.alarm_count),
        '--duration-seconds',
        '300',
        '--iteration-period-seconds',
        '5',
        '--data-refresh-seconds',
        '10',
        '--data-profile',
        'f007-physical-warm',
        '--columns-per-alarm',
        '1',
        '--physical-partition-count',
        '1',
        '--physical-partition-layout',
        'balanced',
        '--operational-churn-percent',
        '0',
        '--durable-history-lookup-mode',
        'baseline',
        '--initial-active-percent',
        '100',
        '--f007-dataset-root',
        args.dataset_root,
        '--f007-manifest',
        args.manifest,
        '--f007-conformance',
        args.conformance,
        '--work-dir',
        args.work_dir,
        '--output-dir',
        args.output_dir,
    ]
    from performance.f007_physical import CgroupIoCacheSnapshot

    cgroup_before = CgroupIoCacheSnapshot.read()
    try:
        return_code = performance_run.main()
    finally:
        sys.argv = original
        cgroup_after = CgroupIoCacheSnapshot.read()
        _write_json(
            Path(args.output_dir) / 'f007-stage-cgroup.json',
            _cgroup_stage_document(before=cgroup_before, after=cgroup_after),
        )
    return return_code


# Ejecuta el único workload final dentro del envelope E2 y compone los argumentos contractuales sin decisiones manuales.
def _run_container_f010(args: argparse.Namespace) -> int:
    from atlanticus.runtime._resource_sampler import CgroupResourceSampler
    from performance import run as performance_run
    from performance.f007_physical import CgroupIoCacheSnapshot

    envelope = ResourceEnvelope.from_memory_gib(_F010_MEMORY_GIB)
    sample = CgroupResourceSampler().sample()
    probe = {
        'cpu_limit_cores': sample.cpu_limit_cores,
        'memory_limit_bytes': sample.memory_limit_bytes,
    }
    print(_PROBE_PREFIX + json.dumps(probe, sort_keys=True), flush=True)
    if not _limits_match(
        envelope=envelope,
        cpu_cores=sample.cpu_limit_cores,
        memory_bytes=sample.memory_limit_bytes,
    ):
        print('F010_RESOURCE_CONTRACT_ERROR requested container limits do not match cgroups')
        return 3
    original = sys.argv
    sys.argv = [
        'performance.run',
        '--test-id',
        'F-010',
        '--alarm-count',
        str(_F010_ALARM_COUNT),
        '--duration-seconds',
        str(_F010_DURATION_SECONDS),
        '--iteration-period-seconds',
        '5',
        '--data-refresh-seconds',
        '10',
        '--data-profile',
        'f010-physical-integrated',
        '--columns-per-alarm',
        '1',
        '--historical-series-per-alarm',
        '1',
        '--historical-window-minutes',
        '60',
        '--historical-step-seconds',
        '10',
        '--physical-partition-count',
        '1',
        '--physical-partition-layout',
        'balanced',
        '--priority-group-size',
        '10',
        '--management-action-at-seconds',
        '300',
        '--management-action-count',
        '480',
        '--management-action-interval-seconds',
        '1',
        '--deactivation-decision-at-seconds',
        '330',
        '--deactivation-decision-count',
        '480',
        '--deactivation-decision-interval-seconds',
        '1',
        '--deactivation-window-seconds',
        '900',
        '--parameter-adoption-at-seconds',
        '900',
        '--parameter-target-threshold',
        '0.75',
        '--operational-churn-percent',
        '0',
        '--durable-history-lookup-mode',
        'baseline',
        '--initial-active-percent',
        '100',
        '--f007-dataset-root',
        args.dataset_root,
        '--f007-manifest',
        args.manifest,
        '--f007-conformance',
        args.conformance,
        '--work-dir',
        args.work_dir,
        '--output-dir',
        args.output_dir,
    ]
    cgroup_before = CgroupIoCacheSnapshot.read()
    try:
        return_code = performance_run.main()
    finally:
        sys.argv = original
        cgroup_after = CgroupIoCacheSnapshot.read()
        _write_json(
            Path(args.output_dir) / 'f010-stage-cgroup.json',
            _cgroup_stage_document(before=cgroup_before, after=cgroup_after),
        )
    return return_code


# Orquesta un contenedor limpio, conserva evidencia y adjudica integración por separado de la búsqueda de capacidad F-007.
# --recheck reutiliza el run ya materializado y sólo repite auditoría/adjudicación host.
# No construye imagen ni vuelve a ejecutar el workload de 30 minutos.
def _run_f010_final_qualification(
    *,
    repository_root: Path,
    replace: bool,
    recheck: bool = False,
) -> int:
    output_dir = (
        repository_root
        / 'scopes/ada/processes/alarms-runtime/performance/results/F-010/final-docker-qualification'
    )
    work_dir = (
        repository_root
        / 'scopes/ada/processes/alarms-runtime/.performance-work/F-010-final-docker-qualification'
    )
    bank_root = _dataset_bank_root(repository_root)
    bank = _load_host_dataset_bank(bank_root)
    if recheck:
        if replace:
            raise RuntimeError('F-010 --recheck must not be combined with --replace')
        if not output_dir.is_dir() or not work_dir.is_dir():
            raise RuntimeError(
                'F-010 recheck requires existing final qualification output and work'
            )
        inspection_document = _read_json_or_none(output_dir / 'docker-inspect.json')
        if not isinstance(inspection_document, dict):
            raise RuntimeError('F-010 recheck requires docker-inspect.json')
        inspection = DockerInspection(
            exit_code=int(inspection_document.get('exit_code', 1)),
            oom_killed=bool(inspection_document.get('oom_killed', False)),
            cpu_cores=_optional_float(inspection_document.get('cpu_cores')),
            memory_bytes=_optional_int(inspection_document.get('memory_bytes')),
        )
        log_path = output_dir / 'docker.log'
        if not log_path.is_file():
            raise RuntimeError('F-010 recheck requires docker.log')
        log_text = log_path.read_text(encoding='utf-8')
        previous_summary = output_dir / 'f010-final-qualification-summary.json'
        if previous_summary.is_file():
            shutil.copyfile(
                previous_summary,
                output_dir / 'f010-final-qualification-summary.pre-recheck.json',
            )
    else:
        if replace:
            shutil.rmtree(output_dir, ignore_errors=True)
            shutil.rmtree(work_dir, ignore_errors=True)
        elif output_dir.exists() or work_dir.exists():
            raise RuntimeError('F-010 final qualification output already exists; use --replace')
        output_dir.mkdir(parents=True)
        work_dir.mkdir(parents=True)
        container_name = f'atlanticus-f010-final-{uuid.uuid4().hex[:8]}'
        command = _docker_create_f010_command(
            container_name=container_name,
            output_dir=output_dir,
            work_dir=work_dir,
            bank_root=bank_root,
        )
        create = subprocess.run(command, cwd=repository_root, text=True, capture_output=True)
        if create.returncode != 0:
            raise RuntimeError(
                create.stderr.strip() or create.stdout.strip() or 'docker create failed'
            )
        log_lines: list[str] = []
        start_return_code = 125
        inspection: DockerInspection | None = None
        try:
            process = subprocess.Popen(
                ['docker', 'start', '--attach', container_name],
                cwd=repository_root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
            )
            if process.stdout is None:
                raise RuntimeError('docker start did not expose stdout')
            for line in process.stdout:
                print(line, end='')
                log_lines.append(line)
            start_return_code = process.wait()
            inspection = _inspect_container(
                container_name=container_name, repository_root=repository_root
            )
        finally:
            subprocess.run(
                ['docker', 'rm', '--force', container_name],
                cwd=repository_root,
                text=True,
                capture_output=True,
            )
        log_text = ''.join(log_lines)
        (output_dir / 'docker.log').write_text(log_text, encoding='utf-8')
        if inspection is None:
            inspection = DockerInspection(
                exit_code=start_return_code,
                oom_killed=False,
                cpu_cores=None,
                memory_bytes=None,
            )
        _write_json(output_dir / 'docker-inspect.json', asdict(inspection))
    probe = _parse_resource_probe(log_text)
    run_work = work_dir / 'run'
    execution = _read_execution_resources_from_work(run_work) or _parse_execution_resources(
        log_text
    )
    result = _read_json_or_none(output_dir / 'result.json')
    samples = _read_jsonl(output_dir / 'samples.jsonl')
    audit = _audit_work_root(run_work)
    binding = _read_json_or_none(output_dir / 'f007-physical-binding.json')
    summary = _adjudicate_f010(
        bank=bank,
        inspection=inspection,
        probe=probe,
        execution=execution,
        result=result,
        samples=samples,
        audit=audit,
        binding=binding,
    )
    _write_json(output_dir / 'f010-final-qualification-summary.json', summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    status = summary['qualification_status']
    if status == _F010_STATUS_PASS:
        return 0
    if status == _F010_STATUS_REVIEW:
        return 2
    if status == _F010_STATUS_INVALID:
        return 3
    return 1


def _docker_create_f010_command(
    *,
    container_name: str,
    output_dir: Path,
    work_dir: Path,
    bank_root: Path,
) -> list[str]:
    envelope = ResourceEnvelope.from_memory_gib(_F010_MEMORY_GIB)
    uid = os.getuid() if hasattr(os, 'getuid') else 1000
    gid = os.getgid() if hasattr(os, 'getgid') else 1000
    memory = f'{envelope.memory_gib}g'
    return [
        'docker',
        'create',
        '--name',
        container_name,
        '--cpus',
        str(envelope.cpu_cores),
        '--memory',
        memory,
        '--memory-swap',
        memory,
        '--user',
        f'{uid}:{gid}',
        '--env',
        'HOME=/tmp',
        '--mount',
        f'type=bind,src={output_dir},dst=/f010/results',
        '--mount',
        f'type=bind,src={work_dir},dst=/f010/work',
        '--mount',
        f'type=bind,src={bank_root / "input"},dst=/f007/input,readonly',
        '--mount',
        (
            f'type=bind,src={bank_root / "f007-dataset-bank-manifest.json"},'
            'dst=/f007/manifest.json,readonly'
        ),
        '--mount',
        (
            f'type=bind,src={bank_root / "f007-dataset-bank-conformance.json"},'
            'dst=/f007/conformance.json,readonly'
        ),
        _F010_IMAGE_TAG,
        'container-f010',
        '--expected-memory-gib',
        str(_F010_MEMORY_GIB),
        '--work-dir',
        '/f010/work/run',
        '--output-dir',
        '/f010/results',
        '--dataset-root',
        '/f007/input',
        '--manifest',
        '/f007/manifest.json',
        '--conformance',
        '/f007/conformance.json',
    ]


# Un fallo semántico/durable es FAIL; una degradación sostenida con integridad limpia deriva a F-011, sin autoescalar recursos.
# La adjudicación final distingue integridad, binding del envelope y degradación que requiere profiling.
def _adjudicate_f010(
    *,
    bank,
    inspection: DockerInspection,
    probe: ResourceProbe | None,
    execution: ExecutionResources | None,
    result: dict[str, Any] | None,
    samples: list[dict[str, Any]],
    audit: PhysicalAudit | None,
    binding: dict[str, Any] | None,
) -> dict[str, Any]:
    envelope = ResourceEnvelope.from_memory_gib(_F010_MEMORY_GIB)
    resource_contract_ok = bool(
        _limits_match(
            envelope=envelope,
            cpu_cores=inspection.cpu_cores,
            memory_bytes=inspection.memory_bytes,
        )
        and probe is not None
        and _limits_match(
            envelope=envelope,
            cpu_cores=probe.cpu_limit_cores,
            memory_bytes=probe.memory_limit_bytes,
        )
        and execution is not None
        and _limits_match(
            envelope=envelope,
            cpu_cores=execution.cpu_limit_cores,
            memory_bytes=execution.memory_limit_bytes,
        )
    )
    physical_binding_ok = bool(
        binding
        and binding.get('dataset_bank_id') == bank.dataset_bank_id
        and binding.get('aggregate_sha256') == bank.aggregate_sha256
        and binding.get('bank_sha256') == bank.bank_sha256
        and binding.get('data_profile') == 'f010-physical-integrated'
        and binding.get('fixed_as_of_utc')
        == bank.window(0).as_of_utc.isoformat().replace('+00:00', 'Z')
        and sorted(binding.get('prewarm_paths', []))
        == sorted((bank.latest_path, bank.pi_daily_path(0)))
    )
    deactivation = None if result is None else result.get('deactivation_decision_pressure')
    adoption = None if result is None else result.get('parameter_adoption_pressure')
    mixed_integrity_ok = bool(
        isinstance(deactivation, dict)
        and deactivation.get('functional_integrity_ok') is True
        and deactivation.get('request_receipt_count') == 480
        and deactivation.get('decision_receipt_count') == 480
        and deactivation.get('management_effect_started_count') == 480
        and deactivation.get('deactivation_effect_started_count') == 480
        and deactivation.get('deactivation_effect_cleared_count') == 480
        and deactivation.get('final_management_pending_count') == 0
        and deactivation.get('final_decision_pending_count') == 0
        and deactivation.get('final_pending_request_count') == 0
    )
    adoption_integrity_ok = bool(
        isinstance(adoption, dict)
        and adoption.get('functional_integrity_ok') is True
        and adoption.get('compatible_change_count') == 1000
        and adoption.get('unchanged_change_count') == 0
        and adoption.get('structural_reset_change_count') == 0
        and adoption.get('disabled_change_count') == 0
        and adoption.get('removed_change_count') == 0
        and adoption.get('rejected_change_count') == 0
        and adoption.get('target_threshold_alarm_count') == 1000
        and adoption.get('effective_cache_revision') == 'PERF-AC-2'
        and adoption.get('adoption_iteration_count') == 1
    )
    result_durable_record_count = _result_int(result, 'durable_record_count')
    result_snapshot_count = _result_int(result, 'snapshot_count')
    audit_ok = bool(
        audit is not None
        and result_durable_record_count is not None
        and audit.durable_record_count == result_durable_record_count
        and audit.duplicate_commit_id_count == 0
        and audit.commit_chain_mismatch_count == 0
        and result_snapshot_count is not None
        and audit.snapshot_count == result_snapshot_count
        and audit.snapshot_alarm_count == _F010_ALARM_COUNT
        and audit.snapshot_last_commit_mismatch_count == 0
        and audit.journal_aligned
    )
    runtime_integrity_ok = bool(
        result
        and result.get('result') == 'PASS'
        and result.get('integrity_ok') is True
        and result.get('snapshot_alarm_count') == _F010_ALARM_COUNT
        and result.get('expected_snapshot_alarm_count') == _F010_ALARM_COUNT
        and result.get('journal_aligned') is True
    )
    natural_stop_ok = bool(execution is not None and execution.stop_reason in _NATURAL_STOP_REASONS)
    hard_integrity_ok = bool(
        inspection.exit_code == 0
        and not inspection.oom_killed
        and runtime_integrity_ok
        and audit_ok
        and mixed_integrity_ok
        and adoption_integrity_ok
        and natural_stop_ok
    )
    iterations = _result_int(result, 'iterations')
    overrun_count = _result_int(result, 'overrun_count')
    overrun_ratio = _result_float(result, 'overrun_ratio')
    p95_start_interval_ms = _sample_percentile(samples, 'start_interval_ms', 95)
    sustained_cadence_degradation = bool(
        iterations is not None
        and (
            iterations < _F010_EXPECTED_ITERATIONS
            or (overrun_ratio is not None and overrun_ratio >= 0.05)
            or (p95_start_interval_ms is not None and p95_start_interval_ms > 5250.0)
        )
    )
    reasons: list[str] = []
    if not resource_contract_ok or not physical_binding_ok:
        qualification_status = _F010_STATUS_INVALID
        if not resource_contract_ok:
            reasons.append('E2 resource contract was not confirmed end-to-end')
        if not physical_binding_ok:
            reasons.append('accepted physical bank binding/prewarm contract was not confirmed')
    elif not hard_integrity_ok:
        qualification_status = _F010_STATUS_FAIL
        reasons.append('final integrated workload failed a hard integrity/durability gate')
    elif sustained_cadence_degradation:
        qualification_status = _F010_STATUS_REVIEW
        reasons.append(
            'clean integrity with sustained cadence degradation; activate F-011 profiling'
        )
    else:
        qualification_status = _F010_STATUS_PASS
        reasons.append(
            'integrated physical workload preserved integrity and 5-second candidate cadence'
        )
    return {
        'contract_version': '1.0.0',
        'test_id': 'F-010',
        'qualification_status': qualification_status,
        'reasons': reasons,
        'operational_representative': False,
        'dataset_bank_id': bank.dataset_bank_id,
        'alarm_count': _F010_ALARM_COUNT,
        'duration_seconds': _F010_DURATION_SECONDS,
        'expected_iterations': _F010_EXPECTED_ITERATIONS,
        'envelope': {
            'key': envelope.key,
            'cpu_cores': envelope.cpu_cores,
            'memory_gib': envelope.memory_gib,
        },
        'resource_contract_ok': resource_contract_ok,
        'physical_binding_ok': physical_binding_ok,
        'hard_integrity_ok': hard_integrity_ok,
        'mixed_deactivation_integrity_ok': mixed_integrity_ok,
        'parameter_adoption_integrity_ok': adoption_integrity_ok,
        'audit_ok': audit_ok,
        'audit_duplicate_commit_id_count': (
            None if audit is None else audit.duplicate_commit_id_count
        ),
        'audit_commit_chain_mismatch_count': (
            None if audit is None else audit.commit_chain_mismatch_count
        ),
        'audit_snapshot_count': None if audit is None else audit.snapshot_count,
        'audit_snapshot_alarm_count': (None if audit is None else audit.snapshot_alarm_count),
        'audit_snapshot_materialization_mismatch_count': (
            None if audit is None else audit.snapshot_last_commit_mismatch_count
        ),
        'audit_journal_aligned': None if audit is None else audit.journal_aligned,
        'natural_stop_ok': natural_stop_ok,
        'iterations': iterations,
        'p50_iteration_ms': _result_float(result, 'p50_iteration_ms'),
        'p95_iteration_ms': _result_float(result, 'p95_iteration_ms'),
        'p99_iteration_ms': _result_float(result, 'p99_iteration_ms'),
        'p95_start_interval_ms': p95_start_interval_ms,
        'overrun_count': overrun_count,
        'overrun_ratio': overrun_ratio,
        'source_load_p95_ms': _result_float(result, 'source_load_p95_ms'),
        'source_view_count': _result_int(result, 'source_view_count'),
        'latest_source_column_count': _result_int(result, 'latest_source_column_count'),
        'historical_source_column_count': _result_int(result, 'historical_source_column_count'),
        'historical_source_row_count': _result_int(result, 'historical_source_row_count'),
        'cpu_peak_percent': None if execution is None else execution.cpu_peak_percent,
        'memory_peak_percent': None if execution is None else execution.memory_peak_percent,
        'cpu_throttled_seconds': (None if execution is None else execution.cpu_throttled_seconds),
        'durable_record_count': None if audit is None else audit.durable_record_count,
        'journal_bytes': None if audit is None else audit.journal_bytes,
        'stop_reason': None if execution is None else execution.stop_reason,
        'f011_profile_required': qualification_status == _F010_STATUS_REVIEW,
    }


def _run_container_physical_gate(args: argparse.Namespace) -> int:
    from atlanticus.runtime._resource_sampler import CgroupResourceSampler
    from performance.f007_physical import F007DatasetBank, run_f007_physical_profile_gate

    envelope = ResourceEnvelope.from_memory_gib(args.expected_memory_gib)
    sample = CgroupResourceSampler().sample()
    probe = {
        'cpu_limit_cores': sample.cpu_limit_cores,
        'memory_limit_bytes': sample.memory_limit_bytes,
    }
    print(_PROBE_PREFIX + json.dumps(probe, sort_keys=True), flush=True)
    if not _limits_match(
        envelope=envelope,
        cpu_cores=sample.cpu_limit_cores,
        memory_bytes=sample.memory_limit_bytes,
    ):
        print('F007_RESOURCE_CONTRACT_ERROR requested container limits do not match cgroups')
        return 3
    bank = F007DatasetBank.load(
        manifest_path=args.manifest,
        conformance_path=args.conformance,
        input_root=args.dataset_root,
        require_read_only=True,
    )
    document = run_f007_physical_profile_gate(bank=bank)
    _write_json(Path(args.output_dir) / 'f007-physical-profile-gate.json', document)
    print(json.dumps(document, indent=2, sort_keys=True))
    return 0 if document.get('status') == 'PASS' else 2


def _run_physical_gate(
    *,
    repository_root: Path,
    memory_gib: int,
    replace: bool,
) -> int:
    envelope = ResourceEnvelope.from_memory_gib(memory_gib)
    bank_root = _dataset_bank_root(repository_root)
    _load_host_dataset_bank(bank_root)
    output_dir = (
        repository_root
        / 'scopes/ada/processes/alarms-runtime/performance/results/F-007/physical-gate'
    )
    if replace:
        shutil.rmtree(output_dir, ignore_errors=True)
    elif output_dir.exists():
        raise RuntimeError('F-007 physical gate output already exists; use --replace')
    output_dir.mkdir(parents=True)
    container_name = f'atlanticus-f007-physical-gate-{uuid.uuid4().hex[:8]}'
    command = _docker_create_physical_gate_command(
        envelope=envelope,
        container_name=container_name,
        output_dir=output_dir,
        bank_root=bank_root,
    )
    create = subprocess.run(command, cwd=repository_root, text=True, capture_output=True)
    if create.returncode != 0:
        raise RuntimeError(create.stderr.strip() or create.stdout.strip() or 'docker create failed')
    log_lines: list[str] = []
    try:
        process = subprocess.Popen(
            ['docker', 'start', '--attach', container_name],
            cwd=repository_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
        )
        if process.stdout is None:
            raise RuntimeError('docker start did not expose stdout')
        for line in process.stdout:
            print(line, end='')
            log_lines.append(line)
        return_code = process.wait()
    finally:
        subprocess.run(
            ['docker', 'rm', '--force', container_name],
            cwd=repository_root,
            text=True,
            capture_output=True,
        )
    (output_dir / 'docker.log').write_text(''.join(log_lines), encoding='utf-8')
    result = _read_json_or_none(output_dir / 'f007-physical-profile-gate.json')
    if return_code != 0 or result is None or result.get('status') != 'PASS':
        return 2
    return 0


# Phase A-P revalida 1000 alarmas con input físico en E2.
# Sólo si E2 pierde margen se prueban E3 y E4 con el mismo volumen.
def _run_phase_a(
    *,
    repository_root: Path,
    output_root: Path,
    work_root: Path,
    replace: bool,
) -> int:
    summaries: list[StageSummary] = []
    anchor = _run_or_reuse_stage(
        repository_root=repository_root,
        stage=DockerStressStage(
            alarm_count=_CAPACITY_ANCHOR_ALARM_COUNT,
            envelope=ResourceEnvelope.from_memory_gib(2),
        ),
        output_root=output_root,
        work_root=work_root,
        replace=replace,
    )
    summaries.append(anchor)
    if anchor.classification in {_BOUNDARY, _SATURATED}:
        for memory_gib in (3, 4):
            summary = _run_or_reuse_stage(
                repository_root=repository_root,
                stage=DockerStressStage(
                    alarm_count=_CAPACITY_ANCHOR_ALARM_COUNT,
                    envelope=ResourceEnvelope.from_memory_gib(memory_gib),
                ),
                output_root=output_root,
                work_root=work_root,
                replace=replace,
            )
            summaries.append(summary)
            if summary.classification in {_PRODUCT_FAIL, _INVALID}:
                break
    e4_anchor = next((item for item in summaries if item.memory_gib == 4), None)
    search_authorized = (
        anchor.classification == _GREEN
        or e4_anchor is not None
        and e4_anchor.classification == _GREEN
    )
    baseline_envelope = next(
        (item.memory_gib for item in summaries if item.classification in {_GREEN, _BOUNDARY}),
        None,
    )
    outcome = {
        'contract_version': _CAPACITY_SEARCH_CONTRACT_VERSION,
        'phase': 'A-P',
        'anchor_alarm_count': _CAPACITY_ANCHOR_ALARM_COUNT,
        'anchor_memory_gib': 2,
        'search_memory_gib': _CAPACITY_SEARCH_MEMORY_GIB,
        'search_authorized': search_authorized,
        'baseline_envelope_memory_gib': baseline_envelope,
        'stages': [item.as_document() for item in summaries],
    }
    _write_json(output_root / 'phase-a-summary.json', outcome)
    print(json.dumps(outcome, indent=2, sort_keys=True))
    return _phase_exit_code(summaries)


# Phase B-P busca el bracket en E4. Primero duplica alarm_count y luego refina binariamente.
# Un corte explícito del operador conserva evidencia, pero no produce stress_reference_load.
def _run_phase_b(
    *,
    repository_root: Path,
    output_root: Path,
    work_root: Path,
    stop_after_alarm_count: int | None,
    replace: bool,
) -> int:
    _require_phase_a_search_authorization(output_root)
    if stop_after_alarm_count is not None and stop_after_alarm_count < _CAPACITY_ANCHOR_ALARM_COUNT:
        raise ValueError('phase-b stop-after-alarm-count must be at least 1000')
    summaries: list[StageSummary] = []
    decisions: list[dict[str, Any]] = []
    lower_green: int | None = None
    upper_non_green: int | None = None
    alarm_count = _CAPACITY_ANCHOR_ALARM_COUNT
    manual_stop = False
    aborted_classification: str | None = None
    # Expansión abierta: 1000, 2000, 4000, ... hasta el primer non-GREEN o corte explícito.
    while True:
        if stop_after_alarm_count is not None and alarm_count > stop_after_alarm_count:
            manual_stop = True
            break
        summary = _run_or_reuse_stage(
            repository_root=repository_root,
            stage=DockerStressStage(
                alarm_count=alarm_count,
                envelope=ResourceEnvelope.from_memory_gib(_CAPACITY_SEARCH_MEMORY_GIB),
            ),
            output_root=output_root,
            work_root=work_root,
            replace=replace,
        )
        summaries.append(summary)
        decisions.append(
            {
                'mode': 'EXPANSION',
                'alarm_count': alarm_count,
                'classification': summary.classification,
            }
        )
        if summary.classification == _GREEN:
            lower_green = alarm_count
            next_alarm_count = alarm_count * 2
            if stop_after_alarm_count is not None and next_alarm_count > stop_after_alarm_count:
                manual_stop = True
                break
            alarm_count = next_alarm_count
            continue
        if summary.classification in {_BOUNDARY, _SATURATED}:
            upper_non_green = alarm_count
            break
        aborted_classification = summary.classification
        break
    # Con bracket cerrado, los midpoints reducen el intervalo sin superar tres refinamientos válidos.
    refinement_stages = 0
    while (
        aborted_classification is None
        and lower_green is not None
        and upper_non_green is not None
        and not _phase_b_refinement_complete(
            lower_green=lower_green,
            upper_non_green=upper_non_green,
            refinement_stages=refinement_stages,
        )
    ):
        candidate = _phase_b_refinement(
            lower_green=lower_green,
            upper_non_green=upper_non_green,
        )
        summary = _run_or_reuse_stage(
            repository_root=repository_root,
            stage=DockerStressStage(
                alarm_count=candidate,
                envelope=ResourceEnvelope.from_memory_gib(_CAPACITY_SEARCH_MEMORY_GIB),
            ),
            output_root=output_root,
            work_root=work_root,
            replace=replace,
        )
        summaries.append(summary)
        decisions.append(
            {
                'mode': 'REFINEMENT',
                'alarm_count': candidate,
                'classification': summary.classification,
                'lower_green_before': lower_green,
                'upper_non_green_before': upper_non_green,
            }
        )
        if summary.classification == _GREEN:
            lower_green = candidate
            refinement_stages += 1
            continue
        if summary.classification in {_BOUNDARY, _SATURATED}:
            upper_non_green = candidate
            refinement_stages += 1
            continue
        aborted_classification = summary.classification
        break
    bracket_closed = lower_green is not None and upper_non_green is not None
    relative_gap = None if not bracket_closed else (upper_non_green - lower_green) / lower_green
    if aborted_classification is not None:
        search_status = f'ABORTED_{aborted_classification.replace(" ", "_")}'
    elif bracket_closed:
        search_status = 'BRACKETED'
    elif manual_stop and lower_green is not None:
        search_status = 'OPEN_HIGHER_THAN_TESTED'
    elif upper_non_green == _CAPACITY_ANCHOR_ALARM_COUNT:
        search_status = 'NO_GREEN_LOWER_BOUND'
    else:
        search_status = 'INCOMPLETE'
    outcome = {
        'contract_version': _CAPACITY_SEARCH_CONTRACT_VERSION,
        'phase': 'B-P',
        'search_memory_gib': _CAPACITY_SEARCH_MEMORY_GIB,
        'search_status': search_status,
        'exploration_multiplier': 2,
        'predetermined_alarm_count_ceiling': None,
        'operator_stop_after_alarm_count': stop_after_alarm_count,
        'manual_stop': manual_stop,
        'refinement_stage_count': refinement_stages,
        'refinement_stage_limit': _CAPACITY_MAX_REFINEMENT_STAGES,
        'refinement_gap_ratio_limit': _CAPACITY_REFINEMENT_GAP_RATIO,
        'lower_green_alarm_count': lower_green,
        'upper_non_green_alarm_count': upper_non_green,
        'relative_gap': relative_gap,
        'stress_reference_load': lower_green if bracket_closed else None,
        'knee_interval': (
            None
            if not bracket_closed
            else {
                'lower_green_alarm_count': lower_green,
                'upper_non_green_alarm_count': upper_non_green,
                'relative_gap': relative_gap,
            }
        ),
        'knee_greater_than_tested_alarm_count': (
            lower_green if search_status == 'OPEN_HIGHER_THAN_TESTED' else None
        ),
        'decisions': decisions,
        'stages': [item.as_document() for item in summaries],
    }
    _write_json(output_root / 'phase-b-summary.json', outcome)
    print(json.dumps(outcome, indent=2, sort_keys=True))
    return _phase_exit_code(summaries)


# Phase C-P caracteriza el último GREEN-MARGIN de E4 sobre envelopes menores.
# E4 se reutiliza como evidencia de Phase B para no introducir una repetición innecesaria.
def _run_phase_c(
    *,
    repository_root: Path,
    output_root: Path,
    work_root: Path,
    headroom_memory_gib: tuple[int, ...],
    replace: bool,
) -> int:
    reference_load = _phase_b_stress_reference(output_root)
    if any(value not in {5, 6} for value in headroom_memory_gib):
        raise ValueError('phase-c headroom memory must be 5 or 6 GiB')
    summaries: list[StageSummary] = []
    for memory_gib in (2, 3):
        summary = _run_or_reuse_stage(
            repository_root=repository_root,
            stage=DockerStressStage(
                alarm_count=reference_load,
                envelope=ResourceEnvelope.from_memory_gib(memory_gib),
            ),
            output_root=output_root,
            work_root=work_root,
            replace=replace,
        )
        summaries.append(summary)
        if summary.classification in {_PRODUCT_FAIL, _INVALID}:
            outcome = _phase_c_outcome(
                reference_load=reference_load,
                summaries=summaries,
                e4_reused_from_phase_b=False,
            )
            _write_json(output_root / 'phase-c-summary.json', outcome)
            print(json.dumps(outcome, indent=2, sort_keys=True))
            return _phase_exit_code(summaries)
    e4_summary_path = output_root / f'e4-a{reference_load}' / 'stage-summary.json'
    if not e4_summary_path.is_file():
        raise RuntimeError('phase-c requires the Phase B E4 stress-reference stage summary')
    e4_summary = _load_summary(e4_summary_path)
    if e4_summary.classification != _GREEN:
        raise RuntimeError('phase-c stress-reference E4 stage must be GREEN-MARGIN')
    summaries.append(e4_summary)
    base_outcome = _phase_c_outcome(
        reference_load=reference_load,
        summaries=summaries,
        e4_reused_from_phase_b=True,
    )
    if headroom_memory_gib and base_outcome['recommended_memory_gib'] != 4:
        raise RuntimeError('phase-c headroom is allowed only when E4 is the recommended envelope')
    for memory_gib in dict.fromkeys(headroom_memory_gib):
        summary = _run_or_reuse_stage(
            repository_root=repository_root,
            stage=DockerStressStage(
                alarm_count=reference_load,
                envelope=ResourceEnvelope.from_memory_gib(memory_gib),
            ),
            output_root=output_root,
            work_root=work_root,
            replace=replace,
        )
        summaries.append(summary)
        if summary.classification in {_PRODUCT_FAIL, _INVALID}:
            break
    outcome = _phase_c_outcome(
        reference_load=reference_load,
        summaries=summaries,
        e4_reused_from_phase_b=True,
    )
    _write_json(output_root / 'phase-c-summary.json', outcome)
    print(json.dumps(outcome, indent=2, sort_keys=True))
    return _phase_exit_code(summaries)


def _phase_c_outcome(
    *,
    reference_load: int,
    summaries: list[StageSummary],
    e4_reused_from_phase_b: bool,
) -> dict[str, Any]:
    viable = [item.memory_gib for item in summaries if item.classification in {_GREEN, _BOUNDARY}]
    recommended = [item.memory_gib for item in summaries if item.classification == _GREEN]
    return {
        'contract_version': _CAPACITY_SEARCH_CONTRACT_VERSION,
        'phase': 'C-P',
        'stress_reference_load': reference_load,
        'stages': [item.as_document() for item in summaries],
        'minimum_viable_memory_gib': min(viable) if viable else None,
        'recommended_memory_gib': min(recommended) if recommended else None,
        'e4_reused_from_phase_b': e4_reused_from_phase_b,
    }


# El midpoint se redondea a centenas para mantener stages legibles y reproducibles.
def _phase_b_refinement(*, lower_green: int, upper_non_green: int) -> int:
    if lower_green >= upper_non_green:
        raise ValueError('phase-b refinement requires lower_green < upper_non_green')
    midpoint = (lower_green + upper_non_green) // 2
    candidate = ((midpoint + 50) // 100) * 100
    if not lower_green < candidate < upper_non_green:
        raise ValueError('phase-b refinement bracket is too narrow for a new 100-alarm candidate')
    return candidate


def _phase_b_refinement_complete(
    *,
    lower_green: int,
    upper_non_green: int,
    refinement_stages: int,
) -> bool:
    if refinement_stages >= _CAPACITY_MAX_REFINEMENT_STAGES:
        return True
    return (upper_non_green - lower_green) / lower_green <= _CAPACITY_REFINEMENT_GAP_RATIO


# La autorización queda persistida en JSON; no se infiere a partir de archivos parciales.
def _require_phase_a_search_authorization(output_root: Path) -> dict[str, Any]:
    document = _read_json_or_none(output_root / 'phase-a-summary.json')
    if document is None:
        raise RuntimeError('phase-b requires an accepted Phase A-P summary')
    if document.get('contract_version') != _CAPACITY_SEARCH_CONTRACT_VERSION:
        raise RuntimeError('phase-b Phase A-P summary uses an unsupported contract version')
    if document.get('search_authorized') is not True:
        raise RuntimeError('phase-b is blocked because Phase A-P did not authorize upward search')
    return document


# Phase C sólo acepta un bracket cerrado y obtiene L directamente de la evidencia de Phase B.
def _phase_b_stress_reference(output_root: Path) -> int:
    document = _read_json_or_none(output_root / 'phase-b-summary.json')
    if document is None:
        raise RuntimeError('phase-c requires a completed Phase B-P summary')
    if document.get('contract_version') != _CAPACITY_SEARCH_CONTRACT_VERSION:
        raise RuntimeError('phase-c Phase B-P summary uses an unsupported contract version')
    if document.get('search_status') != 'BRACKETED':
        raise RuntimeError('phase-c requires a closed Phase B-P capacity bracket')
    reference_load = _optional_int(document.get('stress_reference_load'))
    if reference_load is None or reference_load < _CAPACITY_ANCHOR_ALARM_COUNT:
        raise RuntimeError('phase-c Phase B-P summary has no valid stress-reference load')
    return reference_load


# Cada stage conserva aislamiento de contenedor; sin --replace se reutiliza evidencia ya adjudicada.
def _run_or_reuse_stage(
    *,
    repository_root: Path,
    stage: DockerStressStage,
    output_root: Path,
    work_root: Path,
    replace: bool,
) -> StageSummary:
    stage_output = output_root / stage.stage_id
    summary_path = stage_output / 'stage-summary.json'
    if summary_path.is_file() and not replace:
        return _load_summary(summary_path)
    return _run_stage(
        repository_root=repository_root,
        stage=stage,
        output_root=output_root,
        work_root=work_root,
        replace=replace,
    )


def _run_stage(
    *,
    repository_root: Path,
    stage: DockerStressStage,
    output_root: Path,
    work_root: Path,
    replace: bool,
) -> StageSummary:
    stage_output = output_root / stage.stage_id
    stage_work = work_root / stage.stage_id
    if replace:
        shutil.rmtree(stage_output, ignore_errors=True)
        shutil.rmtree(stage_work, ignore_errors=True)
    elif stage_output.exists() or stage_work.exists():
        raise RuntimeError(f'F-007 stage already exists: {stage.stage_id}; use --replace')
    stage_output.mkdir(parents=True)
    stage_work.mkdir(parents=True)
    bank_root = _dataset_bank_root(repository_root)
    _load_host_dataset_bank(bank_root)
    container_name = f'atlanticus-f007-{stage.stage_id}-{uuid.uuid4().hex[:8]}'
    create_command = _docker_create_command(
        stage=stage,
        container_name=container_name,
        stage_output=stage_output,
        stage_work=stage_work,
        bank_root=bank_root,
    )
    create = subprocess.run(create_command, cwd=repository_root, text=True, capture_output=True)
    if create.returncode != 0:
        raise RuntimeError(create.stderr.strip() or create.stdout.strip() or 'docker create failed')
    log_lines: list[str] = []
    start_return_code = 125
    inspection: DockerInspection | None = None
    try:
        process = subprocess.Popen(
            ['docker', 'start', '--attach', container_name],
            cwd=repository_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
        )
        if process.stdout is None:
            raise RuntimeError('docker start did not expose stdout')
        for line in process.stdout:
            print(line, end='')
            log_lines.append(line)
        start_return_code = process.wait()
        inspection = _inspect_container(
            container_name=container_name, repository_root=repository_root
        )
    finally:
        subprocess.run(
            ['docker', 'rm', '--force', container_name],
            cwd=repository_root,
            text=True,
            capture_output=True,
        )
    log_text = ''.join(log_lines)
    (stage_output / 'docker.log').write_text(log_text, encoding='utf-8')
    if inspection is None:
        inspection = DockerInspection(
            exit_code=start_return_code,
            oom_killed=False,
            cpu_cores=None,
            memory_bytes=None,
        )
    _write_json(stage_output / 'docker-inspect.json', asdict(inspection))
    probe = _parse_resource_probe(log_text)
    run_work = stage_work / 'run'
    execution = _read_execution_resources_from_work(run_work) or _parse_execution_resources(
        log_text
    )
    result = _read_json_or_none(stage_output / 'result.json')
    samples = _read_jsonl(stage_output / 'samples.jsonl')
    audit = _audit_work_root(run_work)
    summary = _adjudicate_stage(
        stage=stage,
        inspection=inspection,
        probe=probe,
        execution=execution,
        result=result,
        samples=samples,
        audit=audit,
    )
    _write_json(stage_output / 'stage-summary.json', summary.as_document())
    return summary


# El banco nunca entra a la imagen: se monta junto con manifest y conformance en read-only.
def _docker_create_command(
    *,
    stage: DockerStressStage,
    container_name: str,
    stage_output: Path,
    stage_work: Path,
    bank_root: Path | None = None,
) -> list[str]:
    uid = os.getuid() if hasattr(os, 'getuid') else 1000
    gid = os.getgid() if hasattr(os, 'getgid') else 1000
    memory = f'{stage.envelope.memory_gib}g'
    resolved_bank_root = (
        _dataset_bank_root(_repository_root()) if bank_root is None else bank_root.resolve()
    )
    return [
        'docker',
        'create',
        '--name',
        container_name,
        '--cpus',
        str(stage.envelope.cpu_cores),
        '--memory',
        memory,
        '--memory-swap',
        memory,
        '--user',
        f'{uid}:{gid}',
        '--env',
        'HOME=/tmp',
        '--mount',
        f'type=bind,src={stage_output},dst=/f007/results',
        '--mount',
        f'type=bind,src={stage_work},dst=/f007/work',
        '--mount',
        f'type=bind,src={resolved_bank_root / "input"},dst=/f007/input,readonly',
        '--mount',
        (
            f'type=bind,src={resolved_bank_root / "f007-dataset-bank-manifest.json"},'
            'dst=/f007/manifest.json,readonly'
        ),
        '--mount',
        (
            f'type=bind,src={resolved_bank_root / "f007-dataset-bank-conformance.json"},'
            'dst=/f007/conformance.json,readonly'
        ),
        _IMAGE_TAG,
        'container-stage',
        '--alarm-count',
        str(stage.alarm_count),
        '--expected-memory-gib',
        str(stage.envelope.memory_gib),
        '--work-dir',
        '/f007/work/run',
        '--output-dir',
        '/f007/results',
        '--dataset-root',
        '/f007/input',
        '--manifest',
        '/f007/manifest.json',
        '--conformance',
        '/f007/conformance.json',
    ]


def _docker_create_physical_gate_command(
    *,
    envelope: ResourceEnvelope,
    container_name: str,
    output_dir: Path,
    bank_root: Path,
) -> list[str]:
    uid = os.getuid() if hasattr(os, 'getuid') else 1000
    gid = os.getgid() if hasattr(os, 'getgid') else 1000
    memory = f'{envelope.memory_gib}g'
    return [
        'docker',
        'create',
        '--name',
        container_name,
        '--cpus',
        str(envelope.cpu_cores),
        '--memory',
        memory,
        '--memory-swap',
        memory,
        '--user',
        f'{uid}:{gid}',
        '--env',
        'HOME=/tmp',
        '--mount',
        f'type=bind,src={output_dir},dst=/f007/results',
        '--mount',
        f'type=bind,src={bank_root / "input"},dst=/f007/input,readonly',
        '--mount',
        (
            f'type=bind,src={bank_root / "f007-dataset-bank-manifest.json"},'
            'dst=/f007/manifest.json,readonly'
        ),
        '--mount',
        (
            f'type=bind,src={bank_root / "f007-dataset-bank-conformance.json"},'
            'dst=/f007/conformance.json,readonly'
        ),
        _IMAGE_TAG,
        'container-physical-gate',
        '--expected-memory-gib',
        str(envelope.memory_gib),
        '--dataset-root',
        '/f007/input',
        '--manifest',
        '/f007/manifest.json',
        '--conformance',
        '/f007/conformance.json',
        '--output-dir',
        '/f007/results',
    ]


def _dataset_bank_root(repository_root: Path) -> Path:
    return (
        repository_root
        / 'scopes'
        / 'ada'
        / 'processes'
        / 'alarms-runtime'
        / '.performance-work'
        / 'F-007-dataset-bank-v1'
    ).resolve()


def _load_host_dataset_bank(bank_root: Path):
    from performance.f007_physical import F007DatasetBank

    return F007DatasetBank.load(
        manifest_path=bank_root / 'f007-dataset-bank-manifest.json',
        conformance_path=bank_root / 'f007-dataset-bank-conformance.json',
        input_root=bank_root / 'input',
        require_read_only=False,
    )


def _cgroup_stage_document(*, before, after) -> dict[str, object]:
    return {
        'memory_current_before': before.memory_current,
        'memory_current_after': after.memory_current,
        'memory_anon_before': before.memory_anon,
        'memory_anon_after': after.memory_anon,
        'memory_file_before': before.memory_file,
        'memory_file_after': after.memory_file,
        'memory_active_file_before': before.memory_active_file,
        'memory_active_file_after': after.memory_active_file,
        'memory_inactive_file_before': before.memory_inactive_file,
        'memory_inactive_file_after': after.memory_inactive_file,
        'io_read_bytes_delta': max(0, after.io_read_bytes - before.io_read_bytes),
        'io_read_operations_delta': max(0, after.io_read_operations - before.io_read_operations),
        'cpu_usage_usec_delta': max(0, after.cpu_usage_usec - before.cpu_usage_usec),
        'cpu_nr_throttled_delta': max(0, after.cpu_nr_throttled - before.cpu_nr_throttled),
        'cpu_throttled_usec_delta': max(0, after.cpu_throttled_usec - before.cpu_throttled_usec),
    }


def _build_image(*, repository_root: Path, rebuild: bool, image_tag: str = _IMAGE_TAG) -> None:
    if not rebuild:
        present = subprocess.run(
            ['docker', 'image', 'inspect', image_tag],
            cwd=repository_root,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if present.returncode == 0:
            return
    with tempfile.TemporaryDirectory(prefix='atlanticus-r35-build-') as temporary:
        context_root = Path(temporary) / 'repository'
        shutil.copytree(repository_root, context_root, ignore=_docker_context_ignore)
        dockerfile = (
            context_root
            / 'scopes'
            / 'ada'
            / 'processes'
            / 'alarms-runtime'
            / 'performance'
            / 'docker'
            / 'F007.Dockerfile'
        )
        completed = subprocess.run(
            [
                'docker',
                'build',
                '--file',
                str(dockerfile),
                '--tag',
                image_tag,
                str(context_root),
            ],
            cwd=repository_root,
            text=True,
        )
    if completed.returncode != 0:
        raise RuntimeError('R3.5 Docker image build failed')


def _docker_context_ignore(directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        if name in {
            '.git',
            '.venv',
            '.runtime',
            '.pytest_cache',
            '.ruff_cache',
            '.performance-work',
            '__pycache__',
            'artifacts',
            'distribution',
        }:
            ignored.add(name)
            continue
        if name == '.env' or name.startswith('.env.'):
            ignored.add(name)
            continue
        if name in {'secrets.json', 'secrets.detail.json'}:
            ignored.add(name)
    return ignored


def _inspect_container(*, container_name: str, repository_root: Path) -> DockerInspection:
    completed = subprocess.run(
        ['docker', 'inspect', container_name],
        cwd=repository_root,
        check=True,
        text=True,
        capture_output=True,
    )
    document = json.loads(completed.stdout)[0]
    state = document.get('State', {})
    host = document.get('HostConfig', {})
    nano_cpus = host.get('NanoCpus')
    memory = host.get('Memory')
    return DockerInspection(
        exit_code=int(state.get('ExitCode', 0)),
        oom_killed=bool(state.get('OOMKilled', False)),
        cpu_cores=(None if not nano_cpus else float(nano_cpus) / 1_000_000_000),
        memory_bytes=None if not memory else int(memory),
    )


def _parse_resource_probe(log_text: str) -> ResourceProbe | None:
    for line in log_text.splitlines():
        if line.startswith(_PROBE_PREFIX):
            document = json.loads(line[len(_PROBE_PREFIX) :])
            return ResourceProbe(
                cpu_limit_cores=_optional_float(document.get('cpu_limit_cores')),
                memory_limit_bytes=_optional_int(document.get('memory_limit_bytes')),
            )
    return None


def _parse_execution_resources(log_text: str) -> ExecutionResources | None:
    for line in reversed(log_text.splitlines()):
        if ' completed' not in line or 'cpu_limit=' not in line or 'memory_limit=' not in line:
            continue
        details: dict[str, str] = {}
        for part in line.split(' | ')[1:]:
            if '=' not in part:
                continue
            key, value = part.split('=', 1)
            details[key.strip()] = value.strip()
        return ExecutionResources(
            cpu_limit_cores=_optional_float(details.get('cpu_limit')),
            memory_limit_bytes=_parse_memory_bytes(details.get('memory_limit')),
            cpu_peak_percent=_parse_percent(details.get('cpu_peak')),
            memory_peak_percent=_parse_percent(details.get('memory_peak')),
            cpu_throttled_seconds=_optional_float(details.get('cpu_throttled_seconds')),
            work_iterations=_optional_int(details.get('work')),
            empty_iterations=_optional_int(details.get('empty')),
            stop_reason=details.get('stop'),
        )
    return None


def _read_execution_resources_from_work(stage_work: Path) -> ExecutionResources | None:
    log_root = stage_work / 'volume' / 'ada-alarms-runtime-performance' / 'logs'
    candidates = sorted(log_root.glob('**/executions.jsonl'))
    for path in reversed(candidates):
        documents = _read_jsonl(path)
        for document in reversed(documents):
            if document.get('event') != 'execution.completed':
                continue
            return ExecutionResources(
                cpu_limit_cores=_optional_float(document.get('cpu_limit_cores')),
                memory_limit_bytes=_optional_int(document.get('memory_limit_bytes')),
                cpu_peak_percent=_optional_float(document.get('cpu_peak_percent')),
                memory_peak_percent=_optional_float(document.get('memory_peak_percent')),
                cpu_throttled_seconds=_optional_float(document.get('cpu_throttled_seconds')),
                work_iterations=_optional_int(document.get('work_iterations')),
                empty_iterations=_optional_int(document.get('empty_iterations')),
                stop_reason=(
                    str(document['stop_reason'])
                    if document.get('stop_reason') is not None
                    else None
                ),
            )
    return None


# El WAL puede intercalar commits de muchos priority_group: previous_commit_id se valida por grupo,
# nunca como una cadena global. El snapshot físico final debe ser exactamente el último snapshot_after
# durable de ese grupo; los last_commit_id de alarmas individuales pueden quedar detrás del HEAD si no cambiaron.
def _audit_work_root(stage_work: Path) -> PhysicalAudit | None:
    runtime_root = stage_work / 'volume' / 'ada' / 'alarms' / 'runtime'
    if not runtime_root.exists():
        return None
    journal_files = sorted((runtime_root / 'journal').glob('**/*.jsonl'))
    records: list[dict[str, Any]] = []
    journal_bytes = 0
    for path in journal_files:
        journal_bytes += path.stat().st_size
        for line in path.read_text(encoding='utf-8').splitlines():
            if line.strip():
                records.append(json.loads(line))
    commit_ids: list[str] = []
    last_commit_by_group: dict[str, str] = {}
    latest_snapshot_by_group: dict[str, dict[str, Any]] = {}
    chain_mismatches = 0
    for record in records:
        commit = record.get('commit')
        if not isinstance(commit, dict):
            continue
        commit_id = str(commit.get('commit_id', ''))
        priority_group = commit.get('priority_group')
        commit_ids.append(commit_id)
        if not isinstance(priority_group, str) or not priority_group:
            chain_mismatches += 1
            continue
        expected_previous = last_commit_by_group.get(priority_group)
        if commit.get('previous_commit_id') != expected_previous:
            chain_mismatches += 1
        last_commit_by_group[priority_group] = commit_id
        snapshot_after = record.get('snapshot_after')
        if isinstance(snapshot_after, dict):
            latest_snapshot_by_group[priority_group] = snapshot_after
    duplicate_count = len(commit_ids) - len(set(commit_ids))
    snapshot_files = sorted((runtime_root / 'state' / 'groups').glob('*.json'))
    snapshot_alarm_count = 0
    physical_snapshot_by_group: dict[str, dict[str, Any]] = {}
    snapshot_materialization_mismatches = 0
    for path in snapshot_files:
        document = json.loads(path.read_text(encoding='utf-8'))
        alarms = document.get('alarms', {})
        if isinstance(alarms, dict):
            snapshot_alarm_count += len(alarms)
        priority_group = document.get('priority_group')
        if (
            not isinstance(priority_group, str)
            or not priority_group
            or priority_group in physical_snapshot_by_group
        ):
            snapshot_materialization_mismatches += 1
            continue
        physical_snapshot_by_group[priority_group] = document
    for priority_group in set(latest_snapshot_by_group) | set(physical_snapshot_by_group):
        if latest_snapshot_by_group.get(priority_group) != physical_snapshot_by_group.get(
            priority_group
        ):
            snapshot_materialization_mismatches += 1
    head = _read_json_or_none(runtime_root / 'state' / 'journal-head.json')
    journal_aligned = bool(
        head
        and isinstance(head.get('durable'), dict)
        and head.get('durable') == head.get('materialized')
    )
    return PhysicalAudit(
        durable_record_count=len(commit_ids),
        journal_bytes=journal_bytes,
        duplicate_commit_id_count=duplicate_count,
        commit_chain_mismatch_count=chain_mismatches,
        snapshot_count=len(snapshot_files),
        snapshot_alarm_count=snapshot_alarm_count,
        snapshot_last_commit_mismatch_count=snapshot_materialization_mismatches,
        journal_aligned=journal_aligned,
    )


def _adjudicate_stage(
    *,
    stage: DockerStressStage,
    inspection: DockerInspection,
    probe: ResourceProbe | None,
    execution: ExecutionResources | None,
    result: dict[str, Any] | None,
    samples: list[dict[str, Any]],
    audit: PhysicalAudit | None,
) -> StageSummary:
    resource_contract_ok = _resource_contract_ok(
        stage=stage,
        inspection=inspection,
        probe=probe,
        execution=execution,
    )
    p95_start_interval = _sample_percentile(samples, 'start_interval_ms', 95)
    iterations = _result_int(result, 'iterations')
    p95_iteration = _result_float(result, 'p95_iteration_ms')
    p99_iteration = _result_float(result, 'p99_iteration_ms')
    overrun_count = _result_int(result, 'overrun_count')
    overrun_ratio = _result_float(result, 'overrun_ratio')
    reasons: list[str] = []
    if not resource_contract_ok:
        classification = _INVALID
        reasons.append(
            'resource envelope was not confirmed by Docker inspect, cgroup probe, and execution summary'
        )
    elif inspection.oom_killed or inspection.exit_code == 137:
        classification = _SATURATED
        reasons.append('container was OOM-killed or exited with code 137')
    elif inspection.exit_code != 0 and result is None:
        classification = _PRODUCT_FAIL
        reasons.append(
            f'container exited with code {inspection.exit_code} without a result document'
        )
    elif result is None:
        classification = _PRODUCT_FAIL
        reasons.append('result.json is missing')
    elif result.get('result') == 'FAIL' or result.get('integrity_ok') is not True:
        classification = _PRODUCT_FAIL
        reasons.append('performance runner reported an integrity/product failure')
    else:
        complete = iterations == stage.expected_iterations
        if complete and not _completed_integrity_ok(
            stage=stage, result=result, audit=audit, execution=execution
        ):
            classification = _PRODUCT_FAIL
            reasons.append('completed stage failed exact durability/snapshot/drain integrity gates')
        elif not complete:
            classification = _SATURATED
            reasons.append('stage did not complete the expected 61 iterations')
        else:
            memory_peak = None if execution is None else execution.memory_peak_percent
            if p95_iteration is not None and p95_iteration >= 5000:
                classification = _SATURATED
                reasons.append('p95 iteration reached or exceeded the 5-second period')
            elif overrun_ratio is not None and overrun_ratio >= 0.05:
                classification = _SATURATED
                reasons.append('overrun ratio reached or exceeded 5%')
            elif memory_peak is not None and memory_peak >= 95:
                classification = _SATURATED
                reasons.append('container memory peak reached or exceeded 95%')
            elif (
                p95_iteration is not None
                and p95_iteration >= 3750
                or p99_iteration is not None
                and p99_iteration >= 5000
                or overrun_count is not None
                and overrun_count > 0
                or memory_peak is not None
                and memory_peak >= 80
                or p95_start_interval is not None
                and p95_start_interval > 5250
            ):
                classification = _BOUNDARY
                reasons.append('stage entered the reduced-margin boundary band')
            else:
                classification = _GREEN
                reasons.append('stage preserved cadence, integrity, and resource margin')
    return StageSummary(
        stage_id=stage.stage_id,
        alarm_count=stage.alarm_count,
        memory_gib=stage.envelope.memory_gib,
        cpu_cores=stage.envelope.cpu_cores,
        classification=classification,
        reasons=tuple(reasons),
        resource_contract_ok=resource_contract_ok,
        container_exit_code=inspection.exit_code,
        oom_killed=inspection.oom_killed,
        iterations=iterations,
        p95_iteration_ms=p95_iteration,
        p99_iteration_ms=p99_iteration,
        p95_start_interval_ms=p95_start_interval,
        overrun_count=overrun_count,
        overrun_ratio=overrun_ratio,
        cpu_peak_percent=None if execution is None else execution.cpu_peak_percent,
        memory_peak_percent=None if execution is None else execution.memory_peak_percent,
        cpu_throttled_seconds=(None if execution is None else execution.cpu_throttled_seconds),
        work_iterations=None if execution is None else execution.work_iterations,
        empty_iterations=None if execution is None else execution.empty_iterations,
        durable_record_count=None if audit is None else audit.durable_record_count,
        journal_bytes=None if audit is None else audit.journal_bytes,
        duplicate_commit_id_count=(None if audit is None else audit.duplicate_commit_id_count),
        commit_chain_mismatch_count=(None if audit is None else audit.commit_chain_mismatch_count),
        snapshot_count=None if audit is None else audit.snapshot_count,
        snapshot_alarm_count=None if audit is None else audit.snapshot_alarm_count,
        snapshot_last_commit_mismatch_count=(
            None if audit is None else audit.snapshot_last_commit_mismatch_count
        ),
        journal_aligned=None if audit is None else audit.journal_aligned,
        stop_reason=None if execution is None else execution.stop_reason,
    )


def _completed_integrity_ok(
    *,
    stage: DockerStressStage,
    result: dict[str, Any],
    audit: PhysicalAudit | None,
    execution: ExecutionResources | None,
) -> bool:
    if audit is None or execution is None:
        return False
    return (
        result.get('journal_aligned') is True
        and audit.journal_aligned
        and audit.durable_record_count == stage.expected_durable_records
        and audit.duplicate_commit_id_count == 0
        and audit.commit_chain_mismatch_count == 0
        and audit.snapshot_count == 1
        and audit.snapshot_alarm_count == stage.alarm_count
        and audit.snapshot_last_commit_mismatch_count == 0
        and execution.work_iterations == stage.expected_durable_records
        and execution.stop_reason in _NATURAL_STOP_REASONS
    )


def _resource_contract_ok(
    *,
    stage: DockerStressStage,
    inspection: DockerInspection,
    probe: ResourceProbe | None,
    execution: ExecutionResources | None,
) -> bool:
    if probe is None:
        return False
    values = [
        (inspection.cpu_cores, inspection.memory_bytes),
        (probe.cpu_limit_cores, probe.memory_limit_bytes),
    ]
    if execution is not None:
        values.append((execution.cpu_limit_cores, execution.memory_limit_bytes))
    return all(
        _limits_match(envelope=stage.envelope, cpu_cores=cpu, memory_bytes=memory)
        for cpu, memory in values
    )


def _limits_match(
    *,
    envelope: ResourceEnvelope,
    cpu_cores: float | None,
    memory_bytes: int | None,
) -> bool:
    return (
        cpu_cores is not None
        and memory_bytes is not None
        and math.isclose(cpu_cores, envelope.cpu_cores, rel_tol=0.0, abs_tol=0.001)
        and abs(memory_bytes - envelope.memory_bytes) <= 1024 * 1024
    )


def _sample_percentile(
    samples: list[dict[str, Any]],
    key: str,
    percentile: float,
) -> float | None:
    values = [
        float(item[key])
        for item in samples
        if isinstance(item.get(key), int | float) and not isinstance(item.get(key), bool)
    ]
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percentile / 100
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _parse_memory_bytes(value: str | None) -> int | None:
    if value is None:
        return None
    match = re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)(KiB|MiB|GiB|B)', value)
    if match is None:
        return None
    amount = float(match.group(1))
    multiplier = {'B': 1, 'KiB': 1024, 'MiB': 1024**2, 'GiB': 1024**3}[match.group(2)]
    return int(round(amount * multiplier))


def _parse_percent(value: str | None) -> float | None:
    if value is None:
        return None
    return _optional_float(value.removesuffix('%'))


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except TypeError, ValueError:
        return None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except TypeError, ValueError:
        return None


def _result_float(document: dict[str, Any] | None, key: str) -> float | None:
    if document is None:
        return None
    return _optional_float(document.get(key))


def _result_int(document: dict[str, Any] | None, key: str) -> int | None:
    if document is None:
        return None
    return _optional_int(document.get(key))


def _read_json_or_none(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    document = json.loads(path.read_text(encoding='utf-8'))
    return document if isinstance(document, dict) else None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [
        document
        for line in path.read_text(encoding='utf-8').splitlines()
        if line.strip()
        if isinstance((document := json.loads(line)), dict)
    ]


def _load_summary(path: Path) -> StageSummary:
    document = json.loads(path.read_text(encoding='utf-8'))
    document['reasons'] = tuple(document.get('reasons', ()))
    return StageSummary(**document)


def _write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def _summary_exit_code(summary: StageSummary) -> int:
    if summary.classification == _PRODUCT_FAIL:
        return 1
    if summary.classification == _INVALID:
        return 2
    return 0


def _phase_exit_code(summaries: list[StageSummary]) -> int:
    if any(item.classification == _PRODUCT_FAIL for item in summaries):
        return 1
    if any(item.classification == _INVALID for item in summaries):
        return 2
    return 0


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[5]


# phase-b permite un corte operacional opcional, no un ceiling contractual.
# phase-c deriva automáticamente el reference load; por eso no recibe --reference-load.
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--output-root',
        default='scopes/ada/processes/alarms-runtime/performance/results/F-007/physical-volume-v2',
    )
    parser.add_argument(
        '--work-root',
        default='scopes/ada/processes/alarms-runtime/.performance-work/F-007-physical-volume-v2',
    )
    parser.add_argument('--rebuild', action='store_true')
    subparsers = parser.add_subparsers(dest='command', required=True)

    build = subparsers.add_parser('build')
    build.add_argument('--rebuild', action='store_true')

    build_f010 = subparsers.add_parser('build-f010')
    build_f010.add_argument('--rebuild', action='store_true')

    physical_gate = subparsers.add_parser('physical-gate')
    physical_gate.add_argument('--memory-gib', type=int, default=2)
    physical_gate.add_argument('--replace', action='store_true')

    stage = subparsers.add_parser('stage')
    stage.add_argument('--memory-gib', type=int, required=True)
    stage.add_argument('--alarm-count', type=int, required=True)
    stage.add_argument('--replace', action='store_true')

    phase_a = subparsers.add_parser('phase-a')
    phase_a.add_argument('--replace', action='store_true')

    phase_b = subparsers.add_parser('phase-b')
    phase_b.add_argument('--stop-after-alarm-count', type=int)
    phase_b.add_argument('--replace', action='store_true')

    phase_c = subparsers.add_parser('phase-c')
    phase_c.add_argument('--headroom-memory-gib', type=int, nargs='*', default=[])
    phase_c.add_argument('--replace', action='store_true')

    f010 = subparsers.add_parser('final-qualification')
    f010_mode = f010.add_mutually_exclusive_group()
    f010_mode.add_argument('--replace', action='store_true')
    f010_mode.add_argument('--recheck', action='store_true')

    f010_container = subparsers.add_parser('container-f010')
    f010_container.add_argument('--expected-memory-gib', type=int, required=True)
    f010_container.add_argument('--work-dir', required=True)
    f010_container.add_argument('--output-dir', required=True)
    f010_container.add_argument('--dataset-root', required=True)
    f010_container.add_argument('--manifest', required=True)
    f010_container.add_argument('--conformance', required=True)

    container = subparsers.add_parser('container-stage')
    container.add_argument('--alarm-count', type=int, required=True)
    container.add_argument('--expected-memory-gib', type=int, required=True)
    container.add_argument('--work-dir', required=True)
    container.add_argument('--output-dir', required=True)
    container.add_argument('--dataset-root', required=True)
    container.add_argument('--manifest', required=True)
    container.add_argument('--conformance', required=True)

    physical_container = subparsers.add_parser('container-physical-gate')
    physical_container.add_argument('--expected-memory-gib', type=int, required=True)
    physical_container.add_argument('--dataset-root', required=True)
    physical_container.add_argument('--manifest', required=True)
    physical_container.add_argument('--conformance', required=True)
    physical_container.add_argument('--output-dir', required=True)
    return parser.parse_args()


if __name__ == '__main__':
    raise SystemExit(main())
