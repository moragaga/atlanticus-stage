from __future__ import annotations

from atlanticus.runtime._resource_sampler import CgroupResourceSampler


def test_cgroup_v2_limits_are_detected(tmp_path) -> None:
    cgroup = tmp_path / 'cgroup'
    proc = tmp_path / 'proc'
    cgroup.mkdir()
    proc.mkdir()
    (cgroup / 'memory.current').write_text('73400320\n', encoding='utf-8')
    (cgroup / 'memory.max').write_text('104857600\n', encoding='utf-8')
    (cgroup / 'cpu.max').write_text('50000 100000\n', encoding='utf-8')
    (cgroup / 'cpu.stat').write_text(
        'usage_usec 1000000\nnr_throttled 7\nthrottled_usec 250000\n',
        encoding='utf-8',
    )
    (cgroup / 'memory.events').write_text('oom 2\noom_kill 1\n', encoding='utf-8')

    sample = CgroupResourceSampler(cgroup_root=cgroup, proc_root=proc).sample()

    assert sample.memory_used_bytes == 73_400_320
    assert sample.memory_limit_bytes == 104_857_600
    assert sample.memory_percent == 70
    assert sample.cpu_limit_cores == 0.5
    assert sample.oom_count == 1
    assert sample.cpu_throttled_periods == 7
    assert sample.cpu_throttled_seconds == 0.25
    assert sample.memory_source == 'cgroup_v2'


def test_cgroup_v1_zero_usage_is_not_replaced_by_fallback_value(tmp_path) -> None:
    cgroup = tmp_path / 'cgroup'
    proc = tmp_path / 'proc'
    memory = cgroup / 'memory'
    cpuacct = cgroup / 'cpuacct'
    memory.mkdir(parents=True)
    cpuacct.mkdir(parents=True)
    proc.mkdir()
    (memory / 'memory.usage_in_bytes').write_text('0\n', encoding='utf-8')
    (cgroup / 'memory.usage_in_bytes').write_text('73400320\n', encoding='utf-8')
    (memory / 'memory.limit_in_bytes').write_text('104857600\n', encoding='utf-8')
    (cpuacct / 'cpuacct.usage').write_text('0\n', encoding='utf-8')
    (cgroup / 'cpuacct.usage').write_text('1000000000\n', encoding='utf-8')

    sampler = CgroupResourceSampler(cgroup_root=cgroup, proc_root=proc)
    sample = sampler.sample()
    cpu_usage_seconds, source = sampler._cpu_usage_seconds()

    assert sample.memory_used_bytes == 0
    assert sample.memory_source == 'cgroup_v1'
    assert cpu_usage_seconds == 0
    assert source == 'cgroup_v1_cpuacct'
