"""Muestreo interno de recursos del proceso y de cgroups."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import psutil

from atlanticus.kernel import utc_now
from atlanticus.runtime._resource_models import ResourceSample

_CGROUP_UNLIMITED_THRESHOLD = 1 << 60


class CgroupResourceSampler:
    """Detecta límites cgroup v1/v2 y atribuye procesos del árbol actual."""

    def __init__(
        self,
        *,
        cgroup_root: str | Path = '/sys/fs/cgroup',
        proc_root: str | Path = '/proc',
        process: psutil.Process | None = None,
    ) -> None:
        self._cgroup_root = Path(cgroup_root)
        self._proc_root = Path(proc_root)
        if process is not None:
            self._process: psutil.Process | None = process
        else:
            try:
                self._process = psutil.Process()
            except psutil.Error:
                self._process = None
        self._previous_cpu_usage_seconds: float | None = None
        self._previous_process_cpu_seconds: float | None = None
        self._previous_monotonic: float | None = None

    def sample(self) -> ResourceSample:
        occurred_at = utc_now()
        now = time.monotonic()
        memory_used, memory_limit, memory_source = self._memory()
        cpu_limit, cpu_limit_source = self._cpu_limit()
        cpu_usage_seconds, cpu_usage_source = self._cpu_usage_seconds()
        cpu_throttled_periods, cpu_throttled_seconds = self._cpu_throttling()
        process_values = self._process_tree()

        cpu_percent = self._cpu_percent(
            now=now,
            usage_seconds=cpu_usage_seconds,
            process_usage_seconds=process_values['cpu_seconds'],
            cpu_limit=cpu_limit,
        )
        memory_percent = None
        if memory_used is not None and memory_limit is not None and memory_limit > 0:
            memory_percent = min(100.0, max(0.0, memory_used / memory_limit * 100))

        self._previous_monotonic = now
        self._previous_cpu_usage_seconds = cpu_usage_seconds
        self._previous_process_cpu_seconds = process_values['cpu_seconds']
        return ResourceSample(
            occurred_at_utc=occurred_at,
            memory_used_bytes=memory_used,
            memory_limit_bytes=memory_limit,
            memory_percent=memory_percent,
            cpu_percent=cpu_percent,
            cpu_limit_cores=cpu_limit,
            process_rss_bytes=process_values['rss_bytes'],
            process_count=process_values['process_count'],
            thread_count=process_values['thread_count'],
            oom_count=self._oom_count(),
            cpu_throttled_periods=cpu_throttled_periods,
            cpu_throttled_seconds=cpu_throttled_seconds,
            top_process_rss_bytes=process_values['top_rss_bytes'],
            memory_source=memory_source,
            cpu_source=f'{cpu_limit_source}+{cpu_usage_source}',
        )

    def _memory(self) -> tuple[int | None, int | None, str]:
        current_v2 = self._read_int(self._cgroup_root / 'memory.current')
        limit_v2 = self._read_limit(self._cgroup_root / 'memory.max')
        if current_v2 is not None:
            return current_v2, limit_v2, 'cgroup_v2'

        current_v1 = self._read_int(
            self._cgroup_root / 'memory/memory.usage_in_bytes'
        ) or self._read_int(self._cgroup_root / 'memory.usage_in_bytes')
        limit_v1 = self._read_limit(
            self._cgroup_root / 'memory/memory.limit_in_bytes'
        ) or self._read_limit(self._cgroup_root / 'memory.limit_in_bytes')
        if current_v1 is not None:
            return current_v1, limit_v1, 'cgroup_v1'

        try:
            rss = None if self._process is None else self._process.memory_info().rss
        except psutil.Error, OSError:
            rss = None
        total = self._proc_memtotal()
        return rss, total, 'process+proc_meminfo'

    def _cpu_limit(self) -> tuple[float, str]:
        cpu_max = self._read_text(self._cgroup_root / 'cpu.max')
        if cpu_max:
            parts = cpu_max.split()
            if len(parts) >= 2 and parts[0] != 'max':
                quota = self._to_int(parts[0])
                period = self._to_int(parts[1])
                if quota is not None and period is not None and quota > 0 and period > 0:
                    return quota / period, 'cgroup_v2_cpu_max'

        quota = self._read_int(self._cgroup_root / 'cpu/cpu.cfs_quota_us') or self._read_int(
            self._cgroup_root / 'cpu.cfs_quota_us'
        )
        period = self._read_int(self._cgroup_root / 'cpu/cpu.cfs_period_us') or self._read_int(
            self._cgroup_root / 'cpu.cfs_period_us'
        )
        if quota is not None and period is not None and quota > 0 and period > 0:
            return quota / period, 'cgroup_v1_cpu_quota'

        cpuset = (
            self._read_text(self._cgroup_root / 'cpuset.cpus.effective')
            or self._read_text(self._cgroup_root / 'cpuset.cpus')
            or self._read_text(self._cgroup_root / 'cpuset/cpuset.cpus')
        )
        count = self._count_cpuset(cpuset)
        if count is not None:
            return float(count), 'cpuset'
        return float(max(1, os.cpu_count() or 1)), 'os_cpu_count'

    def _cpu_usage_seconds(self) -> tuple[float | None, str]:
        cpu_stat = self._read_text(self._cgroup_root / 'cpu.stat')
        if cpu_stat:
            for line in cpu_stat.splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[0] == 'usage_usec':
                    value = self._to_int(parts[1])
                    return (
                        None if value is None else value / 1_000_000,
                        'cgroup_v2_cpu_stat',
                    )
        value = self._read_int(self._cgroup_root / 'cpuacct/cpuacct.usage') or self._read_int(
            self._cgroup_root / 'cpuacct.usage'
        )
        return (None if value is None else value / 1_000_000_000, 'cgroup_v1_cpuacct')

    def _cpu_throttling(self) -> tuple[int | None, float | None]:
        paths = (
            self._cgroup_root / 'cpu.stat',
            self._cgroup_root / 'cpu/cpu.stat',
            self._cgroup_root / 'cpu,cpuacct/cpu.stat',
        )
        for path in paths:
            raw = self._read_text(path)
            if not raw:
                continue
            values: dict[str, int] = {}
            for line in raw.splitlines():
                parts = line.split()
                if len(parts) != 2:
                    continue
                parsed = self._to_int(parts[1])
                if parsed is not None:
                    values[parts[0]] = parsed
            periods = values.get('nr_throttled')
            if 'throttled_usec' in values:
                return periods, values['throttled_usec'] / 1_000_000
            if 'throttled_time' in values:
                return periods, values['throttled_time'] / 1_000_000_000
            if periods is not None:
                return periods, None
        return None, None

    def _cpu_percent(
        self,
        *,
        now: float,
        usage_seconds: float | None,
        process_usage_seconds: float,
        cpu_limit: float,
    ) -> float | None:
        if self._previous_monotonic is None:
            return None
        elapsed = now - self._previous_monotonic
        if elapsed <= 0 or cpu_limit <= 0:
            return None
        if usage_seconds is not None and self._previous_cpu_usage_seconds is not None:
            used = max(0.0, usage_seconds - self._previous_cpu_usage_seconds)
        elif self._previous_process_cpu_seconds is not None:
            used = max(0.0, process_usage_seconds - self._previous_process_cpu_seconds)
        else:
            return None
        return max(0.0, used / (elapsed * cpu_limit) * 100)

    def _process_tree(self) -> dict[str, Any]:
        if self._process is None:
            return {
                'rss_bytes': 0,
                'cpu_seconds': 0.0,
                'process_count': 0,
                'thread_count': 0,
                'top_rss_bytes': None,
            }
        try:
            processes = [self._process, *self._process.children(recursive=True)]
        except psutil.Error, OSError:
            processes = [self._process]
        rss_total = 0
        cpu_total = 0.0
        thread_total = 0
        top_rss = -1
        observed_count = 0
        for process in processes:
            try:
                with process.oneshot():
                    rss = process.memory_info().rss
                    cpu_times = process.cpu_times()
                    threads = process.num_threads()
            except psutil.Error, OSError:
                continue
            observed_count += 1
            rss_total += rss
            cpu_total += cpu_times.user + cpu_times.system
            thread_total += threads
            if rss > top_rss:
                top_rss = rss
        return {
            'rss_bytes': rss_total,
            'cpu_seconds': cpu_total,
            'process_count': observed_count,
            'thread_count': thread_total,
            'top_rss_bytes': None if top_rss < 0 else top_rss,
        }

    def _oom_count(self) -> int | None:
        events = self._read_text(self._cgroup_root / 'memory.events')
        if events:
            values: dict[str, int] = {}
            for line in events.splitlines():
                parts = line.split()
                if len(parts) == 2:
                    parsed = self._to_int(parts[1])
                    if parsed is not None:
                        values[parts[0]] = parsed
            return values.get('oom_kill', values.get('oom'))
        return self._read_int(self._cgroup_root / 'memory/memory.failcnt')

    def _proc_memtotal(self) -> int | None:
        value = self._read_text(self._proc_root / 'meminfo')
        if not value:
            return None
        for line in value.splitlines():
            if line.startswith('MemTotal:'):
                parts = line.split()
                parsed = self._to_int(parts[1]) if len(parts) > 1 else None
                return None if parsed is None else parsed * 1024
        return None

    @staticmethod
    def _count_cpuset(value: str | None) -> int | None:
        if not value:
            return None
        total = 0
        for item in value.split(','):
            item = item.strip()
            if not item:
                continue
            if '-' in item:
                start_raw, end_raw = item.split('-', 1)
                start = CgroupResourceSampler._to_int(start_raw)
                end = CgroupResourceSampler._to_int(end_raw)
                if start is not None and end is not None:
                    total += max(0, end - start + 1)
            elif CgroupResourceSampler._to_int(item) is not None:
                total += 1
        return total or None

    @staticmethod
    def _to_int(value: str | None) -> int | None:
        try:
            return None if value is None else int(value.strip())
        except TypeError, ValueError:
            return None

    @classmethod
    def _read_int(cls, path: Path) -> int | None:
        return cls._to_int(cls._read_text(path))

    @classmethod
    def _read_limit(cls, path: Path) -> int | None:
        raw = cls._read_text(path)
        if raw == 'max':
            return None
        value = cls._to_int(raw)
        if value is None or value <= 0 or value >= _CGROUP_UNLIMITED_THRESHOLD:
            return None
        return value

    @staticmethod
    def _read_text(path: Path) -> str | None:
        try:
            return path.read_text(encoding='utf-8', errors='replace').strip()
        except OSError:
            return None
