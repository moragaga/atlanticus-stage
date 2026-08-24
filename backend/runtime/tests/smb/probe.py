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

from atlanticus.runtime import ConcurrentExecutionError, LeaseOwnershipLostError
from atlanticus.runtime.lease import ExecutionLease

_APPLICATION = 'atlanticus-runtime-smb-probe'
_SERVICE = 'runtime-smb-probe'
_JOB_KEY = 'runtime-smb-probe'
_MODULE = 'atlanticus.runtime.smb_probe'
_INITIAL_TIME = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
_WAIT_TIMEOUT_SECONDS = 30.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('role', choices=('prepare', 'owner-a', 'owner-b', 'verify', 'orchestrate'))
    parser.add_argument('--shared-volume', required=True)
    parser.add_argument('--probe-id', required=True)
    parser.add_argument('--keep', action='store_true')
    arguments = parser.parse_args()

    shared_volume = Path(arguments.shared_volume).expanduser().resolve()
    probe_root = _probe_root(shared_volume, arguments.probe_id)
    if arguments.role == 'prepare':
        _prepare(probe_root)
        return
    if arguments.role == 'owner-a':
        _owner_a(probe_root)
        return
    if arguments.role == 'owner-b':
        _owner_b(probe_root)
        return
    if arguments.role == 'verify':
        _verify(probe_root)
        if not arguments.keep:
            shutil.rmtree(probe_root)
        return
    _orchestrate(
        script_path=Path(__file__).resolve(),
        shared_volume=shared_volume,
        probe_id=arguments.probe_id,
        keep=arguments.keep,
    )


def _orchestrate(
    *,
    script_path: Path,
    shared_volume: Path,
    probe_id: str,
    keep: bool,
) -> None:
    probe_root = _probe_root(shared_volume, probe_id)
    _prepare(probe_root)
    common = ['--shared-volume', str(shared_volume), '--probe-id', probe_id]
    owner_a = subprocess.Popen([sys.executable, str(script_path), 'owner-a', *common])
    owner_b = subprocess.Popen([sys.executable, str(script_path), 'owner-b', *common])
    a_code = owner_a.wait(timeout=_WAIT_TIMEOUT_SECONDS)
    b_code = owner_b.wait(timeout=_WAIT_TIMEOUT_SECONDS)
    if a_code != 0 or b_code != 0:
        raise SystemExit(f'probe workers failed: owner-a={a_code}, owner-b={b_code}')
    _verify(probe_root)
    if not keep:
        shutil.rmtree(probe_root)


def _prepare(probe_root: Path) -> None:
    if probe_root.exists():
        shutil.rmtree(probe_root)
    control_root = probe_root / 'control'
    control_root.mkdir(parents=True, exist_ok=True)
    _atomic_write(control_root / 'clock.txt', _INITIAL_TIME.isoformat())


def _owner_a(probe_root: Path) -> None:
    control_root = probe_root / 'control'
    lease = _lease(probe_root, run_id='owner-a')
    acquisition = lease.acquire()
    if not acquisition.acquired or acquisition.generation != 1:
        raise RuntimeError('owner A did not acquire generation 1')
    _atomic_write(control_root / 'a-generation.txt', f'{acquisition.generation}\n')

    with lease.fenced_mutation():
        _write_marker(control_root / 'a-fence-held')
        outcome = _wait_for_any(control_root, ('b-blocked', 'b-overlapped-takeover'))
        if outcome == 'b-overlapped-takeover':
            raise RuntimeError('owner B crossed the physical fence while owner A held it')
        _atomic_write(probe_root / 'authoritative-generation-1.txt', 'generation=1\n')
        _write_marker(control_root / 'a-authoritative-mutation')

    _write_marker(control_root / 'a-fence-released')
    _wait_for(control_root / 'b-takeover')

    try:
        with lease.fenced_mutation():
            _atomic_write(probe_root / 'forbidden-stale-write.txt', 'stale\n')
    except LeaseOwnershipLostError:
        _write_marker(control_root / 'a-rejected-after-takeover')
    else:
        raise RuntimeError('stale owner A crossed the fence after generation 2 takeover')
    finally:
        lease.release(completed=False)


