from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ada.alarms.persistence import (
    GROUP_RUNTIME_SNAPSHOT_SCHEMA_VERSION,
    AlarmPersistence,
    EngineCommitMetadata,
    EngineCommitRecord,
    GroupRuntimeSnapshot,
)
from ada.processes.alarms_runtime.durability import AlarmRuntimeDurability
from atlanticus.runtime import (
    JobDefinition,
    JobRuntimeContext,
    LeaseOwnershipLostError,
    RuntimeConfiguration,
)
from atlanticus.runtime.lease import ExecutionLease

_APPLICATION = 'ada-alarms-runtime-smb-probe'
_JOB_KEY = 'alarms-runtime-smb-probe'
_SERVICE = 'alarms-runtime-smb-probe'
_MODULE = 'ada.processes.alarms_runtime'
_INITIAL_TIME = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
_WAIT_TIMEOUT_SECONDS = 30.0


class _PausingFence:
    def __init__(
        self,
        *,
        underlying,
        pause_call: int,
        control_root: Path,
    ) -> None:
        self._underlying = underlying
        self._pause_call = pause_call
        self._control_root = control_root
        self._calls = 0

    def __call__(self):
        self._calls += 1
        if self._calls == self._pause_call:
            _write_marker(self._control_root / 'a-paused')
            _wait_for(self._control_root / 'release-a')
        return self._underlying()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('role', choices=('prepare', 'owner-a', 'owner-b', 'verify', 'orchestrate'))
    parser.add_argument('--shared-volume', required=True)
    parser.add_argument('--probe-id', required=True)
    parser.add_argument('--scenario', choices=('pre-durable', 'post-durable'), required=True)
    parser.add_argument('--keep', action='store_true')
    arguments = parser.parse_args()

    shared_volume = Path(arguments.shared_volume).expanduser().resolve()
    probe_root = _probe_root(shared_volume, arguments.probe_id)
    if arguments.role == 'prepare':
        _prepare(probe_root)
        return
    if arguments.role == 'owner-a':
        _owner_a(probe_root, arguments.scenario)
        return
    if arguments.role == 'owner-b':
        _owner_b(probe_root, arguments.scenario)
        return
    if arguments.role == 'verify':
        _verify(probe_root, arguments.scenario)
        if not arguments.keep:
            shutil.rmtree(probe_root)
        return
    _orchestrate(
        script_path=Path(__file__).resolve(),
        shared_volume=shared_volume,
        probe_id=arguments.probe_id,
        scenario=arguments.scenario,
        keep=arguments.keep,
    )


def _orchestrate(
    *,
    script_path: Path,
    shared_volume: Path,
    probe_id: str,
    scenario: str,
    keep: bool,
) -> None:
    probe_root = _probe_root(shared_volume, probe_id)
    _prepare(probe_root)
    common = [
        '--shared-volume',
        str(shared_volume),
        '--probe-id',
        probe_id,
        '--scenario',
        scenario,
    ]
    owner_a = subprocess.Popen([sys.executable, str(script_path), 'owner-a', *common])
    owner_b = subprocess.Popen([sys.executable, str(script_path), 'owner-b', *common])
    a_code = owner_a.wait(timeout=_WAIT_TIMEOUT_SECONDS)
    b_code = owner_b.wait(timeout=_WAIT_TIMEOUT_SECONDS)
    if a_code != 0 or b_code != 0:
        raise SystemExit(f'probe workers failed: owner-a={a_code}, owner-b={b_code}')
    _verify(probe_root, scenario)
    if not keep:
        shutil.rmtree(probe_root)


def _prepare(probe_root: Path) -> None:
    if probe_root.exists():
        shutil.rmtree(probe_root)
    control_root = probe_root / 'control'
    control_root.mkdir(parents=True, exist_ok=True)
    _atomic_write(control_root / 'clock.txt', _INITIAL_TIME.isoformat())


