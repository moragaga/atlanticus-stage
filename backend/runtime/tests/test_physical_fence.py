from __future__ import annotations

from atlanticus.runtime._fence import PhysicalAuthorityFence


def _fence(tmp_path, *, job_key: str = 'job') -> PhysicalAuthorityFence:
    return PhysicalAuthorityFence(
        volume_path=tmp_path,
        application='ada',
        job_key=job_key,
    )


def test_physical_fence_uses_application_runtime_scope(tmp_path) -> None:
    fence = _fence(tmp_path, job_key='alarm-engine')

    assert fence.path == tmp_path / 'ada' / '.runtime' / 'fences' / 'alarm-engine.lock'


def test_physical_fence_excludes_same_job_key(tmp_path) -> None:
    first = _fence(tmp_path)
    second = _fence(tmp_path)

    first_descriptor = first.try_acquire()
    assert first_descriptor is not None
    try:
        assert second.try_acquire() is None
    finally:
        first.release(first_descriptor)

    second_descriptor = second.try_acquire()
    assert second_descriptor is not None
    second.release(second_descriptor)


def test_physical_fence_does_not_serialize_different_job_keys(tmp_path) -> None:
    first = _fence(tmp_path, job_key='job-a')
    second = _fence(tmp_path, job_key='job-b')

    first_descriptor = first.try_acquire()
    second_descriptor = second.try_acquire()

    assert first_descriptor is not None
    assert second_descriptor is not None
    first.release(first_descriptor)
    second.release(second_descriptor)


def test_physical_fence_timeout_returns_none(tmp_path) -> None:
    first = _fence(tmp_path)
    second = _fence(tmp_path)

    first_descriptor = first.try_acquire()
    assert first_descriptor is not None
    try:
        assert second.acquire(wait_seconds=0.01, poll_seconds=0.001) is None
    finally:
        first.release(first_descriptor)