def _owner_b(probe_root: Path) -> None:
    control_root = probe_root / 'control'
    _wait_for(control_root / 'a-fence-held')
    _set_clock(control_root, _INITIAL_TIME + timedelta(seconds=11))

    lease = _lease(probe_root, run_id='owner-b')
    _write_marker(control_root / 'b-acquire-attempted')
    try:
        first_acquisition = lease.acquire()
    except ConcurrentExecutionError:
        _write_marker(control_root / 'b-blocked')
    else:
        if first_acquisition.acquired:
            _write_marker(control_root / 'b-overlapped-takeover')
            lease.release(completed=False)
            raise RuntimeError('owner B took generation 2 while owner A held the physical fence')
        raise RuntimeError('owner B returned an unexpected non-acquired lease result')

    _wait_for(control_root / 'a-fence-released')
    acquisition = lease.acquire()
    if not acquisition.acquired or acquisition.generation != 2:
        raise RuntimeError('owner B did not acquire generation 2 after owner A released the fence')

    with lease.fenced_mutation():
        _atomic_write(probe_root / 'authoritative-generation-2.txt', 'generation=2\n')
        _atomic_write(control_root / 'b-generation.txt', f'{acquisition.generation}\n')
        _write_marker(control_root / 'b-takeover')

    _wait_for(control_root / 'a-rejected-after-takeover')
    lease.release(completed=False)
    _write_marker(control_root / 'b-complete')


def _verify(probe_root: Path) -> None:
    control_root = probe_root / 'control'
    required = (
        'a-fence-held',
        'b-acquire-attempted',
        'b-blocked',
        'a-authoritative-mutation',
        'a-fence-released',
        'b-takeover',
        'a-rejected-after-takeover',
        'b-complete',
    )
    for marker in required:
        if not (control_root / marker).is_file():
            raise RuntimeError(f'probe marker is missing: {marker}')
    for forbidden in ('b-overlapped-takeover',):
        if (control_root / forbidden).exists():
            raise RuntimeError(f'physical fence violation marker exists: {forbidden}')
    if (probe_root / 'forbidden-stale-write.txt').exists():
        raise RuntimeError('stale owner A wrote after generation 2 takeover')

    generation_a = (control_root / 'a-generation.txt').read_text(encoding='utf-8').strip()
    generation_b = (control_root / 'b-generation.txt').read_text(encoding='utf-8').strip()
    if generation_a != '1' or generation_b != '2':
        raise RuntimeError(
            f'unexpected generations: owner-a={generation_a!r}, owner-b={generation_b!r}'
        )
    first_mutation = (probe_root / 'authoritative-generation-1.txt').read_text(encoding='utf-8')
    second_mutation = (probe_root / 'authoritative-generation-2.txt').read_text(encoding='utf-8')
    if first_mutation != 'generation=1\n' or second_mutation != 'generation=2\n':
        raise RuntimeError('authoritative mutations do not match their lease generations')

    print(
        json.dumps(
            {
                'status': 'PASS',
                'blocked_takeover_while_fence_held': True,
                'generation_1_mutation_completed_before_takeover': True,
                'takeover_generation': 2,
                'generation_2_mutation_completed': True,
                'stale_owner_rejected_after_takeover': True,
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


def _probe_root(shared_volume: Path, probe_id: str) -> Path:
    if not probe_id or probe_id != probe_id.strip():
        raise ValueError('probe-id must be a non-empty value without surrounding whitespace')
    if any(value in probe_id for value in ('/', '\\', '\x00')) or probe_id in {'.', '..'}:
        raise ValueError('probe-id must be a safe path segment')
    return shared_volume / '.atlanticus-probes' / 'runtime-smb-fencing' / probe_id


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


def _wait_for_any(control_root: Path, names: tuple[str, ...]) -> str:
    deadline = time.monotonic() + _WAIT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        for name in names:
            if (control_root / name).is_file():
                return name
        time.sleep(0.05)
    joined = ', '.join(names)
    raise TimeoutError(f'timed out waiting for one of: {joined}')


if __name__ == '__main__':
    main()