def _owner_a(probe_root: Path, scenario: str) -> None:
    control_root = probe_root / 'control'
    lease = _lease(probe_root, run_id='owner-a')
    acquisition = lease.acquire()
    if not acquisition.acquired or acquisition.generation is None:
        raise RuntimeError('owner A could not acquire generation 1')
    context = _context(probe_root, run_id='owner-a')
    pause_call = 2 if scenario == 'pre-durable' else 3
    pausing_fence = _PausingFence(
        underlying=lease.fenced_mutation,
        pause_call=pause_call,
        control_root=control_root,
    )
    context._bind_lease_authority(
        generation=acquisition.generation,
        checker=lease.assert_current,
        fence=pausing_fence,
    )
    durability = AlarmRuntimeDurability(persistence=AlarmPersistence(shared_volume_path=probe_root))
    try:
        durability.commit_batch(context, [_build_record(commit_id='C-A')])
    except LeaseOwnershipLostError:
        _write_marker(control_root / 'a-rejected')
    else:
        _write_marker(control_root / 'a-committed')
        raise RuntimeError('stale owner A unexpectedly completed its commit')
    finally:
        lease.release(completed=False)


def _owner_b(probe_root: Path, scenario: str) -> None:
    control_root = probe_root / 'control'
    _wait_for(control_root / 'a-paused')
    _set_clock(control_root, _INITIAL_TIME + timedelta(seconds=11))
    lease = _lease(probe_root, run_id='owner-b')
    acquisition = lease.acquire()
    if not acquisition.acquired or acquisition.generation != 2:
        raise RuntimeError('owner B did not acquire generation 2')
    context = _context(probe_root, run_id='owner-b')
    context._bind_lease_authority(
        generation=acquisition.generation,
        checker=lease.assert_current,
        fence=lease.fenced_mutation,
    )
    _write_marker(control_root / 'b-takeover')
    _write_marker(control_root / 'release-a')
    _wait_for(control_root / 'a-rejected')

    durability = AlarmRuntimeDurability(persistence=AlarmPersistence(shared_volume_path=probe_root))
    recovery = durability.recover(context)
    if scenario == 'pre-durable':
        if recovery.discarded_tail_bytes <= 0:
            raise RuntimeError('pre-durable takeover did not discard the unconfirmed WAL tail')
        durability.commit_batch(context, [_build_record(commit_id='C-B')])
    else:
        if recovery.applied_count != 1:
            raise RuntimeError('post-durable takeover did not replay the durable commit')
    lease.release(completed=False)
    _write_marker(control_root / 'b-complete')


def _verify(probe_root: Path, scenario: str) -> None:
    control_root = probe_root / 'control'
    for marker in ('a-paused', 'b-takeover', 'a-rejected', 'b-complete'):
        if not (control_root / marker).is_file():
            raise RuntimeError(f'probe marker is missing: {marker}')
    if (control_root / 'a-committed').exists():
        raise RuntimeError('stale owner A completed a commit after takeover')

    persistence = AlarmPersistence(shared_volume_path=probe_root)
    head = persistence.read_head()
    if not head.aligned or head.durable is None:
        raise RuntimeError('journal head is not aligned after takeover recovery')
    expected_commit = 'C-B' if scenario == 'pre-durable' else 'C-A'
    if head.durable.commit_id != expected_commit:
        raise RuntimeError(
            f'expected durable commit {expected_commit}, found {head.durable.commit_id}'
        )
    snapshot = persistence.read_snapshot('crusher_pressure')
    if snapshot is None or snapshot.last_commit_id != expected_commit:
        raise RuntimeError('group snapshot does not match the final durable commit')
    print(
        json.dumps(
            {
                'scenario': scenario,
                'status': 'PASS',
                'durable_commit_id': head.durable.commit_id,
                'materialized_commit_id': head.materialized.commit_id,
                'probe_root': str(probe_root),
            },
            sort_keys=True,
        )
    )


