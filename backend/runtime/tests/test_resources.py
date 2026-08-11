from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

from atlanticus.observability import (
    MemoryEventSink,
    ObservabilitySettings,
    close_observability,
    configure_observability,
)
from atlanticus.runtime._resource_models import ResourceSample, ResourceThresholds
from atlanticus.runtime._resource_monitor import ResourceMonitor


class SequenceSampler:
    def __init__(self, percentages: list[float]) -> None:
        self._percentages = iter(percentages)
        self._index = 0

    def sample(self) -> ResourceSample:
        percent = next(self._percentages)
        self._index += 1
        return ResourceSample(
            occurred_at_utc=datetime(2026, 7, 17, tzinfo=UTC) + timedelta(seconds=self._index),
            memory_used_bytes=int(percent * 10),
            memory_limit_bytes=1000,
            memory_percent=percent,
            cpu_percent=20,
            cpu_limit_cores=1,
            process_rss_bytes=100,
            process_count=1,
            thread_count=2,
            oom_count=4 + self._index,
            cpu_throttled_periods=10 + self._index,
            cpu_throttled_seconds=1.0 + self._index / 10,
            top_process_rss_bytes=100,
            memory_source='fake-cgroup',
            cpu_source='fake-cgroup',
        )


class CpuSpikeSampler:
    def __init__(self) -> None:
        self.sampled = threading.Event()

    def sample(self) -> ResourceSample:
        self.sampled.set()
        return ResourceSample(
            occurred_at_utc=datetime(2026, 7, 17, tzinfo=UTC),
            memory_used_bytes=200,
            memory_limit_bytes=1000,
            memory_percent=20,
            cpu_percent=99,
            cpu_limit_cores=0.5,
            process_rss_bytes=100,
            process_count=1,
            thread_count=2,
            memory_source='fake-cgroup',
            cpu_source='fake-cgroup',
        )


def test_checkpoint_updates_peaks_without_emitting_a_log() -> None:
    sink = MemoryEventSink()
    configure_observability(
        settings=ObservabilitySettings.build(application='app', service='job', environment='local'),
        sink=sink,
    )
    monitor = ResourceMonitor(
        sampler=SequenceSampler([20, 40]),
        thresholds=ResourceThresholds(
            warning_samples=2,
            critical_samples=2,
            emergency_samples=2,
            recovered_samples=2,
        ),
        observe_cpu_pressure=False,
    )

    monitor.checkpoint()
    monitor.checkpoint()
    monitor.stop()
    close_observability()

    assert sink.events == []
    assert monitor.statistics.operational_metrics() == {
        'cpu_limit_cores': 1,
        'memory_limit_bytes': 1000,
        'cpu_peak_percent': 20.0,
        'memory_peak_percent': 40.0,
        'oom_events': 1,
        'cpu_throttled_seconds': 0.1,
    }


def test_sustained_pressure_emits_episode_but_raw_samples_remain_in_memory() -> None:
    sink = MemoryEventSink()
    configure_observability(
        settings=ObservabilitySettings.build(application='app', service='job', environment='local'),
        sink=sink,
    )
    thresholds = ResourceThresholds(
        warning_samples=2,
        critical_samples=2,
        emergency_samples=2,
        recovered_samples=2,
    )
    monitor = ResourceMonitor(
        sampler=SequenceSampler([86, 87, 70, 70]),
        thresholds=thresholds,
        observe_cpu_pressure=False,
    )

    for _ in range(4):
        monitor._sample_once(observe_cpu_pressure=False)
    monitor.stop()
    close_observability()

    names = [event['name'] for event in sink.events]
    assert names == ['resource.pressure.started', 'resource.pressure.recovered']
    assert monitor.statistics.sample_count == 4
    assert monitor.pressure_event_count == 1


def test_default_monitor_requires_about_thirty_seconds_of_pressure() -> None:
    monitor = ResourceMonitor(sampler=CpuSpikeSampler(), interval_seconds=5)

    assert monitor._thresholds.warning_samples == 6
    assert monitor._thresholds.critical_samples == 6
    assert monitor._thresholds.emergency_samples == 6
    assert monitor._thresholds.warning_percent == 85


class FailingSampler:
    def __init__(self) -> None:
        self.calls = 0
        self.repeated = threading.Event()

    def sample(self) -> ResourceSample:
        self.calls += 1
        if self.calls >= 3:
            self.repeated.set()
        raise RuntimeError('sampling unavailable')


def test_repeated_sampling_failure_emits_only_one_issue_until_recovery() -> None:
    sink = MemoryEventSink()
    configure_observability(
        settings=ObservabilitySettings.build(application='app', service='job', environment='local'),
        sink=sink,
    )
    sampler = FailingSampler()
    monitor = ResourceMonitor(sampler=sampler, interval_seconds=0.01)

    monitor.start()
    assert sampler.repeated.wait(timeout=1)
    monitor.stop()
    close_observability()

    failures = [event for event in sink.events if event['name'] == 'resource.monitor.failed']
    assert len(failures) == 1
    assert failures[0]['message'] == 'Resource sampling failed; job execution continues'
    assert failures[0]['severity'] == 'warning'
    assert failures[0]['error']['message'] == 'RuntimeError raised'


def test_checkpoint_failure_is_a_warning_and_does_not_escape() -> None:
    sink = MemoryEventSink()
    configure_observability(
        settings=ObservabilitySettings.build(application='app', service='job', environment='local'),
        sink=sink,
    )
    monitor = ResourceMonitor(sampler=FailingSampler())

    sample = monitor.checkpoint()
    close_observability()

    assert sample is None
    assert sink.events[0]['name'] == 'resource.monitor.failed'
    assert 'sampling unavailable' not in str(sink.events[0])


def test_escalation_requires_consecutive_samples() -> None:
    sink = MemoryEventSink()
    configure_observability(
        settings=ObservabilitySettings.build(application='app', service='job', environment='local'),
        sink=sink,
    )
    monitor = ResourceMonitor(
        sampler=SequenceSampler([86, 86, 93, 86, 93, 93]),
        thresholds=ResourceThresholds(
            warning_samples=2,
            critical_samples=2,
            emergency_samples=2,
            recovered_samples=2,
        ),
        observe_cpu_pressure=False,
    )

    for _ in range(6):
        monitor._sample_once(observe_cpu_pressure=False)
    monitor.stop()
    close_observability()

    assert [event['name'] for event in sink.events] == [
        'resource.pressure.started',
        'resource.pressure.escalated',
    ]