def _lease(probe_root: Path, *, run_id: str) -> ExecutionLease:
    control_root = probe_root / 'control'
    return ExecutionLease(
        volume_path=probe_root,
        application=_APPLICATION,
        service_name=_SERVICE,
        job_key=_JOB_KEY,
        module_name=_MODULE,
        run_id=run_id,
        lease_timeout_seconds=10,
        renewal_seconds=5,
        wait_seconds=0,
        poll_seconds=0.05,
        wall_clock=lambda: _read_clock(control_root),
    )


def _context(probe_root: Path, *, run_id: str) -> JobRuntimeContext:
    configuration = RuntimeConfiguration.from_sources(
        environ={
            'ENVIRONMENT': 'local',
            'APPLICATION': _APPLICATION,
            'VOLUMEN_PATH': str(probe_root),
        }
    )
    return JobRuntimeContext.create(
        definition=JobDefinition(
            module_name=_MODULE,
            service_name=_SERVICE,
            job_key=_JOB_KEY,
            iteration_timeout_seconds=10,
            execution_timeout_seconds=30,
            shutdown_grace_seconds=5,
            lease_timeout_seconds=10,
            lease_renew_seconds=5,
            lease_wait_seconds=0,
            lease_poll_seconds=0.05,
            resource_sample_seconds=1,
        ),
        configuration=configuration,
        run_id=run_id,
        correlation_id=f'{run_id}-correlation',
    )


def _build_record(*, commit_id: str) -> EngineCommitRecord:
    evaluated_at = '2026-08-24T12:00:00Z'
    snapshot = GroupRuntimeSnapshot(
        {
            'snapshot_schema_version': GROUP_RUNTIME_SNAPSHOT_SCHEMA_VERSION,
            'priority_group': 'crusher_pressure',
            'last_commit_id': commit_id,
            'state_basis': {
                'alarm_configuration_revision': 'R42',
                'tool_registry_revision': 'T18',
            },
            'episode': None,
            'alarms': {},
        }
    )
    commit = EngineCommitMetadata(
        commit_id=commit_id,
        cycle_id=f'cycle-{commit_id}',
        priority_group='crusher_pressure',
        previous_commit_id=None,
        evaluated_at=evaluated_at,
        committed_at=evaluated_at,
        alarm_configuration_revision='R42',
        tool_registry_revision='T18',
        runtime_artifact_version='ada-alarms-runtime/0.1.0',
        affected_alarms=('crusher_pressure_risk',),
    )
    return EngineCommitRecord.create(
        commit=commit,
        snapshot_after=snapshot,
        records={'journey_events': []},
    )


def _probe_root(shared_volume: Path, probe_id: str) -> Path:
    if not probe_id or probe_id != probe_id.strip():
        raise ValueError('probe-id must be a non-empty value without surrounding whitespace')
    if any(value in probe_id for value in ('/', '\\', '\x00')) or probe_id in {'.', '..'}:
        raise ValueError('probe-id must be a safe path segment')
    return shared_volume / '.atlanticus-probes' / 'alarms-runtime-fencing' / probe_id


def _read_clock(control_root: Path) -> datetime:
    value = datetime.fromisoformat((control_root / 'clock.txt').read_text(encoding='utf-8').strip())
    if value.tzinfo is None:
        raise RuntimeError('probe clock must be timezone-aware')
    return value.astimezone(UTC)


def _set_clock(control_root: Path, value: datetime) -> None:
    _atomic_write(control_root / 'clock.txt', value.astimezone(UTC).isoformat())


def _write_marker(path: Path) -> None:
    _atomic_write(path, 'ready\n')


def _atomic_write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    temporary.write_text(value, encoding='utf-8')
    os.replace(temporary, path)


def _wait_for(path: Path) -> None:
    deadline = time.monotonic() + _WAIT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if path.is_file():
            return
        time.sleep(0.05)
    raise TimeoutError(f'timed out waiting for {path}')


if __name__ == '__main__':
    main()
