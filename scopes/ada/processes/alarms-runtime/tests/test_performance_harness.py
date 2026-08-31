from __future__ import annotations

import io
import tokenize
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from ada.alarms.core import Criticality
from ada.data.core import DataPartition, DataSource
from ada.processes.alarms_runtime import (
    RUNTIME_MANIFEST_SCHEMA_VERSION,
    AlarmInputStream,
    ConfigurationAdoptionDisposition,
    ConfigurationAdoptionRejectionReason,
    RuntimeManifest,
    RuntimeRevisionBundle,
    RuntimeRevisionSourceError,
    plan_configuration_adoption,
)
from performance.baseline import (
    _TECHNICAL_ERROR_SIGNAL_VALUE,
    BaselineScenario,
    InjectedCachePromotionError,
    InvertedDeliveryDeactivationInputSource,
    MixedDeactivationInputSource,
    PerformanceAlarmDurableInputConsumer,
    SingleDeactivationDecisionInputSource,
    SingleManagementInputSource,
    StaleTargetDeactivationInputSource,
    SustainedDeactivationDecisionInputSource,
    SustainedDeactivationRequestInputSource,
    SustainedManagementInputSource,
    build_baseline_runtime,
)
from performance.metrics import (
    C2RoutingAdoptionPressureMetrics,
    DeactivationDecisionPressureMetrics,
    DisabledAdoptionPressureMetrics,
    InvalidSourceCandidatePressureMetrics,
    InvertedDeactivationDecisionPressureMetrics,
    IterationSample,
    ManagementPressureMetrics,
    MixedDeactivationDecisionPressureMetrics,
    MixedRevisionAdoptionPressureMetrics,
    ParameterAdoptionPressureMetrics,
    PerformanceRecorder,
    RejectedTargetPressureMetrics,
    RemovedAdoptionPressureMetrics,
    SourceUnavailablePressureMetrics,
    StaleTargetDeactivationDecisionPressureMetrics,
    StructuralResetAdoptionPressureMetrics,
    SustainedDeactivationDecisionPressureMetrics,
    SustainedManagementPressureMetrics,
)
from performance.run import (
    _build_c2_routing_adoption_pressure_metrics,
    _build_cache_promotion_failure_pressure_metrics,
    _build_deactivation_decision_pressure_metrics,
    _build_disabled_adoption_pressure_metrics,
    _build_drain_under_workload_pressure_metrics,
    _build_functional_pressure_metrics,
    _build_invalid_source_candidate_pressure_metrics,
    _build_inverted_deactivation_decision_pressure_metrics,
    _build_lease_loss_adoption_pressure_metrics,
    _build_management_pressure_metrics,
    _build_mixed_deactivation_decision_pressure_metrics,
    _build_mixed_revision_adoption_pressure_metrics,
    _build_parameter_adoption_pressure_metrics,
    _build_rejected_target_pressure_metrics,
    _build_removed_adoption_pressure_metrics,
    _build_source_unavailable_pressure_metrics,
    _build_stale_target_deactivation_decision_pressure_metrics,
    _build_structural_reset_adoption_pressure_metrics,
    _build_sustained_deactivation_decision_pressure_metrics,
    _build_temporal_soak_metrics,
    _e010_iteration_as_of,
    _e011_iteration_as_of,
    _uses_two_phase_deactivation_runner,
)


def test_a001_builds_100_alarm_session_with_one_shared_view(tmp_path: Path) -> None:
    scenario = BaselineScenario()
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )

    assert len(runtime.revision.session.entries) == 100
    assert len(runtime.revision.session.data_plan.views) == 1
    assert runtime.revision.session.data_plan.views[0].column_names == ('signal',)


def test_baseline_scenario_allows_progressive_alarm_counts() -> None:
    assert BaselineScenario(alarm_count=250).alarm_count == 250
    assert BaselineScenario(alarm_count=1000).alarm_count == 1000


def test_latest_narrow_assigns_one_unique_column_per_alarm(tmp_path: Path) -> None:
    scenario = BaselineScenario(
        test_id='B-001',
        alarm_count=3,
        data_profile='latest-narrow',
    )
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )

    view = runtime.revision.session.data_plan.views[0]
    assert view.column_names == (
        'signal_00001_01',
        'signal_00002_01',
        'signal_00003_01',
    )
    assert tuple(
        entry.requirements[0].column_names for entry in runtime.revision.session.entries
    ) == (
        ('signal_00001_01',),
        ('signal_00002_01',),
        ('signal_00003_01',),
    )


def test_latest_narrow_loader_reports_dataset_shape(tmp_path: Path) -> None:
    scenario = BaselineScenario(
        test_id='B-001',
        alarm_count=4,
        data_profile='latest-narrow',
    )
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )

    loaded = runtime.source_loader.load(
        plan=runtime.revision.session.data_plan,
        as_of=datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
    )

    assert len(loaded.loaded) == 1
    assert runtime.source_loader.view_count == 1
    assert runtime.source_loader.column_count == 4
    assert runtime.source_loader.row_count == 1
    assert runtime.source_loader.frame_bytes > 0
    assert len(runtime.source_loader.load_durations_ms or []) == 1


def test_latest_wide_prepares_multiple_columns_per_alarm(tmp_path: Path) -> None:
    scenario = BaselineScenario(
        test_id='B-002',
        alarm_count=2,
        data_profile='latest-wide',
        columns_per_alarm=3,
    )
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )

    assert runtime.revision.session.data_plan.views[0].column_names == (
        'signal_00001_01',
        'signal_00001_02',
        'signal_00001_03',
        'signal_00002_01',
        'signal_00002_02',
        'signal_00002_03',
    )


def test_b003_balances_columns_across_four_physical_partitions(tmp_path: Path) -> None:
    scenario = BaselineScenario(
        test_id='B-003',
        alarm_count=8,
        data_profile='latest-narrow',
        physical_partition_count=4,
    )
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )

    loaded = runtime.source_loader.load(
        plan=runtime.revision.session.data_plan,
        as_of=datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
    )

    assert len(runtime.revision.session.data_plan.views) == 1
    assert len(loaded.loaded) == 1
    assert runtime.source_loader.physical_partition_column_counts == (2, 2, 2, 2)
    assert runtime.source_loader.column_count == 8
    assert tuple(next(iter(loaded.loaded.values())).frame.columns) == (
        'signal_00001_01',
        'signal_00002_01',
        'signal_00003_01',
        'signal_00004_01',
        'signal_00005_01',
        'signal_00006_01',
        'signal_00007_01',
        'signal_00008_01',
    )
    assert runtime.source_loader.merge_durations_ms[0] >= 0


def test_b007_skews_columns_across_thirty_six_physical_partitions(tmp_path: Path) -> None:
    scenario = BaselineScenario(
        test_id='B-007',
        alarm_count=1000,
        data_profile='latest-narrow',
        physical_partition_count=36,
        physical_partition_layout='skewed',
    )
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )

    loaded = runtime.source_loader.load(
        plan=runtime.revision.session.data_plan,
        as_of=datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
    )

    counts = runtime.source_loader.physical_partition_column_counts
    assert len(counts) == 36
    assert counts[:8] == (100,) * 8
    assert sum(counts[:8]) == 800
    assert sum(counts[8:]) == 200
    assert min(counts[8:]) == 7
    assert max(counts[8:]) == 8
    assert sum(counts) == 1000
    assert runtime.source_loader.column_count == 1000
    assert tuple(next(iter(loaded.loaded.values())).frame.columns) == tuple(
        f'signal_{index:05d}_01' for index in range(1, 1001)
    )


def test_b008_mixed_layout_synthesizes_nulls_and_keeps_logical_columns(
    tmp_path: Path,
) -> None:
    scenario = BaselineScenario(
        test_id='B-008',
        alarm_count=1000,
        data_profile='latest-narrow',
        physical_partition_count=36,
        physical_partition_layout='mixed',
    )
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )

    loaded = runtime.source_loader.load(
        plan=runtime.revision.session.data_plan,
        as_of=datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
    )

    frame = next(iter(loaded.loaded.values())).frame
    assert runtime.source_loader.empty_physical_partition_count == 4
    assert runtime.source_loader.missing_source_column_count == 120
    assert len(runtime.source_loader.missing_source_columns) == 120
    assert runtime.source_loader.synthesized_null_column_count == 120
    assert (
        scenario.expected_snapshot_alarm_count(
            missing_source_columns=runtime.source_loader.missing_source_columns
        )
        == 880
    )
    assert runtime.source_loader.column_count == 1000
    assert tuple(frame.columns) == tuple(f'signal_{index:05d}_01' for index in range(1, 1001))
    assert int(frame.isna().sum().sum()) == 120


def test_b008_mixed_layout_reports_empty_physical_partitions(tmp_path: Path) -> None:
    scenario = BaselineScenario(
        test_id='B-008',
        alarm_count=36,
        data_profile='latest-narrow',
        physical_partition_count=36,
        physical_partition_layout='mixed',
    )
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )

    runtime.source_loader.load(
        plan=runtime.revision.session.data_plan,
        as_of=datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
    )

    counts = runtime.source_loader.physical_partition_column_counts
    assert len(counts) == 36
    assert counts[0] == 0
    assert counts[9] == 0
    assert counts[18] == 0
    assert counts[27] == 0


def test_physical_partition_layout_rejects_unknown_value() -> None:
    try:
        BaselineScenario(physical_partition_layout='unknown')
    except ValueError as error:
        assert str(error) == 'physical_partition_layout must be one of: balanced, skewed, mixed'
    else:
        raise AssertionError('expected physical partition layout validation error')


def test_physical_partition_count_cannot_exceed_source_columns() -> None:
    try:
        BaselineScenario(
            alarm_count=3,
            data_profile='latest-narrow',
            physical_partition_count=4,
        )
    except ValueError as error:
        assert str(error) == 'physical_partition_count must not exceed source column count'
    else:
        raise AssertionError('expected physical partition validation error')


def test_b009_historical_stress_builds_latest_and_daily_requirements(tmp_path: Path) -> None:
    scenario = BaselineScenario(
        test_id='B-009',
        alarm_count=2,
        data_profile='latest-historical',
        physical_partition_count=2,
        historical_series_per_alarm=3,
        historical_window_minutes=60,
        historical_step_seconds=10,
    )
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )

    views = runtime.revision.session.data_plan.views
    assert len(views) == 2
    assert views[0].source is DataSource.PI_INTERPOLATED
    assert views[0].partition is DataPartition.LATEST
    assert views[0].column_names == ('signal_00001_01', 'signal_00002_01')
    assert views[1].partition is DataPartition.DAILY
    assert views[1].column_names == (
        'history_00001_01',
        'history_00001_02',
        'history_00001_03',
        'history_00002_01',
        'history_00002_02',
        'history_00002_03',
    )
    assert views[1].time_windows[0].value == 60
    assert len(runtime.revision.session.entries[0].requirements) == 2
    assert scenario.historical_points_per_series == 360
    assert scenario.historical_value_count == 2160


def test_b009_historical_loader_materializes_expected_pressure_shape(tmp_path: Path) -> None:
    scenario = BaselineScenario(
        test_id='B-009',
        alarm_count=4,
        data_profile='latest-historical',
        physical_partition_count=2,
        historical_series_per_alarm=3,
        historical_window_minutes=1,
        historical_step_seconds=10,
    )
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )

    loaded = runtime.source_loader.load(
        plan=runtime.revision.session.data_plan,
        as_of=datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
    )

    latest = loaded.loaded[
        next(view for view in loaded.loaded if view.partition is DataPartition.LATEST)
    ]
    historical = loaded.loaded[
        next(view for view in loaded.loaded if view.partition is DataPartition.DAILY)
    ]
    assert latest.frame.shape == (1, 4)
    assert historical.frame.shape == (6, 13)
    assert historical.frame.columns[0] == 'timestamp_utc'
    assert runtime.source_loader.view_count == 2
    assert runtime.source_loader.column_count == 16
    assert runtime.source_loader.latest_column_count == 4
    assert runtime.source_loader.historical_column_count == 12
    assert runtime.source_loader.historical_row_count == 6
    assert runtime.source_loader.historical_value_count == 72
    assert runtime.source_loader.numeric_value_count == 76
    assert runtime.source_loader.physical_partition_column_counts == (2, 2, 6, 6)
    assert runtime.source_loader.frame_bytes > 0


def test_latest_historical_requires_complete_temporal_parameters() -> None:
    try:
        BaselineScenario(
            data_profile='latest-historical',
            historical_series_per_alarm=3,
            historical_window_minutes=60,
            historical_step_seconds=7,
        )
    except ValueError as error:
        assert str(error) == (
            'historical window seconds must be divisible by historical_step_seconds'
        )
    else:
        raise AssertionError('expected historical step validation error')


def test_c001_builds_balanced_priority_groups(tmp_path: Path) -> None:
    full_scenario = BaselineScenario(
        test_id='C-001',
        alarm_count=1000,
        data_profile='latest-narrow',
        priority_group_size=10,
        operational_churn_percent=10,
        initial_active_percent=50,
    )
    assert full_scenario.priority_group_count == 100
    assert full_scenario.changed_priority_group_count == 10
    assert full_scenario.changed_alarm_count == 100
    assert full_scenario.initial_active_alarm_count == 500

    scenario = BaselineScenario(
        test_id='C-001',
        alarm_count=100,
        data_profile='latest-narrow',
        priority_group_size=10,
        operational_churn_percent=10,
        initial_active_percent=50,
    )
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )

    groups: dict[str, list[int]] = {}
    for entry in runtime.revision.session.entries:
        plan = entry.planned_alarm
        groups.setdefault(plan.priority_group, []).append(plan.priority_order)

    assert scenario.priority_group_count == 10
    assert len(groups) == 10
    assert tuple(groups) == tuple(f'perf-group-{index:03d}' for index in range(1, 11))
    assert all(orders == list(range(1, 11)) for orders in groups.values())


def test_c001_loader_rotates_exact_ten_percent_churn_by_generation(tmp_path: Path) -> None:
    scenario = BaselineScenario(
        test_id='C-001',
        alarm_count=100,
        data_profile='latest-narrow',
        priority_group_size=10,
        operational_churn_percent=10,
        initial_active_percent=50,
    )
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    plan = runtime.revision.session.data_plan

    initial = next(
        iter(
            runtime.source_loader.load(
                plan=plan,
                as_of=datetime(2026, 8, 25, 12, 0, 0, tzinfo=UTC),
            ).loaded.values()
        )
    ).frame.iloc[0]
    unchanged = next(
        iter(
            runtime.source_loader.load(
                plan=plan,
                as_of=datetime(2026, 8, 25, 12, 0, 5, tzinfo=UTC),
            ).loaded.values()
        )
    ).frame.iloc[0]
    generation_one = next(
        iter(
            runtime.source_loader.load(
                plan=plan,
                as_of=datetime(2026, 8, 25, 12, 0, 10, tzinfo=UTC),
            ).loaded.values()
        )
    ).frame.iloc[0]
    generation_two = next(
        iter(
            runtime.source_loader.load(
                plan=plan,
                as_of=datetime(2026, 8, 25, 12, 0, 20, tzinfo=UTC),
            ).loaded.values()
        )
    ).frame.iloc[0]

    assert initial.equals(unchanged)
    assert int((initial != generation_one).sum()) == 10
    assert int((generation_one != generation_two).sum()) == 10
    assert tuple(generation_one.iloc[:10]) == tuple(reversed(tuple(initial.iloc[:10])))
    assert tuple(generation_two.iloc[:10]) == tuple(generation_one.iloc[:10])
    assert tuple(generation_two.iloc[10:20]) == tuple(reversed(tuple(generation_one.iloc[10:20])))
    assert int((initial >= scenario.threshold).sum()) == 50
    assert int((generation_two >= scenario.threshold).sum()) == 50
    assert runtime.source_loader.churn_generation_count == 2
    assert runtime.source_loader.churn_group_transition_count == 2
    assert runtime.source_loader.churn_transition_count == 20
    assert scenario.expected_snapshot_alarm_count(missing_source_columns=()) == 50


def test_operational_churn_rejects_ambiguous_layouts() -> None:
    for kwargs, message in (
        (
            {'data_profile': 'shared-latest', 'priority_group_size': 10},
            'operational churn requires data_profile=latest-narrow',
        ),
        (
            {'data_profile': 'latest-narrow', 'priority_group_size': 0},
            'operational churn requires priority_group_size > 0',
        ),
        (
            {
                'data_profile': 'latest-narrow',
                'priority_group_size': 10,
                'initial_active_percent': 40,
            },
            'operational churn requires initial_active_percent=50',
        ),
    ):
        try:
            BaselineScenario(
                alarm_count=100,
                operational_churn_percent=10,
                **kwargs,
            )
        except ValueError as error:
            assert str(error) == message
        else:
            raise AssertionError('expected operational churn validation error')


def test_functional_pressure_summary_separates_lifecycle_from_periodic_evidence() -> None:
    scenario = BaselineScenario(
        test_id='C-001',
        alarm_count=100,
        data_profile='latest-narrow',
        priority_group_size=10,
        operational_churn_percent=10,
        initial_active_percent=50,
    )
    first_generation = int(datetime(2026, 8, 25, 12, 0, tzinfo=UTC).timestamp()) // 10
    source_loader = SimpleNamespace(
        first_generation=first_generation,
        churn_generation_count=2,
        churn_group_transition_count=2,
        churn_transition_count=20,
    )
    records = [
        _functional_entry(
            priority_group=f'perf-group-{group_index:03d}',
            evaluated_at='2026-08-25T12:00:00Z',
            occurrence_started=5,
            episode_started=1,
            assignment_changes=5,
            evidence_records=5,
        )
        for group_index in range(1, 11)
    ]
    records.extend(
        (
            _functional_entry(
                priority_group='perf-group-001',
                evaluated_at='2026-08-25T12:00:10Z',
                occurrence_started=5,
                occurrence_closed=5,
                assignment_changes=5,
                evidence_records=10,
            ),
            _functional_entry(
                priority_group='perf-group-002',
                evaluated_at='2026-08-25T12:00:20Z',
                occurrence_started=5,
                occurrence_closed=5,
                assignment_changes=5,
                evidence_records=10,
            ),
            _functional_entry(
                priority_group='perf-group-003',
                evaluated_at='2026-08-25T12:00:20Z',
                evidence_records=5,
            ),
        )
    )

    summary = _build_functional_pressure_metrics(
        scenario=scenario,
        source_loader=source_loader,
        records=tuple(records),
    )

    assert summary is not None
    assert summary.functional_integrity_ok is True
    assert summary.lifecycle_commit_count == 12
    assert summary.expected_lifecycle_commit_count == 12
    assert summary.duplicate_lifecycle_commit_count == 0
    assert summary.occurrence_started_count == 60
    assert summary.occurrence_closed_count == 10
    assert summary.episode_started_count == 10
    assert summary.episode_closed_count == 0
    assert summary.evidence_only_commit_count == 1


def test_c002_loader_pairs_error_and_recovery_generations(tmp_path: Path) -> None:
    scenario = BaselineScenario(
        test_id='C-002',
        alarm_count=100,
        data_profile='latest-narrow',
        priority_group_size=10,
        technical_hold_churn_percent=10,
        initial_active_percent=100,
    )
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    plan = runtime.revision.session.data_plan

    frames = []
    for second in (0, 10, 20, 30, 40):
        loaded = runtime.source_loader.load(
            plan=plan,
            as_of=datetime(2026, 8, 25, 12, 0, second, tzinfo=UTC),
        )
        frames.append(next(iter(loaded.loaded.values())).frame.iloc[0])

    initial, error_one, recovered_one, error_two, recovered_two = frames
    assert int((initial == scenario.signal_value).sum()) == 100
    assert int((error_one == _TECHNICAL_ERROR_SIGNAL_VALUE).sum()) == 10
    assert tuple(error_one.iloc[:10]) == (_TECHNICAL_ERROR_SIGNAL_VALUE,) * 10
    assert recovered_one.equals(initial)
    assert int((error_two == _TECHNICAL_ERROR_SIGNAL_VALUE).sum()) == 10
    assert tuple(error_two.iloc[10:20]) == (_TECHNICAL_ERROR_SIGNAL_VALUE,) * 10
    assert recovered_two.equals(initial)
    assert runtime.source_loader.churn_generation_count == 4
    assert runtime.source_loader.churn_group_transition_count == 4
    assert runtime.source_loader.churn_transition_count == 40
    assert runtime.source_loader.technical_hold_started_transition_count == 20
    assert runtime.source_loader.technical_hold_cleared_transition_count == 20
    assert scenario.expected_snapshot_alarm_count(missing_source_columns=()) == 100


def test_technical_hold_churn_rejects_ambiguous_configuration() -> None:
    for kwargs, message in (
        (
            {'data_profile': 'shared-latest', 'priority_group_size': 10},
            'technical hold churn requires data_profile=latest-narrow',
        ),
        (
            {'data_profile': 'latest-narrow', 'priority_group_size': 0},
            'technical hold churn requires priority_group_size > 0',
        ),
        (
            {
                'data_profile': 'latest-narrow',
                'priority_group_size': 10,
                'initial_active_percent': 50,
            },
            'technical hold churn requires initial_active_percent=100',
        ),
    ):
        try:
            BaselineScenario(
                alarm_count=100,
                technical_hold_churn_percent=10,
                **kwargs,
            )
        except ValueError as error:
            assert str(error) == message
        else:
            raise AssertionError('expected technical hold churn validation error')

    try:
        BaselineScenario(
            alarm_count=100,
            data_profile='latest-narrow',
            priority_group_size=10,
            operational_churn_percent=10,
            technical_hold_churn_percent=10,
            initial_active_percent=50,
        )
    except ValueError as error:
        assert str(error) == 'functional churn modes are mutually exclusive'
    else:
        raise AssertionError('expected mutually exclusive functional churn error')


def test_c002_functional_summary_requires_hold_recovery_without_occurrence_restart() -> None:
    scenario = BaselineScenario(
        test_id='C-002',
        alarm_count=20,
        data_profile='latest-narrow',
        priority_group_size=10,
        technical_hold_churn_percent=50,
        initial_active_percent=100,
    )
    first_generation = int(datetime(2026, 8, 25, 12, 0, tzinfo=UTC).timestamp()) // 10
    source_loader = SimpleNamespace(
        first_generation=first_generation,
        churn_generation_count=2,
        churn_group_transition_count=2,
        churn_transition_count=20,
        technical_hold_started_transition_count=10,
        technical_hold_cleared_transition_count=10,
    )
    records = [
        _functional_entry(
            priority_group='perf-group-001',
            evaluated_at='2026-08-25T12:00:00Z',
            occurrence_started=10,
            episode_started=1,
            assignment_changes=10,
            evidence_records=10,
        ),
        _functional_entry(
            priority_group='perf-group-002',
            evaluated_at='2026-08-25T12:00:00Z',
            occurrence_started=10,
            episode_started=1,
            assignment_changes=10,
            evidence_records=10,
        ),
        _functional_entry(
            priority_group='perf-group-001',
            evaluated_at='2026-08-25T12:00:10Z',
            technical_hold_started=10,
            evidence_records=10,
        ),
        _functional_entry(
            priority_group='perf-group-001',
            evaluated_at='2026-08-25T12:00:20Z',
            technical_hold_cleared=10,
            evidence_records=10,
        ),
    ]

    summary = _build_functional_pressure_metrics(
        scenario=scenario,
        source_loader=source_loader,
        records=tuple(records),
    )

    assert summary is not None
    assert summary.functional_integrity_ok is True
    assert summary.lifecycle_commit_count == 4
    assert summary.expected_lifecycle_commit_count == 4
    assert summary.occurrence_started_count == 20
    assert summary.occurrence_closed_count == 0
    assert summary.episode_started_count == 2
    assert summary.episode_closed_count == 0
    assert summary.assignment_change_count == 20
    assert summary.expected_assignment_change_count == 20
    assert summary.technical_hold_started_count == 10
    assert summary.expected_technical_hold_started_count == 10
    assert summary.technical_hold_cleared_count == 10
    assert summary.expected_technical_hold_cleared_count == 10
    assert summary.technical_hold_expired_count == 0
    assert summary.occurrence_identity_mismatch_count == 0


def test_c003_loader_staggers_expiry_cohorts_and_counts_reappearance(tmp_path: Path) -> None:
    scenario = BaselineScenario(
        test_id='C-003',
        alarm_count=100,
        duration_seconds=600,
        data_profile='latest-narrow',
        priority_group_size=10,
        technical_hold_expiry_percent=10,
        technical_hold_expiry_stagger_seconds=30,
        technical_hold_error_duration_seconds=320,
        initial_active_percent=100,
    )
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    plan = runtime.revision.session.data_plan
    frames = {}
    start = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    for generation_index in range(61):
        loaded = runtime.source_loader.load(
            plan=plan,
            as_of=start.replace(second=0) + timedelta(seconds=generation_index * 10),
        )
        if generation_index in {0, 1, 4, 31, 33, 34, 36, 60}:
            frames[generation_index] = next(iter(loaded.loaded.values())).frame.iloc[0]

    assert int((frames[0] == scenario.signal_value).sum()) == 100
    assert tuple(frames[1].iloc[:10]) == (_TECHNICAL_ERROR_SIGNAL_VALUE,) * 10
    assert tuple(frames[4].iloc[10:20]) == (_TECHNICAL_ERROR_SIGNAL_VALUE,) * 10
    assert tuple(frames[31].iloc[:10]) == (_TECHNICAL_ERROR_SIGNAL_VALUE,) * 10
    assert tuple(frames[33].iloc[:10]) == (scenario.signal_value,) * 10
    assert tuple(frames[34].iloc[10:20]) == (_TECHNICAL_ERROR_SIGNAL_VALUE,) * 10
    assert tuple(frames[36].iloc[10:20]) == (scenario.signal_value,) * 10
    assert int((frames[60] == scenario.signal_value).sum()) == 100
    assert runtime.source_loader.churn_generation_count == 60
    assert runtime.source_loader.churn_group_transition_count == 20
    assert runtime.source_loader.churn_transition_count == 200
    assert runtime.source_loader.technical_hold_started_transition_count == 100
    assert runtime.source_loader.technical_hold_cleared_transition_count == 0
    assert runtime.source_loader.technical_hold_expired_group_transition_count == 10
    assert runtime.source_loader.technical_hold_expired_transition_count == 100
    assert runtime.source_loader.technical_hold_reappearance_group_transition_count == 10
    assert runtime.source_loader.technical_hold_reappearance_transition_count == 100
    assert scenario.expected_snapshot_alarm_count(missing_source_columns=()) == 100


def test_technical_hold_expiry_rejects_ambiguous_configuration() -> None:
    cases = (
        (
            {'data_profile': 'shared-latest', 'priority_group_size': 10},
            'technical hold expiry requires data_profile=latest-narrow',
        ),
        (
            {'data_profile': 'latest-narrow', 'priority_group_size': 0},
            'technical hold expiry requires priority_group_size > 0',
        ),
        (
            {
                'data_profile': 'latest-narrow',
                'priority_group_size': 10,
                'initial_active_percent': 50,
            },
            'technical hold expiry requires initial_active_percent=100',
        ),
        (
            {
                'data_profile': 'latest-narrow',
                'priority_group_size': 10,
                'technical_hold_expiry_stagger_seconds': 25,
            },
            'technical hold expiry stagger must align with data refresh',
        ),
        (
            {
                'data_profile': 'latest-narrow',
                'priority_group_size': 10,
                'technical_hold_expiry_stagger_seconds': 30,
                'technical_hold_error_duration_seconds': 300,
            },
            'technical hold error duration must exceed technical hold grace',
        ),
    )
    for kwargs, message in cases:
        values = {
            'alarm_count': 100,
            'duration_seconds': 600,
            'technical_hold_expiry_percent': 10,
            'technical_hold_expiry_stagger_seconds': 30,
            'technical_hold_error_duration_seconds': 320,
            **kwargs,
        }
        try:
            BaselineScenario(**values)
        except ValueError as error:
            assert str(error) == message
        else:
            raise AssertionError('expected technical hold expiry validation error')

    try:
        BaselineScenario(
            alarm_count=100,
            duration_seconds=600,
            data_profile='latest-narrow',
            priority_group_size=10,
            technical_hold_churn_percent=10,
            technical_hold_expiry_percent=10,
            technical_hold_expiry_stagger_seconds=30,
            technical_hold_error_duration_seconds=320,
            initial_active_percent=100,
        )
    except ValueError as error:
        assert str(error) == 'functional churn modes are mutually exclusive'
    else:
        raise AssertionError('expected mutually exclusive functional churn error')

    try:
        BaselineScenario(
            alarm_count=100,
            duration_seconds=590,
            data_profile='latest-narrow',
            priority_group_size=10,
            technical_hold_expiry_percent=10,
            technical_hold_expiry_stagger_seconds=30,
            technical_hold_error_duration_seconds=320,
            initial_active_percent=100,
        )
    except ValueError as error:
        assert str(error) == 'duration_seconds must cover all technical hold expiry cohorts'
    else:
        raise AssertionError('expected technical hold expiry duration coverage error')


def test_c003_functional_summary_requires_expiry_and_new_occurrence_identity() -> None:
    scenario = BaselineScenario(
        test_id='C-003',
        alarm_count=20,
        duration_seconds=400,
        data_profile='latest-narrow',
        priority_group_size=10,
        technical_hold_expiry_percent=50,
        technical_hold_expiry_stagger_seconds=30,
        technical_hold_error_duration_seconds=320,
        initial_active_percent=100,
    )
    first_generation = int(datetime(2026, 8, 25, 12, 0, tzinfo=UTC).timestamp()) // 10
    source_loader = SimpleNamespace(
        first_generation=first_generation,
        churn_generation_count=36,
        churn_group_transition_count=4,
        churn_transition_count=40,
        technical_hold_started_transition_count=20,
        technical_hold_cleared_transition_count=0,
        technical_hold_expired_transition_count=20,
        technical_hold_expired_group_transition_count=2,
        technical_hold_reappearance_transition_count=20,
        technical_hold_reappearance_group_transition_count=2,
    )
    records = [
        _functional_entry(
            priority_group='perf-group-001',
            evaluated_at='2026-08-25T12:00:00Z',
            occurrence_started=10,
            episode_started=1,
            assignment_changes=10,
        ),
        _functional_entry(
            priority_group='perf-group-002',
            evaluated_at='2026-08-25T12:00:00Z',
            occurrence_started=10,
            episode_started=1,
            assignment_changes=10,
        ),
        _functional_entry(
            priority_group='perf-group-001',
            evaluated_at='2026-08-25T12:00:10Z',
            technical_hold_started=10,
        ),
        _functional_entry(
            priority_group='perf-group-002',
            evaluated_at='2026-08-25T12:00:40Z',
            technical_hold_started=10,
        ),
        _functional_entry(
            priority_group='perf-group-001',
            evaluated_at='2026-08-25T12:05:10Z',
            occurrence_closed=10,
            occurrence_closed_reason='technical_hold_expired',
            episode_closed=1,
            episode_closed_reason='technical_uncertainty',
            technical_hold_expired=10,
        ),
        _functional_entry(
            priority_group='perf-group-001',
            evaluated_at='2026-08-25T12:05:30Z',
            reappearance_started=10,
            episode_started=1,
            assignment_changes=10,
        ),
        _functional_entry(
            priority_group='perf-group-002',
            evaluated_at='2026-08-25T12:05:40Z',
            occurrence_closed=10,
            occurrence_closed_reason='technical_hold_expired',
            episode_closed=1,
            episode_closed_reason='technical_uncertainty',
            technical_hold_expired=10,
        ),
        _functional_entry(
            priority_group='perf-group-002',
            evaluated_at='2026-08-25T12:06:00Z',
            reappearance_started=10,
            episode_started=1,
            assignment_changes=10,
        ),
    ]

    summary = _build_functional_pressure_metrics(
        scenario=scenario,
        source_loader=source_loader,
        records=tuple(records),
    )

    assert summary is not None
    assert summary.functional_integrity_ok is True
    assert summary.lifecycle_commit_count == 8
    assert summary.expected_lifecycle_commit_count == 8
    assert summary.occurrence_started_count == 40
    assert summary.expected_occurrence_started_count == 40
    assert summary.occurrence_closed_count == 20
    assert summary.expected_occurrence_closed_count == 20
    assert summary.episode_started_count == 4
    assert summary.expected_episode_started_count == 4
    assert summary.episode_closed_count == 2
    assert summary.expected_episode_closed_count == 2
    assert summary.assignment_change_count == 40
    assert summary.expected_assignment_change_count == 40
    assert summary.technical_hold_started_count == 20
    assert summary.expected_technical_hold_started_count == 20
    assert summary.technical_hold_cleared_count == 0
    assert summary.technical_hold_expired_count == 20
    assert summary.expected_technical_hold_expired_count == 20
    assert summary.post_expiry_occurrence_started_count == 20
    assert summary.expected_post_expiry_occurrence_started_count == 20
    assert summary.post_expiry_occurrence_identity_reuse_count == 0
    assert summary.invalid_occurrence_closure_reason_count == 0
    assert summary.invalid_episode_closure_reason_count == 0
    assert summary.occurrence_identity_mismatch_count == 0


def test_c004_loader_starts_in_error_and_staggers_first_activation(tmp_path: Path) -> None:
    scenario = BaselineScenario(
        test_id='C-004',
        alarm_count=100,
        duration_seconds=600,
        data_profile='latest-narrow',
        priority_group_size=10,
        initial_error_activation_percent=10,
        initial_error_hold_seconds=20,
        initial_error_activation_stagger_seconds=20,
        initial_active_percent=0,
    )
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    plan = runtime.revision.session.data_plan
    frames = {}
    start = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    for generation_index in range(22):
        loaded = runtime.source_loader.load(
            plan=plan,
            as_of=start + timedelta(seconds=generation_index * 10),
        )
        if generation_index in {0, 1, 2, 4, 20, 21}:
            frames[generation_index] = next(iter(loaded.loaded.values())).frame.iloc[0]

    assert tuple(frames[0]) == (_TECHNICAL_ERROR_SIGNAL_VALUE,) * 100
    assert tuple(frames[1]) == (_TECHNICAL_ERROR_SIGNAL_VALUE,) * 100
    assert tuple(frames[2].iloc[:10]) == (scenario.signal_value,) * 10
    assert tuple(frames[2].iloc[10:]) == (_TECHNICAL_ERROR_SIGNAL_VALUE,) * 90
    assert tuple(frames[4].iloc[:20]) == (scenario.signal_value,) * 20
    assert tuple(frames[20]) == (scenario.signal_value,) * 100
    assert tuple(frames[21]) == (scenario.signal_value,) * 100
    assert runtime.source_loader.churn_generation_count == 21
    assert runtime.source_loader.churn_group_transition_count == 10
    assert runtime.source_loader.churn_transition_count == 100
    assert runtime.source_loader.initial_error_activation_group_transition_count == 10
    assert runtime.source_loader.initial_error_activation_transition_count == 100
    assert runtime.source_loader.technical_hold_started_transition_count == 0
    assert scenario.expected_snapshot_alarm_count(missing_source_columns=()) == 100


def test_initial_error_activation_rejects_ambiguous_configuration() -> None:
    cases = (
        (
            {'data_profile': 'shared-latest', 'priority_group_size': 10},
            'initial error activation requires data_profile=latest-narrow',
        ),
        (
            {'data_profile': 'latest-narrow', 'priority_group_size': 0},
            'initial error activation requires priority_group_size > 0',
        ),
        (
            {
                'data_profile': 'latest-narrow',
                'priority_group_size': 10,
                'initial_active_percent': 100,
            },
            'initial error activation requires initial_active_percent=0',
        ),
        (
            {
                'data_profile': 'latest-narrow',
                'priority_group_size': 10,
                'initial_active_percent': 0,
                'initial_error_hold_seconds': 10,
            },
            'initial error hold must cover at least two data generations',
        ),
        (
            {
                'data_profile': 'latest-narrow',
                'priority_group_size': 10,
                'initial_active_percent': 0,
                'initial_error_hold_seconds': 20,
                'initial_error_activation_stagger_seconds': 15,
            },
            'initial error activation stagger must align with data refresh',
        ),
    )
    for kwargs, message in cases:
        values = {
            'alarm_count': 100,
            'duration_seconds': 600,
            'initial_error_activation_percent': 10,
            'initial_error_hold_seconds': 20,
            'initial_error_activation_stagger_seconds': 20,
            'initial_active_percent': 0,
            **kwargs,
        }
        try:
            BaselineScenario(**values)
        except ValueError as error:
            assert str(error) == message
        else:
            raise AssertionError('expected initial error activation validation error')

    try:
        BaselineScenario(
            alarm_count=100,
            duration_seconds=600,
            data_profile='latest-narrow',
            priority_group_size=10,
            technical_hold_churn_percent=10,
            initial_error_activation_percent=10,
            initial_error_hold_seconds=20,
            initial_error_activation_stagger_seconds=20,
            initial_active_percent=0,
        )
    except ValueError as error:
        assert str(error) == 'functional churn modes are mutually exclusive'
    else:
        raise AssertionError('expected mutually exclusive functional churn error')

    try:
        BaselineScenario(
            alarm_count=100,
            duration_seconds=190,
            data_profile='latest-narrow',
            priority_group_size=10,
            initial_error_activation_percent=10,
            initial_error_hold_seconds=20,
            initial_error_activation_stagger_seconds=20,
            initial_active_percent=0,
        )
    except ValueError as error:
        assert str(error) == 'duration_seconds must cover all initial error activation cohorts'
    else:
        raise AssertionError('expected initial error activation duration coverage error')


def test_c004_functional_summary_requires_neutral_error_bootstrap_then_first_activation() -> None:
    scenario = BaselineScenario(
        test_id='C-004',
        alarm_count=20,
        duration_seconds=600,
        data_profile='latest-narrow',
        priority_group_size=10,
        initial_error_activation_percent=50,
        initial_error_hold_seconds=20,
        initial_error_activation_stagger_seconds=20,
        initial_active_percent=0,
    )
    first_generation = int(datetime(2026, 8, 25, 12, 0, tzinfo=UTC).timestamp()) // 10
    source_loader = SimpleNamespace(
        first_generation=first_generation,
        churn_generation_count=6,
        churn_group_transition_count=2,
        churn_transition_count=20,
        initial_error_activation_group_transition_count=2,
        initial_error_activation_transition_count=20,
        technical_hold_started_transition_count=0,
        technical_hold_cleared_transition_count=0,
    )
    records = [
        _functional_entry(
            priority_group='perf-group-001',
            evaluated_at='2026-08-25T12:00:20Z',
            occurrence_started=10,
            episode_started=1,
            assignment_changes=10,
            evidence_records=10,
        ),
        _functional_entry(
            priority_group='perf-group-002',
            evaluated_at='2026-08-25T12:00:40Z',
            occurrence_started=10,
            episode_started=1,
            assignment_changes=10,
            evidence_records=10,
        ),
    ]

    summary = _build_functional_pressure_metrics(
        scenario=scenario,
        source_loader=source_loader,
        records=tuple(records),
    )

    assert summary is not None
    assert summary.functional_integrity_ok is True
    assert summary.lifecycle_commit_count == 2
    assert summary.expected_lifecycle_commit_count == 2
    assert summary.pre_activation_lifecycle_commit_count == 0
    assert summary.initial_error_activation_count == 20
    assert summary.expected_initial_error_activation_count == 20
    assert summary.occurrence_started_count == 20
    assert summary.expected_occurrence_started_count == 20
    assert summary.occurrence_closed_count == 0
    assert summary.episode_started_count == 2
    assert summary.episode_closed_count == 0
    assert summary.assignment_change_count == 20
    assert summary.technical_hold_started_count == 0
    assert summary.technical_hold_cleared_count == 0
    assert summary.technical_hold_expired_count == 0


def test_c004_functional_summary_rejects_lifecycle_commit_before_activation() -> None:
    scenario = BaselineScenario(
        test_id='C-004',
        alarm_count=20,
        duration_seconds=600,
        data_profile='latest-narrow',
        priority_group_size=10,
        initial_error_activation_percent=50,
        initial_error_hold_seconds=20,
        initial_error_activation_stagger_seconds=20,
        initial_active_percent=0,
    )
    first_generation = int(datetime(2026, 8, 25, 12, 0, tzinfo=UTC).timestamp()) // 10
    source_loader = SimpleNamespace(
        first_generation=first_generation,
        churn_generation_count=6,
        churn_group_transition_count=2,
        churn_transition_count=20,
        initial_error_activation_group_transition_count=2,
        initial_error_activation_transition_count=20,
        technical_hold_started_transition_count=0,
        technical_hold_cleared_transition_count=0,
    )
    records = [
        _functional_entry(
            priority_group='perf-group-001',
            evaluated_at='2026-08-25T12:00:10Z',
            occurrence_started=10,
            episode_started=1,
            assignment_changes=10,
        ),
        _functional_entry(
            priority_group='perf-group-002',
            evaluated_at='2026-08-25T12:00:40Z',
            occurrence_started=10,
            episode_started=1,
            assignment_changes=10,
        ),
    ]

    summary = _build_functional_pressure_metrics(
        scenario=scenario,
        source_loader=source_loader,
        records=tuple(records),
    )

    assert summary is not None
    assert summary.pre_activation_lifecycle_commit_count == 1
    assert summary.functional_integrity_ok is False


def test_performance_recorder_classifies_percentiles_and_overruns() -> None:
    recorder = PerformanceRecorder(iteration_period_seconds=5)
    assert (
        recorder.build_report(
            test_id='A-001',
            alarm_count=100,
            planned_duration_seconds=120,
            actual_duration_seconds=120,
            data_refresh_seconds=10,
            data_profile='shared-latest',
            columns_per_alarm=1,
            historical_series_per_alarm=0,
            historical_window_minutes=0,
            historical_step_seconds=0,
            historical_points_per_series=0,
            historical_value_count=0,
            physical_partition_count=1,
            physical_partition_column_counts=(1,),
            physical_partition_layout='balanced',
            empty_physical_partition_count=0,
            missing_source_column_count=0,
            synthesized_null_column_count=0,
            source_view_count=1,
            source_column_count=1,
            source_row_count=1,
            source_frame_bytes=140,
            source_numeric_value_count=1,
            latest_source_column_count=1,
            historical_source_column_count=0,
            historical_source_row_count=0,
            source_load_durations_ms=[1.0, 2.0, 3.0],
            source_merge_durations_ms=[0.0, 0.0, 0.0],
            journal_aligned=True,
            durable_record_count=1,
            snapshot_count=1,
            snapshot_alarm_count=100,
            expected_snapshot_alarm_count=100,
            source_load_count=1,
        ).performance_class
        == 'GREEN'
    )


def test_performance_recorder_accepts_neutral_alarms_omitted_from_snapshot() -> None:
    recorder = PerformanceRecorder(iteration_period_seconds=5)
    report = recorder.build_report(
        test_id='B-008',
        alarm_count=1000,
        planned_duration_seconds=600,
        actual_duration_seconds=600,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        columns_per_alarm=1,
        historical_series_per_alarm=0,
        historical_window_minutes=0,
        historical_step_seconds=0,
        historical_points_per_series=0,
        historical_value_count=0,
        physical_partition_count=36,
        physical_partition_column_counts=(0,) + (28,) * 8 + (0,),
        physical_partition_layout='mixed',
        empty_physical_partition_count=4,
        missing_source_column_count=120,
        synthesized_null_column_count=120,
        source_view_count=1,
        source_column_count=1000,
        source_row_count=1,
        source_frame_bytes=8132,
        source_numeric_value_count=1000,
        latest_source_column_count=1000,
        historical_source_column_count=0,
        historical_source_row_count=0,
        source_load_durations_ms=[8.0, 9.0, 10.0],
        source_merge_durations_ms=[0.8, 1.0, 1.2],
        journal_aligned=True,
        durable_record_count=3,
        snapshot_count=1,
        snapshot_alarm_count=880,
        expected_snapshot_alarm_count=880,
        source_load_count=121,
    )

    assert report.result == 'PASS'
    assert report.integrity_ok is True
    assert report.expected_snapshot_alarm_count == 880
    assert report.neutral_alarm_count == 120


def test_performance_mirror_only_adds_comments() -> None:
    root = Path(__file__).resolve().parents[1] / 'performance'
    for name in ('__init__.py', 'baseline.py', 'metrics.py', 'run.py'):
        production = root / name
        commented = root / 'commented' / name
        assert _python_tokens(commented) == _python_tokens(production)


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


def test_c004r1_loader_keeps_fixed_half_active_half_initial_error(tmp_path: Path) -> None:
    scenario = BaselineScenario(
        test_id='C-004-R1',
        alarm_count=100,
        duration_seconds=300,
        data_profile='latest-narrow',
        priority_group_size=10,
        fixed_initial_error_percent=50,
        initial_active_percent=50,
    )
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    plan = runtime.revision.session.data_plan
    start = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)

    frames = []
    for generation_index in (0, 1, 5, 20):
        loaded = runtime.source_loader.load(
            plan=plan,
            as_of=start + timedelta(seconds=generation_index * 10),
        )
        frames.append(next(iter(loaded.loaded.values())).frame.iloc[0])

    for frame in frames:
        assert tuple(frame.iloc[:50]) == (scenario.signal_value,) * 50
        assert tuple(frame.iloc[50:]) == (_TECHNICAL_ERROR_SIGNAL_VALUE,) * 50
    assert runtime.source_loader.churn_group_transition_count == 0
    assert runtime.source_loader.churn_transition_count == 0
    assert runtime.source_loader.technical_hold_started_transition_count == 0
    assert scenario.expected_snapshot_alarm_count(missing_source_columns=()) == 50


def test_fixed_initial_error_rejects_ambiguous_configuration() -> None:
    cases = (
        (
            {'data_profile': 'shared-latest', 'priority_group_size': 10},
            'fixed initial error requires data_profile=latest-narrow',
        ),
        (
            {'data_profile': 'latest-narrow', 'priority_group_size': 0},
            'fixed initial error requires priority_group_size > 0',
        ),
        (
            {
                'data_profile': 'latest-narrow',
                'priority_group_size': 10,
                'initial_active_percent': 100,
            },
            'fixed initial error requires initial_active_percent to complement error percent',
        ),
    )
    for kwargs, message in cases:
        values = {
            'alarm_count': 100,
            'fixed_initial_error_percent': 50,
            'initial_active_percent': 50,
            **kwargs,
        }
        try:
            BaselineScenario(**values)
        except ValueError as error:
            assert str(error) == message
        else:
            raise AssertionError('expected fixed initial error validation error')

    try:
        BaselineScenario(
            alarm_count=100,
            data_profile='latest-narrow',
            priority_group_size=10,
            technical_hold_churn_percent=10,
            fixed_initial_error_percent=50,
            initial_active_percent=50,
        )
    except ValueError as error:
        assert str(error) == 'functional churn modes are mutually exclusive'
    else:
        raise AssertionError('expected mutually exclusive functional churn error')


def test_c004r1_functional_summary_requires_only_active_half_to_materialize() -> None:
    scenario = BaselineScenario(
        test_id='C-004-R1',
        alarm_count=20,
        duration_seconds=300,
        data_profile='latest-narrow',
        priority_group_size=10,
        fixed_initial_error_percent=50,
        initial_active_percent=50,
    )
    first_generation = int(datetime(2026, 8, 25, 12, 0, tzinfo=UTC).timestamp()) // 10
    source_loader = SimpleNamespace(
        first_generation=first_generation,
        churn_generation_count=10,
        churn_group_transition_count=0,
        churn_transition_count=0,
        technical_hold_started_transition_count=0,
        technical_hold_cleared_transition_count=0,
    )
    records = [
        _functional_entry(
            priority_group='perf-group-001',
            evaluated_at='2026-08-25T12:00:00Z',
            occurrence_started=10,
            episode_started=1,
            assignment_changes=10,
            evidence_records=10,
        ),
    ]

    summary = _build_functional_pressure_metrics(
        scenario=scenario,
        source_loader=source_loader,
        records=tuple(records),
    )

    assert summary is not None
    assert summary.functional_integrity_ok is True
    assert summary.fixed_initial_error_percent == 50
    assert summary.lifecycle_commit_count == 1
    assert summary.expected_lifecycle_commit_count == 1
    assert summary.planned_state_transition_count == 0
    assert summary.occurrence_started_count == 10
    assert summary.expected_occurrence_started_count == 10
    assert summary.occurrence_closed_count == 0
    assert summary.episode_started_count == 1
    assert summary.expected_episode_started_count == 1
    assert summary.episode_closed_count == 0
    assert summary.assignment_change_count == 10
    assert summary.expected_assignment_change_count == 10
    assert summary.technical_hold_started_count == 0
    assert summary.technical_hold_cleared_count == 0
    assert summary.technical_hold_expired_count == 0


def test_c004r2_lookup_mode_requires_fixed_initial_error_scenario() -> None:
    scenario = BaselineScenario(
        test_id='C-004-R2B',
        alarm_count=100,
        data_profile='latest-narrow',
        priority_group_size=10,
        fixed_initial_error_percent=50,
        durable_history_lookup_mode='indexed',
        initial_active_percent=50,
    )

    assert scenario.durable_history_lookup_mode == 'indexed'

    try:
        BaselineScenario(durable_history_lookup_mode='unknown')
    except ValueError as error:
        assert str(error) == 'durable_history_lookup_mode must be one of: baseline, indexed'
    else:
        raise AssertionError('expected durable history lookup mode validation error')

    try:
        BaselineScenario(durable_history_lookup_mode='indexed')
    except ValueError as error:
        assert (
            str(error) == 'indexed durable history lookup requires fixed_initial_error_percent > 0'
        )
    else:
        raise AssertionError('expected indexed lookup scope validation error')


def test_c004r3_product_batch_loader_does_not_collide_with_performance_index_state(
    tmp_path: Path,
) -> None:
    scenario = BaselineScenario(
        test_id='C-004-R3-SMOKE',
        alarm_count=100,
        data_profile='latest-narrow',
        priority_group_size=10,
        fixed_initial_error_percent=50,
        initial_active_percent=50,
    )
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )

    groups = runtime.composition.load_groups({'neutral-a': (), 'neutral-b': ()})

    assert tuple(groups) == ('neutral-a', 'neutral-b')
    assert all(group.snapshot is None for group in groups.values())


def test_c004r2_baseline_lookup_scans_for_each_missing_group(tmp_path: Path) -> None:
    scenario = BaselineScenario(
        test_id='C-004-R2A',
        alarm_count=100,
        data_profile='latest-narrow',
        priority_group_size=10,
        fixed_initial_error_percent=50,
        durable_history_lookup_mode='baseline',
        initial_active_percent=50,
    )
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    persistence = runtime.composition.durability.persistence
    records = (
        SimpleNamespace(record=SimpleNamespace(commit=SimpleNamespace(priority_group='group-a'))),
        SimpleNamespace(record=SimpleNamespace(commit=SimpleNamespace(priority_group='group-b'))),
    )
    persistence.read_head = lambda: SimpleNamespace(durable=object())  # type: ignore[method-assign]
    persistence.read_durable_records = lambda: records  # type: ignore[method-assign]

    runtime.composition.begin_durable_history_lookup_cycle()
    assert runtime.composition._has_durable_group_history('group-a') is True
    assert runtime.composition._has_durable_group_history('group-x') is False
    assert runtime.composition._has_durable_group_history('group-y') is False

    metrics = runtime.composition.build_durable_history_lookup_metrics()
    assert metrics.mode == 'baseline'
    assert metrics.cycle_count == 1
    assert metrics.lookup_call_count == 3
    assert metrics.durable_record_scan_count == 3
    assert metrics.durable_record_entries_seen == 6
    assert metrics.index_build_count == 0


def test_c004r2_indexed_lookup_scans_once_per_cycle(tmp_path: Path) -> None:
    scenario = BaselineScenario(
        test_id='C-004-R2B',
        alarm_count=100,
        data_profile='latest-narrow',
        priority_group_size=10,
        fixed_initial_error_percent=50,
        durable_history_lookup_mode='indexed',
        initial_active_percent=50,
    )
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    persistence = runtime.composition.durability.persistence
    records = (
        SimpleNamespace(record=SimpleNamespace(commit=SimpleNamespace(priority_group='group-a'))),
        SimpleNamespace(record=SimpleNamespace(commit=SimpleNamespace(priority_group='group-b'))),
    )
    persistence.read_head = lambda: SimpleNamespace(durable=object())  # type: ignore[method-assign]
    persistence.read_durable_records = lambda: records  # type: ignore[method-assign]

    runtime.composition.begin_durable_history_lookup_cycle()
    assert runtime.composition._has_durable_group_history('group-a') is True
    assert runtime.composition._has_durable_group_history('group-x') is False
    assert runtime.composition._has_durable_group_history('group-y') is False

    first_cycle = runtime.composition.build_durable_history_lookup_metrics()
    assert first_cycle.mode == 'indexed'
    assert first_cycle.cycle_count == 1
    assert first_cycle.lookup_call_count == 3
    assert first_cycle.durable_record_scan_count == 1
    assert first_cycle.durable_record_entries_seen == 2
    assert first_cycle.index_build_count == 1

    runtime.composition.begin_durable_history_lookup_cycle()
    assert runtime.composition._has_durable_group_history('group-x') is False
    assert runtime.composition._has_durable_group_history('group-y') is False

    second_cycle = runtime.composition.build_durable_history_lookup_metrics()
    assert second_cycle.cycle_count == 2
    assert second_cycle.lookup_call_count == 5
    assert second_cycle.durable_record_scan_count == 2
    assert second_cycle.durable_record_entries_seen == 4
    assert second_cycle.index_build_count == 2


def _functional_entry(
    *,
    priority_group: str,
    evaluated_at: str,
    occurrence_started: int = 0,
    occurrence_closed: int = 0,
    occurrence_closed_reason: str | None = 'condition_normalized',
    reappearance_started: int = 0,
    episode_started: int = 0,
    episode_closed: int = 0,
    episode_closed_reason: str | None = None,
    assignment_changes: int = 0,
    evidence_records: int = 0,
    technical_hold_started: int = 0,
    technical_hold_cleared: int = 0,
    technical_hold_expired: int = 0,
):
    started_occurrences = [
        {
            'kind': 'STARTED',
            'alarm_key': f'{priority_group}/alarm-{index + 1:02d}',
            'occurrence_id': f'{priority_group}-occ-{index + 1:02d}',
        }
        for index in range(occurrence_started)
    ]
    closed_occurrences = [
        {
            'kind': 'CLOSED',
            'alarm_key': f'{priority_group}/alarm-{index + 1:02d}',
            'occurrence_id': f'{priority_group}-occ-{index + 1:02d}',
            'closure_reason': occurrence_closed_reason,
        }
        for index in range(occurrence_closed)
    ]
    reappeared_occurrences = [
        {
            'kind': 'STARTED',
            'alarm_key': f'{priority_group}/alarm-{index + 1:02d}',
            'occurrence_id': f'{priority_group}-reappearance-occ-{index + 1:02d}',
        }
        for index in range(reappearance_started)
    ]
    technical_events = [
        {
            'event_key': event_key,
            'alarm_key': f'{priority_group}/alarm-{index + 1:02d}',
            'occurrence_id': f'{priority_group}-occ-{index + 1:02d}',
        }
        for event_key, count in (
            ('technical_hold_started', technical_hold_started),
            ('technical_hold_recovered', technical_hold_cleared),
            ('technical_hold_expired', technical_hold_expired),
        )
        for index in range(count)
    ]
    return SimpleNamespace(
        record=SimpleNamespace(
            commit=SimpleNamespace(
                priority_group=priority_group,
                evaluated_at=evaluated_at,
            ),
            records={
                'occurrence_changes': (
                    started_occurrences + closed_occurrences + reappeared_occurrences
                ),
                'episode_changes': [{'kind': 'STARTED'} for _ in range(episode_started)]
                + [
                    {'kind': 'CLOSED', 'closure_reason': episode_closed_reason}
                    for _ in range(episode_closed)
                ],
                'assignment_changes': [{'kind': 'ASSIGNED'} for _ in range(assignment_changes)],
                'journey_events': technical_events,
                'evidence_records': [{} for _ in range(evidence_records)],
            },
        )
    )


def test_c005_builds_real_c1_origin_plus_three_destination_routes(tmp_path: Path) -> None:
    scenario = BaselineScenario(
        test_id='C-005',
        alarm_count=100,
        duration_seconds=300,
        data_profile='latest-narrow',
        priority_group_size=10,
        c1_routing_destination_count=3,
        initial_active_percent=100,
        physical_partition_count=10,
        physical_partition_layout='balanced',
    )
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )

    assert scenario.has_routing_pressure is True
    assert scenario.expected_snapshot_alarm_count(missing_source_columns=()) == 100
    for planned in runtime.revision.session.planned_alarms:
        assert planned.criticality.value == 'C1'
        assert planned.routing.origin_tool_key == 'perf-tool'
        assert tuple(
            (destination.tool_key, destination.delay_seconds)
            for destination in planned.routing.destinations
        ) == (
            ('perf-route-01', None),
            ('perf-route-02', None),
            ('perf-route-03', None),
        )


def test_c005_rejects_routing_mixed_with_other_functional_pressure() -> None:
    cases = (
        (
            {'data_profile': 'shared-latest', 'priority_group_size': 10},
            'C1 routing pressure requires data_profile=latest-narrow',
        ),
        (
            {'data_profile': 'latest-narrow', 'priority_group_size': 0},
            'C1 routing pressure requires priority_group_size > 0',
        ),
        (
            {
                'data_profile': 'latest-narrow',
                'priority_group_size': 10,
                'initial_active_percent': 50,
            },
            'C1 routing pressure requires initial_active_percent=100',
        ),
        (
            {
                'data_profile': 'latest-narrow',
                'priority_group_size': 10,
                'operational_churn_percent': 10,
                'initial_active_percent': 50,
            },
            'C1 routing pressure requires initial_active_percent=100',
        ),
    )
    for kwargs, message in cases:
        values = {
            'alarm_count': 100,
            'c1_routing_destination_count': 3,
            **kwargs,
        }
        try:
            BaselineScenario(**values)
        except ValueError as error:
            assert str(error) == message
        else:
            raise AssertionError('expected C1 routing pressure validation error')

    try:
        BaselineScenario(
            alarm_count=100,
            data_profile='latest-narrow',
            priority_group_size=10,
            c1_routing_destination_count=3,
            technical_hold_churn_percent=10,
            initial_active_percent=100,
        )
    except ValueError as error:
        assert str(error) == 'C1 routing pressure must not combine with functional churn'
    else:
        raise AssertionError('expected C1 routing/churn isolation error')


def test_c005_functional_summary_requires_four_immediate_assignments_per_alarm() -> None:
    scenario = BaselineScenario(
        test_id='C-005',
        alarm_count=20,
        duration_seconds=300,
        data_profile='latest-narrow',
        priority_group_size=10,
        c1_routing_destination_count=3,
        initial_active_percent=100,
    )
    first_generation = int(datetime(2026, 8, 26, 12, 0, tzinfo=UTC).timestamp()) // 10
    source_loader = SimpleNamespace(
        first_generation=first_generation,
        churn_generation_count=0,
        churn_group_transition_count=0,
        churn_transition_count=0,
        technical_hold_started_transition_count=0,
        technical_hold_cleared_transition_count=0,
    )
    records = tuple(
        _functional_entry(
            priority_group=f'perf-group-{group_index:03d}',
            evaluated_at='2026-08-26T12:00:00Z',
            occurrence_started=10,
            episode_started=1,
            assignment_changes=40,
            evidence_records=10,
        )
        for group_index in (1, 2)
    )
    snapshots = tuple(
        SimpleNamespace(
            as_document=lambda group_index=group_index: {
                'alarms': {
                    f'perf/alarm_{alarm_index:05d}': {
                        'occurrence': {
                            'assignments': {
                                'perf-tool': {},
                                'perf-route-01': {},
                                'perf-route-02': {},
                                'perf-route-03': {},
                            },
                            'pending_assignments': {},
                        }
                    }
                    for alarm_index in range((group_index - 1) * 10 + 1, group_index * 10 + 1)
                }
            }
        )
        for group_index in (1, 2)
    )

    summary = _build_functional_pressure_metrics(
        scenario=scenario,
        source_loader=source_loader,
        records=records,
        snapshots=snapshots,
    )

    assert summary is not None
    assert summary.functional_integrity_ok is True
    assert summary.routing_criticality == 'C1'
    assert summary.c1_routing_destination_count == 3
    assert summary.lifecycle_commit_count == 2
    assert summary.occurrence_started_count == 20
    assert summary.episode_started_count == 2
    assert summary.assignment_change_count == 80
    assert summary.expected_assignment_change_count == 80
    assert summary.assignment_assigned_count == 80
    assert summary.assignment_scheduled_count == 0
    assert summary.assignment_rescheduled_count == 0
    assert summary.assignment_removed_count == 0
    assert summary.snapshot_assignment_count == 80
    assert summary.expected_snapshot_assignment_count == 80
    assert summary.snapshot_pending_assignment_count == 0


def test_c005_functional_summary_rejects_delayed_assignment_kind() -> None:
    scenario = BaselineScenario(
        test_id='C-005',
        alarm_count=10,
        duration_seconds=300,
        data_profile='latest-narrow',
        priority_group_size=10,
        c1_routing_destination_count=3,
        initial_active_percent=100,
    )
    first_generation = int(datetime(2026, 8, 26, 12, 0, tzinfo=UTC).timestamp()) // 10
    source_loader = SimpleNamespace(
        first_generation=first_generation,
        churn_generation_count=0,
        churn_group_transition_count=0,
        churn_transition_count=0,
        technical_hold_started_transition_count=0,
        technical_hold_cleared_transition_count=0,
    )
    record = _functional_entry(
        priority_group='perf-group-001',
        evaluated_at='2026-08-26T12:00:00Z',
        occurrence_started=10,
        episode_started=1,
        assignment_changes=40,
        evidence_records=10,
    )
    record.record.records['assignment_changes'][0]['kind'] = 'SCHEDULED'
    snapshot = SimpleNamespace(
        as_document=lambda: {
            'alarms': {
                f'perf/alarm_{alarm_index:05d}': {
                    'occurrence': {
                        'assignments': {
                            'perf-tool': {},
                            'perf-route-01': {},
                            'perf-route-02': {},
                            'perf-route-03': {},
                        },
                        'pending_assignments': {},
                    }
                }
                for alarm_index in range(1, 11)
            }
        }
    )

    summary = _build_functional_pressure_metrics(
        scenario=scenario,
        source_loader=source_loader,
        records=(record,),
        snapshots=(snapshot,),
    )

    assert summary is not None
    assert summary.functional_integrity_ok is False
    assert summary.assignment_assigned_count == 39
    assert summary.assignment_scheduled_count == 1


def _c2_functional_entry(
    *,
    priority_group: str,
    evaluated_at: datetime,
    occurrence_started_at: datetime,
    alarm_start_index: int,
    assigned_destination_count: int,
    pending_delays: tuple[int, ...],
    bootstrap: bool,
    alarm_configuration_revision: str = 'PERF-AC-1',
) -> SimpleNamespace:
    evaluated_at_text = evaluated_at.isoformat().replace('+00:00', 'Z')
    started_at_text = occurrence_started_at.isoformat().replace('+00:00', 'Z')
    alarms: dict[str, object] = {}
    assignment_changes: list[dict[str, object]] = []
    occurrence_changes: list[dict[str, object]] = []
    for offset in range(10):
        alarm_number = alarm_start_index + offset
        alarm_key = f'perf/alarm_{alarm_number:05d}'
        occurrence_id = f'{priority_group}-occ-{offset + 1:02d}'
        assignments = {'perf-tool': {'assigned_at': started_at_text}}
        for destination_index in range(assigned_destination_count):
            assignments[f'perf-route-{destination_index + 1:02d}'] = {
                'assigned_at': evaluated_at_text
            }
        pending = {
            f'perf-route-{assigned_destination_count + index + 1:02d}': {
                'due_at': (evaluated_at + timedelta(seconds=delay))
                .isoformat()
                .replace('+00:00', 'Z')
            }
            for index, delay in enumerate(pending_delays)
        }
        alarms[alarm_key] = {
            'occurrence': {
                'occurrence_id': occurrence_id,
                'started_at': started_at_text,
                'assignments': assignments,
                'pending_assignments': pending,
            }
        }
        if bootstrap:
            occurrence_changes.append(
                {
                    'kind': 'STARTED',
                    'alarm_key': alarm_key,
                    'occurrence_id': occurrence_id,
                    'started_at': started_at_text,
                }
            )
            assignment_changes.append(
                {
                    'kind': 'ASSIGNED',
                    'alarm_key': alarm_key,
                    'occurrence_id': occurrence_id,
                    'tool_key': 'perf-tool',
                    'effective_at': evaluated_at_text,
                }
            )
            for delay_index, delay in enumerate((60, 120, 180), start=1):
                assignment_changes.append(
                    {
                        'kind': 'SCHEDULED',
                        'alarm_key': alarm_key,
                        'occurrence_id': occurrence_id,
                        'tool_key': f'perf-route-{delay_index:02d}',
                        'due_at': (occurrence_started_at + timedelta(seconds=delay))
                        .isoformat()
                        .replace('+00:00', 'Z'),
                    }
                )
        else:
            assignment_changes.append(
                {
                    'kind': 'ASSIGNED',
                    'alarm_key': alarm_key,
                    'occurrence_id': occurrence_id,
                    'tool_key': f'perf-route-{assigned_destination_count:02d}',
                    'effective_at': evaluated_at_text,
                }
            )
    return SimpleNamespace(
        record=SimpleNamespace(
            commit=SimpleNamespace(
                priority_group=priority_group,
                evaluated_at=evaluated_at_text,
                alarm_configuration_revision=alarm_configuration_revision,
            ),
            records={
                'occurrence_changes': occurrence_changes,
                'episode_changes': ([{'kind': 'STARTED'}] if bootstrap else []),
                'assignment_changes': assignment_changes,
                'journey_events': [],
                'evidence_records': ([{} for _ in range(10)] if bootstrap else []),
            },
            snapshot_after=SimpleNamespace(as_document=lambda alarms=alarms: {'alarms': alarms}),
        )
    )


def _c2_reschedule_functional_entry(
    *,
    priority_group: str,
    evaluated_at: datetime,
    occurrence_started_at: datetime,
    alarm_start_index: int,
    revised_delays: tuple[int, ...],
) -> SimpleNamespace:
    evaluated_at_text = evaluated_at.isoformat().replace('+00:00', 'Z')
    started_at_text = occurrence_started_at.isoformat().replace('+00:00', 'Z')
    alarms: dict[str, object] = {}
    assignment_changes: list[dict[str, object]] = []
    for offset in range(10):
        alarm_number = alarm_start_index + offset
        alarm_key = f'perf/alarm_{alarm_number:05d}'
        occurrence_id = f'{priority_group}-occ-{offset + 1:02d}'
        pending = {}
        for delay_index, delay in enumerate(revised_delays, start=1):
            due_at = (
                (occurrence_started_at + timedelta(seconds=delay))
                .isoformat()
                .replace('+00:00', 'Z')
            )
            tool_key = f'perf-route-{delay_index:02d}'
            pending[tool_key] = {'due_at': due_at}
            assignment_changes.append(
                {
                    'kind': 'RESCHEDULED',
                    'alarm_key': alarm_key,
                    'occurrence_id': occurrence_id,
                    'tool_key': tool_key,
                    'due_at': due_at,
                }
            )
        alarms[alarm_key] = {
            'occurrence': {
                'occurrence_id': occurrence_id,
                'started_at': started_at_text,
                'assignments': {'perf-tool': {'assigned_at': started_at_text}},
                'pending_assignments': pending,
            }
        }
    return SimpleNamespace(
        record=SimpleNamespace(
            commit=SimpleNamespace(
                priority_group=priority_group,
                evaluated_at=evaluated_at_text,
                alarm_configuration_revision='PERF-AC-2',
            ),
            records={
                'occurrence_changes': [],
                'episode_changes': [],
                'assignment_changes': assignment_changes,
                'journey_events': [],
                'evidence_records': [],
            },
            snapshot_after=SimpleNamespace(as_document=lambda alarms=alarms: {'alarms': alarms}),
        )
    )


def test_c006_builds_real_c2_routes_with_three_absolute_delays(tmp_path: Path) -> None:
    scenario = BaselineScenario(
        test_id='C-006',
        alarm_count=100,
        duration_seconds=300,
        data_profile='latest-narrow',
        priority_group_size=10,
        c2_routing_delay_seconds=(60, 120, 180),
        initial_active_percent=100,
        physical_partition_count=10,
        physical_partition_layout='balanced',
    )
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )

    assert scenario.has_routing_pressure is True
    assert scenario.has_c2_routing_pressure is True
    assert scenario.routing_criticality == 'C2'
    for planned in runtime.revision.session.planned_alarms:
        assert planned.criticality.value == 'C2'
        assert planned.routing.origin_tool_key == 'perf-tool'
        assert tuple(
            (destination.tool_key, destination.delay_seconds)
            for destination in planned.routing.destinations
        ) == (
            ('perf-route-01', 60),
            ('perf-route-02', 120),
            ('perf-route-03', 180),
        )


def test_c006_rejects_invalid_or_mixed_delayed_routing() -> None:
    invalid = (
        ((0, 60), 'c2_routing_delay_seconds must contain positive values'),
        ((60, 60), 'c2_routing_delay_seconds must be unique and strictly increasing'),
        ((120, 60), 'c2_routing_delay_seconds must be unique and strictly increasing'),
    )
    for delays, message in invalid:
        try:
            BaselineScenario(c2_routing_delay_seconds=delays)
        except ValueError as error:
            assert str(error) == message
        else:
            raise AssertionError('expected C2 delay validation error')

    try:
        BaselineScenario(c2_routing_delay_seconds=[60, 120, 180])  # type: ignore[arg-type]
    except TypeError as error:
        assert str(error) == 'c2_routing_delay_seconds must be a tuple of ints'
    else:
        raise AssertionError('expected C2 tuple validation error')

    try:
        BaselineScenario(
            alarm_count=100,
            duration_seconds=300,
            data_profile='latest-narrow',
            priority_group_size=10,
            c1_routing_destination_count=3,
            c2_routing_delay_seconds=(60, 120, 180),
        )
    except ValueError as error:
        assert str(error) == 'C1 and C2 routing pressure are mutually exclusive'
    else:
        raise AssertionError('expected C1/C2 mutual exclusion error')

    try:
        BaselineScenario(
            alarm_count=100,
            duration_seconds=300,
            data_profile='latest-narrow',
            priority_group_size=10,
            c2_routing_delay_seconds=(5, 10, 20),
        )
    except ValueError as error:
        assert str(error) == 'C2 routing delays must align with data refresh'
    else:
        raise AssertionError('expected C2 delay alignment error')

    try:
        BaselineScenario(
            alarm_count=100,
            duration_seconds=180,
            data_profile='latest-narrow',
            priority_group_size=10,
            c2_routing_delay_seconds=(60, 120, 180),
        )
    except ValueError as error:
        assert str(error) == 'C2 routing pressure duration must exceed the maximum delay'
    else:
        raise AssertionError('expected C2 duration validation error')


def test_c006_functional_summary_validates_scheduled_due_waves_and_pending_counts() -> None:
    scenario = BaselineScenario(
        test_id='C-006',
        alarm_count=20,
        duration_seconds=300,
        data_profile='latest-narrow',
        priority_group_size=10,
        c2_routing_delay_seconds=(60, 120, 180),
        initial_active_percent=100,
    )
    start = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    source_loader = SimpleNamespace(
        first_generation=int(start.timestamp()) // 10,
        churn_generation_count=0,
        churn_group_transition_count=0,
        churn_transition_count=0,
        technical_hold_started_transition_count=0,
        technical_hold_cleared_transition_count=0,
    )
    records = []
    for group_index in (1, 2):
        records.append(
            _c2_functional_entry(
                priority_group=f'perf-group-{group_index:03d}',
                evaluated_at=start,
                occurrence_started_at=start,
                alarm_start_index=(group_index - 1) * 10 + 1,
                assigned_destination_count=0,
                pending_delays=(60, 120, 180),
                bootstrap=True,
            )
        )
    for wave_index, seconds in enumerate((60, 120, 180), start=1):
        for group_index in (1, 2):
            records.append(
                _c2_functional_entry(
                    priority_group=f'perf-group-{group_index:03d}',
                    evaluated_at=start + timedelta(seconds=seconds),
                    occurrence_started_at=start,
                    alarm_start_index=(group_index - 1) * 10 + 1,
                    assigned_destination_count=wave_index,
                    pending_delays=tuple(delay - seconds for delay in (60, 120, 180)[wave_index:]),
                    bootstrap=False,
                )
            )
    final_snapshots = tuple(record.record.snapshot_after for record in records[-2:])

    summary = _build_functional_pressure_metrics(
        scenario=scenario,
        source_loader=source_loader,
        records=tuple(records),
        snapshots=final_snapshots,
    )

    assert summary is not None
    assert summary.functional_integrity_ok is True
    assert summary.routing_criticality == 'C2'
    assert summary.c2_routing_delay_seconds == (60, 120, 180)
    assert summary.lifecycle_commit_count == 8
    assert summary.expected_lifecycle_commit_count == 8
    assert summary.assignment_assigned_count == 80
    assert summary.expected_assignment_assigned_count == 80
    assert summary.assignment_scheduled_count == 60
    assert summary.expected_assignment_scheduled_count == 60
    assert summary.assignment_change_count == 140
    assert summary.routing_scheduled_delay_counts == (20, 20, 20)
    assert summary.routing_wave_assignment_counts == (20, 40, 60, 80)
    assert summary.routing_wave_pending_counts == (60, 40, 20, 0)
    assert summary.snapshot_assignment_count == 80
    assert summary.snapshot_pending_assignment_count == 0


def test_c006_functional_summary_rejects_missing_due_wave() -> None:
    scenario = BaselineScenario(
        test_id='C-006',
        alarm_count=10,
        duration_seconds=300,
        data_profile='latest-narrow',
        priority_group_size=10,
        c2_routing_delay_seconds=(60, 120, 180),
    )
    start = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    source_loader = SimpleNamespace(
        first_generation=int(start.timestamp()) // 10,
        churn_generation_count=0,
        churn_group_transition_count=0,
        churn_transition_count=0,
        technical_hold_started_transition_count=0,
        technical_hold_cleared_transition_count=0,
    )
    bootstrap = _c2_functional_entry(
        priority_group='perf-group-001',
        evaluated_at=start,
        occurrence_started_at=start,
        alarm_start_index=1,
        assigned_destination_count=0,
        pending_delays=(60, 120, 180),
        bootstrap=True,
    )
    first_wave = _c2_functional_entry(
        priority_group='perf-group-001',
        evaluated_at=start + timedelta(seconds=60),
        occurrence_started_at=start,
        alarm_start_index=1,
        assigned_destination_count=1,
        pending_delays=(60, 120),
        bootstrap=False,
    )

    summary = _build_functional_pressure_metrics(
        scenario=scenario,
        source_loader=source_loader,
        records=(bootstrap, first_wave),
        snapshots=(first_wave.record.snapshot_after,),
    )

    assert summary is not None
    assert summary.functional_integrity_ok is False
    assert summary.routing_wave_pending_counts == (30, 20)
    assert summary.expected_routing_wave_pending_counts == (30, 20, 10, 0)


def _c007_scenario(*, alarm_count: int = 100) -> BaselineScenario:
    return BaselineScenario(
        test_id='C-007',
        alarm_count=alarm_count,
        duration_seconds=270,
        data_profile='latest-narrow',
        priority_group_size=10,
        c2_routing_delay_seconds=(60, 120, 180),
        c2_reschedule_delay_seconds=(90, 150, 210),
        c2_reschedule_phase_a_seconds=30,
        initial_active_percent=100,
        physical_partition_count=10,
        physical_partition_layout='balanced',
    )


def test_c007_builds_compatible_c2_revision_boundary_on_same_destinations(tmp_path: Path) -> None:
    scenario = _c007_scenario()
    phase_a = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    phase_b_scenario = replace(
        scenario,
        c2_routing_delay_seconds=scenario.c2_reschedule_delay_seconds,
        c2_reschedule_delay_seconds=(),
        c2_reschedule_phase_a_seconds=0,
    )
    phase_b = build_baseline_runtime(
        scenario=phase_b_scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
        alarm_configuration_revision='PERF-AC-2',
        additional_revisions=(phase_a.revision,),
        occurrence_id_start=scenario.alarm_count,
        episode_id_start=scenario.priority_group_count,
    )

    assert scenario.has_c2_reschedule_pressure is True
    assert scenario.c2_phase_b_duration_seconds == 240.0
    assert phase_a.revision.alarm_configuration_revision == 'PERF-AC-1'
    assert phase_b.revision.alarm_configuration_revision == 'PERF-AC-2'
    for planned in phase_b.revision.session.planned_alarms:
        assert tuple(
            (destination.tool_key, destination.delay_seconds)
            for destination in planned.routing.destinations
        ) == (
            ('perf-route-01', 90),
            ('perf-route-02', 150),
            ('perf-route-03', 210),
        )

    adoption = plan_configuration_adoption(phase_a.revision, phase_b.revision)
    assert adoption.is_adoptable is True
    assert {change.disposition for change in adoption.changes} == {
        ConfigurationAdoptionDisposition.COMPATIBLE
    }


def test_c007_rejects_invalid_reschedule_window_contract() -> None:
    cases = (
        (
            dict(
                c2_routing_delay_seconds=(),
                c2_reschedule_delay_seconds=(90, 150, 210),
                c2_reschedule_phase_a_seconds=30,
            ),
            'C2 reschedule requires initial C2 routing delays',
        ),
        (
            dict(
                c2_routing_delay_seconds=(60, 120, 180),
                c2_reschedule_delay_seconds=(90, 150),
                c2_reschedule_phase_a_seconds=30,
            ),
            'C2 reschedule must preserve the destination count',
        ),
        (
            dict(
                c2_routing_delay_seconds=(60, 120, 180),
                c2_reschedule_delay_seconds=(60, 150, 210),
                c2_reschedule_phase_a_seconds=30,
            ),
            'C2 reschedule must change every destination delay',
        ),
        (
            dict(
                c2_routing_delay_seconds=(60, 120, 180),
                c2_reschedule_delay_seconds=(90, 150, 210),
                c2_reschedule_phase_a_seconds=0,
            ),
            'C2 reschedule requires phase A duration > 0',
        ),
        (
            dict(
                c2_routing_delay_seconds=(60, 120, 180),
                c2_reschedule_delay_seconds=(90, 150, 210),
                c2_reschedule_phase_a_seconds=60,
            ),
            'C2 reschedule phase A must stop before the first initial due_at',
        ),
    )
    for overrides, message in cases:
        try:
            BaselineScenario(
                alarm_count=100,
                duration_seconds=270,
                data_profile='latest-narrow',
                priority_group_size=10,
                initial_active_percent=100,
                **overrides,
            )
        except ValueError as error:
            assert str(error) == message
        else:
            raise AssertionError(f'expected validation error: {message}')

    try:
        BaselineScenario(
            alarm_count=100,
            duration_seconds=270,
            data_profile='latest-narrow',
            priority_group_size=10,
            c2_routing_delay_seconds=(60, 120, 180),
            c2_reschedule_delay_seconds=(90, 155, 210),
            c2_reschedule_phase_a_seconds=30,
            initial_active_percent=100,
        )
    except ValueError as error:
        assert str(error) == 'C2 reschedule delays must align with data refresh'
    else:
        raise AssertionError('expected revised-delay alignment error')

    try:
        BaselineScenario(
            alarm_count=100,
            duration_seconds=210,
            data_profile='latest-narrow',
            priority_group_size=10,
            c2_routing_delay_seconds=(60, 120, 180),
            c2_reschedule_delay_seconds=(90, 150, 210),
            c2_reschedule_phase_a_seconds=30,
            initial_active_percent=100,
        )
    except ValueError as error:
        assert str(error) == 'C2 routing pressure duration must exceed the maximum delay'
    else:
        raise AssertionError('expected total-duration validation error')


def test_c007_functional_summary_validates_reschedule_and_revised_due_waves() -> None:
    scenario = BaselineScenario(
        test_id='C-007',
        alarm_count=20,
        duration_seconds=270,
        data_profile='latest-narrow',
        priority_group_size=10,
        c2_routing_delay_seconds=(60, 120, 180),
        c2_reschedule_delay_seconds=(90, 150, 210),
        c2_reschedule_phase_a_seconds=30,
        initial_active_percent=100,
    )
    start = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    source_loader = SimpleNamespace(
        first_generation=int(start.timestamp()) // 10,
        churn_generation_count=0,
        churn_group_transition_count=0,
        churn_transition_count=0,
        technical_hold_started_transition_count=0,
        technical_hold_cleared_transition_count=0,
    )
    records = []
    for group_index in (1, 2):
        records.append(
            _c2_functional_entry(
                priority_group=f'perf-group-{group_index:03d}',
                evaluated_at=start,
                occurrence_started_at=start,
                alarm_start_index=(group_index - 1) * 10 + 1,
                assigned_destination_count=0,
                pending_delays=(60, 120, 180),
                bootstrap=True,
            )
        )
    for group_index in (1, 2):
        records.append(
            _c2_reschedule_functional_entry(
                priority_group=f'perf-group-{group_index:03d}',
                evaluated_at=start + timedelta(seconds=30),
                occurrence_started_at=start,
                alarm_start_index=(group_index - 1) * 10 + 1,
                revised_delays=(90, 150, 210),
            )
        )
    for wave_index, seconds in enumerate((90, 150, 210), start=1):
        next_delays = tuple(delay - seconds for delay in (90, 150, 210)[wave_index:])
        for group_index in (1, 2):
            records.append(
                _c2_functional_entry(
                    priority_group=f'perf-group-{group_index:03d}',
                    evaluated_at=start + timedelta(seconds=seconds),
                    occurrence_started_at=start,
                    alarm_start_index=(group_index - 1) * 10 + 1,
                    assigned_destination_count=wave_index,
                    pending_delays=next_delays,
                    bootstrap=False,
                    alarm_configuration_revision='PERF-AC-2',
                )
            )
    final_snapshots = tuple(record.record.snapshot_after for record in records[-2:])

    summary = _build_functional_pressure_metrics(
        scenario=scenario,
        source_loader=source_loader,
        records=tuple(records),
        snapshots=final_snapshots,
    )

    assert summary is not None
    assert summary.functional_integrity_ok is True
    assert summary.lifecycle_commit_count == 10
    assert summary.expected_lifecycle_commit_count == 10
    assert summary.occurrence_started_count == 20
    assert summary.episode_started_count == 2
    assert summary.assignment_assigned_count == 80
    assert summary.assignment_scheduled_count == 60
    assert summary.assignment_rescheduled_count == 60
    assert summary.assignment_removed_count == 0
    assert summary.assignment_change_count == 200
    assert summary.routing_scheduled_delay_counts == (20, 20, 20)
    assert summary.routing_rescheduled_delay_counts == (20, 20, 20)
    assert summary.routing_delayed_assignment_delay_counts == (20, 20, 20)
    assert summary.reschedule_commit_count == 2
    assert summary.routing_revision_transition_ok is True
    assert summary.routing_wave_assignment_counts == (20, 20, 40, 60, 80)
    assert summary.routing_wave_pending_counts == (60, 60, 40, 20, 0)
    assert summary.snapshot_assignment_count == 80
    assert summary.snapshot_pending_assignment_count == 0
    assert summary.occurrence_identity_mismatch_count == 0


def test_c007_functional_summary_rejects_missing_reschedule_revision_transition() -> None:
    scenario = BaselineScenario(
        test_id='C-007',
        alarm_count=10,
        duration_seconds=270,
        data_profile='latest-narrow',
        priority_group_size=10,
        c2_routing_delay_seconds=(60, 120, 180),
        c2_reschedule_delay_seconds=(90, 150, 210),
        c2_reschedule_phase_a_seconds=30,
        initial_active_percent=100,
    )
    start = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    source_loader = SimpleNamespace(
        first_generation=int(start.timestamp()) // 10,
        churn_generation_count=0,
        churn_group_transition_count=0,
        churn_transition_count=0,
        technical_hold_started_transition_count=0,
        technical_hold_cleared_transition_count=0,
    )
    bootstrap = _c2_functional_entry(
        priority_group='perf-group-001',
        evaluated_at=start,
        occurrence_started_at=start,
        alarm_start_index=1,
        assigned_destination_count=0,
        pending_delays=(60, 120, 180),
        bootstrap=True,
    )
    bad_reschedule = _c2_reschedule_functional_entry(
        priority_group='perf-group-001',
        evaluated_at=start + timedelta(seconds=30),
        occurrence_started_at=start,
        alarm_start_index=1,
        revised_delays=(90, 150, 210),
    )
    bad_reschedule.record.commit.alarm_configuration_revision = 'PERF-AC-1'

    summary = _build_functional_pressure_metrics(
        scenario=scenario,
        source_loader=source_loader,
        records=(bootstrap, bad_reschedule),
        snapshots=(bad_reschedule.record.snapshot_after,),
    )

    assert summary is not None
    assert summary.functional_integrity_ok is False
    assert summary.routing_revision_transition_ok is False


def _c2_removal_functional_entry(
    *,
    priority_group: str,
    evaluated_at: datetime,
    occurrence_started_at: datetime,
    alarm_start_index: int,
) -> SimpleNamespace:
    evaluated_at_text = evaluated_at.isoformat().replace('+00:00', 'Z')
    started_at_text = occurrence_started_at.isoformat().replace('+00:00', 'Z')
    alarms: dict[str, object] = {}
    assignment_changes: list[dict[str, object]] = []
    for offset in range(10):
        alarm_number = alarm_start_index + offset
        alarm_key = f'perf/alarm_{alarm_number:05d}'
        occurrence_id = f'{priority_group}-occ-{offset + 1:02d}'
        alarms[alarm_key] = {
            'occurrence': {
                'occurrence_id': occurrence_id,
                'started_at': started_at_text,
                'assignments': {'perf-tool': {'assigned_at': started_at_text}},
                'pending_assignments': {},
            }
        }
        for tool_key in ('perf-route-01', 'perf-route-02', 'perf-route-03'):
            assignment_changes.append(
                {
                    'kind': 'REMOVED',
                    'alarm_key': alarm_key,
                    'occurrence_id': occurrence_id,
                    'tool_key': tool_key,
                    'effective_at': evaluated_at_text,
                }
            )
    return SimpleNamespace(
        record=SimpleNamespace(
            commit=SimpleNamespace(
                priority_group=priority_group,
                evaluated_at=evaluated_at_text,
                alarm_configuration_revision='PERF-AC-2',
            ),
            records={
                'occurrence_changes': [],
                'episode_changes': [],
                'assignment_changes': assignment_changes,
                'journey_events': [],
                'evidence_records': [],
            },
            snapshot_after=SimpleNamespace(as_document=lambda alarms=alarms: {'alarms': alarms}),
        )
    )


def test_c008_builds_compatible_empty_destination_revision(tmp_path: Path) -> None:
    scenario = BaselineScenario(
        test_id='C-008',
        alarm_count=20,
        duration_seconds=210,
        data_profile='latest-narrow',
        priority_group_size=10,
        c2_routing_delay_seconds=(60, 120, 180),
        c2_remove_destinations_phase_a_seconds=90,
        initial_active_percent=100,
    )
    phase_a = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    phase_b_scenario = replace(
        scenario,
        c2_remove_destinations_phase_a_seconds=0,
        c2_remove_destinations_target=True,
    )
    phase_b = build_baseline_runtime(
        scenario=phase_b_scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
        alarm_configuration_revision='PERF-AC-2',
        additional_revisions=(phase_a.revision,),
        occurrence_id_start=scenario.alarm_count,
        episode_id_start=scenario.priority_group_count,
    )

    assert scenario.has_c2_remove_destinations_pressure is True
    assert scenario.c2_phase_b_duration_seconds == 120.0
    for planned in phase_b.revision.session.planned_alarms:
        assert planned.criticality is Criticality.C2
        assert planned.routing.origin_tool_key == 'perf-tool'
        assert planned.routing.destinations == ()

    adoption = plan_configuration_adoption(phase_a.revision, phase_b.revision)
    assert adoption.is_adoptable is True
    assert {change.disposition for change in adoption.changes} == {
        ConfigurationAdoptionDisposition.COMPATIBLE
    }


def test_c008_rejects_invalid_removal_boundary_contract() -> None:
    cases = (
        (
            dict(
                c2_routing_delay_seconds=(),
                c2_remove_destinations_phase_a_seconds=90,
            ),
            'C2 destination removal requires initial C2 routing delays',
        ),
        (
            dict(
                c2_routing_delay_seconds=(60,),
                c2_remove_destinations_phase_a_seconds=90,
            ),
            'C2 destination removal requires at least two destinations',
        ),
        (
            dict(
                c2_routing_delay_seconds=(60, 120, 180),
                c2_remove_destinations_phase_a_seconds=60,
            ),
            'C2 destination removal phase A must pass the first initial due_at',
        ),
        (
            dict(
                c2_routing_delay_seconds=(60, 120, 180),
                c2_remove_destinations_phase_a_seconds=120,
            ),
            'C2 destination removal phase A must stop before the second initial due_at',
        ),
    )
    for overrides, message in cases:
        try:
            BaselineScenario(
                alarm_count=100,
                duration_seconds=210,
                data_profile='latest-narrow',
                priority_group_size=10,
                initial_active_percent=100,
                **overrides,
            )
        except ValueError as error:
            assert str(error) == message
        else:
            raise AssertionError(f'expected validation error: {message}')

    try:
        BaselineScenario(
            alarm_count=100,
            duration_seconds=210,
            data_profile='latest-narrow',
            priority_group_size=10,
            c2_routing_delay_seconds=(60, 120, 180),
            c2_remove_destinations_phase_a_seconds=95,
            initial_active_percent=100,
        )
    except ValueError as error:
        assert str(error) == 'C2 destination removal phase A must align with data refresh'
    else:
        raise AssertionError('expected removal-boundary alignment error')


def test_c008_functional_summary_validates_reached_and_pending_removal() -> None:
    scenario = BaselineScenario(
        test_id='C-008',
        alarm_count=20,
        duration_seconds=210,
        data_profile='latest-narrow',
        priority_group_size=10,
        c2_routing_delay_seconds=(60, 120, 180),
        c2_remove_destinations_phase_a_seconds=90,
        initial_active_percent=100,
    )
    start = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    source_loader = SimpleNamespace(
        first_generation=int(start.timestamp()) // 10,
        churn_generation_count=0,
        churn_group_transition_count=0,
        churn_transition_count=0,
        technical_hold_started_transition_count=0,
        technical_hold_cleared_transition_count=0,
    )
    records = []
    for group_index in (1, 2):
        records.append(
            _c2_functional_entry(
                priority_group=f'perf-group-{group_index:03d}',
                evaluated_at=start,
                occurrence_started_at=start,
                alarm_start_index=(group_index - 1) * 10 + 1,
                assigned_destination_count=0,
                pending_delays=(60, 120, 180),
                bootstrap=True,
            )
        )
    for group_index in (1, 2):
        records.append(
            _c2_functional_entry(
                priority_group=f'perf-group-{group_index:03d}',
                evaluated_at=start + timedelta(seconds=60),
                occurrence_started_at=start,
                alarm_start_index=(group_index - 1) * 10 + 1,
                assigned_destination_count=1,
                pending_delays=(60, 120),
                bootstrap=False,
            )
        )
    for group_index in (1, 2):
        records.append(
            _c2_removal_functional_entry(
                priority_group=f'perf-group-{group_index:03d}',
                evaluated_at=start + timedelta(seconds=90),
                occurrence_started_at=start,
                alarm_start_index=(group_index - 1) * 10 + 1,
            )
        )
    final_snapshots = tuple(record.record.snapshot_after for record in records[-2:])

    summary = _build_functional_pressure_metrics(
        scenario=scenario,
        source_loader=source_loader,
        records=tuple(records),
        snapshots=final_snapshots,
    )

    assert summary is not None
    assert summary.functional_integrity_ok is True
    assert summary.lifecycle_commit_count == 6
    assert summary.expected_lifecycle_commit_count == 6
    assert summary.occurrence_started_count == 20
    assert summary.episode_started_count == 2
    assert summary.assignment_assigned_count == 40
    assert summary.assignment_scheduled_count == 60
    assert summary.assignment_rescheduled_count == 0
    assert summary.assignment_removed_count == 60
    assert summary.expected_assignment_removed_count == 60
    assert summary.assignment_change_count == 160
    assert summary.routing_removed_tool_counts == (20, 20, 20)
    assert summary.removed_assigned_count == 20
    assert summary.removed_pending_count == 40
    assert summary.removal_commit_count == 2
    assert summary.routing_revision_transition_ok is True
    assert summary.routing_delayed_assignment_delay_counts == (20, 0, 0)
    assert summary.routing_wave_assignment_counts == (20, 40, 20)
    assert summary.routing_wave_pending_counts == (60, 40, 0)
    assert summary.snapshot_assignment_count == 20
    assert summary.snapshot_pending_assignment_count == 0
    assert summary.occurrence_identity_mismatch_count == 0


def test_c008_functional_summary_rejects_missing_removal_revision_transition() -> None:
    scenario = BaselineScenario(
        test_id='C-008',
        alarm_count=10,
        duration_seconds=210,
        data_profile='latest-narrow',
        priority_group_size=10,
        c2_routing_delay_seconds=(60, 120, 180),
        c2_remove_destinations_phase_a_seconds=90,
        initial_active_percent=100,
    )
    start = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    source_loader = SimpleNamespace(
        first_generation=int(start.timestamp()) // 10,
        churn_generation_count=0,
        churn_group_transition_count=0,
        churn_transition_count=0,
        technical_hold_started_transition_count=0,
        technical_hold_cleared_transition_count=0,
    )
    bootstrap = _c2_functional_entry(
        priority_group='perf-group-001',
        evaluated_at=start,
        occurrence_started_at=start,
        alarm_start_index=1,
        assigned_destination_count=0,
        pending_delays=(60, 120, 180),
        bootstrap=True,
    )
    reached = _c2_functional_entry(
        priority_group='perf-group-001',
        evaluated_at=start + timedelta(seconds=60),
        occurrence_started_at=start,
        alarm_start_index=1,
        assigned_destination_count=1,
        pending_delays=(60, 120),
        bootstrap=False,
    )
    bad_removal = _c2_removal_functional_entry(
        priority_group='perf-group-001',
        evaluated_at=start + timedelta(seconds=90),
        occurrence_started_at=start,
        alarm_start_index=1,
    )
    bad_removal.record.commit.alarm_configuration_revision = 'PERF-AC-1'

    summary = _build_functional_pressure_metrics(
        scenario=scenario,
        source_loader=source_loader,
        records=(bootstrap, reached, bad_removal),
        snapshots=(bad_removal.record.snapshot_after,),
    )

    assert summary is not None
    assert summary.functional_integrity_ok is False
    assert summary.routing_revision_transition_ok is False


def test_management_pressure_scenario_contract() -> None:
    scenario = BaselineScenario(
        test_id='D-001',
        alarm_count=1000,
        duration_seconds=120,
        iteration_period_seconds=5,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        columns_per_alarm=1,
        priority_group_size=10,
        management_action_at_seconds=30,
        initial_active_percent=100,
        physical_partition_count=36,
        physical_partition_layout='balanced',
    )

    assert scenario.has_management_pressure is True
    assert scenario.routing_criticality == 'C3'
    assert scenario.priority_group_count == 100


def test_management_pressure_rejects_mixed_or_invalid_timing() -> None:
    base = dict(
        test_id='D-001',
        alarm_count=100,
        duration_seconds=60,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        priority_group_size=10,
        management_action_at_seconds=30,
        initial_active_percent=100,
        physical_partition_count=10,
        physical_partition_layout='balanced',
    )
    with pytest.raises(ValueError, match='align with data refresh'):
        BaselineScenario(**{**base, 'management_action_at_seconds': 25})
    with pytest.raises(ValueError, match='must not combine'):
        BaselineScenario(**{**base, 'c1_routing_destination_count': 1})
    with pytest.raises(ValueError, match='extend beyond the action cycle'):
        BaselineScenario(**{**base, 'duration_seconds': 40})


def test_management_runtime_builds_real_consumer_and_single_source(tmp_path: Path) -> None:
    scenario = BaselineScenario(
        test_id='D-001',
        alarm_count=10,
        duration_seconds=30,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        priority_group_size=10,
        management_action_at_seconds=10,
        initial_active_percent=100,
        physical_partition_count=10,
        physical_partition_layout='balanced',
    )
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )

    assert isinstance(runtime.input_source, SingleManagementInputSource)
    assert isinstance(runtime.input_consumer, PerformanceAlarmDurableInputConsumer)
    assert runtime.job.input_consumer is runtime.input_consumer
    assert runtime.input_source.target_identity.canonical_key == 'perf/alarm_00001'


def test_management_pressure_metrics_participate_in_report_integrity(tmp_path: Path) -> None:
    recorder = PerformanceRecorder(iteration_period_seconds=5)
    metrics = ManagementPressureMetrics(
        action_at_seconds=30,
        input_id='PERF-M-000001',
        target_alarm_key='perf/alarm_00001',
        target_occurrence_id='PERF-O-00000001',
        input_receipt_count=0,
        expected_input_receipt_count=1,
        effective_receipt_count=0,
        expected_effective_receipt_count=1,
        management_effect_started_count=0,
        expected_management_effect_started_count=1,
        management_effect_cleared_count=0,
        expected_management_effect_cleared_count=0,
        management_commit_count=0,
        expected_management_commit_count=1,
        deactivation_request_count=0,
        expected_deactivation_request_count=0,
        target_group_durable_record_count=1,
        expected_target_group_durable_record_count=2,
        total_durable_record_count=100,
        expected_total_durable_record_count=101,
        consumer_cursor_byte_offset=None,
        expected_consumer_cursor_byte_offset=256,
        consumer_pending_count=0,
        expected_consumer_pending_count=0,
        snapshot_management_effect_count=0,
        expected_snapshot_management_effect_count=1,
        occurrence_identity_mismatch_count=0,
        receipt_commit_id=None,
        receipt_before_cursor_advance_ok=False,
        input_to_receipt_ms=None,
        functional_integrity_ok=False,
    )
    report = recorder.build_report(
        test_id='D-001',
        alarm_count=10,
        planned_duration_seconds=30,
        actual_duration_seconds=30,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        columns_per_alarm=1,
        historical_series_per_alarm=0,
        historical_window_minutes=0,
        historical_step_seconds=0,
        historical_points_per_series=0,
        historical_value_count=0,
        physical_partition_count=1,
        physical_partition_column_counts=(10,),
        physical_partition_layout='balanced',
        empty_physical_partition_count=0,
        missing_source_column_count=0,
        synthesized_null_column_count=0,
        source_view_count=1,
        source_column_count=10,
        source_row_count=1,
        source_frame_bytes=100,
        source_numeric_value_count=10,
        latest_source_column_count=10,
        historical_source_column_count=0,
        historical_source_row_count=0,
        source_load_durations_ms=[],
        source_merge_durations_ms=[],
        journal_aligned=True,
        durable_record_count=100,
        snapshot_count=1,
        snapshot_alarm_count=10,
        expected_snapshot_alarm_count=10,
        source_load_count=1,
        management_pressure=metrics,
    )

    assert report.result == 'FAIL'
    assert report.management_pressure is metrics


def test_sustained_management_pressure_scenario_contract() -> None:
    scenario = BaselineScenario(
        test_id='D-002',
        alarm_count=1000,
        duration_seconds=600,
        iteration_period_seconds=5,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        columns_per_alarm=1,
        priority_group_size=10,
        management_action_at_seconds=30,
        management_action_count=540,
        management_action_interval_seconds=1,
        initial_active_percent=100,
        physical_partition_count=36,
        physical_partition_layout='balanced',
    )

    assert scenario.has_management_pressure is True
    assert scenario.has_sustained_management_pressure is True
    assert scenario.effective_management_action_count == 540
    assert scenario.management_last_action_at_seconds == 569
    assert scenario.routing_criticality == 'C3'


def test_sustained_management_pressure_rejects_invalid_count_interval_or_tail() -> None:
    base = dict(
        test_id='D-002',
        alarm_count=100,
        duration_seconds=180,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        priority_group_size=10,
        management_action_at_seconds=30,
        management_action_count=20,
        management_action_interval_seconds=1,
        initial_active_percent=100,
        physical_partition_count=10,
        physical_partition_layout='balanced',
    )
    with pytest.raises(ValueError, match='requires more than one action'):
        BaselineScenario(
            **{
                **base,
                'management_action_count': 1,
                'management_action_interval_seconds': 1,
            }
        )
    with pytest.raises(ValueError, match='must not exceed alarm_count'):
        BaselineScenario(**{**base, 'management_action_count': 101})
    with pytest.raises(ValueError, match='extend beyond the action cycle'):
        BaselineScenario(**{**base, 'duration_seconds': 59})


def test_sustained_management_runtime_rotates_unique_targets_by_priority_group(
    tmp_path: Path,
) -> None:
    scenario = BaselineScenario(
        test_id='D-002',
        alarm_count=100,
        duration_seconds=90,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        priority_group_size=10,
        management_action_at_seconds=10,
        management_action_count=12,
        management_action_interval_seconds=1,
        initial_active_percent=100,
        physical_partition_count=10,
        physical_partition_layout='balanced',
    )
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )

    assert isinstance(runtime.input_source, SustainedManagementInputSource)
    assert isinstance(runtime.input_consumer, PerformanceAlarmDurableInputConsumer)
    source = runtime.input_source
    assert source.target_for_action(0)[0].canonical_key == 'perf/alarm_00001'
    assert source.target_for_action(1)[0].canonical_key == 'perf/alarm_00011'
    assert source.target_for_action(9)[0].canonical_key == 'perf/alarm_00091'
    assert source.target_for_action(10)[0].canonical_key == 'perf/alarm_00002'
    assert source.target_for_action(0)[1] == 'perf-group-001'
    assert source.target_for_action(9)[1] == 'perf-group-010'
    assert len({source.target_for_action(i)[0].canonical_key for i in range(12)}) == 12


def test_sustained_management_metrics_participate_in_report_integrity() -> None:
    recorder = PerformanceRecorder(iteration_period_seconds=5)
    metrics = SustainedManagementPressureMetrics(
        action_at_seconds=30,
        action_count=10,
        action_interval_seconds=1,
        arrival_mode='sustained',
        first_input_id='PERF-M-000001',
        last_input_id='PERF-M-000010',
        input_receipt_count=9,
        expected_input_receipt_count=10,
        effective_receipt_count=9,
        expected_effective_receipt_count=10,
        management_effect_started_count=9,
        expected_management_effect_started_count=10,
        management_effect_cleared_count=0,
        expected_management_effect_cleared_count=0,
        management_commit_count=9,
        deactivation_request_count=0,
        expected_deactivation_request_count=0,
        lost_input_count=1,
        duplicate_receipt_count=0,
        unique_target_count=10,
        expected_unique_target_count=10,
        consumer_cursor_byte_offset=2304,
        expected_consumer_cursor_byte_offset=2560,
        consumer_pending_count=0,
        expected_consumer_pending_count=0,
        consumer_pending_high_water_count=0,
        max_batch_size=5,
        nonempty_batch_count=2,
        nonempty_batch_sizes=(5, 4),
        first_nonempty_batch_size=5,
        expected_first_nonempty_batch_size=None,
        fully_absorbed_in_first_eligible_iteration=None,
        snapshot_management_effect_count=9,
        expected_snapshot_management_effect_count=10,
        occurrence_identity_mismatch_count=0,
        receipt_before_cursor_checked_count=9,
        receipt_before_cursor_advance_ok=False,
        input_to_receipt_p50_ms=3000.0,
        input_to_receipt_p95_ms=5000.0,
        input_to_receipt_p99_ms=5100.0,
        input_to_receipt_max_ms=5200.0,
        durable_record_count=109,
        functional_integrity_ok=False,
    )
    report = recorder.build_report(
        test_id='D-002',
        alarm_count=10,
        planned_duration_seconds=60,
        actual_duration_seconds=60,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        columns_per_alarm=1,
        historical_series_per_alarm=0,
        historical_window_minutes=0,
        historical_step_seconds=0,
        historical_points_per_series=0,
        historical_value_count=0,
        physical_partition_count=1,
        physical_partition_column_counts=(10,),
        physical_partition_layout='balanced',
        empty_physical_partition_count=0,
        missing_source_column_count=0,
        synthesized_null_column_count=0,
        source_view_count=1,
        source_column_count=10,
        source_row_count=1,
        source_frame_bytes=100,
        source_numeric_value_count=10,
        latest_source_column_count=10,
        historical_source_column_count=0,
        historical_source_row_count=0,
        source_load_durations_ms=[],
        source_merge_durations_ms=[],
        journal_aligned=True,
        durable_record_count=109,
        snapshot_count=1,
        snapshot_alarm_count=10,
        expected_snapshot_alarm_count=10,
        source_load_count=1,
        management_pressure=metrics,
    )

    assert report.result == 'FAIL'
    assert report.management_pressure is metrics


def test_management_burst_pressure_scenario_contract() -> None:
    scenario = BaselineScenario(
        test_id='D-003',
        alarm_count=1000,
        duration_seconds=120,
        iteration_period_seconds=5,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        columns_per_alarm=1,
        priority_group_size=10,
        management_action_at_seconds=30,
        management_action_count=50,
        management_action_interval_seconds=0,
        initial_active_percent=100,
        physical_partition_count=36,
        physical_partition_layout='balanced',
    )

    assert scenario.has_management_pressure is True
    assert scenario.has_multi_management_pressure is True
    assert scenario.has_sustained_management_pressure is False
    assert scenario.has_burst_management_pressure is True
    assert scenario.management_arrival_mode == 'burst'
    assert scenario.effective_management_action_count == 50
    assert scenario.management_last_action_at_seconds == 30
    assert scenario.routing_criticality == 'C3'


def test_management_burst_runtime_targets_50_distinct_priority_groups(tmp_path: Path) -> None:
    scenario = BaselineScenario(
        test_id='D-003',
        alarm_count=1000,
        duration_seconds=120,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        priority_group_size=10,
        management_action_at_seconds=30,
        management_action_count=50,
        management_action_interval_seconds=0,
        initial_active_percent=100,
        physical_partition_count=36,
        physical_partition_layout='balanced',
    )
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )

    assert isinstance(runtime.input_source, SustainedManagementInputSource)
    source = runtime.input_source
    targets = tuple(source.target_for_action(index) for index in range(50))
    assert targets[0][0].canonical_key == 'perf/alarm_00001'
    assert targets[0][1] == 'perf-group-001'
    assert targets[-1][0].canonical_key == 'perf/alarm_00491'
    assert targets[-1][1] == 'perf-group-050'
    assert len({identity.canonical_key for identity, _group in targets}) == 50
    assert len({group for _identity, group in targets}) == 50


def test_management_burst_metrics_require_first_eligible_iteration_absorption(
    tmp_path: Path,
) -> None:
    scenario = BaselineScenario(
        test_id='D-003',
        alarm_count=100,
        duration_seconds=60,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        priority_group_size=10,
        management_action_at_seconds=10,
        management_action_count=5,
        management_action_interval_seconds=0,
        initial_active_percent=100,
        physical_partition_count=10,
        physical_partition_layout='balanced',
    )
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    assert isinstance(runtime.input_source, SustainedManagementInputSource)
    source = runtime.input_source
    consumer = runtime.input_consumer

    expected_targets = tuple(source.target_for_action(index) for index in range(5))
    for index, (identity, _group) in enumerate(expected_targets, start=1):
        source.target_occurrence_ids[identity.canonical_key] = f'PERF-O-{index:08d}'
        source.visible_monotonic_by_input_id[f'PERF-M-{index:06d}'] = 100.0
        consumer.receipt_confirmed_monotonic_by_input_id[f'PERF-M-{index:06d}'] = 101.0
    consumer.management_receipt_batch_sizes.extend((3, 2))
    consumer.management_pending_high_water_count = 0
    consumer.receipt_before_cursor_advance_ok = True
    consumer.receipt_before_cursor_checked_count = 5

    records = []
    for index in range(1, 6):
        records.append(
            SimpleNamespace(
                record=SimpleNamespace(
                    records={
                        'input_receipts': [
                            {
                                'input_kind': 'MANAGEMENT',
                                'input_id': f'PERF-M-{index:06d}',
                                'outcome': 'EFFECTIVE',
                            }
                        ],
                        'management_effects': [
                            {
                                'effect_id': f'PERF-ME-PERF-M-{index:06d}',
                                'kind': 'STARTED',
                            }
                        ],
                        'deactivation_requests': [],
                    }
                )
            )
        )
    snapshots = (
        SimpleNamespace(
            as_document=lambda: {
                'alarms': {
                    identity.canonical_key: {
                        'occurrence': {'occurrence_id': f'PERF-O-{index:08d}'},
                        'management_effect': {'effect_id': f'PERF-ME-PERF-M-{index:06d}'},
                    }
                    for index, (identity, _group) in enumerate(expected_targets, start=1)
                }
            }
        ),
    )

    import performance.run as run_module

    original_store = run_module.AtomicJsonStore
    run_module.AtomicJsonStore = lambda **_kwargs: SimpleNamespace(
        read=lambda _path: {'management': {'cursor': {'byte_offset': 1280}, 'pending': []}}
    )
    try:
        metrics = _build_management_pressure_metrics(
            scenario=scenario,
            runtime=runtime,
            records=tuple(records),
            snapshots=snapshots,
        )
    finally:
        run_module.AtomicJsonStore = original_store

    assert isinstance(metrics, SustainedManagementPressureMetrics)
    assert metrics.arrival_mode == 'burst'
    assert metrics.first_nonempty_batch_size == 3
    assert metrics.expected_first_nonempty_batch_size == 5
    assert metrics.fully_absorbed_in_first_eligible_iteration is False
    assert metrics.functional_integrity_ok is False


def test_single_deactivation_decision_scenario_contract_and_target_policy(tmp_path: Path) -> None:
    scenario = BaselineScenario(
        test_id='D-005',
        alarm_count=1000,
        duration_seconds=120,
        iteration_period_seconds=5,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        columns_per_alarm=1,
        priority_group_size=10,
        management_action_at_seconds=30,
        deactivation_decision_at_seconds=60,
        deactivation_window_seconds=300,
        initial_active_percent=100,
        physical_partition_count=36,
        physical_partition_layout='balanced',
    )
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )

    assert scenario.has_deactivation_decision_pressure is True
    assert scenario.effective_management_action_count == 1
    assert scenario.management_arrival_mode == 'single'
    assert isinstance(runtime.input_source, SingleDeactivationDecisionInputSource)
    assert runtime.input_source.request_id == 'PERF-DR-PERF-M-000001'
    assert runtime.input_source.decision_id == 'PERF-D-000001'
    assert (
        runtime.input_source.effective_until - runtime.input_source.decision_decided_at
        == timedelta(seconds=270)
    )
    target = runtime.revision.session.planned_alarms[0]
    assert target.identity.canonical_key == 'perf/alarm_00001'
    assert target.deactivation_policy is not None
    assert target.deactivation_policy.approval_required is True
    assert all(
        planned.deactivation_policy is None
        for planned in runtime.revision.session.planned_alarms[1:]
    )


def test_single_deactivation_decision_scenario_rejects_invalid_order_window_or_tail() -> None:
    base = dict(
        test_id='D-005',
        alarm_count=100,
        duration_seconds=120,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        priority_group_size=10,
        management_action_at_seconds=30,
        deactivation_decision_at_seconds=60,
        deactivation_window_seconds=300,
        initial_active_percent=100,
        physical_partition_count=10,
        physical_partition_layout='balanced',
    )
    with pytest.raises(ValueError, match='must occur after management request setup'):
        BaselineScenario(**{**base, 'deactivation_decision_at_seconds': 30})
    with pytest.raises(ValueError, match='window must remain open'):
        BaselineScenario(**{**base, 'deactivation_window_seconds': 30})
    with pytest.raises(ValueError, match='duration must extend beyond the decision cycle'):
        BaselineScenario(**{**base, 'duration_seconds': 70})


def test_deactivation_decision_metrics_participate_in_report_integrity() -> None:
    recorder = PerformanceRecorder(iteration_period_seconds=5)
    metrics = DeactivationDecisionPressureMetrics(
        request_at_seconds=30,
        decision_at_seconds=60,
        deactivation_window_seconds=300,
        management_input_id='PERF-M-000001',
        request_id='PERF-DR-PERF-M-000001',
        decision_id='PERF-D-000001',
        target_alarm_key='perf/alarm_00001',
        target_occurrence_id='PERF-O-00000001',
        request_receipt_count=1,
        expected_request_receipt_count=1,
        pending_approval_receipt_count=1,
        expected_pending_approval_receipt_count=1,
        deactivation_request_count=1,
        expected_deactivation_request_count=1,
        approval_required_request_count=1,
        expected_approval_required_request_count=1,
        decision_receipt_count=0,
        expected_decision_receipt_count=1,
        applied_decision_receipt_count=0,
        expected_applied_decision_receipt_count=1,
        management_effect_started_count=1,
        expected_management_effect_started_count=1,
        deactivation_effect_started_count=0,
        expected_deactivation_effect_started_count=1,
        deactivation_effect_cleared_count=0,
        expected_deactivation_effect_cleared_count=0,
        management_cursor_byte_offset=256,
        expected_management_cursor_byte_offset=256,
        decision_cursor_byte_offset=None,
        expected_decision_cursor_byte_offset=256,
        management_pending_count=0,
        decision_pending_count=0,
        pending_request_count=1,
        pending_request_high_water_count=1,
        decision_pending_high_water_count=0,
        snapshot_management_effect_count=1,
        snapshot_deactivation_effect_count=0,
        request_occurrence_identity_mismatch_count=0,
        final_occurrence_identity_mismatch_count=0,
        request_before_decision_ok=False,
        target_visible_while_pending_ok=False,
        request_receipt_before_management_cursor_ok=True,
        decision_receipt_before_decision_cursor_ok=False,
        effect_window_preserved_ok=False,
        remaining_window_seconds=None,
        expected_remaining_window_seconds=270,
        request_receipt_commit_id='C1',
        decision_receipt_commit_id=None,
        decision_input_to_receipt_ms=None,
        target_group_durable_record_count=2,
        expected_target_group_durable_record_count=3,
        total_durable_record_count=2,
        expected_total_durable_record_count=3,
        functional_integrity_ok=False,
    )
    report = recorder.build_report(
        test_id='D-005',
        alarm_count=10,
        planned_duration_seconds=120,
        actual_duration_seconds=120,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        columns_per_alarm=1,
        historical_series_per_alarm=0,
        historical_window_minutes=0,
        historical_step_seconds=0,
        historical_points_per_series=0,
        historical_value_count=0,
        physical_partition_count=1,
        physical_partition_column_counts=(10,),
        physical_partition_layout='balanced',
        empty_physical_partition_count=0,
        missing_source_column_count=0,
        synthesized_null_column_count=0,
        source_view_count=1,
        source_column_count=10,
        source_row_count=1,
        source_frame_bytes=100,
        source_numeric_value_count=10,
        latest_source_column_count=10,
        historical_source_column_count=0,
        historical_source_row_count=0,
        source_load_durations_ms=[],
        source_merge_durations_ms=[],
        journal_aligned=True,
        durable_record_count=2,
        snapshot_count=1,
        snapshot_alarm_count=10,
        expected_snapshot_alarm_count=10,
        source_load_count=1,
        deactivation_decision_pressure=metrics,
    )

    assert report.result == 'FAIL'
    assert report.deactivation_decision_pressure is metrics


def test_deactivation_decision_metrics_require_exact_causality_and_remaining_window(
    tmp_path: Path,
) -> None:
    scenario = BaselineScenario(
        test_id='D-005',
        alarm_count=10,
        duration_seconds=120,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        priority_group_size=10,
        management_action_at_seconds=30,
        deactivation_decision_at_seconds=60,
        deactivation_window_seconds=300,
        initial_active_percent=100,
        physical_partition_count=10,
        physical_partition_layout='balanced',
    )
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    assert isinstance(runtime.input_source, SingleDeactivationDecisionInputSource)
    source = runtime.input_source
    consumer = runtime.input_consumer
    source.target_occurrence_id = 'PERF-O-00000001'
    source.decision_exposed_monotonic = 100.0
    source.target_visible_while_pending = True
    consumer.request_receipt_confirmed_monotonic = 99.0
    consumer.decision_receipt_confirmed_monotonic = 101.0
    consumer.request_receipt_commit_id = 'REQUEST-COMMIT'
    consumer.decision_receipt_commit_id = 'DECISION-COMMIT'
    consumer.request_receipt_before_cursor_advance_ok = True
    consumer.decision_receipt_before_cursor_advance_ok = True
    consumer.pending_request_high_water_count = 1
    consumer.decision_pending_high_water_count = 0

    requested_at = source.request_created_at.isoformat().replace('+00:00', 'Z')
    decided_at = source.decision_decided_at.isoformat().replace('+00:00', 'Z')
    effective_until = source.effective_until.isoformat().replace('+00:00', 'Z')
    target = source.target_identity.canonical_key
    empty_records = {
        'input_receipts': [],
        'deactivation_requests': [],
        'management_effects': [],
        'deactivation_effects': [],
    }
    records = (
        SimpleNamespace(
            record=SimpleNamespace(
                commit=SimpleNamespace(priority_group=source.target_priority_group),
                records=empty_records,
            )
        ),
        SimpleNamespace(
            record=SimpleNamespace(
                commit=SimpleNamespace(priority_group=source.target_priority_group),
                records={
                    'input_receipts': [
                        {
                            'input_kind': 'DEACTIVATION_REQUEST',
                            'input_id': source.management_input_id,
                            'outcome': 'PENDING_APPROVAL',
                        }
                    ],
                    'deactivation_requests': [
                        {
                            'request_id': source.request_id,
                            'alarm_key': target,
                            'source_management_input_id': source.management_input_id,
                            'source_occurrence_id': source.target_occurrence_id,
                            'requested_at': requested_at,
                            'effective_until': effective_until,
                            'approval_required': True,
                        }
                    ],
                    'management_effects': [
                        {
                            'alarm_key': target,
                            'effect_id': 'PERF-ME-PERF-M-000001',
                            'kind': 'STARTED',
                        }
                    ],
                    'deactivation_effects': [],
                },
            )
        ),
        SimpleNamespace(
            record=SimpleNamespace(
                commit=SimpleNamespace(priority_group=source.target_priority_group),
                records={
                    'input_receipts': [
                        {
                            'input_kind': 'DEACTIVATION_DECISION',
                            'input_id': source.decision_id,
                            'outcome': 'APPLIED',
                        }
                    ],
                    'deactivation_requests': [],
                    'management_effects': [],
                    'deactivation_effects': [
                        {
                            'alarm_key': target,
                            'effect_id': 'PERF-DE-PERF-DR-PERF-M-000001',
                            'kind': 'STARTED',
                            'effective_from': decided_at,
                            'effective_until': effective_until,
                        }
                    ],
                },
            )
        ),
    )
    snapshots = (
        SimpleNamespace(
            as_document=lambda: {
                'alarms': {
                    target: {
                        'occurrence': {'occurrence_id': source.target_occurrence_id},
                        'management_effect': {'effect_id': 'PERF-ME-PERF-M-000001'},
                        'deactivation_effect': {
                            'effect_id': 'PERF-DE-PERF-DR-PERF-M-000001',
                            'effective_from': decided_at,
                            'effective_until': effective_until,
                        },
                    }
                }
            }
        ),
    )

    import performance.run as run_module

    original_store = run_module.AtomicJsonStore
    run_module.AtomicJsonStore = lambda **_kwargs: SimpleNamespace(
        read=lambda _path: {
            'management': {'cursor': {'byte_offset': 256}, 'pending': []},
            'decisions': {'cursor': {'byte_offset': 256}, 'pending': []},
            'pending_deactivation_request_ids': [],
        }
    )
    try:
        metrics = _build_deactivation_decision_pressure_metrics(
            scenario=scenario,
            runtime=runtime,
            records=records,
            snapshots=snapshots,
        )
    finally:
        run_module.AtomicJsonStore = original_store

    assert isinstance(metrics, DeactivationDecisionPressureMetrics)
    assert metrics.pending_approval_receipt_count == 1
    assert metrics.applied_decision_receipt_count == 1
    assert metrics.request_before_decision_ok is True
    assert metrics.target_visible_while_pending_ok is True
    assert metrics.effect_window_preserved_ok is True
    assert metrics.remaining_window_seconds == 270
    assert metrics.decision_input_to_receipt_ms == 1000
    assert metrics.functional_integrity_ok is True


def test_sustained_deactivation_decision_scenario_uses_two_phase_contract_and_target_policy(
    tmp_path: Path,
) -> None:
    scenario = BaselineScenario(
        test_id='D-006',
        alarm_count=100,
        duration_seconds=160,
        iteration_period_seconds=5,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        columns_per_alarm=1,
        priority_group_size=10,
        management_action_at_seconds=10,
        management_action_count=20,
        management_action_interval_seconds=1,
        deactivation_decision_at_seconds=10,
        deactivation_decision_count=20,
        deactivation_decision_interval_seconds=1,
        deactivation_window_seconds=180,
        initial_active_percent=100,
        physical_partition_count=10,
        physical_partition_layout='balanced',
    )
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
        deactivation_phase='requests',
    )

    assert scenario.has_sustained_deactivation_decision_pressure is True
    assert scenario.effective_deactivation_decision_count == 20
    assert scenario.deactivation_phase_duration_seconds == 80
    assert scenario.management_last_action_at_seconds == 29
    assert scenario.deactivation_decision_last_at_seconds == 29
    assert isinstance(runtime.input_source, SustainedDeactivationRequestInputSource)
    assert runtime.input_source.expected_final_cursor.byte_offset == 5120
    expected_targets = {
        runtime.input_source.target_for_request(index)[0].canonical_key for index in range(20)
    }
    approval_targets = {
        planned.identity.canonical_key
        for planned in runtime.revision.session.planned_alarms
        if planned.deactivation_policy is not None and planned.deactivation_policy.approval_required
    }
    assert approval_targets == expected_targets


def test_sustained_deactivation_decision_scenario_rejects_mismatched_or_unsafe_phases() -> None:
    base = dict(
        test_id='D-006',
        alarm_count=100,
        duration_seconds=160,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        priority_group_size=10,
        management_action_at_seconds=10,
        management_action_count=20,
        management_action_interval_seconds=1,
        deactivation_decision_at_seconds=10,
        deactivation_decision_count=20,
        deactivation_decision_interval_seconds=1,
        deactivation_window_seconds=180,
        initial_active_percent=100,
        physical_partition_count=10,
        physical_partition_layout='balanced',
    )
    with pytest.raises(ValueError, match='matching request and decision counts'):
        BaselineScenario(**{**base, 'management_action_count': 19})
    with pytest.raises(ValueError, match='request and decision intervals must match'):
        BaselineScenario(**{**base, 'deactivation_decision_interval_seconds': 2})
    with pytest.raises(ValueError, match='request phase must extend beyond'):
        BaselineScenario(**{**base, 'duration_seconds': 70})
    with pytest.raises(ValueError, match='window must remain open across'):
        BaselineScenario(**{**base, 'deactivation_window_seconds': 80})


def test_sustained_deactivation_decision_phase_b_requires_all_durable_requests(
    tmp_path: Path,
) -> None:
    scenario = BaselineScenario(
        test_id='D-006',
        alarm_count=100,
        duration_seconds=160,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        priority_group_size=10,
        management_action_at_seconds=10,
        management_action_count=20,
        management_action_interval_seconds=1,
        deactivation_decision_at_seconds=10,
        deactivation_decision_count=20,
        deactivation_decision_interval_seconds=1,
        deactivation_window_seconds=180,
        initial_active_percent=100,
        physical_partition_count=10,
        physical_partition_layout='balanced',
    )
    with pytest.raises(RuntimeError, match='requires all durable requests'):
        build_baseline_runtime(
            scenario=scenario,
            volume_path=tmp_path / 'volume',
            source_path=tmp_path / 'source',
            deactivation_phase='decisions',
        )


def test_sustained_deactivation_decision_metrics_require_exact_two_phase_integrity(
    tmp_path: Path,
) -> None:
    scenario = BaselineScenario(
        test_id='D-006',
        alarm_count=20,
        duration_seconds=100,
        data_refresh_seconds=5,
        data_profile='latest-narrow',
        priority_group_size=5,
        management_action_at_seconds=5,
        management_action_count=3,
        management_action_interval_seconds=1,
        deactivation_decision_at_seconds=5,
        deactivation_decision_count=3,
        deactivation_decision_interval_seconds=1,
        deactivation_window_seconds=120,
        initial_active_percent=100,
        physical_partition_count=4,
        physical_partition_layout='balanced',
    )
    phase_a_runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
        deactivation_phase='requests',
    )
    assert isinstance(phase_a_runtime.input_source, SustainedDeactivationRequestInputSource)
    request_source = phase_a_runtime.input_source
    request_consumer = phase_a_runtime.input_consumer
    occurrence_ids = {}
    for index in range(3):
        identity, _priority_group = request_source.target_for_request(index)
        occurrence_ids[identity.canonical_key] = f'PERF-O-{index + 1:08d}'
    request_source.target_occurrence_ids.update(occurrence_ids)
    request_consumer.pending_request_high_water_count = 3
    request_consumer.decision_pending_high_water_count = 0
    request_consumer.deactivation_request_receipt_before_cursor_checked_count = 3
    request_consumer.deactivation_request_receipt_before_cursor_advance_ok = True
    request_consumer.deactivation_request_receipt_batch_sizes.extend((1, 2))

    decision_source = object.__new__(SustainedDeactivationDecisionInputSource)
    decision_source.composition = phase_a_runtime.composition
    decision_source.visible_after_seconds = 5
    decision_source.decision_count = 3
    decision_source.interval_seconds = 1
    decision_source.byte_length = 256
    decision_source.actor_key = 'perf-approver'
    decision_source.started_monotonic = 100.0
    decision_source.first_decided_at = request_source.first_source_created_at + timedelta(
        seconds=60
    )
    decision_source.hour_bucket = decision_source.first_decided_at.strftime('%Y-%m-%dT%HZ')
    decision_source._records = {}
    decision_source.visible_monotonic_by_input_id = {
        decision_id: 100.0 + index for index, decision_id in enumerate(decision_source.decision_ids)
    }
    decision_source.read_batch_sizes = [0, 1, 2]
    decision_consumer = SimpleNamespace(
        pending_request_high_water_count=2,
        decision_pending_high_water_count=0,
        deactivation_decision_receipt_before_cursor_checked_count=3,
        deactivation_decision_receipt_before_cursor_advance_ok=True,
        deactivation_decision_receipt_batch_sizes=[1, 2],
        deactivation_decision_receipt_confirmed_monotonic_by_input_id={
            decision_id: 101.0 + index
            for index, decision_id in enumerate(decision_source.decision_ids)
        },
    )
    phase_b_runtime = SimpleNamespace(
        input_source=decision_source,
        input_consumer=decision_consumer,
        composition=phase_a_runtime.composition,
    )

    records = []
    snapshots_alarms = {}
    for index in range(3):
        identity, priority_group = request_source.target_for_request(index)
        alarm_key = identity.canonical_key
        input_id = request_source.input_ids[index]
        request_id = request_source.request_ids[index]
        decision_id = decision_source.decision_ids[index]
        occurrence_id = occurrence_ids[alarm_key]
        requested_at = request_source.requested_at_for(index).isoformat().replace('+00:00', 'Z')
        effective_until = (
            request_source.effective_until_for(index).isoformat().replace('+00:00', 'Z')
        )
        decided_at = decision_source.decided_at_for(index).isoformat().replace('+00:00', 'Z')
        records.append(
            SimpleNamespace(
                record=SimpleNamespace(
                    commit=SimpleNamespace(priority_group=priority_group),
                    records={
                        'input_receipts': [
                            {
                                'input_kind': 'DEACTIVATION_REQUEST',
                                'input_id': input_id,
                                'outcome': 'PENDING_APPROVAL',
                            }
                        ],
                        'deactivation_requests': [
                            {
                                'request_id': request_id,
                                'alarm_key': alarm_key,
                                'source_management_input_id': input_id,
                                'source_occurrence_id': occurrence_id,
                                'requested_at': requested_at,
                                'effective_until': effective_until,
                                'approval_required': True,
                            }
                        ],
                        'management_effects': [
                            {
                                'alarm_key': alarm_key,
                                'effect_id': f'PERF-ME-{input_id}',
                                'kind': 'STARTED',
                            }
                        ],
                        'deactivation_effects': [],
                    },
                )
            )
        )
        records.append(
            SimpleNamespace(
                record=SimpleNamespace(
                    commit=SimpleNamespace(priority_group=priority_group),
                    records={
                        'input_receipts': [
                            {
                                'input_kind': 'DEACTIVATION_DECISION',
                                'input_id': decision_id,
                                'outcome': 'APPLIED',
                            }
                        ],
                        'deactivation_requests': [],
                        'management_effects': [],
                        'deactivation_effects': [
                            {
                                'alarm_key': alarm_key,
                                'effect_id': f'PERF-DE-{request_id}',
                                'kind': 'STARTED',
                                'effective_from': decided_at,
                                'effective_until': effective_until,
                            }
                        ],
                    },
                )
            )
        )
        snapshots_alarms[alarm_key] = {
            'occurrence': {'occurrence_id': occurrence_id},
            'management_effect': {'effect_id': f'PERF-ME-{input_id}'},
            'deactivation_effect': {
                'effect_id': f'PERF-DE-{request_id}',
                'effective_from': decided_at,
                'effective_until': effective_until,
            },
        }
    snapshots = (SimpleNamespace(as_document=lambda: {'alarms': snapshots_alarms}),)
    phase_a_state = {
        'management': {'cursor': {'byte_offset': 768}, 'pending': []},
        'decisions': {'cursor': None, 'pending': []},
        'pending_deactivation_request_ids': list(request_source.request_ids),
    }

    import performance.run as run_module

    original_store = run_module.AtomicJsonStore
    run_module.AtomicJsonStore = lambda **_kwargs: SimpleNamespace(
        read=lambda _path: {
            'management': {'cursor': {'byte_offset': 768}, 'pending': []},
            'decisions': {'cursor': {'byte_offset': 768}, 'pending': []},
            'pending_deactivation_request_ids': [],
        }
    )
    try:
        metrics = _build_sustained_deactivation_decision_pressure_metrics(
            scenario=scenario,
            phase_a_runtime=phase_a_runtime,
            phase_b_runtime=phase_b_runtime,
            phase_a_state=phase_a_state,
            records=tuple(records),
            snapshots=snapshots,
        )
    finally:
        run_module.AtomicJsonStore = original_store

    assert isinstance(metrics, SustainedDeactivationDecisionPressureMetrics)
    assert metrics.request_receipt_count == 3
    assert metrics.phase_a_pending_request_count == 3
    assert metrics.decision_receipt_count == 3
    assert metrics.applied_decision_receipt_count == 3
    assert metrics.unique_target_count == 3
    assert metrics.wrong_decision_request_correlation_count == 0
    assert metrics.effect_window_mismatch_count == 0
    assert metrics.snapshot_deactivation_effect_count == 3
    assert metrics.final_pending_request_count == 0
    assert metrics.decision_input_to_receipt_p95_ms == 1000
    assert metrics.remaining_window_min_seconds == 60
    assert metrics.remaining_window_max_seconds == 60
    assert metrics.decision_arrival_mode == 'sustained'
    assert metrics.expected_decision_first_nonempty_receipt_batch_size is None
    assert metrics.decision_fully_absorbed_in_first_eligible_iteration is None
    assert metrics.functional_integrity_ok is True


def test_deactivation_decision_burst_scenario_uses_two_phase_contract_and_target_policy(
    tmp_path: Path,
) -> None:
    scenario = BaselineScenario(
        test_id='D-007',
        alarm_count=1000,
        duration_seconds=240,
        iteration_period_seconds=5,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        columns_per_alarm=1,
        priority_group_size=10,
        management_action_at_seconds=30,
        management_action_count=50,
        management_action_interval_seconds=0,
        deactivation_decision_at_seconds=30,
        deactivation_decision_count=50,
        deactivation_decision_interval_seconds=0,
        deactivation_window_seconds=600,
        initial_active_percent=100,
        physical_partition_count=36,
        physical_partition_layout='balanced',
    )
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
        deactivation_phase='requests',
    )

    assert scenario.has_multi_deactivation_decision_pressure is True
    assert scenario.has_sustained_deactivation_decision_pressure is False
    assert scenario.has_burst_deactivation_decision_pressure is True
    assert scenario.deactivation_decision_arrival_mode == 'burst'
    assert scenario.deactivation_phase_duration_seconds == 120
    assert scenario.management_last_action_at_seconds == 30
    assert scenario.deactivation_decision_last_at_seconds == 30
    assert isinstance(runtime.input_source, SustainedDeactivationRequestInputSource)
    assert runtime.input_source.expected_final_cursor.byte_offset == 12800
    targets = tuple(runtime.input_source.target_for_request(index) for index in range(50))
    assert targets[0][0].canonical_key == 'perf/alarm_00001'
    assert targets[0][1] == 'perf-group-001'
    assert targets[-1][0].canonical_key == 'perf/alarm_00491'
    assert targets[-1][1] == 'perf-group-050'
    assert len({identity.canonical_key for identity, _group in targets}) == 50
    assert len({group for _identity, group in targets}) == 50
    approval_targets = {
        planned.identity.canonical_key
        for planned in runtime.revision.session.planned_alarms
        if planned.deactivation_policy is not None and planned.deactivation_policy.approval_required
    }
    assert approval_targets == {identity.canonical_key for identity, _group in targets}


def test_deactivation_request_burst_source_exposes_all_setups_simultaneously(
    monkeypatch,
) -> None:
    source = object.__new__(SustainedDeactivationRequestInputSource)
    source.visible_after_seconds = 30
    source.request_count = 3
    source.interval_seconds = 0
    source.byte_length = 256
    source.started_monotonic = 100.0
    source.first_source_created_at = datetime(2026, 8, 27, 17, 0, 30, tzinfo=UTC)
    source.read_batch_sizes = []
    source.iteration_as_of = None

    source.prepare_iteration(as_of=source.first_source_created_at)
    monkeypatch.setattr('performance.baseline.time.perf_counter', lambda: 130.5)
    monkeypatch.setattr(
        SustainedDeactivationRequestInputSource,
        '_record_for',
        lambda _source, index: index,
    )

    records = source.read_after(
        stream=AlarmInputStream.MANAGEMENT,
        cursor=None,
    )

    assert records == (0, 1, 2)
    assert source.read_batch_sizes == [3]


def test_deactivation_decision_burst_source_exposes_all_decisions_simultaneously(
    monkeypatch,
) -> None:
    source = object.__new__(SustainedDeactivationDecisionInputSource)
    source.composition = object()
    source.visible_after_seconds = 30
    source.decision_count = 3
    source.interval_seconds = 0
    source.byte_length = 256
    source.actor_key = 'perf-approver'
    source.started_monotonic = 100.0
    source.first_decided_at = datetime(2026, 8, 27, 17, 0, 30, tzinfo=UTC)
    source.hour_bucket = '2026-08-27T17Z'
    source._records = {}
    source.visible_monotonic_by_input_id = {}
    source.read_batch_sizes = []
    source.iteration_as_of = None

    source.prepare_iteration(as_of=source.first_decided_at)
    monkeypatch.setattr('performance.baseline.time.perf_counter', lambda: 130.5)

    records = source.read_after(
        stream=AlarmInputStream.DEACTIVATION_DECISION,
        cursor=None,
    )

    assert [record.value.decision_id for record in records] == [
        'PERF-D-000001',
        'PERF-D-000002',
        'PERF-D-000003',
    ]
    assert {record.value.decided_at for record in records} == {source.first_decided_at}
    assert source.read_batch_sizes == [3]


def test_deactivation_decision_burst_metrics_require_first_eligible_iteration_absorption(
    tmp_path: Path,
) -> None:
    scenario = BaselineScenario(
        test_id='D-007',
        alarm_count=20,
        duration_seconds=60,
        data_refresh_seconds=5,
        data_profile='latest-narrow',
        priority_group_size=5,
        management_action_at_seconds=5,
        management_action_count=2,
        management_action_interval_seconds=0,
        deactivation_decision_at_seconds=5,
        deactivation_decision_count=2,
        deactivation_decision_interval_seconds=0,
        deactivation_window_seconds=60,
        initial_active_percent=100,
        physical_partition_count=4,
        physical_partition_layout='balanced',
    )
    phase_a_runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
        deactivation_phase='requests',
    )
    request_source = phase_a_runtime.input_source
    request_consumer = phase_a_runtime.input_consumer
    assert isinstance(request_source, SustainedDeactivationRequestInputSource)
    occurrence_ids = {}
    for index in range(2):
        identity, _priority_group = request_source.target_for_request(index)
        occurrence_ids[identity.canonical_key] = f'PERF-O-{index + 1:08d}'
    request_source.target_occurrence_ids.update(occurrence_ids)
    request_consumer.pending_request_high_water_count = 2
    request_consumer.decision_pending_high_water_count = 0
    request_consumer.deactivation_request_receipt_before_cursor_checked_count = 2
    request_consumer.deactivation_request_receipt_before_cursor_advance_ok = True
    request_consumer.deactivation_request_receipt_batch_sizes.extend((2,))

    decision_source = object.__new__(SustainedDeactivationDecisionInputSource)
    decision_source.composition = phase_a_runtime.composition
    decision_source.visible_after_seconds = 5
    decision_source.decision_count = 2
    decision_source.interval_seconds = 0
    decision_source.byte_length = 256
    decision_source.actor_key = 'perf-approver'
    decision_source.started_monotonic = 100.0
    decision_source.first_decided_at = request_source.first_source_created_at + timedelta(
        seconds=30
    )
    decision_source.hour_bucket = decision_source.first_decided_at.strftime('%Y-%m-%dT%HZ')
    decision_source._records = {}
    decision_source.visible_monotonic_by_input_id = {
        decision_id: 100.0 for decision_id in decision_source.decision_ids
    }
    decision_source.read_batch_sizes = [0, 2]
    decision_consumer = SimpleNamespace(
        pending_request_high_water_count=1,
        decision_pending_high_water_count=0,
        deactivation_decision_receipt_before_cursor_checked_count=2,
        deactivation_decision_receipt_before_cursor_advance_ok=True,
        deactivation_decision_receipt_batch_sizes=[1, 1],
        deactivation_decision_receipt_confirmed_monotonic_by_input_id={
            decision_id: 101.0 for decision_id in decision_source.decision_ids
        },
    )
    phase_b_runtime = SimpleNamespace(
        input_source=decision_source,
        input_consumer=decision_consumer,
        composition=phase_a_runtime.composition,
    )

    records = []
    snapshots_alarms = {}
    for index in range(2):
        identity, priority_group = request_source.target_for_request(index)
        alarm_key = identity.canonical_key
        input_id = request_source.input_ids[index]
        request_id = request_source.request_ids[index]
        decision_id = decision_source.decision_ids[index]
        occurrence_id = occurrence_ids[alarm_key]
        requested_at = request_source.requested_at_for(index).isoformat().replace('+00:00', 'Z')
        effective_until = (
            request_source.effective_until_for(index).isoformat().replace('+00:00', 'Z')
        )
        decided_at = decision_source.decided_at_for(index).isoformat().replace('+00:00', 'Z')
        records.append(
            SimpleNamespace(
                record=SimpleNamespace(
                    commit=SimpleNamespace(priority_group=priority_group),
                    records={
                        'input_receipts': [
                            {
                                'input_kind': 'DEACTIVATION_REQUEST',
                                'input_id': input_id,
                                'outcome': 'PENDING_APPROVAL',
                            }
                        ],
                        'deactivation_requests': [
                            {
                                'request_id': request_id,
                                'alarm_key': alarm_key,
                                'source_management_input_id': input_id,
                                'source_occurrence_id': occurrence_id,
                                'requested_at': requested_at,
                                'effective_until': effective_until,
                                'approval_required': True,
                            }
                        ],
                        'management_effects': [
                            {
                                'alarm_key': alarm_key,
                                'effect_id': f'PERF-ME-{input_id}',
                                'kind': 'STARTED',
                            }
                        ],
                        'deactivation_effects': [],
                    },
                )
            )
        )
        records.append(
            SimpleNamespace(
                record=SimpleNamespace(
                    commit=SimpleNamespace(priority_group=priority_group),
                    records={
                        'input_receipts': [
                            {
                                'input_kind': 'DEACTIVATION_DECISION',
                                'input_id': decision_id,
                                'outcome': 'APPLIED',
                            }
                        ],
                        'deactivation_requests': [],
                        'management_effects': [],
                        'deactivation_effects': [
                            {
                                'alarm_key': alarm_key,
                                'effect_id': f'PERF-DE-{request_id}',
                                'kind': 'STARTED',
                                'effective_from': decided_at,
                                'effective_until': effective_until,
                            }
                        ],
                    },
                )
            )
        )
        snapshots_alarms[alarm_key] = {
            'occurrence': {'occurrence_id': occurrence_id},
            'management_effect': {'effect_id': f'PERF-ME-{input_id}'},
            'deactivation_effect': {
                'effect_id': f'PERF-DE-{request_id}',
                'effective_from': decided_at,
                'effective_until': effective_until,
            },
        }
    snapshots = (SimpleNamespace(as_document=lambda: {'alarms': snapshots_alarms}),)
    phase_a_state = {
        'management': {'cursor': {'byte_offset': 512}, 'pending': []},
        'decisions': {'cursor': None, 'pending': []},
        'pending_deactivation_request_ids': list(request_source.request_ids),
    }

    import performance.run as run_module

    original_store = run_module.AtomicJsonStore
    run_module.AtomicJsonStore = lambda **_kwargs: SimpleNamespace(
        read=lambda _path: {
            'management': {'cursor': {'byte_offset': 512}, 'pending': []},
            'decisions': {'cursor': {'byte_offset': 512}, 'pending': []},
            'pending_deactivation_request_ids': [],
        }
    )
    try:
        metrics = _build_sustained_deactivation_decision_pressure_metrics(
            scenario=scenario,
            phase_a_runtime=phase_a_runtime,
            phase_b_runtime=phase_b_runtime,
            phase_a_state=phase_a_state,
            records=tuple(records),
            snapshots=snapshots,
        )
    finally:
        run_module.AtomicJsonStore = original_store

    assert metrics.decision_arrival_mode == 'burst'
    assert metrics.decision_receipt_nonempty_batch_sizes == (1, 1)
    assert metrics.decision_first_nonempty_receipt_batch_size == 1
    assert metrics.expected_decision_first_nonempty_receipt_batch_size == 2
    assert metrics.decision_fully_absorbed_in_first_eligible_iteration is False
    assert metrics.functional_integrity_ok is False


def test_sustained_deactivation_decision_source_never_exposes_future_decided_at(
    monkeypatch,
) -> None:
    source = object.__new__(SustainedDeactivationDecisionInputSource)
    source.composition = object()
    source.visible_after_seconds = 30
    source.decision_count = 3
    source.interval_seconds = 1
    source.byte_length = 256
    source.actor_key = 'perf-approver'
    source.started_monotonic = 100.0
    source.first_decided_at = datetime(2026, 8, 27, 16, 0, 30, tzinfo=UTC)
    source.hour_bucket = '2026-08-27T16Z'
    source._records = {}
    source.visible_monotonic_by_input_id = {}
    source.read_batch_sizes = []
    source.iteration_as_of = None

    source.prepare_iteration(as_of=source.first_decided_at + timedelta(seconds=1))
    monkeypatch.setattr('performance.baseline.time.perf_counter', lambda: 132.5)

    records = source.read_after(
        stream=AlarmInputStream.DEACTIVATION_DECISION,
        cursor=None,
    )

    assert [record.value.decision_id for record in records] == [
        'PERF-D-000001',
        'PERF-D-000002',
    ]
    assert all(record.value.decided_at <= source.iteration_as_of for record in records)
    assert source.read_batch_sizes == [2]


def test_inverted_deactivation_delivery_scenario_uses_valid_domain_time_and_inverted_delivery(
    tmp_path: Path,
) -> None:
    scenario = BaselineScenario(
        test_id='D-008',
        alarm_count=1000,
        duration_seconds=180,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        priority_group_size=10,
        management_action_at_seconds=30,
        management_action_count=50,
        management_action_interval_seconds=0,
        deactivation_decision_at_seconds=45,
        deactivation_decision_count=50,
        deactivation_decision_interval_seconds=0,
        deactivation_request_delivery_at_seconds=60,
        deactivation_window_seconds=600,
        initial_active_percent=100,
        physical_partition_count=36,
        physical_partition_layout='balanced',
    )
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )

    assert scenario.has_inverted_deactivation_delivery_pressure is True
    assert scenario.deactivation_phase_duration_seconds == 180
    assert isinstance(runtime.input_source, InvertedDeliveryDeactivationInputSource)
    source = runtime.input_source
    assert source.request_created_at < source.decision_decided_at
    assert source.request_delivery_after_seconds > source.decision_visible_after_seconds
    assert source.input_ids[0] == 'PERF-M-000001'
    assert source.input_ids[-1] == 'PERF-M-000050'
    assert source.decision_ids[0] == 'PERF-D-000001'
    assert source.decision_ids[-1] == 'PERF-D-000050'
    targets = [source.target_for_input(index) for index in range(50)]
    assert [priority_group for _identity, priority_group in targets] == [
        f'perf-group-{index:03d}' for index in range(1, 51)
    ]
    assert [identity.alarm_key for identity, _priority_group in targets] == [
        f'alarm_{1 + index * 10:05d}' for index in range(50)
    ]


def test_inverted_deactivation_delivery_scenario_rejects_invalid_ordering() -> None:
    base = dict(
        test_id='D-008',
        alarm_count=100,
        duration_seconds=180,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        priority_group_size=10,
        management_action_at_seconds=30,
        management_action_count=10,
        management_action_interval_seconds=0,
        deactivation_decision_at_seconds=45,
        deactivation_decision_count=10,
        deactivation_decision_interval_seconds=0,
        deactivation_request_delivery_at_seconds=60,
        deactivation_window_seconds=600,
        initial_active_percent=100,
        physical_partition_count=10,
        physical_partition_layout='balanced',
    )

    with pytest.raises(ValueError, match='request logical time before decision time'):
        BaselineScenario(**{**base, 'management_action_at_seconds': 50})
    with pytest.raises(ValueError, match='requests to be delivered after decisions'):
        BaselineScenario(**{**base, 'deactivation_request_delivery_at_seconds': 40})
    with pytest.raises(ValueError, match='request interval zero'):
        BaselineScenario(**{**base, 'management_action_interval_seconds': 1})
    with pytest.raises(ValueError, match='decision interval zero'):
        BaselineScenario(**{**base, 'deactivation_decision_interval_seconds': 1})


def test_inverted_deactivation_source_exposes_decisions_once_then_supports_pending_read(
    tmp_path: Path,
    monkeypatch,
) -> None:
    scenario = BaselineScenario(
        test_id='D-008',
        alarm_count=20,
        duration_seconds=90,
        data_refresh_seconds=5,
        data_profile='latest-narrow',
        priority_group_size=5,
        management_action_at_seconds=10,
        management_action_count=2,
        management_action_interval_seconds=0,
        deactivation_decision_at_seconds=15,
        deactivation_decision_count=2,
        deactivation_decision_interval_seconds=0,
        deactivation_request_delivery_at_seconds=25,
        deactivation_window_seconds=120,
        initial_active_percent=100,
        physical_partition_count=4,
        physical_partition_layout='balanced',
    )
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    source = runtime.input_source
    assert isinstance(source, InvertedDeliveryDeactivationInputSource)
    source.started_monotonic = 100.0
    source.prepare_iteration(as_of=source.decision_decided_at)
    monkeypatch.setattr('performance.baseline.time.perf_counter', lambda: 115.5)

    fresh = source.read_after(
        stream=AlarmInputStream.DEACTIVATION_DECISION,
        cursor=None,
    )
    assert [record.value.decision_id for record in fresh] == [
        'PERF-D-000001',
        'PERF-D-000002',
    ]
    assert {record.value.decided_at for record in fresh} == {source.decision_decided_at}

    reread = source.read_after(
        stream=AlarmInputStream.DEACTIVATION_DECISION,
        cursor=source.expected_decision_cursor,
    )
    assert reread == ()
    pending = source.read_at(
        stream=AlarmInputStream.DEACTIVATION_DECISION,
        locator=fresh[0].locator,
    )
    assert pending == fresh[0]
    assert source.decision_read_at_count == 1
    assert sum(source.decision_read_batch_sizes) == 2


def test_inverted_deactivation_metrics_require_pending_milestones_and_exact_replay(
    tmp_path: Path,
) -> None:
    scenario = BaselineScenario(
        test_id='D-008',
        alarm_count=20,
        duration_seconds=90,
        data_refresh_seconds=5,
        data_profile='latest-narrow',
        priority_group_size=5,
        management_action_at_seconds=10,
        management_action_count=2,
        management_action_interval_seconds=0,
        deactivation_decision_at_seconds=15,
        deactivation_decision_count=2,
        deactivation_decision_interval_seconds=0,
        deactivation_request_delivery_at_seconds=25,
        deactivation_window_seconds=120,
        initial_active_percent=100,
        physical_partition_count=4,
        physical_partition_layout='balanced',
    )
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    source = runtime.input_source
    consumer = runtime.input_consumer
    assert isinstance(source, InvertedDeliveryDeactivationInputSource)

    occurrence_ids = {}
    for index in range(2):
        identity, _priority_group = source.target_for_input(index)
        occurrence_ids[identity.canonical_key] = f'PERF-O-{index + 1:08d}'
    source.target_occurrence_ids.update(occurrence_ids)
    source.decision_read_batch_sizes = [0, 2, 0, 0]
    source.decision_read_at_count = 4
    source.decision_visible_monotonic_by_input_id = {
        decision_id: 100.0 for decision_id in source.decision_ids
    }
    consumer.decision_pending_high_water_count = 2
    consumer.pending_request_high_water_count = 2
    consumer.inverted_early_decision_cursor_byte_offset = 512
    consumer.inverted_early_decision_pending_count = 2
    consumer.inverted_early_decision_receipt_count = 0
    consumer.inverted_post_request_management_cursor_byte_offset = 512
    consumer.inverted_post_request_decision_pending_count = 2
    consumer.inverted_post_request_pending_request_count = 2
    consumer.inverted_post_request_decision_receipt_count = 0
    consumer.inverted_final_resolved_observed = True
    consumer.inverted_decision_receipt_batch_sizes = [2]
    consumer.inverted_decision_receipt_confirmed_monotonic_by_input_id = {
        decision_id: 101.0 for decision_id in source.decision_ids
    }

    records = []
    for index in range(scenario.priority_group_count):
        records.append(
            SimpleNamespace(
                record=SimpleNamespace(
                    commit=SimpleNamespace(priority_group=f'perf-group-{index + 1:03d}'),
                    records={
                        'input_receipts': [],
                        'deactivation_requests': [],
                        'management_effects': [],
                        'deactivation_effects': [],
                    },
                )
            )
        )

    snapshots_alarms = {}
    expected_requested_at = source.request_created_at.isoformat().replace('+00:00', 'Z')
    expected_decided_at = source.decision_decided_at.isoformat().replace('+00:00', 'Z')
    expected_effective_until = source.effective_until.isoformat().replace('+00:00', 'Z')
    for index in range(2):
        identity, priority_group = source.target_for_input(index)
        alarm_key = identity.canonical_key
        input_id = source.input_ids[index]
        request_id = source.request_ids[index]
        decision_id = source.decision_ids[index]
        occurrence_id = occurrence_ids[alarm_key]
        records.append(
            SimpleNamespace(
                record=SimpleNamespace(
                    commit=SimpleNamespace(priority_group=priority_group),
                    records={
                        'input_receipts': [
                            {
                                'input_kind': 'DEACTIVATION_REQUEST',
                                'input_id': input_id,
                                'outcome': 'PENDING_APPROVAL',
                            }
                        ],
                        'deactivation_requests': [
                            {
                                'request_id': request_id,
                                'alarm_key': alarm_key,
                                'source_management_input_id': input_id,
                                'source_occurrence_id': occurrence_id,
                                'requested_at': expected_requested_at,
                                'effective_until': expected_effective_until,
                                'approval_required': True,
                            }
                        ],
                        'management_effects': [
                            {
                                'alarm_key': alarm_key,
                                'effect_id': f'PERF-ME-{input_id}',
                                'kind': 'STARTED',
                            }
                        ],
                        'deactivation_effects': [],
                    },
                )
            )
        )
        records.append(
            SimpleNamespace(
                record=SimpleNamespace(
                    commit=SimpleNamespace(priority_group=priority_group),
                    records={
                        'input_receipts': [
                            {
                                'input_kind': 'DEACTIVATION_DECISION',
                                'input_id': decision_id,
                                'outcome': 'APPLIED',
                            }
                        ],
                        'deactivation_requests': [],
                        'management_effects': [],
                        'deactivation_effects': [
                            {
                                'alarm_key': alarm_key,
                                'effect_id': f'PERF-DE-{request_id}',
                                'kind': 'STARTED',
                                'effective_from': expected_decided_at,
                                'effective_until': expected_effective_until,
                            }
                        ],
                    },
                )
            )
        )
        snapshots_alarms[alarm_key] = {
            'occurrence': {'occurrence_id': occurrence_id},
            'management_effect': {'effect_id': f'PERF-ME-{input_id}'},
            'deactivation_effect': {
                'effect_id': f'PERF-DE-{request_id}',
                'effective_from': expected_decided_at,
                'effective_until': expected_effective_until,
            },
        }
    snapshots = (SimpleNamespace(as_document=lambda: {'alarms': snapshots_alarms}),)

    import performance.run as run_module

    original_store = run_module.AtomicJsonStore
    run_module.AtomicJsonStore = lambda **_kwargs: SimpleNamespace(
        read=lambda _path: {
            'management': {'cursor': {'byte_offset': 512}, 'pending': []},
            'decisions': {'cursor': {'byte_offset': 512}, 'pending': []},
            'pending_deactivation_request_ids': [],
        }
    )
    try:
        metrics = _build_inverted_deactivation_decision_pressure_metrics(
            scenario=scenario,
            runtime=runtime,
            records=tuple(records),
            snapshots=snapshots,
        )
        assert isinstance(metrics, InvertedDeactivationDecisionPressureMetrics)
        assert metrics.early_pending_state_ok is True
        assert metrics.post_request_pending_state_ok is True
        assert metrics.final_replay_state_ok is True
        assert metrics.decision_fresh_record_count == 2
        assert metrics.decision_pending_read_count == 4
        assert metrics.decision_receipt_nonempty_batch_sizes == (2,)
        assert metrics.durable_record_count == 8
        assert metrics.expected_durable_record_count == 8
        assert metrics.functional_integrity_ok is True

        consumer.inverted_early_decision_pending_count = None
        invalid = _build_inverted_deactivation_decision_pressure_metrics(
            scenario=scenario,
            runtime=runtime,
            records=tuple(records),
            snapshots=snapshots,
        )
        assert invalid.early_pending_state_ok is False
        assert invalid.functional_integrity_ok is False
    finally:
        run_module.AtomicJsonStore = original_store


def test_mixed_deactivation_scenario_builds_single_concurrent_source(tmp_path: Path) -> None:
    scenario = BaselineScenario(
        test_id='D-009',
        alarm_count=1000,
        duration_seconds=600,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        priority_group_size=10,
        management_action_at_seconds=30,
        management_action_count=480,
        management_action_interval_seconds=1,
        deactivation_decision_at_seconds=60,
        deactivation_decision_count=480,
        deactivation_decision_interval_seconds=1,
        deactivation_window_seconds=900,
        initial_active_percent=100,
        physical_partition_count=36,
        physical_partition_layout='balanced',
    )
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )

    assert scenario.has_mixed_deactivation_pressure is True
    assert scenario.deactivation_phase_duration_seconds == 600
    assert scenario.management_last_action_at_seconds == 509
    assert scenario.deactivation_decision_last_at_seconds == 539
    assert isinstance(runtime.input_source, MixedDeactivationInputSource)
    source = runtime.input_source
    assert source.input_ids[0] == 'PERF-M-000001'
    assert source.input_ids[-1] == 'PERF-M-000480'
    assert source.decision_ids[0] == 'PERF-D-000001'
    assert source.decision_ids[-1] == 'PERF-D-000480'
    assert source.decision_decided_at_for(0) - source.request_created_at_for(0) == timedelta(
        seconds=30
    )
    targets = [source.target_for_input(index) for index in range(480)]
    assert len({identity.canonical_key for identity, _group in targets}) == 480
    assert targets[0][1] == 'perf-group-001'
    assert targets[99][1] == 'perf-group-100'
    assert targets[100][0].alarm_key == 'alarm_00002'
    assert targets[479][0].alarm_key == 'alarm_00795'


def test_mixed_deactivation_scenario_rejects_invalid_concurrent_contract() -> None:
    base = dict(
        test_id='D-009',
        alarm_count=100,
        duration_seconds=120,
        data_refresh_seconds=5,
        data_profile='latest-narrow',
        priority_group_size=10,
        management_action_at_seconds=10,
        management_action_count=20,
        management_action_interval_seconds=1,
        deactivation_decision_at_seconds=20,
        deactivation_decision_count=20,
        deactivation_decision_interval_seconds=1,
        deactivation_window_seconds=120,
        initial_active_percent=100,
        physical_partition_count=10,
        physical_partition_layout='balanced',
    )

    with pytest.raises(ValueError, match='matching request and decision counts'):
        BaselineScenario(**{**base, 'deactivation_decision_count': 19})
    with pytest.raises(ValueError, match='positive request interval'):
        BaselineScenario(
            **{
                **base,
                'management_action_interval_seconds': 0,
                'deactivation_decision_interval_seconds': 1,
            }
        )
    with pytest.raises(ValueError, match='request and decision intervals must match'):
        BaselineScenario(**{**base, 'deactivation_decision_interval_seconds': 2})
    with pytest.raises(ValueError, match='duration must extend beyond both final input cycles'):
        BaselineScenario(**{**base, 'duration_seconds': 44})


def test_mixed_deactivation_source_respects_monotonic_and_logical_visibility(
    tmp_path: Path,
    monkeypatch,
) -> None:
    scenario = BaselineScenario(
        test_id='D-009',
        alarm_count=20,
        duration_seconds=90,
        data_refresh_seconds=5,
        data_profile='latest-narrow',
        priority_group_size=5,
        management_action_at_seconds=10,
        management_action_count=3,
        management_action_interval_seconds=1,
        deactivation_decision_at_seconds=15,
        deactivation_decision_count=3,
        deactivation_decision_interval_seconds=1,
        deactivation_window_seconds=120,
        initial_active_percent=100,
        physical_partition_count=4,
        physical_partition_layout='balanced',
    )
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    source = runtime.input_source
    assert isinstance(source, MixedDeactivationInputSource)
    source.started_monotonic = 100.0
    source.prepare_iteration(as_of=source.first_decision_decided_at + timedelta(seconds=1))
    monkeypatch.setattr('performance.baseline.time.perf_counter', lambda: 117.5)

    records = source.read_after(
        stream=AlarmInputStream.DEACTIVATION_DECISION,
        cursor=None,
    )

    assert [record.value.decision_id for record in records] == [
        'PERF-D-000001',
        'PERF-D-000002',
    ]
    assert all(record.value.decided_at <= source.iteration_as_of for record in records)
    assert source.decision_read_batch_sizes == [2]
    reread = source.read_after(
        stream=AlarmInputStream.DEACTIVATION_DECISION,
        cursor=records[-1].next_cursor,
    )
    assert reread == ()
    assert (
        source.read_at(
            stream=AlarmInputStream.DEACTIVATION_DECISION,
            locator=records[0].locator,
        )
        == records[0]
    )
    assert source.decision_read_at_count == 1


def test_mixed_deactivation_metrics_require_exact_final_convergence(tmp_path: Path) -> None:
    scenario = BaselineScenario(
        test_id='D-009',
        alarm_count=20,
        duration_seconds=90,
        data_refresh_seconds=5,
        data_profile='latest-narrow',
        priority_group_size=5,
        management_action_at_seconds=10,
        management_action_count=2,
        management_action_interval_seconds=1,
        deactivation_decision_at_seconds=15,
        deactivation_decision_count=2,
        deactivation_decision_interval_seconds=1,
        deactivation_window_seconds=120,
        initial_active_percent=100,
        physical_partition_count=4,
        physical_partition_layout='balanced',
    )
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    source = runtime.input_source
    consumer = runtime.input_consumer
    assert isinstance(source, MixedDeactivationInputSource)

    occurrence_ids = {}
    for index in range(2):
        identity, _priority_group = source.target_for_input(index)
        occurrence_ids[identity.canonical_key] = f'PERF-O-{index + 1:08d}'
    source.target_occurrence_ids.update(occurrence_ids)
    source.management_read_batch_sizes = [0, 1, 1]
    source.decision_read_batch_sizes = [0, 1, 1]
    source.management_visible_monotonic_by_input_id = {
        input_id: 100.0 + index for index, input_id in enumerate(source.input_ids)
    }
    source.decision_visible_monotonic_by_input_id = {
        decision_id: 110.0 + index for index, decision_id in enumerate(source.decision_ids)
    }
    consumer.mixed_request_receipt_confirmed_monotonic_by_input_id = {
        input_id: 101.0 + index for index, input_id in enumerate(source.input_ids)
    }
    consumer.mixed_decision_receipt_confirmed_monotonic_by_input_id = {
        decision_id: 111.0 + index for index, decision_id in enumerate(source.decision_ids)
    }
    consumer.mixed_request_receipt_batch_sizes = [1, 1]
    consumer.mixed_decision_receipt_batch_sizes = [1, 1]
    consumer.mixed_receipt_cycle_batches = [(1, 0), (1, 1), (0, 1)]
    consumer.pending_request_high_water_count = 2
    consumer.decision_pending_high_water_count = 1

    records = []
    snapshots_alarms = {}
    for index in range(2):
        identity, priority_group = source.target_for_input(index)
        alarm_key = identity.canonical_key
        input_id = source.input_ids[index]
        request_id = source.request_ids[index]
        decision_id = source.decision_ids[index]
        occurrence_id = occurrence_ids[alarm_key]
        requested_at = source.request_created_at_for(index).isoformat().replace('+00:00', 'Z')
        decided_at = source.decision_decided_at_for(index).isoformat().replace('+00:00', 'Z')
        effective_until = source.effective_until_for(index).isoformat().replace('+00:00', 'Z')
        records.append(
            SimpleNamespace(
                record=SimpleNamespace(
                    commit=SimpleNamespace(priority_group=priority_group),
                    records={
                        'input_receipts': [
                            {
                                'input_kind': 'DEACTIVATION_REQUEST',
                                'input_id': input_id,
                                'outcome': 'PENDING_APPROVAL',
                            }
                        ],
                        'deactivation_requests': [
                            {
                                'request_id': request_id,
                                'alarm_key': alarm_key,
                                'source_management_input_id': input_id,
                                'source_occurrence_id': occurrence_id,
                                'requested_at': requested_at,
                                'effective_until': effective_until,
                                'approval_required': True,
                            }
                        ],
                        'management_effects': [
                            {
                                'alarm_key': alarm_key,
                                'effect_id': f'PERF-ME-{input_id}',
                                'kind': 'STARTED',
                            }
                        ],
                        'deactivation_effects': [],
                    },
                )
            )
        )
        records.append(
            SimpleNamespace(
                record=SimpleNamespace(
                    commit=SimpleNamespace(priority_group=priority_group),
                    records={
                        'input_receipts': [
                            {
                                'input_kind': 'DEACTIVATION_DECISION',
                                'input_id': decision_id,
                                'outcome': 'APPLIED',
                            }
                        ],
                        'deactivation_requests': [],
                        'management_effects': [],
                        'deactivation_effects': [
                            {
                                'alarm_key': alarm_key,
                                'effect_id': f'PERF-DE-{request_id}',
                                'kind': 'STARTED',
                                'effective_from': decided_at,
                                'effective_until': effective_until,
                            }
                        ],
                    },
                )
            )
        )
        snapshots_alarms[alarm_key] = {
            'occurrence': {'occurrence_id': occurrence_id},
            'management_effect': {'effect_id': f'PERF-ME-{input_id}'},
            'deactivation_effect': {
                'effect_id': f'PERF-DE-{request_id}',
                'effective_from': decided_at,
                'effective_until': effective_until,
            },
        }
    snapshots = (SimpleNamespace(as_document=lambda: {'alarms': snapshots_alarms}),)

    import performance.run as run_module

    final_state = {
        'management': {'cursor': {'byte_offset': 512}, 'pending': []},
        'decisions': {'cursor': {'byte_offset': 512}, 'pending': []},
        'pending_deactivation_request_ids': [],
    }
    original_store = run_module.AtomicJsonStore
    run_module.AtomicJsonStore = lambda **_kwargs: SimpleNamespace(read=lambda _path: final_state)
    try:
        metrics = _build_mixed_deactivation_decision_pressure_metrics(
            scenario=scenario,
            runtime=runtime,
            records=tuple(records),
            snapshots=snapshots,
        )
        assert isinstance(metrics, MixedDeactivationDecisionPressureMetrics)
        assert metrics.management_fresh_record_count == 2
        assert metrics.decision_fresh_record_count == 2
        assert metrics.mixed_receipt_cycle_count == 1
        assert metrics.pending_request_high_water_count == 2
        assert metrics.decision_pending_high_water_count == 1
        assert metrics.request_input_to_receipt_p95_ms == 1000.0
        assert metrics.decision_input_to_receipt_p95_ms == 1000.0
        assert metrics.functional_integrity_ok is True

        final_state['decisions']['pending'] = [{'input_id': 'PERF-D-000002'}]
        invalid = _build_mixed_deactivation_decision_pressure_metrics(
            scenario=scenario,
            runtime=runtime,
            records=tuple(records),
            snapshots=snapshots,
        )
        assert invalid.final_decision_pending_count == 1
        assert invalid.functional_integrity_ok is False
    finally:
        run_module.AtomicJsonStore = original_store


def _e001_scenario(*, alarm_count: int = 1000) -> BaselineScenario:
    return BaselineScenario(
        test_id='E-001',
        alarm_count=alarm_count,
        duration_seconds=180,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        columns_per_alarm=1,
        priority_group_size=10,
        physical_partition_count=36 if alarm_count >= 36 else 10,
        physical_partition_layout='balanced',
        initial_active_percent=100,
        signal_value=1.0,
        threshold=0.5,
        parameter_adoption_at_seconds=60,
        parameter_target_threshold=0.75,
    )


def test_e001_builds_parameter_only_compatible_target_revision(tmp_path: Path) -> None:
    scenario = _e001_scenario()
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )

    assert scenario.has_parameter_adoption_pressure is True
    assert runtime.revision.alarm_configuration_revision == 'PERF-AC-1'
    assert runtime.target_revision is not None
    assert runtime.target_revision.alarm_configuration_revision == 'PERF-AC-2'
    assert len(runtime.target_revision.session.entries) == 1000
    assert {float(entry.parameters['threshold']) for entry in runtime.revision.session.entries} == {
        0.5
    }
    assert {
        float(entry.parameters['threshold']) for entry in runtime.target_revision.session.entries
    } == {0.75}
    adoption = plan_configuration_adoption(runtime.revision, runtime.target_revision)
    assert adoption.is_adoptable is True
    assert len(adoption.changes) == 1000
    assert {change.disposition for change in adoption.changes} == {
        ConfigurationAdoptionDisposition.COMPATIBLE
    }


def test_e001_rejects_invalid_parameter_adoption_contract() -> None:
    base = dict(
        test_id='E-001',
        alarm_count=1000,
        duration_seconds=180,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        priority_group_size=10,
        physical_partition_count=36,
        physical_partition_layout='balanced',
        initial_active_percent=100,
        signal_value=1.0,
        threshold=0.5,
        parameter_adoption_at_seconds=60,
        parameter_target_threshold=0.75,
    )
    cases = (
        (
            {'parameter_target_threshold': None},
            'parameter adoption requires parameter_target_threshold',
        ),
        ({'parameter_target_threshold': 0.5}, 'parameter adoption must change the threshold'),
        ({'parameter_target_threshold': 1.25}, 'parameter adoption requires signal_value'),
        (
            {'parameter_adoption_at_seconds': 65},
            'parameter adoption timing must align with data refresh',
        ),
        ({'duration_seconds': 70}, 'parameter adoption duration must extend beyond adoption'),
    )
    for override, message in cases:
        with pytest.raises(ValueError, match=message):
            BaselineScenario(**(base | override))


def test_e001_report_integrity_includes_parameter_adoption_gate() -> None:
    recorder = PerformanceRecorder(iteration_period_seconds=5)
    pressure = ParameterAdoptionPressureMetrics(
        adoption_at_seconds=60,
        source_revision='PERF-AC-1',
        target_revision='PERF-AC-2',
        source_threshold=0.5,
        target_threshold=0.75,
        plan_change_count=10,
        compatible_change_count=10,
        unchanged_change_count=0,
        structural_reset_change_count=0,
        disabled_change_count=0,
        removed_change_count=0,
        rejected_change_count=0,
        target_runtime_alarm_count=10,
        expected_target_runtime_alarm_count=10,
        target_threshold_alarm_count=10,
        effective_cache_revision='PERF-AC-2',
        adoption_iteration_count=1,
        adoption_iteration=13,
        adoption_iteration_ms=1000.0,
        adoption_iteration_cpu_percent=50.0,
        adoption_cycle_executed=True,
        post_adoption_cache_current_iteration_count=2,
        source_revision_durable_record_count=1,
        target_revision_durable_record_count=0,
        durable_record_count=1,
        expected_durable_record_count=1,
        source_state_basis_snapshot_count=1,
        target_state_basis_snapshot_count=0,
        open_occurrence_count=10,
        expected_open_occurrence_count=10,
        functional_integrity_ok=False,
    )
    report = recorder.build_report(
        test_id='E-001',
        alarm_count=10,
        planned_duration_seconds=180,
        actual_duration_seconds=180,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        columns_per_alarm=1,
        historical_series_per_alarm=0,
        historical_window_minutes=0,
        historical_step_seconds=0,
        historical_points_per_series=0,
        historical_value_count=0,
        physical_partition_count=1,
        physical_partition_column_counts=(10,),
        physical_partition_layout='balanced',
        empty_physical_partition_count=0,
        missing_source_column_count=0,
        synthesized_null_column_count=0,
        source_view_count=1,
        source_column_count=10,
        source_row_count=1,
        source_frame_bytes=100,
        source_numeric_value_count=10,
        latest_source_column_count=10,
        historical_source_column_count=0,
        historical_source_row_count=0,
        source_load_durations_ms=[],
        source_merge_durations_ms=[],
        journal_aligned=True,
        durable_record_count=1,
        snapshot_count=1,
        snapshot_alarm_count=10,
        expected_snapshot_alarm_count=10,
        source_load_count=1,
        parameter_adoption_pressure=pressure,
    )
    assert report.integrity_ok is False
    assert report.result == 'FAIL'
    assert report.parameter_adoption_pressure is pressure


def test_e001_metrics_require_cache_promotion_without_target_revision_commits(
    tmp_path: Path,
) -> None:
    scenario = _e001_scenario(alarm_count=10)
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    assert runtime.target_revision is not None
    target_bundle = RuntimeRevisionBundle(
        manifest=RuntimeManifest(
            schema_version=RUNTIME_MANIFEST_SCHEMA_VERSION,
            alarm_configuration_revision='PERF-AC-2',
            tool_registry_revision='PERF-TR-1',
            published_at=datetime(2026, 8, 27, 12, 1, tzinfo=UTC),
        ),
        alarm_configuration={'revision': 'PERF-AC-2'},
        tool_registry={'revision': 'PERF-TR-1'},
    )
    runtime.job.revision_resolver.cache.replace_effective(bundle=target_bundle)
    source_snapshot = SimpleNamespace(
        as_document=lambda: {
            'state_basis': {
                'alarm_configuration_revision': 'PERF-AC-1',
                'tool_registry_revision': 'PERF-TR-1',
            },
            'alarms': {
                f'perf/alarm_{index + 1:05d}': {'occurrence': {'occurrence_id': f'O-{index + 1}'}}
                for index in range(10)
            },
        }
    )
    source_record = SimpleNamespace(
        record=SimpleNamespace(commit=SimpleNamespace(alarm_configuration_revision='PERF-AC-1'))
    )
    samples = (
        SimpleNamespace(
            iteration=13,
            duration_ms=900.0,
            cpu_percent=40.0,
            revision_origin='source_candidate',
            adoption_outcome='adopted',
            cycle_executed=True,
        ),
        SimpleNamespace(
            iteration=14,
            duration_ms=800.0,
            cpu_percent=40.0,
            revision_origin='cache_current',
            adoption_outcome='not_required',
            cycle_executed=True,
        ),
    )
    metrics = _build_parameter_adoption_pressure_metrics(
        scenario=scenario,
        runtime=runtime,
        records=(source_record,),
        snapshots=(source_snapshot,),
        samples=samples,
    )
    assert metrics is not None
    assert metrics.functional_integrity_ok is True
    assert metrics.compatible_change_count == 10
    assert metrics.target_revision_durable_record_count == 0
    assert metrics.effective_cache_revision == 'PERF-AC-2'
    assert metrics.adoption_cycle_executed is True
    assert metrics.target_state_basis_snapshot_count == 0


def _e002_scenario(*, alarm_count: int = 1000) -> BaselineScenario:
    return BaselineScenario(
        test_id='E-002',
        alarm_count=alarm_count,
        duration_seconds=210,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        columns_per_alarm=1,
        priority_group_size=10,
        physical_partition_count=36 if alarm_count >= 36 else 10,
        physical_partition_layout='balanced',
        initial_active_percent=100,
        signal_value=1.0,
        threshold=0.5,
        c2_routing_delay_seconds=(60, 120, 180),
        c2_routing_adoption_at_seconds=90,
        c2_routing_adoption_target_delay_seconds=(60, 90, 150),
    )


def test_e002_builds_single_run_c2_compatible_routing_target_revision(tmp_path: Path) -> None:
    scenario = _e002_scenario()
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )

    assert scenario.has_c2_routing_adoption_pressure is True
    assert scenario.has_c2_reschedule_pressure is False
    assert runtime.revision.alarm_configuration_revision == 'PERF-AC-1'
    assert runtime.target_revision is not None
    assert runtime.target_revision.alarm_configuration_revision == 'PERF-AC-2'
    source_delays = tuple(
        destination.delay_seconds
        for destination in runtime.revision.session.entries[0].planned_alarm.routing.destinations
    )
    target_delays = tuple(
        destination.delay_seconds
        for destination in runtime.target_revision.session.entries[
            0
        ].planned_alarm.routing.destinations
    )
    assert source_delays == (60, 120, 180)
    assert target_delays == (60, 90, 150)
    adoption = plan_configuration_adoption(runtime.revision, runtime.target_revision)
    assert adoption.is_adoptable is True
    assert len(adoption.changes) == 1000
    assert {change.disposition for change in adoption.changes} == {
        ConfigurationAdoptionDisposition.COMPATIBLE
    }


def test_e002_rejects_invalid_mixed_c2_routing_adoption_contract() -> None:
    base = dict(
        test_id='E-002',
        alarm_count=1000,
        duration_seconds=210,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        priority_group_size=10,
        physical_partition_count=36,
        physical_partition_layout='balanced',
        initial_active_percent=100,
        c2_routing_delay_seconds=(60, 120, 180),
        c2_routing_adoption_at_seconds=90,
        c2_routing_adoption_target_delay_seconds=(60, 90, 150),
    )
    cases = (
        (
            {'c2_routing_adoption_target_delay_seconds': ()},
            'C2 routing adoption requires target delays',
        ),
        (
            {'c2_routing_adoption_target_delay_seconds': (60, 90)},
            'C2 routing adoption must preserve the destination count',
        ),
        (
            {'c2_routing_adoption_target_delay_seconds': (60, 120, 180)},
            'C2 routing adoption must change at least one destination delay',
        ),
        (
            {'c2_routing_adoption_at_seconds': 95},
            'C2 routing adoption timing must align with data refresh',
        ),
        (
            {'c2_routing_adoption_at_seconds': 50},
            'C2 routing adoption must occur with both assigned and pending destinations',
        ),
        (
            {'c2_routing_adoption_target_delay_seconds': (60, 120, 150)},
            'C2 routing adoption requires at least one pending destination to become assigned',
        ),
        (
            {'c2_routing_adoption_target_delay_seconds': (60, 90, 180)},
            'C2 routing adoption requires at least one pending destination to be rescheduled',
        ),
    )
    for override, message in cases:
        with pytest.raises(ValueError, match=message):
            BaselineScenario(**(base | override))


def test_e002_report_integrity_includes_c2_routing_adoption_gate() -> None:
    recorder = PerformanceRecorder(iteration_period_seconds=5)
    pressure = C2RoutingAdoptionPressureMetrics(
        adoption_at_seconds=90,
        source_revision='PERF-AC-1',
        target_revision='PERF-AC-2',
        source_delay_seconds=(60, 120, 180),
        target_delay_seconds=(60, 90, 150),
        plan_change_count=10,
        compatible_change_count=10,
        unchanged_change_count=0,
        structural_reset_change_count=0,
        disabled_change_count=0,
        removed_change_count=0,
        rejected_change_count=0,
        target_runtime_alarm_count=10,
        expected_target_runtime_alarm_count=10,
        effective_cache_revision='PERF-AC-2',
        adoption_iteration_count=1,
        adoption_iteration=19,
        adoption_iteration_ms=1500.0,
        adoption_iteration_cpu_percent=60.0,
        adoption_cycle_executed=False,
        post_adoption_cache_current_iteration_count=2,
        adoption_commit_count=1,
        expected_adoption_commit_count=1,
        adoption_assigned_count=10,
        expected_adoption_assigned_count=10,
        adoption_rescheduled_count=10,
        expected_adoption_rescheduled_count=10,
        source_revision_durable_record_count=2,
        expected_source_revision_durable_record_count=2,
        target_revision_durable_record_count=2,
        expected_target_revision_durable_record_count=2,
        durable_record_count=4,
        expected_durable_record_count=4,
        source_state_basis_snapshot_count=0,
        target_state_basis_snapshot_count=1,
        final_assignment_count=40,
        expected_final_assignment_count=40,
        final_pending_assignment_count=0,
        open_occurrence_count=10,
        expected_open_occurrence_count=10,
        occurrence_identity_mismatch_count=0,
        functional_integrity_ok=False,
    )
    report = recorder.build_report(
        test_id='E-002',
        alarm_count=10,
        planned_duration_seconds=210,
        actual_duration_seconds=210,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        columns_per_alarm=1,
        historical_series_per_alarm=0,
        historical_window_minutes=0,
        historical_step_seconds=0,
        historical_points_per_series=0,
        historical_value_count=0,
        physical_partition_count=1,
        physical_partition_column_counts=(10,),
        physical_partition_layout='balanced',
        empty_physical_partition_count=0,
        missing_source_column_count=0,
        synthesized_null_column_count=0,
        source_view_count=1,
        source_column_count=10,
        source_row_count=1,
        source_frame_bytes=100,
        source_numeric_value_count=10,
        latest_source_column_count=10,
        historical_source_column_count=0,
        historical_source_row_count=0,
        source_load_durations_ms=[],
        source_merge_durations_ms=[],
        journal_aligned=True,
        durable_record_count=4,
        snapshot_count=1,
        snapshot_alarm_count=10,
        expected_snapshot_alarm_count=10,
        source_load_count=1,
        c2_routing_adoption_pressure=pressure,
    )
    assert report.integrity_ok is False
    assert report.result == 'FAIL'
    assert report.c2_routing_adoption_pressure is pressure


def test_e002_metrics_require_mixed_reconcile_and_exact_revision_geometry(tmp_path: Path) -> None:
    scenario = _e002_scenario(alarm_count=10)
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    assert runtime.target_revision is not None
    target_bundle = RuntimeRevisionBundle(
        manifest=RuntimeManifest(
            schema_version=RUNTIME_MANIFEST_SCHEMA_VERSION,
            alarm_configuration_revision='PERF-AC-2',
            tool_registry_revision='PERF-TR-1',
            published_at=datetime(2026, 8, 27, 12, 1, 30, tzinfo=UTC),
        ),
        alarm_configuration={'revision': 'PERF-AC-2'},
        tool_registry={'revision': 'PERF-TR-1'},
    )
    runtime.job.revision_resolver.cache.replace_effective(bundle=target_bundle)

    occurrence_ids = {f'perf/alarm_{index + 1:05d}': f'O-{index + 1}' for index in range(10)}
    bootstrap_occurrences = [
        {'kind': 'STARTED', 'alarm_key': alarm_key, 'occurrence_id': occurrence_id}
        for alarm_key, occurrence_id in occurrence_ids.items()
    ]
    first_wave = [
        {
            'kind': 'ASSIGNED',
            'alarm_key': alarm_key,
            'occurrence_id': occurrence_id,
            'tool_key': 'perf-route-01',
        }
        for alarm_key, occurrence_id in occurrence_ids.items()
    ]
    adoption_changes = []
    for alarm_key, occurrence_id in occurrence_ids.items():
        adoption_changes.extend(
            (
                {
                    'kind': 'ASSIGNED',
                    'alarm_key': alarm_key,
                    'occurrence_id': occurrence_id,
                    'tool_key': 'perf-route-02',
                },
                {
                    'kind': 'RESCHEDULED',
                    'alarm_key': alarm_key,
                    'occurrence_id': occurrence_id,
                    'tool_key': 'perf-route-03',
                    'due_at': '2026-08-27T12:02:30Z',
                },
            )
        )
    final_wave = [
        {
            'kind': 'ASSIGNED',
            'alarm_key': alarm_key,
            'occurrence_id': occurrence_id,
            'tool_key': 'perf-route-03',
        }
        for alarm_key, occurrence_id in occurrence_ids.items()
    ]

    def record(revision: str, records: dict[str, object]) -> SimpleNamespace:
        return SimpleNamespace(
            record=SimpleNamespace(
                commit=SimpleNamespace(alarm_configuration_revision=revision),
                records=records,
            )
        )

    records = (
        record(
            'PERF-AC-1', {'occurrence_changes': bootstrap_occurrences, 'assignment_changes': []}
        ),
        record('PERF-AC-1', {'occurrence_changes': [], 'assignment_changes': first_wave}),
        record('PERF-AC-2', {'occurrence_changes': [], 'assignment_changes': adoption_changes}),
        record('PERF-AC-2', {'occurrence_changes': [], 'assignment_changes': final_wave}),
    )
    final_snapshot = SimpleNamespace(
        as_document=lambda: {
            'state_basis': {
                'alarm_configuration_revision': 'PERF-AC-2',
                'tool_registry_revision': 'PERF-TR-1',
            },
            'alarms': {
                alarm_key: {
                    'occurrence': {
                        'occurrence_id': occurrence_id,
                        'assignments': [{}, {}, {}, {}],
                        'pending_assignments': [],
                    }
                }
                for alarm_key, occurrence_id in occurrence_ids.items()
            },
        }
    )
    samples = (
        SimpleNamespace(
            iteration=19,
            duration_ms=1500.0,
            cpu_percent=60.0,
            revision_origin='source_candidate',
            adoption_outcome='adopted',
            cycle_executed=False,
        ),
        SimpleNamespace(
            iteration=20,
            duration_ms=900.0,
            cpu_percent=50.0,
            revision_origin='cache_current',
            adoption_outcome='not_required',
            cycle_executed=True,
        ),
    )
    metrics = _build_c2_routing_adoption_pressure_metrics(
        scenario=scenario,
        runtime=runtime,
        records=records,
        snapshots=(final_snapshot,),
        samples=samples,
    )
    assert metrics is not None
    assert metrics.functional_integrity_ok is True
    assert metrics.compatible_change_count == 10
    assert metrics.adoption_commit_count == 1
    assert metrics.adoption_assigned_count == 10
    assert metrics.adoption_rescheduled_count == 10
    assert metrics.source_revision_durable_record_count == 2
    assert metrics.target_revision_durable_record_count == 2
    assert metrics.final_assignment_count == 40
    assert metrics.final_pending_assignment_count == 0
    assert metrics.occurrence_identity_mismatch_count == 0


def _e003_scenario(*, alarm_count: int = 1000) -> BaselineScenario:
    return BaselineScenario(
        test_id='E-003',
        alarm_count=alarm_count,
        duration_seconds=180,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        columns_per_alarm=1,
        priority_group_size=10,
        physical_partition_count=36 if alarm_count >= 36 else 10,
        physical_partition_layout='balanced',
        initial_active_percent=100,
        signal_value=1.0,
        threshold=0.5,
        disabled_adoption_at_seconds=60,
        disabled_alarm_percent=10,
    )


def test_e003_builds_disabled_target_that_remains_defined_but_not_executable(
    tmp_path: Path,
) -> None:
    scenario = _e003_scenario()
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )

    assert scenario.has_disabled_adoption_pressure is True
    assert scenario.disabled_alarm_count == 100
    assert runtime.target_revision is not None
    assert len(runtime.revision.session.identities) == 1000
    assert len(runtime.target_revision.defined_alarm_identities) == 1000
    assert len(runtime.target_revision.session.identities) == 900
    disabled = set(runtime.target_revision.defined_alarm_identities) - set(
        runtime.target_revision.session.identities
    )
    assert len(disabled) == 100
    assert {identity.alarm_key for identity in disabled} == {
        f'alarm_{index:05d}' for index in range(1, 1000, 10)
    }
    adoption = plan_configuration_adoption(runtime.revision, runtime.target_revision)
    counts = {item: 0 for item in ConfigurationAdoptionDisposition}
    for change in adoption.changes:
        counts[change.disposition] += 1
    assert adoption.is_adoptable is True
    assert counts[ConfigurationAdoptionDisposition.DISABLED] == 100
    assert counts[ConfigurationAdoptionDisposition.UNCHANGED] == 900
    assert counts[ConfigurationAdoptionDisposition.COMPATIBLE] == 0
    assert counts[ConfigurationAdoptionDisposition.REMOVED] == 0


def test_e003_rejects_invalid_disabled_adoption_contract() -> None:
    base = dict(
        test_id='E-003',
        alarm_count=1000,
        duration_seconds=180,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        priority_group_size=10,
        physical_partition_count=36,
        physical_partition_layout='balanced',
        initial_active_percent=100,
        disabled_adoption_at_seconds=60,
        disabled_alarm_percent=10,
    )
    cases = (
        ({'disabled_alarm_percent': 0}, 'disabled adoption requires disabled_alarm_percent > 0'),
        ({'disabled_adoption_at_seconds': 0}, 'disabled_alarm_percent requires'),
        ({'disabled_alarm_percent': 20}, 'exactly one disabled alarm per priority group'),
        (
            {'disabled_adoption_at_seconds': 65},
            'disabled adoption timing must align with data refresh',
        ),
        ({'duration_seconds': 70}, 'disabled adoption duration must extend beyond adoption'),
        (
            {'parameter_adoption_at_seconds': 60, 'parameter_target_threshold': 0.75},
            'must not combine with another configuration adoption pressure',
        ),
    )
    for override, message in cases:
        with pytest.raises(ValueError, match=message):
            BaselineScenario(**(base | override))


def test_e003_report_integrity_includes_disabled_adoption_gate() -> None:
    recorder = PerformanceRecorder(iteration_period_seconds=5)
    pressure = DisabledAdoptionPressureMetrics(
        adoption_at_seconds=60,
        disabled_alarm_percent=10,
        source_revision='PERF-AC-1',
        target_revision='PERF-AC-2',
        plan_change_count=10,
        compatible_change_count=0,
        unchanged_change_count=9,
        structural_reset_change_count=0,
        disabled_change_count=1,
        expected_disabled_change_count=1,
        removed_change_count=0,
        rejected_change_count=0,
        target_defined_alarm_count=10,
        expected_target_defined_alarm_count=10,
        target_runtime_alarm_count=9,
        expected_target_runtime_alarm_count=9,
        effective_cache_revision='PERF-AC-2',
        adoption_iteration_count=1,
        adoption_iteration=13,
        adoption_iteration_ms=1200.0,
        adoption_iteration_cpu_percent=50.0,
        adoption_cycle_executed=False,
        post_adoption_cache_current_iteration_count=2,
        adoption_commit_count=1,
        expected_adoption_commit_count=1,
        configuration_disabled_occurrence_count=1,
        expected_configuration_disabled_occurrence_count=1,
        configuration_removed_occurrence_count=0,
        occurrence_identity_mismatch_count=0,
        source_revision_durable_record_count=1,
        expected_source_revision_durable_record_count=1,
        target_revision_durable_record_count=1,
        expected_target_revision_durable_record_count=1,
        durable_record_count=2,
        expected_durable_record_count=2,
        source_state_basis_snapshot_count=0,
        target_state_basis_snapshot_count=1,
        final_alarm_count=9,
        expected_final_alarm_count=9,
        final_assignment_count=9,
        expected_final_assignment_count=9,
        open_occurrence_count=9,
        expected_open_occurrence_count=9,
        open_episode_count=1,
        expected_open_episode_count=1,
        disabled_target_present_count=0,
        functional_integrity_ok=False,
    )
    report = recorder.build_report(
        test_id='E-003',
        alarm_count=10,
        planned_duration_seconds=180,
        actual_duration_seconds=180,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        columns_per_alarm=1,
        historical_series_per_alarm=0,
        historical_window_minutes=0,
        historical_step_seconds=0,
        historical_points_per_series=0,
        historical_value_count=0,
        physical_partition_count=1,
        physical_partition_column_counts=(10,),
        physical_partition_layout='balanced',
        empty_physical_partition_count=0,
        missing_source_column_count=0,
        synthesized_null_column_count=0,
        source_view_count=1,
        source_column_count=10,
        source_row_count=1,
        source_frame_bytes=100,
        source_numeric_value_count=10,
        latest_source_column_count=10,
        historical_source_column_count=0,
        historical_source_row_count=0,
        source_load_durations_ms=[],
        source_merge_durations_ms=[],
        journal_aligned=True,
        durable_record_count=2,
        snapshot_count=1,
        snapshot_alarm_count=9,
        expected_snapshot_alarm_count=9,
        source_load_count=1,
        disabled_adoption_pressure=pressure,
    )
    assert report.integrity_ok is False
    assert report.result == 'FAIL'
    assert report.disabled_adoption_pressure is pressure


def test_e003_metrics_require_exact_disabled_closure_geometry(tmp_path: Path) -> None:
    scenario = _e003_scenario(alarm_count=10)
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    assert runtime.target_revision is not None
    target_bundle = RuntimeRevisionBundle(
        manifest=RuntimeManifest(
            schema_version=RUNTIME_MANIFEST_SCHEMA_VERSION,
            alarm_configuration_revision='PERF-AC-2',
            tool_registry_revision='PERF-TR-1',
            published_at=datetime(2026, 8, 28, 12, 1, tzinfo=UTC),
        ),
        alarm_configuration={'revision': 'PERF-AC-2'},
        tool_registry={'revision': 'PERF-TR-1'},
    )
    runtime.job.revision_resolver.cache.replace_effective(bundle=target_bundle)

    starts = [
        {
            'kind': 'STARTED',
            'alarm_key': f'perf/alarm_{index + 1:05d}',
            'occurrence_id': f'O-{index + 1}',
        }
        for index in range(10)
    ]
    closed = [
        {
            'kind': 'CLOSED',
            'alarm_key': 'perf/alarm_00001',
            'occurrence_id': 'O-1',
            'closure_reason': 'configuration_disabled',
        }
    ]

    def record(revision: str, occurrence_changes: list[dict[str, object]]) -> SimpleNamespace:
        return SimpleNamespace(
            record=SimpleNamespace(
                commit=SimpleNamespace(alarm_configuration_revision=revision),
                records={'occurrence_changes': occurrence_changes},
            )
        )

    records = (
        record('PERF-AC-1', starts),
        record('PERF-AC-2', closed),
    )
    final_snapshot = SimpleNamespace(
        as_document=lambda: {
            'state_basis': {
                'alarm_configuration_revision': 'PERF-AC-2',
                'tool_registry_revision': 'PERF-TR-1',
            },
            'episode': {'episode_id': 'E-1'},
            'alarms': {
                f'perf/alarm_{index + 1:05d}': {
                    'occurrence': {
                        'occurrence_id': f'O-{index + 1}',
                        'assignments': [{}],
                        'pending_assignments': [],
                    }
                }
                for index in range(1, 10)
            },
        }
    )
    samples = (
        SimpleNamespace(
            iteration=13,
            duration_ms=1200.0,
            cpu_percent=50.0,
            revision_origin='source_candidate',
            adoption_outcome='adopted',
            cycle_executed=False,
        ),
        SimpleNamespace(
            iteration=14,
            duration_ms=900.0,
            cpu_percent=45.0,
            revision_origin='cache_current',
            adoption_outcome='not_required',
            cycle_executed=True,
        ),
    )
    metrics = _build_disabled_adoption_pressure_metrics(
        scenario=scenario,
        runtime=runtime,
        records=records,
        snapshots=(final_snapshot,),
        samples=samples,
    )
    assert metrics is not None
    assert metrics.functional_integrity_ok is True
    assert metrics.disabled_change_count == 1
    assert metrics.unchanged_change_count == 9
    assert metrics.configuration_disabled_occurrence_count == 1
    assert metrics.configuration_removed_occurrence_count == 0
    assert metrics.target_defined_alarm_count == 10
    assert metrics.target_runtime_alarm_count == 9
    assert metrics.durable_record_count == 2
    assert metrics.final_alarm_count == 9
    assert metrics.open_occurrence_count == 9
    assert metrics.open_episode_count == 1
    assert metrics.disabled_target_present_count == 0


def test_stale_target_scenario_routes_to_single_execution_not_two_phase() -> None:
    stale_target = BaselineScenario(
        test_id='D-010',
        alarm_count=1000,
        duration_seconds=180,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        physical_partition_count=36,
        physical_partition_layout='balanced',
        priority_group_size=10,
        management_action_at_seconds=30,
        management_action_count=100,
        management_action_interval_seconds=0,
        deactivation_target_removal_at_seconds=60,
        deactivation_decision_at_seconds=90,
        deactivation_decision_count=100,
        deactivation_decision_interval_seconds=0,
        deactivation_window_seconds=600,
        initial_active_percent=100,
    )
    two_phase = BaselineScenario(
        test_id='D-007',
        alarm_count=1000,
        duration_seconds=240,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        physical_partition_count=36,
        physical_partition_layout='balanced',
        priority_group_size=10,
        management_action_at_seconds=30,
        management_action_count=50,
        management_action_interval_seconds=0,
        deactivation_decision_at_seconds=30,
        deactivation_decision_count=50,
        deactivation_decision_interval_seconds=0,
        deactivation_window_seconds=600,
        initial_active_percent=100,
    )

    assert stale_target.has_multi_deactivation_decision_pressure is True
    assert stale_target.has_stale_target_deactivation_pressure is True
    assert _uses_two_phase_deactivation_runner(stale_target) is False
    assert _uses_two_phase_deactivation_runner(two_phase) is True


def test_stale_target_scenario_builds_removed_target_revision_and_burst_source(
    tmp_path: Path,
) -> None:
    scenario = BaselineScenario(
        test_id='D-010',
        alarm_count=1000,
        duration_seconds=180,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        priority_group_size=10,
        management_action_at_seconds=30,
        management_action_count=100,
        management_action_interval_seconds=0,
        deactivation_target_removal_at_seconds=60,
        deactivation_decision_at_seconds=90,
        deactivation_decision_count=100,
        deactivation_decision_interval_seconds=0,
        deactivation_window_seconds=600,
        initial_active_percent=100,
        physical_partition_count=36,
        physical_partition_layout='balanced',
    )
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )

    assert scenario.has_stale_target_deactivation_pressure is True
    assert scenario.has_mixed_deactivation_pressure is False
    assert scenario.deactivation_decision_arrival_mode == 'stale-target'
    assert scenario.expected_snapshot_alarm_count(missing_source_columns=()) == 900
    assert isinstance(runtime.input_source, StaleTargetDeactivationInputSource)
    assert runtime.target_revision is not None
    assert len(runtime.revision.session.entries) == 1000
    assert len(runtime.target_revision.session.entries) == 900
    source = runtime.input_source
    targets = [source.target_for_input(index) for index in range(100)]
    assert targets[0][0].alarm_key == 'alarm_00001'
    assert targets[-1][0].alarm_key == 'alarm_00991'
    assert len({priority_group for _identity, priority_group in targets}) == 100
    assert all(not runtime.target_revision.is_defined(identity) for identity, _group in targets)


def test_stale_target_scenario_rejects_invalid_timing_or_shape() -> None:
    base = dict(
        test_id='D-010',
        alarm_count=100,
        duration_seconds=120,
        data_refresh_seconds=5,
        data_profile='latest-narrow',
        priority_group_size=10,
        management_action_at_seconds=10,
        management_action_count=10,
        management_action_interval_seconds=0,
        deactivation_target_removal_at_seconds=20,
        deactivation_decision_at_seconds=30,
        deactivation_decision_count=10,
        deactivation_decision_interval_seconds=0,
        deactivation_window_seconds=60,
        initial_active_percent=100,
        physical_partition_count=10,
        physical_partition_layout='balanced',
    )
    assert BaselineScenario(**base).has_stale_target_deactivation_pressure is True
    with pytest.raises(
        ValueError, match='removal must occur after request setup and before decisions'
    ):
        BaselineScenario(**{**base, 'deactivation_target_removal_at_seconds': 35})
    with pytest.raises(ValueError, match='request interval zero'):
        BaselineScenario(**{**base, 'management_action_interval_seconds': 1})
    with pytest.raises(ValueError, match='decision interval zero'):
        BaselineScenario(**{**base, 'deactivation_decision_interval_seconds': 1})


def test_stale_target_source_exposes_each_burst_once_with_logical_time(
    tmp_path: Path,
    monkeypatch,
) -> None:
    scenario = BaselineScenario(
        test_id='D-010',
        alarm_count=20,
        duration_seconds=90,
        data_refresh_seconds=5,
        data_profile='latest-narrow',
        priority_group_size=5,
        management_action_at_seconds=10,
        management_action_count=4,
        management_action_interval_seconds=0,
        deactivation_target_removal_at_seconds=20,
        deactivation_decision_at_seconds=30,
        deactivation_decision_count=4,
        deactivation_decision_interval_seconds=0,
        deactivation_window_seconds=120,
        initial_active_percent=100,
        physical_partition_count=4,
        physical_partition_layout='balanced',
    )
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    source = runtime.input_source
    assert isinstance(source, StaleTargetDeactivationInputSource)
    source.started_monotonic = 100.0
    source.prepare_iteration(as_of=source.decision_decided_at)
    monkeypatch.setattr('performance.baseline.time.perf_counter', lambda: 130.5)

    decisions = source.read_after(stream=AlarmInputStream.DEACTIVATION_DECISION, cursor=None)

    assert len(decisions) == 4
    assert all(record.value.decided_at == source.decision_decided_at for record in decisions)
    assert source.decision_read_batch_sizes == [4]
    assert (
        source.read_after(
            stream=AlarmInputStream.DEACTIVATION_DECISION,
            cursor=decisions[-1].next_cursor,
        )
        == ()
    )


def test_stale_target_consumer_prepares_iteration_as_of_before_bootstrap_read(
    tmp_path: Path,
    monkeypatch,
) -> None:
    scenario = BaselineScenario(
        test_id='D-010',
        alarm_count=20,
        duration_seconds=90,
        data_refresh_seconds=5,
        data_profile='latest-narrow',
        priority_group_size=5,
        management_action_at_seconds=10,
        management_action_count=4,
        management_action_interval_seconds=0,
        deactivation_target_removal_at_seconds=20,
        deactivation_decision_at_seconds=30,
        deactivation_decision_count=4,
        deactivation_decision_interval_seconds=0,
        deactivation_window_seconds=120,
        initial_active_percent=100,
        physical_partition_count=4,
        physical_partition_layout='balanced',
    )
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    source = runtime.input_source
    consumer = runtime.input_consumer
    assert isinstance(source, StaleTargetDeactivationInputSource)
    assert isinstance(consumer, PerformanceAlarmDurableInputConsumer)
    assert source.iteration_as_of is None
    as_of = source.base_at + timedelta(seconds=1)
    delegated = []

    def fake_execute(self, context, *, cycle, iteration):
        assert self is consumer
        assert source.iteration_as_of == as_of
        delegated.append(True)
        return 'delegated'

    monkeypatch.setattr(
        'performance.baseline.AlarmDurableInputConsumer.execute',
        fake_execute,
    )

    result = consumer.execute(
        SimpleNamespace(),
        cycle=SimpleNamespace(),
        iteration=SimpleNamespace(as_of=as_of),
    )

    assert result == 'delegated'
    assert delegated == [True]


def test_stale_target_metrics_require_exact_removal_and_stale_receipts(tmp_path: Path) -> None:
    scenario = BaselineScenario(
        test_id='D-010',
        alarm_count=20,
        duration_seconds=90,
        data_refresh_seconds=5,
        data_profile='latest-narrow',
        priority_group_size=5,
        management_action_at_seconds=10,
        management_action_count=4,
        management_action_interval_seconds=0,
        deactivation_target_removal_at_seconds=20,
        deactivation_decision_at_seconds=30,
        deactivation_decision_count=4,
        deactivation_decision_interval_seconds=0,
        deactivation_window_seconds=120,
        initial_active_percent=100,
        physical_partition_count=4,
        physical_partition_layout='balanced',
    )
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    source = runtime.input_source
    consumer = runtime.input_consumer
    assert isinstance(source, StaleTargetDeactivationInputSource)

    occurrence_ids = {}
    for index in range(4):
        identity, _group = source.target_for_input(index)
        occurrence_ids[identity.canonical_key] = f'PERF-O-{index + 1:08d}'
    source.target_occurrence_ids.update(occurrence_ids)
    source.management_read_batch_sizes = [0, 4]
    source.decision_read_batch_sizes = [0, 4]
    source.management_visible_monotonic_by_input_id = {
        input_id: 100.0 for input_id in source.input_ids
    }
    source.decision_visible_monotonic_by_input_id = {
        decision_id: 200.0 for decision_id in source.decision_ids
    }
    consumer.stale_request_receipt_confirmed_monotonic_by_input_id = {
        input_id: 101.0 for input_id in source.input_ids
    }
    consumer.stale_decision_receipt_confirmed_monotonic_by_input_id = {
        decision_id: 201.0 for decision_id in source.decision_ids
    }
    consumer.stale_request_receipt_batch_sizes = [4]
    consumer.stale_decision_receipt_batch_sizes = [4]
    consumer.pending_request_high_water_count = 4

    records = []
    for group_index in range(scenario.priority_group_count):
        records.append(
            SimpleNamespace(
                record=SimpleNamespace(
                    commit=SimpleNamespace(priority_group=f'perf-group-{group_index + 1:03d}'),
                    records={
                        'input_receipts': [],
                        'deactivation_requests': [],
                        'management_effects': [],
                        'deactivation_effects': [],
                        'occurrence_changes': [],
                        'journey_events': [],
                    },
                )
            )
        )
    for index in range(4):
        identity, priority_group = source.target_for_input(index)
        alarm_key = identity.canonical_key
        input_id = source.input_ids[index]
        request_id = source.request_ids[index]
        decision_id = source.decision_ids[index]
        occurrence_id = occurrence_ids[alarm_key]
        records.append(
            SimpleNamespace(
                record=SimpleNamespace(
                    commit=SimpleNamespace(priority_group=priority_group),
                    records={
                        'input_receipts': [
                            {
                                'input_kind': 'DEACTIVATION_REQUEST',
                                'input_id': input_id,
                                'outcome': 'PENDING_APPROVAL',
                            }
                        ],
                        'deactivation_requests': [
                            {
                                'request_id': request_id,
                                'alarm_key': alarm_key,
                                'source_management_input_id': input_id,
                                'source_occurrence_id': occurrence_id,
                                'requested_at': source.request_created_at.isoformat().replace(
                                    '+00:00', 'Z'
                                ),
                                'effective_until': source.effective_until.isoformat().replace(
                                    '+00:00', 'Z'
                                ),
                                'approval_required': True,
                            }
                        ],
                        'management_effects': [
                            {
                                'alarm_key': alarm_key,
                                'effect_id': f'PERF-ME-{input_id}',
                                'kind': 'STARTED',
                            }
                        ],
                        'deactivation_effects': [],
                        'occurrence_changes': [],
                        'journey_events': [],
                    },
                )
            )
        )
        records.append(
            SimpleNamespace(
                record=SimpleNamespace(
                    commit=SimpleNamespace(priority_group=priority_group),
                    records={
                        'input_receipts': [],
                        'deactivation_requests': [],
                        'management_effects': [
                            {
                                'alarm_key': alarm_key,
                                'effect_id': f'PERF-ME-{input_id}',
                                'kind': 'CLEARED',
                            }
                        ],
                        'deactivation_effects': [],
                        'occurrence_changes': [
                            {
                                'alarm_key': alarm_key,
                                'kind': 'CLOSED',
                                'closure_reason': 'configuration_removed',
                            }
                        ],
                        'journey_events': [],
                    },
                )
            )
        )
        records.append(
            SimpleNamespace(
                record=SimpleNamespace(
                    commit=SimpleNamespace(priority_group=priority_group),
                    records={
                        'input_receipts': [
                            {
                                'input_kind': 'DEACTIVATION_DECISION',
                                'input_id': decision_id,
                                'outcome': 'STALE_TARGET',
                            }
                        ],
                        'deactivation_requests': [],
                        'management_effects': [],
                        'deactivation_effects': [],
                        'occurrence_changes': [],
                        'journey_events': [
                            {
                                'event_key': 'deactivation_decision_stale_target',
                                'alarm_key': alarm_key,
                                'occurrence_id': occurrence_id,
                            }
                        ],
                    },
                )
            )
        )
    snapshots = (
        SimpleNamespace(
            as_document=lambda: {
                'alarms': {
                    f'perf/alarm_{index + 1:05d}': {
                        'occurrence': {'occurrence_id': f'OTHER-{index}'}
                    }
                    for index in range(20)
                    if index not in {0, 5, 10, 15}
                }
            }
        ),
    )

    import performance.run as run_module

    final_state = {
        'management': {'cursor': {'byte_offset': 1024}, 'pending': []},
        'decisions': {'cursor': {'byte_offset': 1024}, 'pending': []},
        'pending_deactivation_request_ids': [],
    }
    original_store = run_module.AtomicJsonStore
    run_module.AtomicJsonStore = lambda **_kwargs: SimpleNamespace(read=lambda _path: final_state)
    try:
        metrics = _build_stale_target_deactivation_decision_pressure_metrics(
            scenario=scenario,
            runtime=runtime,
            records=tuple(records),
            snapshots=snapshots,
        )
        assert isinstance(metrics, StaleTargetDeactivationDecisionPressureMetrics)
        assert metrics.stale_target_receipt_count == 4
        assert metrics.applied_decision_receipt_count == 0
        assert metrics.configuration_removed_occurrence_count == 4
        assert metrics.management_effect_cleared_count == 4
        assert metrics.wrong_decision_request_correlation_count == 0
        assert metrics.stale_target_occurrence_mismatch_count == 0
        assert metrics.deactivation_effect_started_count == 0
        assert metrics.target_runtime_alarm_count == 16
        assert metrics.durable_record_count == 16
        assert metrics.expected_durable_record_count == 16
        assert metrics.functional_integrity_ok is True

        records[-1].record.records['input_receipts'][0]['outcome'] = 'APPLIED'
        invalid = _build_stale_target_deactivation_decision_pressure_metrics(
            scenario=scenario,
            runtime=runtime,
            records=tuple(records),
            snapshots=snapshots,
        )
        assert invalid.stale_target_receipt_count == 3
        assert invalid.functional_integrity_ok is False
    finally:
        run_module.AtomicJsonStore = original_store


def _e004_scenario(*, alarm_count: int = 1000) -> BaselineScenario:
    return BaselineScenario(
        test_id='E-004',
        alarm_count=alarm_count,
        duration_seconds=180,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        columns_per_alarm=1,
        priority_group_size=10,
        physical_partition_count=36 if alarm_count >= 36 else 10,
        physical_partition_layout='balanced',
        initial_active_percent=100,
        signal_value=1.0,
        threshold=0.5,
        management_action_at_seconds=20,
        management_action_count=alarm_count // 10,
        management_action_interval_seconds=0,
        deactivation_decision_at_seconds=30,
        deactivation_decision_count=alarm_count // 10,
        deactivation_decision_interval_seconds=0,
        deactivation_window_seconds=300,
        removed_adoption_at_seconds=60,
        removed_alarm_percent=10,
    )


def test_e004_builds_removed_target_and_pre_removal_deactivation_contract(
    tmp_path: Path,
) -> None:
    scenario = _e004_scenario()
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    assert scenario.has_removed_adoption_pressure is True
    assert scenario.removed_alarm_count == 100
    assert scenario.deactivation_decision_arrival_mode == 'pre-removal'
    assert isinstance(runtime.input_source, StaleTargetDeactivationInputSource)
    assert runtime.target_revision is not None
    assert len(runtime.revision.session.identities) == 1000
    assert len(runtime.target_revision.defined_alarm_identities) == 900
    assert len(runtime.target_revision.session.identities) == 900
    removed = set(runtime.revision.session.identities) - set(
        runtime.target_revision.defined_alarm_identities
    )
    assert {identity.alarm_key for identity in removed} == {
        f'alarm_{index:05d}' for index in range(1, 1000, 10)
    }
    assert {
        runtime.input_source.target_for_input(index)[0]
        for index in range(scenario.removed_alarm_count)
    } == removed
    adoption = plan_configuration_adoption(runtime.revision, runtime.target_revision)
    counts = {item: 0 for item in ConfigurationAdoptionDisposition}
    for change in adoption.changes:
        counts[change.disposition] += 1
    assert adoption.is_adoptable is True
    assert counts[ConfigurationAdoptionDisposition.UNCHANGED] == 900
    assert counts[ConfigurationAdoptionDisposition.REMOVED] == 100
    assert counts[ConfigurationAdoptionDisposition.DISABLED] == 0
    assert counts[ConfigurationAdoptionDisposition.COMPATIBLE] == 0


def test_e004_rejects_invalid_removed_adoption_contract() -> None:
    base = dict(
        test_id='E-004',
        alarm_count=1000,
        duration_seconds=180,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        priority_group_size=10,
        physical_partition_count=36,
        physical_partition_layout='balanced',
        initial_active_percent=100,
        management_action_at_seconds=20,
        management_action_count=100,
        management_action_interval_seconds=0,
        deactivation_decision_at_seconds=30,
        deactivation_decision_count=100,
        deactivation_decision_interval_seconds=0,
        deactivation_window_seconds=300,
        removed_adoption_at_seconds=60,
        removed_alarm_percent=10,
    )
    cases = (
        ({'removed_alarm_percent': 0}, 'removed adoption requires removed_alarm_percent > 0'),
        ({'removed_adoption_at_seconds': 0}, 'removed_alarm_percent requires'),
        (
            {
                'removed_alarm_percent': 20,
                'management_action_count': 200,
                'deactivation_decision_count': 200,
            },
            'exactly one removed alarm per priority group',
        ),
        (
            {'deactivation_decision_at_seconds': 70},
            'removed adoption requires request < decision < adoption',
        ),
        (
            {'deactivation_window_seconds': 100},
            'removed adoption deactivation window must remain active through the run',
        ),
        (
            {'management_action_count': 50},
            'removed adoption requires one management request per removed alarm',
        ),
        (
            {'deactivation_decision_count': 50},
            'removed adoption requires one decision per removed alarm',
        ),
        (
            {'management_action_interval_seconds': 1},
            'removed adoption request setup requires request interval zero',
        ),
        (
            {'disabled_adoption_at_seconds': 60, 'disabled_alarm_percent': 10},
            'must not combine with another configuration adoption pressure',
        ),
    )
    for override, message in cases:
        with pytest.raises(ValueError, match=message):
            BaselineScenario(**(base | override))


def test_e004_report_integrity_includes_removed_adoption_gate() -> None:
    recorder = PerformanceRecorder(iteration_period_seconds=5)
    pressure = RemovedAdoptionPressureMetrics(
        adoption_at_seconds=60,
        removed_alarm_percent=10,
        request_at_seconds=20,
        decision_at_seconds=30,
        deactivation_window_seconds=300,
        source_revision='PERF-AC-1',
        target_revision='PERF-AC-2',
        plan_change_count=10,
        compatible_change_count=0,
        unchanged_change_count=9,
        structural_reset_change_count=0,
        disabled_change_count=0,
        removed_change_count=1,
        expected_removed_change_count=1,
        rejected_change_count=0,
        target_defined_alarm_count=9,
        expected_target_defined_alarm_count=9,
        target_runtime_alarm_count=9,
        expected_target_runtime_alarm_count=9,
        effective_cache_revision='PERF-AC-2',
        adoption_iteration_count=1,
        adoption_iteration=13,
        adoption_iteration_ms=1200.0,
        adoption_iteration_cpu_percent=50.0,
        adoption_cycle_executed=False,
        post_adoption_cache_current_iteration_count=2,
        request_receipt_count=1,
        pending_approval_receipt_count=1,
        deactivation_request_count=1,
        management_effect_started_count=1,
        decision_receipt_count=1,
        applied_decision_receipt_count=1,
        deactivation_effect_started_count=1,
        deactivation_effect_cleared_count=0,
        adoption_commit_count=1,
        expected_adoption_commit_count=1,
        configuration_removed_occurrence_count=1,
        expected_configuration_removed_occurrence_count=1,
        configuration_disabled_occurrence_count=0,
        management_effect_cleared_count=1,
        occurrence_identity_mismatch_count=0,
        control_plane_correlation_mismatch_count=0,
        source_revision_durable_record_count=3,
        expected_source_revision_durable_record_count=3,
        target_revision_durable_record_count=1,
        expected_target_revision_durable_record_count=1,
        durable_record_count=4,
        expected_durable_record_count=4,
        source_state_basis_snapshot_count=0,
        target_state_basis_snapshot_count=1,
        final_alarm_state_count=10,
        expected_final_alarm_state_count=10,
        final_assignment_count=9,
        expected_final_assignment_count=9,
        open_occurrence_count=9,
        expected_open_occurrence_count=9,
        open_episode_count=1,
        expected_open_episode_count=1,
        orphan_deactivation_state_count=1,
        expected_orphan_deactivation_state_count=1,
        orphan_occurrence_count=0,
        orphan_management_effect_count=0,
        management_cursor_byte_offset=256,
        decision_cursor_byte_offset=256,
        management_pending_count=0,
        decision_pending_count=0,
        pending_deactivation_request_count=0,
        functional_integrity_ok=False,
    )
    report = recorder.build_report(
        test_id='E-004',
        alarm_count=10,
        planned_duration_seconds=180,
        actual_duration_seconds=180,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        columns_per_alarm=1,
        historical_series_per_alarm=0,
        historical_window_minutes=0,
        historical_step_seconds=0,
        historical_points_per_series=0,
        historical_value_count=0,
        physical_partition_count=1,
        physical_partition_column_counts=(10,),
        physical_partition_layout='balanced',
        empty_physical_partition_count=0,
        missing_source_column_count=0,
        synthesized_null_column_count=0,
        source_view_count=1,
        source_column_count=10,
        source_row_count=1,
        source_frame_bytes=100,
        source_numeric_value_count=10,
        latest_source_column_count=10,
        historical_source_column_count=0,
        historical_source_row_count=0,
        source_load_durations_ms=[],
        source_merge_durations_ms=[],
        journal_aligned=True,
        durable_record_count=4,
        snapshot_count=1,
        snapshot_alarm_count=10,
        expected_snapshot_alarm_count=10,
        source_load_count=1,
        removed_adoption_pressure=pressure,
    )
    assert report.integrity_ok is False
    assert report.result == 'FAIL'
    assert report.removed_adoption_pressure is pressure


def test_e004_metrics_require_removed_closure_and_orphan_deactivation_geometry(
    tmp_path: Path,
) -> None:
    scenario = _e004_scenario(alarm_count=10)
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    source = runtime.input_source
    assert isinstance(source, StaleTargetDeactivationInputSource)
    assert runtime.target_revision is not None
    target_bundle = RuntimeRevisionBundle(
        manifest=RuntimeManifest(
            schema_version=RUNTIME_MANIFEST_SCHEMA_VERSION,
            alarm_configuration_revision='PERF-AC-2',
            tool_registry_revision='PERF-TR-1',
            published_at=datetime(2026, 8, 28, 12, 1, tzinfo=UTC),
        ),
        alarm_configuration={'revision': 'PERF-AC-2'},
        tool_registry={'revision': 'PERF-TR-1'},
    )
    runtime.job.revision_resolver.cache.replace_effective(bundle=target_bundle)
    identity, priority_group = source.target_for_input(0)
    alarm_key = identity.canonical_key
    input_id = source.input_ids[0]
    request_id = source.request_ids[0]
    decision_id = source.decision_ids[0]
    occurrence_id = 'O-1'
    source.target_occurrence_ids[alarm_key] = occurrence_id

    starts = [
        {
            'kind': 'STARTED',
            'alarm_key': f'perf/alarm_{index + 1:05d}',
            'occurrence_id': f'O-{index + 1}',
        }
        for index in range(10)
    ]
    request_record = {
        'input_receipts': [
            {
                'input_kind': 'DEACTIVATION_REQUEST',
                'input_id': input_id,
                'outcome': 'PENDING_APPROVAL',
            }
        ],
        'deactivation_requests': [
            {
                'request_id': request_id,
                'alarm_key': alarm_key,
                'source_management_input_id': input_id,
                'source_occurrence_id': occurrence_id,
                'approval_required': True,
            }
        ],
        'management_effects': [
            {'kind': 'STARTED', 'alarm_key': alarm_key, 'effect_id': f'PERF-ME-{input_id}'}
        ],
        'deactivation_effects': [],
        'occurrence_changes': [],
    }
    decision_record = {
        'input_receipts': [
            {'input_kind': 'DEACTIVATION_DECISION', 'input_id': decision_id, 'outcome': 'APPLIED'}
        ],
        'deactivation_requests': [],
        'management_effects': [],
        'deactivation_effects': [
            {'kind': 'STARTED', 'alarm_key': alarm_key, 'effect_id': f'PERF-DE-{request_id}'}
        ],
        'occurrence_changes': [],
    }
    adoption_record = {
        'input_receipts': [],
        'deactivation_requests': [],
        'management_effects': [
            {'kind': 'CLEARED', 'alarm_key': alarm_key, 'effect_id': f'PERF-ME-{input_id}'}
        ],
        'deactivation_effects': [],
        'occurrence_changes': [
            {
                'kind': 'CLOSED',
                'alarm_key': alarm_key,
                'occurrence_id': occurrence_id,
                'closure_reason': 'configuration_removed',
            }
        ],
    }

    def record(revision: str, payload: dict[str, object]) -> SimpleNamespace:
        return SimpleNamespace(
            record=SimpleNamespace(
                commit=SimpleNamespace(
                    alarm_configuration_revision=revision, priority_group=priority_group
                ),
                records=payload,
            )
        )

    records = (
        record(
            'PERF-AC-1',
            {
                'occurrence_changes': starts,
                'input_receipts': [],
                'deactivation_requests': [],
                'management_effects': [],
                'deactivation_effects': [],
            },
        ),
        record('PERF-AC-1', request_record),
        record('PERF-AC-1', decision_record),
        record('PERF-AC-2', adoption_record),
    )
    alarms = {
        f'perf/alarm_{index + 1:05d}': {
            'occurrence': {'occurrence_id': f'O-{index + 1}', 'assignments': [{}]},
            'management_effect': None,
            'deactivation_effect': None,
        }
        for index in range(1, 10)
    }
    alarms[alarm_key] = {
        'occurrence': None,
        'management_effect': None,
        'deactivation_effect': {'effect_id': f'PERF-DE-{request_id}'},
    }
    snapshot = SimpleNamespace(
        as_document=lambda: {
            'state_basis': {
                'alarm_configuration_revision': 'PERF-AC-2',
                'tool_registry_revision': 'PERF-TR-1',
            },
            'episode': {'episode_id': 'E-1'},
            'alarms': alarms,
        }
    )
    samples = (
        SimpleNamespace(
            iteration=13,
            duration_ms=1200.0,
            cpu_percent=50.0,
            revision_origin='source_candidate',
            adoption_outcome='adopted',
            cycle_executed=False,
        ),
        SimpleNamespace(
            iteration=14,
            duration_ms=900.0,
            cpu_percent=45.0,
            revision_origin='cache_current',
            adoption_outcome='not_required',
            cycle_executed=True,
        ),
    )
    import performance.run as run_module

    final_state = {
        'management': {'cursor': {'byte_offset': 256}, 'pending': []},
        'decisions': {'cursor': {'byte_offset': 256}, 'pending': []},
        'pending_deactivation_request_ids': [],
    }
    original_store = run_module.AtomicJsonStore
    run_module.AtomicJsonStore = lambda **_kwargs: SimpleNamespace(read=lambda _path: final_state)
    try:
        metrics = _build_removed_adoption_pressure_metrics(
            scenario=scenario,
            runtime=runtime,
            records=records,
            snapshots=(snapshot,),
            samples=samples,
        )
    finally:
        run_module.AtomicJsonStore = original_store
    assert metrics is not None
    assert metrics.functional_integrity_ok is True
    assert metrics.unchanged_change_count == 9
    assert metrics.removed_change_count == 1
    assert metrics.disabled_change_count == 0
    assert metrics.request_receipt_count == 1
    assert metrics.applied_decision_receipt_count == 1
    assert metrics.deactivation_effect_started_count == 1
    assert metrics.deactivation_effect_cleared_count == 0
    assert metrics.configuration_removed_occurrence_count == 1
    assert metrics.configuration_disabled_occurrence_count == 0
    assert metrics.management_effect_cleared_count == 1
    assert metrics.orphan_deactivation_state_count == 1
    assert metrics.orphan_occurrence_count == 0
    assert metrics.orphan_management_effect_count == 0
    assert metrics.durable_record_count == 4


def _e005_scenario(*, alarm_count: int = 1000) -> BaselineScenario:
    return BaselineScenario(
        test_id='E-005',
        alarm_count=alarm_count,
        duration_seconds=180,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        columns_per_alarm=1,
        priority_group_size=10,
        physical_partition_count=36 if alarm_count >= 36 else alarm_count,
        physical_partition_layout='balanced',
        initial_active_percent=100,
        signal_value=1.0,
        threshold=0.5,
        structural_reset_adoption_at_seconds=60,
        structural_reset_alarm_percent=5 if alarm_count == 1000 else 50,
    )


def test_e005_builds_five_complete_structural_reset_groups(tmp_path: Path) -> None:
    scenario = _e005_scenario()
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    assert scenario.has_structural_reset_adoption_pressure is True
    assert scenario.structural_reset_alarm_count == 50
    assert scenario.structural_reset_priority_group_count == 5
    assert runtime.target_revision is not None

    adoption = plan_configuration_adoption(runtime.revision, runtime.target_revision)
    counts = {item: 0 for item in ConfigurationAdoptionDisposition}
    for change in adoption.changes:
        counts[change.disposition] += 1

    assert adoption.is_adoptable is True
    assert counts[ConfigurationAdoptionDisposition.UNCHANGED] == 950
    assert counts[ConfigurationAdoptionDisposition.STRUCTURAL_RESET] == 50
    assert counts[ConfigurationAdoptionDisposition.COMPATIBLE] == 0
    assert counts[ConfigurationAdoptionDisposition.DISABLED] == 0
    assert counts[ConfigurationAdoptionDisposition.REMOVED] == 0
    assert counts[ConfigurationAdoptionDisposition.REJECTED] == 0
    assert adoption.structural_reset_groups == tuple(
        f'perf-group-{index:03d}' for index in range(1, 6)
    )

    source_by_key = {
        entry.planned_alarm.identity.canonical_key: entry.planned_alarm
        for entry in runtime.revision.session.entries
    }
    target_by_key = {
        entry.planned_alarm.identity.canonical_key: entry.planned_alarm
        for entry in runtime.target_revision.session.entries
    }
    changed = sorted(
        key
        for key, source_plan in source_by_key.items()
        if source_plan.criticality is not target_by_key[key].criticality
    )
    assert changed == [f'perf/alarm_{index:05d}' for index in range(1, 51)]
    assert all(target_by_key[key].criticality is Criticality.C2 for key in changed)
    assert all(
        target_by_key[f'perf/alarm_{index:05d}'].criticality is Criticality.C3
        for index in range(51, 1001)
    )


def test_e005_rejects_invalid_structural_reset_contract() -> None:
    base = dict(
        test_id='E-005',
        alarm_count=1000,
        duration_seconds=180,
        iteration_period_seconds=5,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        priority_group_size=10,
        physical_partition_count=36,
        physical_partition_layout='balanced',
        initial_active_percent=100,
        structural_reset_adoption_at_seconds=60,
        structural_reset_alarm_percent=5,
    )
    cases = (
        (
            {'structural_reset_alarm_percent': 0},
            'structural reset adoption requires structural_reset_alarm_percent > 0',
        ),
        (
            {'structural_reset_adoption_at_seconds': 0},
            'structural_reset_alarm_percent requires',
        ),
        (
            {
                'alarm_count': 100,
                'priority_group_size': 20,
                'physical_partition_count': 20,
                'structural_reset_alarm_percent': 10,
            },
            'structural reset adoption must cover complete priority groups',
        ),
        (
            {'structural_reset_adoption_at_seconds': 65},
            'structural reset adoption timing must align with data refresh',
        ),
        (
            {'duration_seconds': 65},
            'structural reset adoption duration must include the next iteration',
        ),
        (
            {'c1_routing_destination_count': 1},
            'structural reset adoption requires source C3 without routing pressure',
        ),
        (
            {'disabled_adoption_at_seconds': 60, 'disabled_alarm_percent': 10},
            'structural reset adoption must not combine with another pressure',
        ),
    )
    for override, message in cases:
        with pytest.raises(ValueError, match=message):
            BaselineScenario(**(base | override))


def test_e005_report_integrity_includes_structural_reset_gate() -> None:
    recorder = PerformanceRecorder(iteration_period_seconds=5)
    pressure = StructuralResetAdoptionPressureMetrics(
        adoption_at_seconds=60,
        structural_reset_alarm_percent=50,
        source_revision='PERF-AC-1',
        target_revision='PERF-AC-2',
        plan_change_count=20,
        compatible_change_count=0,
        unchanged_change_count=10,
        structural_reset_change_count=10,
        expected_structural_reset_change_count=10,
        structural_reset_group_count=1,
        expected_structural_reset_group_count=1,
        disabled_change_count=0,
        removed_change_count=0,
        rejected_change_count=0,
        effective_cache_revision='PERF-AC-2',
        adoption_iteration_count=1,
        adoption_iteration=13,
        adoption_iteration_ms=800.0,
        adoption_iteration_cpu_percent=50.0,
        adoption_cycle_executed=False,
        immediate_next_iteration=14,
        immediate_next_iteration_cycle_executed=True,
        immediate_next_iteration_cache_current=True,
        adoption_commit_count=1,
        expected_adoption_commit_count=1,
        next_cycle_commit_count=1,
        expected_next_cycle_commit_count=1,
        configuration_reconfigured_occurrence_count=10,
        expected_configuration_reconfigured_occurrence_count=10,
        configuration_terminated_episode_count=1,
        expected_configuration_terminated_episode_count=1,
        restarted_occurrence_count=10,
        expected_restarted_occurrence_count=10,
        restarted_episode_count=1,
        expected_restarted_episode_count=1,
        occurrence_identity_reuse_count=0,
        source_revision_durable_record_count=2,
        expected_source_revision_durable_record_count=2,
        target_revision_durable_record_count=2,
        expected_target_revision_durable_record_count=2,
        durable_record_count=4,
        expected_durable_record_count=4,
        source_state_basis_snapshot_count=1,
        expected_source_state_basis_snapshot_count=1,
        target_state_basis_snapshot_count=1,
        expected_target_state_basis_snapshot_count=1,
        final_alarm_count=20,
        expected_final_alarm_count=20,
        final_assignment_count=20,
        expected_final_assignment_count=20,
        open_occurrence_count=20,
        expected_open_occurrence_count=20,
        open_episode_count=2,
        expected_open_episode_count=2,
        functional_integrity_ok=False,
    )
    report = recorder.build_report(
        test_id='E-005',
        alarm_count=20,
        planned_duration_seconds=180,
        actual_duration_seconds=180,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        columns_per_alarm=1,
        historical_series_per_alarm=0,
        historical_window_minutes=0,
        historical_step_seconds=0,
        historical_points_per_series=0,
        historical_value_count=0,
        physical_partition_count=1,
        physical_partition_column_counts=(20,),
        physical_partition_layout='balanced',
        empty_physical_partition_count=0,
        missing_source_column_count=0,
        synthesized_null_column_count=0,
        source_view_count=1,
        source_column_count=20,
        source_row_count=1,
        source_frame_bytes=100,
        source_numeric_value_count=20,
        latest_source_column_count=20,
        historical_source_column_count=0,
        historical_source_row_count=0,
        source_load_durations_ms=[],
        source_merge_durations_ms=[],
        journal_aligned=True,
        durable_record_count=4,
        snapshot_count=2,
        snapshot_alarm_count=20,
        expected_snapshot_alarm_count=20,
        source_load_count=1,
        structural_reset_adoption_pressure=pressure,
    )
    assert report.integrity_ok is False
    assert report.result == 'FAIL'
    assert report.structural_reset_adoption_pressure is pressure


def test_e005_metrics_require_immediate_next_iteration_epoch_restart(
    tmp_path: Path,
) -> None:
    scenario = _e005_scenario(alarm_count=20)
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    assert runtime.target_revision is not None
    runtime.job.revision_resolver.cache.replace_effective(
        bundle=RuntimeRevisionBundle(
            manifest=RuntimeManifest(
                schema_version=RUNTIME_MANIFEST_SCHEMA_VERSION,
                alarm_configuration_revision='PERF-AC-2',
                tool_registry_revision='PERF-TR-1',
                published_at=datetime(2026, 8, 28, 12, 1, tzinfo=UTC),
            ),
            alarm_configuration={'revision': 'PERF-AC-2'},
            tool_registry={'revision': 'PERF-TR-1'},
        )
    )

    def record(
        revision: str,
        priority_group: str,
        occurrence_changes: list[dict[str, object]],
        episode_changes: list[dict[str, object]],
    ) -> SimpleNamespace:
        return SimpleNamespace(
            record=SimpleNamespace(
                commit=SimpleNamespace(
                    alarm_configuration_revision=revision,
                    priority_group=priority_group,
                ),
                records={
                    'occurrence_changes': occurrence_changes,
                    'episode_changes': episode_changes,
                },
            )
        )

    group_1_initial = [
        {
            'kind': 'STARTED',
            'alarm_key': f'perf/alarm_{index:05d}',
            'occurrence_id': f'O-{index}',
        }
        for index in range(1, 11)
    ]
    group_2_initial = [
        {
            'kind': 'STARTED',
            'alarm_key': f'perf/alarm_{index:05d}',
            'occurrence_id': f'O-{index}',
        }
        for index in range(11, 21)
    ]
    reset_closures = [
        {
            'kind': 'CLOSED',
            'alarm_key': f'perf/alarm_{index:05d}',
            'occurrence_id': f'O-{index}',
            'closure_reason': 'configuration_reconfigured',
        }
        for index in range(1, 11)
    ]
    restarted = [
        {
            'kind': 'STARTED',
            'alarm_key': f'perf/alarm_{index:05d}',
            'occurrence_id': f'O-{index + 20}',
        }
        for index in range(1, 11)
    ]
    records = (
        record(
            'PERF-AC-1',
            'perf-group-001',
            group_1_initial,
            [{'kind': 'STARTED', 'episode_id': 'E-1', 'closure_reason': None}],
        ),
        record(
            'PERF-AC-1',
            'perf-group-002',
            group_2_initial,
            [{'kind': 'STARTED', 'episode_id': 'E-2', 'closure_reason': None}],
        ),
        record(
            'PERF-AC-2',
            'perf-group-001',
            reset_closures,
            [
                {
                    'kind': 'CLOSED',
                    'episode_id': 'E-1',
                    'closure_reason': 'configuration_terminated',
                }
            ],
        ),
        record(
            'PERF-AC-2',
            'perf-group-001',
            restarted,
            [{'kind': 'STARTED', 'episode_id': 'E-3', 'closure_reason': None}],
        ),
    )

    target_alarms = {
        f'perf/alarm_{index:05d}': {
            'occurrence': {
                'occurrence_id': f'O-{index + 20}',
                'assignments': [{}],
            }
        }
        for index in range(1, 11)
    }
    source_alarms = {
        f'perf/alarm_{index:05d}': {
            'occurrence': {'occurrence_id': f'O-{index}', 'assignments': [{}]}
        }
        for index in range(11, 21)
    }
    snapshots = (
        SimpleNamespace(
            as_document=lambda: {
                'state_basis': {'alarm_configuration_revision': 'PERF-AC-2'},
                'episode': {'episode_id': 'E-3'},
                'alarms': target_alarms,
            }
        ),
        SimpleNamespace(
            as_document=lambda: {
                'state_basis': {'alarm_configuration_revision': 'PERF-AC-1'},
                'episode': {'episode_id': 'E-2'},
                'alarms': source_alarms,
            }
        ),
    )
    samples = (
        SimpleNamespace(
            iteration=13,
            duration_ms=800.0,
            cpu_percent=50.0,
            revision_origin='source_candidate',
            adoption_outcome='adopted',
            cycle_executed=False,
        ),
        SimpleNamespace(
            iteration=14,
            duration_ms=850.0,
            cpu_percent=55.0,
            revision_origin='cache_current',
            adoption_outcome='not_required',
            cycle_executed=True,
        ),
    )
    metrics = _build_structural_reset_adoption_pressure_metrics(
        scenario=scenario,
        runtime=runtime,
        records=records,
        snapshots=snapshots,
        samples=samples,
    )
    assert metrics is not None
    assert metrics.functional_integrity_ok is True
    assert metrics.structural_reset_change_count == 10
    assert metrics.structural_reset_group_count == 1
    assert metrics.adoption_commit_count == 1
    assert metrics.next_cycle_commit_count == 1
    assert metrics.configuration_reconfigured_occurrence_count == 10
    assert metrics.configuration_terminated_episode_count == 1
    assert metrics.restarted_occurrence_count == 10
    assert metrics.restarted_episode_count == 1
    assert metrics.occurrence_identity_reuse_count == 0
    assert metrics.immediate_next_iteration == 14
    assert metrics.immediate_next_iteration_cycle_executed is True
    assert metrics.immediate_next_iteration_cache_current is True
    assert metrics.source_state_basis_snapshot_count == 1
    assert metrics.target_state_basis_snapshot_count == 1
    assert metrics.durable_record_count == 4


def _e006_scenario(*, alarm_count: int = 1000) -> BaselineScenario:
    if alarm_count == 1000:
        priority_group_size = 10
        disabled_percent = 5
        removed_percent = 5
        structural_reset_percent = 5
    else:
        priority_group_size = 4
        disabled_percent = 20
        removed_percent = 20
        structural_reset_percent = 10
    return BaselineScenario(
        test_id='E-006',
        alarm_count=alarm_count,
        duration_seconds=180,
        iteration_period_seconds=5,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        columns_per_alarm=1,
        priority_group_size=priority_group_size,
        physical_partition_count=36 if alarm_count >= 36 else alarm_count,
        physical_partition_layout='balanced',
        initial_active_percent=100,
        signal_value=1.0,
        threshold=0.5,
        mixed_revision_adoption_at_seconds=60,
        mixed_revision_target_threshold=0.75,
        mixed_revision_disabled_alarm_percent=disabled_percent,
        mixed_revision_removed_alarm_percent=removed_percent,
        mixed_revision_structural_reset_alarm_percent=structural_reset_percent,
    )


def test_e006_builds_large_mixed_revision_across_all_priority_groups(tmp_path: Path) -> None:
    scenario = _e006_scenario()
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    assert scenario.has_mixed_revision_adoption_pressure is True
    assert scenario.mixed_revision_compatible_alarm_count == 850
    assert scenario.mixed_revision_disabled_alarm_count == 50
    assert scenario.mixed_revision_removed_alarm_count == 50
    assert scenario.mixed_revision_structural_reset_alarm_count == 50
    assert scenario.mixed_revision_structural_reset_priority_group_count == 5
    assert scenario.mixed_revision_disabled_removed_overlap_group_count == 5
    assert runtime.target_revision is not None

    adoption = plan_configuration_adoption(runtime.revision, runtime.target_revision)
    counts = {item: 0 for item in ConfigurationAdoptionDisposition}
    for change in adoption.changes:
        counts[change.disposition] += 1

    assert adoption.is_adoptable is True
    assert counts[ConfigurationAdoptionDisposition.COMPATIBLE] == 850
    assert counts[ConfigurationAdoptionDisposition.UNCHANGED] == 0
    assert counts[ConfigurationAdoptionDisposition.STRUCTURAL_RESET] == 50
    assert counts[ConfigurationAdoptionDisposition.DISABLED] == 50
    assert counts[ConfigurationAdoptionDisposition.REMOVED] == 50
    assert counts[ConfigurationAdoptionDisposition.REJECTED] == 0
    assert adoption.structural_reset_groups == tuple(
        f'perf-group-{index:03d}' for index in range(1, 6)
    )
    assert len(runtime.target_revision.defined_alarm_identities) == 950
    assert len(runtime.target_revision.session.identities) == 900
    assert {
        float(entry.parameters['threshold']) for entry in runtime.target_revision.session.entries
    } == {0.75}

    by_disposition = {
        disposition: {
            change.identity.canonical_key
            for change in adoption.changes
            if change.disposition is disposition
        }
        for disposition in ConfigurationAdoptionDisposition
    }
    assert by_disposition[ConfigurationAdoptionDisposition.STRUCTURAL_RESET] == {
        f'perf/alarm_{index:05d}' for index in range(1, 51)
    }
    assert by_disposition[ConfigurationAdoptionDisposition.DISABLED] == {
        f'perf/alarm_{index:05d}' for index in range(51, 542, 10)
    }
    assert by_disposition[ConfigurationAdoptionDisposition.REMOVED] == {
        f'perf/alarm_{index:05d}' for index in range(502, 993, 10)
    }


def test_e006_rejects_invalid_large_mixed_revision_contract() -> None:
    base = dict(
        test_id='E-006',
        alarm_count=1000,
        duration_seconds=180,
        iteration_period_seconds=5,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        priority_group_size=10,
        physical_partition_count=36,
        physical_partition_layout='balanced',
        initial_active_percent=100,
        signal_value=1.0,
        threshold=0.5,
        mixed_revision_adoption_at_seconds=60,
        mixed_revision_target_threshold=0.75,
        mixed_revision_disabled_alarm_percent=5,
        mixed_revision_removed_alarm_percent=5,
        mixed_revision_structural_reset_alarm_percent=5,
    )
    cases = (
        ({'mixed_revision_adoption_at_seconds': 0}, 'mixed revision parameters require'),
        (
            {'mixed_revision_target_threshold': None},
            'mixed revision adoption requires target threshold',
        ),
        (
            {'mixed_revision_target_threshold': 0.5},
            'mixed revision adoption must change target threshold',
        ),
        ({'mixed_revision_disabled_alarm_percent': 0}, 'requires disabled alarm percent > 0'),
        ({'mixed_revision_removed_alarm_percent': 0}, 'requires removed alarm percent > 0'),
        (
            {'mixed_revision_structural_reset_alarm_percent': 0},
            'requires structural reset alarm percent > 0',
        ),
        (
            {'mixed_revision_adoption_at_seconds': 65},
            'mixed revision adoption timing must align with data refresh',
        ),
        (
            {'duration_seconds': 65},
            'mixed revision adoption duration must include the next iteration',
        ),
        (
            {'c1_routing_destination_count': 1},
            'mixed revision adoption requires steady C3 source without routing/churn',
        ),
        (
            {'management_action_at_seconds': 20},
            'mixed revision adoption must not combine with another pressure',
        ),
        (
            {
                'mixed_revision_disabled_alarm_percent': 1,
                'mixed_revision_removed_alarm_percent': 1,
            },
            'mixed revision disabled/removed groups must cover every non-reset group',
        ),
    )
    for override, message in cases:
        with pytest.raises(ValueError, match=message):
            BaselineScenario(**(base | override))


def test_e006_report_integrity_includes_large_mixed_revision_gate() -> None:
    recorder = PerformanceRecorder(iteration_period_seconds=5)
    pressure = MixedRevisionAdoptionPressureMetrics(
        adoption_at_seconds=60,
        target_threshold=0.75,
        disabled_alarm_percent=20,
        removed_alarm_percent=20,
        structural_reset_alarm_percent=10,
        source_revision='PERF-AC-1',
        target_revision='PERF-AC-2',
        plan_change_count=40,
        compatible_change_count=20,
        expected_compatible_change_count=20,
        unchanged_change_count=0,
        structural_reset_change_count=4,
        expected_structural_reset_change_count=4,
        structural_reset_group_count=1,
        expected_structural_reset_group_count=1,
        disabled_change_count=8,
        expected_disabled_change_count=8,
        removed_change_count=8,
        expected_removed_change_count=8,
        rejected_change_count=0,
        target_defined_alarm_count=32,
        expected_target_defined_alarm_count=32,
        target_runtime_alarm_count=24,
        expected_target_runtime_alarm_count=24,
        touched_priority_group_count=10,
        expected_touched_priority_group_count=10,
        disabled_removed_overlap_group_count=7,
        expected_disabled_removed_overlap_group_count=7,
        effective_cache_revision='PERF-AC-2',
        adoption_iteration_count=1,
        adoption_iteration=13,
        adoption_iteration_ms=1500.0,
        adoption_iteration_cpu_percent=80.0,
        adoption_cycle_executed=False,
        immediate_next_iteration=14,
        immediate_next_iteration_cycle_executed=True,
        immediate_next_iteration_cache_current=True,
        immediate_next_start_interval_ms=1500.0,
        immediate_next_duration_ms=900.0,
        adoption_commit_count=10,
        expected_adoption_commit_count=10,
        next_cycle_commit_count=1,
        expected_next_cycle_commit_count=1,
        configuration_reconfigured_occurrence_count=4,
        expected_configuration_reconfigured_occurrence_count=4,
        configuration_disabled_occurrence_count=8,
        expected_configuration_disabled_occurrence_count=8,
        configuration_removed_occurrence_count=8,
        expected_configuration_removed_occurrence_count=8,
        configuration_terminated_episode_count=1,
        expected_configuration_terminated_episode_count=1,
        restarted_occurrence_count=4,
        expected_restarted_occurrence_count=4,
        restarted_episode_count=1,
        expected_restarted_episode_count=1,
        occurrence_identity_reuse_count=0,
        episode_identity_reuse_count=0,
        source_revision_durable_record_count=10,
        expected_source_revision_durable_record_count=10,
        target_revision_durable_record_count=11,
        expected_target_revision_durable_record_count=11,
        durable_record_count=21,
        expected_durable_record_count=21,
        groups_with_two_records=9,
        expected_groups_with_two_records=9,
        groups_with_three_records=1,
        expected_groups_with_three_records=1,
        source_state_basis_snapshot_count=0,
        expected_source_state_basis_snapshot_count=0,
        target_state_basis_snapshot_count=10,
        expected_target_state_basis_snapshot_count=10,
        final_alarm_count=24,
        expected_final_alarm_count=24,
        final_assignment_count=24,
        expected_final_assignment_count=24,
        open_occurrence_count=24,
        expected_open_occurrence_count=24,
        open_episode_count=10,
        expected_open_episode_count=10,
        functional_integrity_ok=False,
    )
    report = recorder.build_report(
        test_id='E-006',
        alarm_count=40,
        planned_duration_seconds=180,
        actual_duration_seconds=180,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        columns_per_alarm=1,
        historical_series_per_alarm=0,
        historical_window_minutes=0,
        historical_step_seconds=0,
        historical_points_per_series=0,
        historical_value_count=0,
        physical_partition_count=1,
        physical_partition_column_counts=(40,),
        physical_partition_layout='balanced',
        empty_physical_partition_count=0,
        missing_source_column_count=0,
        synthesized_null_column_count=0,
        source_view_count=1,
        source_column_count=40,
        source_row_count=1,
        source_frame_bytes=100,
        source_numeric_value_count=40,
        latest_source_column_count=40,
        historical_source_column_count=0,
        historical_source_row_count=0,
        source_load_durations_ms=[],
        source_merge_durations_ms=[],
        journal_aligned=True,
        durable_record_count=21,
        snapshot_count=10,
        snapshot_alarm_count=24,
        expected_snapshot_alarm_count=24,
        source_load_count=1,
        mixed_revision_adoption_pressure=pressure,
    )
    assert report.integrity_ok is False
    assert report.result == 'FAIL'
    assert report.mixed_revision_adoption_pressure is pressure


def test_e006_metrics_require_all_group_mixed_adoption_and_reset_restart(
    tmp_path: Path,
) -> None:
    scenario = _e006_scenario(alarm_count=40)
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    assert runtime.target_revision is not None
    runtime.job.revision_resolver.cache.replace_effective(
        bundle=RuntimeRevisionBundle(
            manifest=RuntimeManifest(
                schema_version=RUNTIME_MANIFEST_SCHEMA_VERSION,
                alarm_configuration_revision='PERF-AC-2',
                tool_registry_revision='PERF-TR-1',
                published_at=datetime(2026, 8, 29, 12, 1, tzinfo=UTC),
            ),
            alarm_configuration={'revision': 'PERF-AC-2'},
            tool_registry={'revision': 'PERF-TR-1'},
        )
    )

    adoption = plan_configuration_adoption(runtime.revision, runtime.target_revision)
    reset_keys = {
        change.identity.canonical_key
        for change in adoption.changes
        if change.disposition is ConfigurationAdoptionDisposition.STRUCTURAL_RESET
    }
    disabled_keys = {
        change.identity.canonical_key
        for change in adoption.changes
        if change.disposition is ConfigurationAdoptionDisposition.DISABLED
    }
    removed_keys = {
        change.identity.canonical_key
        for change in adoption.changes
        if change.disposition is ConfigurationAdoptionDisposition.REMOVED
    }

    def record(
        revision: str,
        priority_group: str,
        occurrence_changes: list[dict[str, object]],
        episode_changes: list[dict[str, object]],
    ) -> SimpleNamespace:
        return SimpleNamespace(
            record=SimpleNamespace(
                commit=SimpleNamespace(
                    alarm_configuration_revision=revision,
                    priority_group=priority_group,
                ),
                records={
                    'occurrence_changes': occurrence_changes,
                    'episode_changes': episode_changes,
                },
            )
        )

    records: list[SimpleNamespace] = []
    for group_number in range(1, 11):
        start = (group_number - 1) * 4 + 1
        records.append(
            record(
                'PERF-AC-1',
                f'perf-group-{group_number:03d}',
                [
                    {
                        'kind': 'STARTED',
                        'alarm_key': f'perf/alarm_{alarm_number:05d}',
                        'occurrence_id': f'O-{alarm_number}',
                    }
                    for alarm_number in range(start, start + 4)
                ],
                [{'kind': 'STARTED', 'episode_id': f'E-{group_number}', 'closure_reason': None}],
            )
        )

    for group_number in range(1, 11):
        start = (group_number - 1) * 4 + 1
        occurrence_changes: list[dict[str, object]] = []
        episode_changes: list[dict[str, object]] = []
        for alarm_number in range(start, start + 4):
            key = f'perf/alarm_{alarm_number:05d}'
            reason = None
            if key in reset_keys:
                reason = 'configuration_reconfigured'
            elif key in disabled_keys:
                reason = 'configuration_disabled'
            elif key in removed_keys:
                reason = 'configuration_removed'
            if reason is not None:
                occurrence_changes.append(
                    {
                        'kind': 'CLOSED',
                        'alarm_key': key,
                        'occurrence_id': f'O-{alarm_number}',
                        'closure_reason': reason,
                    }
                )
        if group_number == 1:
            episode_changes.append(
                {
                    'kind': 'CLOSED',
                    'episode_id': 'E-1',
                    'closure_reason': 'configuration_terminated',
                }
            )
        records.append(
            record(
                'PERF-AC-2',
                f'perf-group-{group_number:03d}',
                occurrence_changes,
                episode_changes,
            )
        )

    records.append(
        record(
            'PERF-AC-2',
            'perf-group-001',
            [
                {
                    'kind': 'STARTED',
                    'alarm_key': f'perf/alarm_{alarm_number:05d}',
                    'occurrence_id': f'N-{alarm_number}',
                }
                for alarm_number in range(1, 5)
            ],
            [{'kind': 'STARTED', 'episode_id': 'E-11', 'closure_reason': None}],
        )
    )

    snapshots = []
    for group_number in range(1, 11):
        start = (group_number - 1) * 4 + 1
        alarms: dict[str, dict[str, object]] = {}
        for alarm_number in range(start, start + 4):
            key = f'perf/alarm_{alarm_number:05d}'
            if key in disabled_keys or key in removed_keys:
                continue
            alarms[key] = {
                'occurrence': {
                    'occurrence_id': (
                        f'N-{alarm_number}' if key in reset_keys else f'O-{alarm_number}'
                    ),
                    'assignments': [{}],
                }
            }
        episode_id = 'E-11' if group_number == 1 else f'E-{group_number}'
        snapshots.append(
            SimpleNamespace(
                as_document=lambda alarms=alarms, episode_id=episode_id: {
                    'state_basis': {'alarm_configuration_revision': 'PERF-AC-2'},
                    'episode': {'episode_id': episode_id},
                    'alarms': alarms,
                }
            )
        )

    samples = (
        SimpleNamespace(
            iteration=13,
            start_interval_ms=5000.0,
            duration_ms=1500.0,
            cpu_percent=80.0,
            revision_origin='source_candidate',
            adoption_outcome='adopted',
            cycle_executed=False,
        ),
        SimpleNamespace(
            iteration=14,
            start_interval_ms=1501.0,
            duration_ms=900.0,
            cpu_percent=85.0,
            revision_origin='cache_current',
            adoption_outcome='not_required',
            cycle_executed=True,
        ),
    )
    metrics = _build_mixed_revision_adoption_pressure_metrics(
        scenario=scenario,
        runtime=runtime,
        records=tuple(records),
        snapshots=tuple(snapshots),
        samples=samples,
    )
    assert metrics is not None
    assert metrics.functional_integrity_ok is True
    assert metrics.compatible_change_count == 20
    assert metrics.structural_reset_change_count == 4
    assert metrics.disabled_change_count == 8
    assert metrics.removed_change_count == 8
    assert metrics.touched_priority_group_count == 10
    assert metrics.disabled_removed_overlap_group_count == 7
    assert metrics.adoption_commit_count == 10
    assert metrics.next_cycle_commit_count == 1
    assert metrics.configuration_reconfigured_occurrence_count == 4
    assert metrics.configuration_disabled_occurrence_count == 8
    assert metrics.configuration_removed_occurrence_count == 8
    assert metrics.restarted_occurrence_count == 4
    assert metrics.occurrence_identity_reuse_count == 0
    assert metrics.groups_with_two_records == 9
    assert metrics.groups_with_three_records == 1
    assert metrics.target_state_basis_snapshot_count == 10
    assert metrics.final_alarm_count == 24
    assert metrics.final_assignment_count == 24
    assert metrics.open_occurrence_count == 24
    assert metrics.open_episode_count == 10
    assert metrics.durable_record_count == 21


def _e007_scenario(*, alarm_count: int = 1000) -> BaselineScenario:
    return BaselineScenario(
        test_id='E-007',
        alarm_count=alarm_count,
        duration_seconds=180,
        iteration_period_seconds=5,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        columns_per_alarm=1,
        priority_group_size=10 if alarm_count == 1000 else 4,
        physical_partition_count=36 if alarm_count >= 36 else alarm_count,
        physical_partition_layout='balanced',
        initial_active_percent=100,
        signal_value=1.0,
        threshold=0.5,
        rejected_candidate_at_seconds=60,
    )


def test_e007_builds_single_priority_group_rejected_candidate(tmp_path: Path) -> None:
    scenario = _e007_scenario()
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    assert scenario.has_rejected_candidate_pressure is True
    assert runtime.target_revision is not None

    adoption = plan_configuration_adoption(runtime.revision, runtime.target_revision)
    counts = {item: 0 for item in ConfigurationAdoptionDisposition}
    for change in adoption.changes:
        counts[change.disposition] += 1

    assert adoption.is_adoptable is False
    assert counts[ConfigurationAdoptionDisposition.UNCHANGED] == 999
    assert counts[ConfigurationAdoptionDisposition.COMPATIBLE] == 0
    assert counts[ConfigurationAdoptionDisposition.STRUCTURAL_RESET] == 0
    assert counts[ConfigurationAdoptionDisposition.DISABLED] == 0
    assert counts[ConfigurationAdoptionDisposition.REMOVED] == 0
    assert counts[ConfigurationAdoptionDisposition.REJECTED] == 1
    rejected = adoption.rejected_changes[0]
    assert rejected.identity.canonical_key == 'perf/alarm_00001'
    assert rejected.rejection_reason is ConfigurationAdoptionRejectionReason.PRIORITY_GROUP_CHANGED
    assert runtime.revision.plan_for(rejected.identity).priority_group == 'perf-group-001'
    assert runtime.target_revision.plan_for(rejected.identity).priority_group == (
        'perf-rejected-group'
    )
    assert len(runtime.target_revision.defined_alarm_identities) == 1000
    assert len(runtime.target_revision.session.identities) == 1000


def test_e007_rejects_invalid_last_known_good_contract() -> None:
    base = dict(
        test_id='E-007',
        alarm_count=1000,
        duration_seconds=180,
        iteration_period_seconds=5,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        priority_group_size=10,
        physical_partition_count=36,
        physical_partition_layout='balanced',
        initial_active_percent=100,
        signal_value=1.0,
        threshold=0.5,
        rejected_candidate_at_seconds=60,
    )
    cases = (
        (
            {'rejected_candidate_at_seconds': -1},
            'rejected_candidate_at_seconds must not be negative',
        ),
        (
            {'data_profile': 'shared-latest', 'physical_partition_count': 1},
            'rejected candidate requires data_profile=latest-narrow',
        ),
        (
            {'physical_partition_layout': 'skewed'},
            'rejected candidate requires physical_partition_layout=balanced',
        ),
        ({'priority_group_size': 0}, 'rejected candidate requires priority_group_size > 0'),
        ({'initial_active_percent': 90}, 'rejected candidate requires initial_active_percent=100'),
        (
            {'rejected_candidate_at_seconds': 65},
            'rejected candidate timing must align with data refresh',
        ),
        (
            {'duration_seconds': 65},
            'rejected candidate duration must include continued LKG iterations',
        ),
        (
            {'c1_routing_destination_count': 1},
            'rejected candidate requires steady C3 source without routing/churn',
        ),
        (
            {'parameter_adoption_at_seconds': 60, 'parameter_target_threshold': 0.75},
            'rejected candidate must not combine with another pressure',
        ),
    )
    for override, message in cases:
        with pytest.raises(ValueError, match=message):
            BaselineScenario(**(base | override))


def test_e007_report_integrity_includes_rejected_target_gate() -> None:
    recorder = PerformanceRecorder(iteration_period_seconds=5)
    pressure = RejectedTargetPressureMetrics(
        candidate_at_seconds=60,
        source_revision='PERF-AC-1',
        target_revision='PERF-AC-2',
        rejected_alarm_key='alarm_00001',
        rejected_target_priority_group='perf-rejected-group',
        plan_change_count=40,
        unchanged_change_count=39,
        compatible_change_count=0,
        structural_reset_change_count=0,
        disabled_change_count=0,
        removed_change_count=0,
        rejected_change_count=1,
        expected_rejected_change_count=1,
        priority_group_changed_rejection_count=1,
        plan_adoptable=False,
        effective_cache_revision='PERF-AC-1',
        first_rejected_iteration=13,
        expected_first_rejected_iteration=13,
        rejected_iteration_count=2,
        degraded_rejected_iteration_count=2,
        rejected_cycle_executed_count=2,
        post_candidate_non_rejected_iteration_count=0,
        target_revision_durable_record_count=0,
        expected_target_revision_durable_record_count=0,
        source_revision_durable_record_count=10,
        expected_source_revision_durable_record_count=10,
        durable_record_count=10,
        expected_durable_record_count=10,
        source_state_basis_snapshot_count=10,
        target_state_basis_snapshot_count=0,
        rejected_priority_group_materialized_count=0,
        final_alarm_count=40,
        expected_final_alarm_count=40,
        final_assignment_count=40,
        expected_final_assignment_count=40,
        open_occurrence_count=40,
        expected_open_occurrence_count=40,
        open_episode_count=10,
        expected_open_episode_count=10,
        functional_integrity_ok=False,
    )
    report = recorder.build_report(
        test_id='E-007',
        alarm_count=40,
        planned_duration_seconds=180,
        actual_duration_seconds=180,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        columns_per_alarm=1,
        historical_series_per_alarm=0,
        historical_window_minutes=0,
        historical_step_seconds=0,
        historical_points_per_series=0,
        historical_value_count=0,
        physical_partition_count=1,
        physical_partition_column_counts=(40,),
        physical_partition_layout='balanced',
        empty_physical_partition_count=0,
        missing_source_column_count=0,
        synthesized_null_column_count=0,
        source_view_count=1,
        source_column_count=40,
        source_row_count=1,
        source_frame_bytes=100,
        source_numeric_value_count=40,
        latest_source_column_count=40,
        historical_source_column_count=0,
        historical_source_row_count=0,
        source_load_durations_ms=[],
        source_merge_durations_ms=[],
        journal_aligned=True,
        durable_record_count=10,
        snapshot_count=10,
        snapshot_alarm_count=40,
        expected_snapshot_alarm_count=40,
        source_load_count=1,
        rejected_target_pressure=pressure,
    )
    assert report.integrity_ok is False
    assert report.result == 'FAIL'
    assert report.rejected_target_pressure is pressure


def test_e007_metrics_require_repeated_rejection_and_unchanged_lkg_state(
    tmp_path: Path,
) -> None:
    scenario = _e007_scenario(alarm_count=40)
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    assert runtime.target_revision is not None
    runtime.job.revision_resolver.cache.replace_effective(
        bundle=RuntimeRevisionBundle(
            manifest=RuntimeManifest(
                schema_version=RUNTIME_MANIFEST_SCHEMA_VERSION,
                alarm_configuration_revision='PERF-AC-1',
                tool_registry_revision='PERF-TR-1',
                published_at=datetime(2026, 8, 29, 12, 0, tzinfo=UTC),
            ),
            alarm_configuration={'revision': 'PERF-AC-1'},
            tool_registry={'revision': 'PERF-TR-1'},
        )
    )

    records = tuple(
        SimpleNamespace(
            record=SimpleNamespace(commit=SimpleNamespace(alarm_configuration_revision='PERF-AC-1'))
        )
        for _ in range(10)
    )
    snapshots = tuple(
        SimpleNamespace(
            as_document=lambda group_number=group_number: {
                'priority_group': f'perf-group-{group_number:03d}',
                'state_basis': {'alarm_configuration_revision': 'PERF-AC-1'},
                'episode': {'episode_id': f'E-{group_number}'},
                'alarms': {
                    f'perf/alarm_{alarm_number:05d}': {
                        'occurrence': {
                            'occurrence_id': f'O-{alarm_number}',
                            'assignments': {'perf-tool': {}},
                        }
                    }
                    for alarm_number in range((group_number - 1) * 4 + 1, group_number * 4 + 1)
                },
            }
        )
        for group_number in range(1, 11)
    )
    samples = (
        SimpleNamespace(
            iteration=13,
            duration_ms=800.0,
            cpu_percent=50.0,
            revision_origin='source_candidate',
            adoption_outcome='rejected',
            cycle_executed=True,
            degraded=True,
        ),
        SimpleNamespace(
            iteration=14,
            duration_ms=810.0,
            cpu_percent=52.0,
            revision_origin='source_candidate',
            adoption_outcome='rejected',
            cycle_executed=True,
            degraded=True,
        ),
    )
    metrics = _build_rejected_target_pressure_metrics(
        scenario=scenario,
        runtime=runtime,
        records=records,
        snapshots=snapshots,
        samples=samples,
    )
    assert metrics is not None
    assert metrics.functional_integrity_ok is True
    assert metrics.plan_adoptable is False
    assert metrics.unchanged_change_count == 39
    assert metrics.rejected_change_count == 1
    assert metrics.priority_group_changed_rejection_count == 1
    assert metrics.rejected_alarm_key == 'alarm_00001'
    assert metrics.rejected_target_priority_group == 'perf-rejected-group'
    assert metrics.effective_cache_revision == 'PERF-AC-1'
    assert metrics.first_rejected_iteration == 13
    assert metrics.rejected_iteration_count == 2
    assert metrics.degraded_rejected_iteration_count == 2
    assert metrics.rejected_cycle_executed_count == 2
    assert metrics.post_candidate_non_rejected_iteration_count == 0
    assert metrics.source_revision_durable_record_count == 10
    assert metrics.target_revision_durable_record_count == 0
    assert metrics.source_state_basis_snapshot_count == 10
    assert metrics.target_state_basis_snapshot_count == 0
    assert metrics.rejected_priority_group_materialized_count == 0
    assert metrics.final_alarm_count == 40
    assert metrics.final_assignment_count == 40
    assert metrics.open_occurrence_count == 40
    assert metrics.open_episode_count == 10
    assert metrics.durable_record_count == 10


def _e008_scenario(*, alarm_count: int = 1000) -> BaselineScenario:
    return BaselineScenario(
        test_id='E-008',
        alarm_count=alarm_count,
        duration_seconds=600,
        iteration_period_seconds=5,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        columns_per_alarm=1,
        priority_group_size=10 if alarm_count == 1000 else 4,
        operational_churn_percent=10,
        physical_partition_count=36 if alarm_count >= 36 else alarm_count,
        physical_partition_layout='balanced',
        initial_active_percent=50,
        signal_value=1.0,
        threshold=0.5,
        source_unavailable_at_seconds=60,
    )


def test_e008_builds_persistent_manifest_unavailability_over_operational_churn(
    tmp_path: Path,
) -> None:
    scenario = _e008_scenario()
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    assert scenario.has_source_unavailable_pressure is True
    assert scenario.initial_active_alarm_count == 500
    assert runtime.target_revision is None
    assert runtime.source_unavailable_revision_source is not None
    assert runtime.tracked_revision_cache is not None

    source = runtime.source_unavailable_revision_source
    source.started_monotonic -= 61
    with pytest.raises(
        RuntimeRevisionSourceError, match='scheduled runtime manifest is unavailable'
    ):
        source.read_manifest()
    assert source.manifest_failure_count == 1


def test_e008_rejects_invalid_cache_fallback_contract() -> None:
    base = dict(
        test_id='E-008',
        alarm_count=1000,
        duration_seconds=600,
        iteration_period_seconds=5,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        priority_group_size=10,
        operational_churn_percent=10,
        physical_partition_count=36,
        physical_partition_layout='balanced',
        initial_active_percent=50,
        signal_value=1.0,
        threshold=0.5,
        source_unavailable_at_seconds=60,
    )
    cases = (
        (
            {'source_unavailable_at_seconds': -1},
            'source_unavailable_at_seconds must not be negative',
        ),
        (
            {'data_profile': 'shared-latest', 'physical_partition_count': 1},
            'operational churn requires data_profile=latest-narrow',
        ),
        (
            {'physical_partition_layout': 'skewed'},
            'operational churn requires physical_partition_layout=balanced',
        ),
        (
            {'priority_group_size': 0},
            'operational churn requires priority_group_size > 0',
        ),
        (
            {'operational_churn_percent': 0},
            'source unavailable pressure requires operational churn',
        ),
        (
            {'source_unavailable_at_seconds': 65},
            'source unavailable timing must align with data refresh',
        ),
        (
            {'duration_seconds': 65},
            'source unavailable duration must include fallback iterations',
        ),
        (
            {'management_action_at_seconds': 60, 'management_action_count': 1},
            'source unavailable pressure must not combine with another pressure',
        ),
    )
    for override, message in cases:
        with pytest.raises(ValueError, match=message):
            BaselineScenario(**(base | override))


def test_e008_report_integrity_includes_source_unavailable_gate() -> None:
    recorder = PerformanceRecorder(iteration_period_seconds=5)
    pressure = SourceUnavailablePressureMetrics(
        unavailable_at_seconds=60,
        source_alarm_revision='PERF-AC-1',
        source_tool_revision='PERF-TR-1',
        first_fallback_iteration=13,
        expected_first_fallback_iteration=13,
        fallback_iteration_count=2,
        expected_fallback_iteration_count=2,
        degraded_fallback_iteration_count=2,
        fallback_cycle_executed_count=2,
        not_required_fallback_iteration_count=2,
        post_failure_non_fallback_iteration_count=0,
        manifest_success_count=12,
        manifest_failure_count=2,
        cache_replace_count=1,
        post_failure_cache_replace_count=0,
        effective_cache_alarm_revision='PERF-AC-1',
        effective_cache_tool_revision='PERF-TR-1',
        source_revision_durable_record_count=10,
        unexpected_revision_durable_record_count=0,
        source_state_basis_snapshot_count=10,
        unexpected_state_basis_snapshot_count=0,
        functional_integrity_ok=False,
    )
    report = recorder.build_report(
        test_id='E-008',
        alarm_count=40,
        planned_duration_seconds=600,
        actual_duration_seconds=600,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        columns_per_alarm=1,
        historical_series_per_alarm=0,
        historical_window_minutes=0,
        historical_step_seconds=0,
        historical_points_per_series=0,
        historical_value_count=0,
        physical_partition_count=1,
        physical_partition_column_counts=(40,),
        physical_partition_layout='balanced',
        empty_physical_partition_count=0,
        missing_source_column_count=0,
        synthesized_null_column_count=0,
        source_view_count=1,
        source_column_count=40,
        source_row_count=1,
        source_frame_bytes=100,
        source_numeric_value_count=40,
        latest_source_column_count=40,
        historical_source_column_count=0,
        historical_source_row_count=0,
        source_load_durations_ms=[],
        source_merge_durations_ms=[],
        journal_aligned=True,
        durable_record_count=10,
        snapshot_count=10,
        snapshot_alarm_count=40,
        expected_snapshot_alarm_count=40,
        source_load_count=1,
        source_unavailable_pressure=pressure,
    )
    assert report.integrity_ok is False
    assert report.result == 'FAIL'
    assert report.source_unavailable_pressure is pressure


def test_e008_metrics_require_continuous_fallback_and_unchanged_lkg_basis() -> None:
    scenario = _e008_scenario(alarm_count=40)
    source = SimpleNamespace(
        started_monotonic=1000.0,
        manifest_success_count=12,
        manifest_failure_count=2,
    )
    cache_bundle = RuntimeRevisionBundle(
        manifest=RuntimeManifest(
            schema_version=RUNTIME_MANIFEST_SCHEMA_VERSION,
            alarm_configuration_revision='PERF-AC-1',
            tool_registry_revision='PERF-TR-1',
            published_at=datetime(2026, 8, 29, 12, 0, tzinfo=UTC),
        ),
        alarm_configuration={'revision': 'PERF-AC-1'},
        tool_registry={'revision': 'PERF-TR-1'},
    )
    cache = SimpleNamespace(
        replace_monotonic=[1001.0],
        load_effective=lambda: cache_bundle,
    )
    runtime = SimpleNamespace(
        source_unavailable_revision_source=source,
        tracked_revision_cache=cache,
        revision=SimpleNamespace(
            alarm_configuration_revision='PERF-AC-1',
            tool_registry_revision='PERF-TR-1',
        ),
    )
    records = tuple(
        SimpleNamespace(
            record=SimpleNamespace(
                commit=SimpleNamespace(
                    alarm_configuration_revision='PERF-AC-1',
                    tool_registry_revision='PERF-TR-1',
                )
            )
        )
        for _ in range(10)
    )
    snapshots = tuple(
        SimpleNamespace(
            as_document=lambda: {
                'state_basis': {
                    'alarm_configuration_revision': 'PERF-AC-1',
                    'tool_registry_revision': 'PERF-TR-1',
                }
            }
        )
        for _ in range(10)
    )
    samples = tuple(
        SimpleNamespace(
            iteration=iteration,
            revision_origin='source_candidate' if iteration == 1 else 'cache_current',
            adoption_outcome='bootstrapped' if iteration == 1 else 'not_required',
            cycle_executed=True,
            degraded=False,
        )
        for iteration in range(1, 13)
    ) + tuple(
        SimpleNamespace(
            iteration=iteration,
            revision_origin='cache_fallback',
            adoption_outcome='not_required',
            cycle_executed=True,
            degraded=True,
        )
        for iteration in range(13, 15)
    )

    metrics = _build_source_unavailable_pressure_metrics(
        scenario=scenario,
        runtime=runtime,
        records=records,
        snapshots=snapshots,
        samples=samples,
    )
    assert metrics is not None
    assert metrics.functional_integrity_ok is True
    assert metrics.first_fallback_iteration == 13
    assert metrics.expected_first_fallback_iteration == 13
    assert metrics.fallback_iteration_count == 2
    assert metrics.expected_fallback_iteration_count == 2
    assert metrics.degraded_fallback_iteration_count == 2
    assert metrics.fallback_cycle_executed_count == 2
    assert metrics.not_required_fallback_iteration_count == 2
    assert metrics.post_failure_non_fallback_iteration_count == 0
    assert metrics.manifest_success_count == 12
    assert metrics.manifest_failure_count == 2
    assert metrics.cache_replace_count == 1
    assert metrics.post_failure_cache_replace_count == 0
    assert metrics.effective_cache_alarm_revision == 'PERF-AC-1'
    assert metrics.effective_cache_tool_revision == 'PERF-TR-1'
    assert metrics.source_revision_durable_record_count == 10
    assert metrics.unexpected_revision_durable_record_count == 0
    assert metrics.source_state_basis_snapshot_count == 10
    assert metrics.unexpected_state_basis_snapshot_count == 0


def _e009_scenario(*, alarm_count: int = 1000) -> BaselineScenario:
    return BaselineScenario(
        test_id='E-009',
        alarm_count=alarm_count,
        duration_seconds=600,
        iteration_period_seconds=5,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        columns_per_alarm=1,
        priority_group_size=10 if alarm_count == 1000 else 4,
        operational_churn_percent=10,
        physical_partition_count=36 if alarm_count >= 36 else alarm_count,
        physical_partition_layout='balanced',
        initial_active_percent=50,
        signal_value=1.0,
        threshold=0.5,
        invalid_candidate_at_seconds=60,
    )


def test_e009_builds_readable_but_contract_invalid_candidate(tmp_path: Path) -> None:
    scenario = _e009_scenario()
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    assert scenario.has_invalid_source_candidate_pressure is True
    assert scenario.initial_active_alarm_count == 500
    assert runtime.target_revision is None
    assert runtime.invalid_candidate_revision_source is not None
    assert runtime.invalid_candidate_revision_decoder is not None
    assert runtime.tracked_revision_cache is not None

    source = runtime.invalid_candidate_revision_source
    initial_manifest = source.read_manifest()
    initial_bundle = RuntimeRevisionBundle(
        manifest=initial_manifest,
        alarm_configuration=source.read_alarm_configuration(
            revision=initial_manifest.alarm_configuration_revision
        ),
        tool_registry=source.read_tool_registry(revision=initial_manifest.tool_registry_revision),
    )
    runtime.tracked_revision_cache.replace_effective(bundle=initial_bundle)

    source.started_monotonic -= 61
    resolution = runtime.job.revision_resolver.resolve()
    assert resolution.origin.value == 'cache_fallback'
    assert resolution.target.revision_key == ('PERF-AC-1', 'PERF-TR-1')
    assert source.manifest_failure_count == 0
    assert source.candidate_manifest_count == 1
    assert source.candidate_alarm_document_read_count == 1
    assert source.candidate_tool_document_read_count == 1
    assert runtime.invalid_candidate_revision_decoder.contract_failure_count == 1


def test_e009_rejects_invalid_source_candidate_contract() -> None:
    base = dict(
        test_id='E-009',
        alarm_count=1000,
        duration_seconds=600,
        iteration_period_seconds=5,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        priority_group_size=10,
        operational_churn_percent=10,
        physical_partition_count=36,
        physical_partition_layout='balanced',
        initial_active_percent=50,
        signal_value=1.0,
        threshold=0.5,
        invalid_candidate_at_seconds=60,
    )
    cases = (
        (
            {'invalid_candidate_at_seconds': -1},
            'invalid_candidate_at_seconds must not be negative',
        ),
        (
            {'data_profile': 'shared-latest', 'physical_partition_count': 1},
            'operational churn requires data_profile=latest-narrow',
        ),
        (
            {'physical_partition_layout': 'skewed'},
            'operational churn requires physical_partition_layout=balanced',
        ),
        (
            {'priority_group_size': 0},
            'operational churn requires priority_group_size > 0',
        ),
        (
            {'operational_churn_percent': 0},
            'invalid candidate pressure requires operational churn',
        ),
        (
            {'invalid_candidate_at_seconds': 65},
            'invalid candidate timing must align with data refresh',
        ),
        (
            {'duration_seconds': 65},
            'invalid candidate duration must include fallback iterations',
        ),
        (
            {'source_unavailable_at_seconds': 60},
            'source unavailable pressure must not combine with another pressure',
        ),
    )
    for override, message in cases:
        with pytest.raises(ValueError, match=message):
            BaselineScenario(**(base | override))


def test_e009_report_integrity_includes_invalid_candidate_gate() -> None:
    recorder = PerformanceRecorder(iteration_period_seconds=5)
    pressure = InvalidSourceCandidatePressureMetrics(
        invalid_at_seconds=60,
        source_alarm_revision='PERF-AC-1',
        source_tool_revision='PERF-TR-1',
        candidate_alarm_revision='PERF-AC-2',
        candidate_tool_revision='PERF-TR-1',
        invalid_alarm_document_revision='PERF-AC-CORRUPT',
        first_fallback_iteration=13,
        expected_first_fallback_iteration=13,
        fallback_iteration_count=2,
        expected_fallback_iteration_count=2,
        degraded_fallback_iteration_count=2,
        fallback_cycle_executed_count=2,
        not_required_fallback_iteration_count=2,
        post_candidate_non_fallback_iteration_count=0,
        post_candidate_source_candidate_iteration_count=0,
        post_candidate_rejected_iteration_count=0,
        manifest_success_count=14,
        manifest_failure_count=0,
        candidate_manifest_count=2,
        candidate_alarm_document_read_count=2,
        candidate_tool_document_read_count=2,
        candidate_contract_failure_count=2,
        cache_replace_count=1,
        post_candidate_cache_replace_count=0,
        effective_cache_alarm_revision='PERF-AC-1',
        effective_cache_tool_revision='PERF-TR-1',
        source_revision_durable_record_count=10,
        candidate_revision_durable_record_count=0,
        unexpected_revision_durable_record_count=0,
        source_state_basis_snapshot_count=10,
        candidate_state_basis_snapshot_count=0,
        unexpected_state_basis_snapshot_count=0,
        functional_integrity_ok=False,
    )
    report = recorder.build_report(
        test_id='E-009',
        alarm_count=40,
        planned_duration_seconds=600,
        actual_duration_seconds=600,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        columns_per_alarm=1,
        historical_series_per_alarm=0,
        historical_window_minutes=0,
        historical_step_seconds=0,
        historical_points_per_series=0,
        historical_value_count=0,
        physical_partition_count=1,
        physical_partition_column_counts=(40,),
        physical_partition_layout='balanced',
        empty_physical_partition_count=0,
        missing_source_column_count=0,
        synthesized_null_column_count=0,
        source_view_count=1,
        source_column_count=40,
        source_row_count=1,
        source_frame_bytes=100,
        source_numeric_value_count=40,
        latest_source_column_count=40,
        historical_source_column_count=0,
        historical_source_row_count=0,
        source_load_durations_ms=[],
        source_merge_durations_ms=[],
        journal_aligned=True,
        durable_record_count=10,
        snapshot_count=10,
        snapshot_alarm_count=40,
        expected_snapshot_alarm_count=40,
        source_load_count=1,
        invalid_source_candidate_pressure=pressure,
    )
    assert report.integrity_ok is False
    assert report.result == 'FAIL'
    assert report.invalid_source_candidate_pressure is pressure


def test_e009_metrics_require_readable_invalid_candidate_and_unchanged_lkg_basis() -> None:
    scenario = _e009_scenario(alarm_count=40)
    source = SimpleNamespace(
        started_monotonic=1000.0,
        candidate_alarm_revision='PERF-AC-2',
        invalid_alarm_document_revision='PERF-AC-CORRUPT',
        manifest_success_count=14,
        manifest_failure_count=0,
        candidate_manifest_count=2,
        candidate_alarm_document_read_count=2,
        candidate_tool_document_read_count=2,
    )
    decoder = SimpleNamespace(contract_failure_count=2)
    cache_bundle = RuntimeRevisionBundle(
        manifest=RuntimeManifest(
            schema_version=RUNTIME_MANIFEST_SCHEMA_VERSION,
            alarm_configuration_revision='PERF-AC-1',
            tool_registry_revision='PERF-TR-1',
            published_at=datetime(2026, 8, 29, 12, 0, tzinfo=UTC),
        ),
        alarm_configuration={'revision': 'PERF-AC-1'},
        tool_registry={'revision': 'PERF-TR-1'},
    )
    cache = SimpleNamespace(
        replace_monotonic=[1001.0],
        load_effective=lambda: cache_bundle,
    )
    runtime = SimpleNamespace(
        invalid_candidate_revision_source=source,
        invalid_candidate_revision_decoder=decoder,
        tracked_revision_cache=cache,
        revision=SimpleNamespace(
            alarm_configuration_revision='PERF-AC-1',
            tool_registry_revision='PERF-TR-1',
        ),
    )
    records = tuple(
        SimpleNamespace(
            record=SimpleNamespace(
                commit=SimpleNamespace(
                    alarm_configuration_revision='PERF-AC-1',
                    tool_registry_revision='PERF-TR-1',
                )
            )
        )
        for _ in range(10)
    )
    snapshots = tuple(
        SimpleNamespace(
            as_document=lambda: {
                'state_basis': {
                    'alarm_configuration_revision': 'PERF-AC-1',
                    'tool_registry_revision': 'PERF-TR-1',
                }
            }
        )
        for _ in range(10)
    )
    samples = tuple(
        SimpleNamespace(
            iteration=iteration,
            revision_origin='source_candidate' if iteration == 1 else 'cache_current',
            adoption_outcome='bootstrapped' if iteration == 1 else 'not_required',
            cycle_executed=True,
            degraded=False,
        )
        for iteration in range(1, 13)
    ) + tuple(
        SimpleNamespace(
            iteration=iteration,
            revision_origin='cache_fallback',
            adoption_outcome='not_required',
            cycle_executed=True,
            degraded=True,
        )
        for iteration in range(13, 15)
    )

    metrics = _build_invalid_source_candidate_pressure_metrics(
        scenario=scenario,
        runtime=runtime,
        records=records,
        snapshots=snapshots,
        samples=samples,
    )
    assert metrics is not None
    assert metrics.functional_integrity_ok is True
    assert metrics.first_fallback_iteration == 13
    assert metrics.expected_first_fallback_iteration == 13
    assert metrics.fallback_iteration_count == 2
    assert metrics.expected_fallback_iteration_count == 2
    assert metrics.degraded_fallback_iteration_count == 2
    assert metrics.fallback_cycle_executed_count == 2
    assert metrics.not_required_fallback_iteration_count == 2
    assert metrics.post_candidate_non_fallback_iteration_count == 0
    assert metrics.post_candidate_source_candidate_iteration_count == 0
    assert metrics.post_candidate_rejected_iteration_count == 0
    assert metrics.manifest_success_count == 14
    assert metrics.manifest_failure_count == 0
    assert metrics.candidate_manifest_count == 2
    assert metrics.candidate_alarm_document_read_count == 2
    assert metrics.candidate_tool_document_read_count == 2
    assert metrics.candidate_contract_failure_count == 2
    assert metrics.cache_replace_count == 1
    assert metrics.post_candidate_cache_replace_count == 0
    assert metrics.effective_cache_alarm_revision == 'PERF-AC-1'
    assert metrics.effective_cache_tool_revision == 'PERF-TR-1'
    assert metrics.source_revision_durable_record_count == 10
    assert metrics.candidate_revision_durable_record_count == 0
    assert metrics.unexpected_revision_durable_record_count == 0
    assert metrics.source_state_basis_snapshot_count == 10
    assert metrics.candidate_state_basis_snapshot_count == 0
    assert metrics.unexpected_state_basis_snapshot_count == 0


def _e010_scenario(*, alarm_count: int = 1000) -> BaselineScenario:
    return BaselineScenario(
        test_id='E-010',
        alarm_count=alarm_count,
        duration_seconds=600,
        iteration_period_seconds=5,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        columns_per_alarm=1,
        priority_group_size=10 if alarm_count == 1000 else 4,
        operational_churn_percent=10,
        physical_partition_count=36,
        physical_partition_layout='balanced',
        initial_active_percent=50,
        signal_value=1.0,
        threshold=0.5,
        lease_loss_adoption_at_seconds=60,
    )


def test_e010_builds_structural_reset_target_for_lease_takeover(tmp_path: Path) -> None:
    scenario = _e010_scenario()
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    assert scenario.has_lease_loss_adoption_pressure is True
    assert scenario.lease_loss_structural_reset_alarm_count == 50
    assert scenario.lease_loss_structural_reset_priority_group_count == 5
    assert runtime.target_revision is not None
    assert runtime.tracked_revision_cache is not None

    plan = plan_configuration_adoption(runtime.revision, runtime.target_revision)
    assert plan.is_adoptable is True
    assert (
        sum(
            change.disposition is ConfigurationAdoptionDisposition.STRUCTURAL_RESET
            for change in plan.changes
        )
        == 50
    )
    assert (
        sum(
            change.disposition is ConfigurationAdoptionDisposition.UNCHANGED
            for change in plan.changes
        )
        == 950
    )
    assert plan.structural_reset_groups == tuple(f'perf-group-{index:03d}' for index in range(1, 6))

    base_at = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    assert (
        _e010_iteration_as_of(
            schedule_base_at=base_at,
            iteration=1,
            period_seconds=scenario.iteration_period_seconds,
        )
        == base_at
    )
    assert _e010_iteration_as_of(
        schedule_base_at=base_at,
        iteration=13,
        period_seconds=scenario.iteration_period_seconds,
    ) == base_at + timedelta(seconds=60)
    with pytest.raises(
        RuntimeError,
        match='E-010 iteration schedule must resolve to whole-second as_of',
    ):
        _e010_iteration_as_of(
            schedule_base_at=base_at,
            iteration=2,
            period_seconds=0.5,
        )


def test_e010_rejects_invalid_lease_loss_adoption_contract() -> None:
    base = dict(
        test_id='E-010',
        alarm_count=1000,
        duration_seconds=600,
        iteration_period_seconds=5,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        priority_group_size=10,
        operational_churn_percent=10,
        physical_partition_count=36,
        physical_partition_layout='balanced',
        initial_active_percent=50,
        signal_value=1.0,
        threshold=0.5,
        lease_loss_adoption_at_seconds=60,
    )
    cases = (
        (
            {'lease_loss_adoption_at_seconds': -1},
            'lease_loss_adoption_at_seconds must not be negative',
        ),
        (
            {'data_profile': 'shared-latest', 'physical_partition_count': 1},
            'operational churn requires data_profile=latest-narrow',
        ),
        (
            {'operational_churn_percent': 5},
            'lease loss adoption pressure requires operational churn 10%',
        ),
        (
            {'initial_active_percent': 100},
            'operational churn requires initial_active_percent=50',
        ),
        (
            {'lease_loss_adoption_at_seconds': 65},
            'lease loss adoption timing must align with data refresh',
        ),
        (
            {'iteration_period_seconds': 7},
            'lease loss adoption timing must align with iteration period',
        ),
        (
            {'duration_seconds': 65},
            'lease loss adoption duration must include takeover replay',
        ),
        (
            {'source_unavailable_at_seconds': 60},
            'lease loss adoption pressure must not combine with another pressure',
        ),
    )
    for override, message in cases:
        with pytest.raises(ValueError, match=message):
            BaselineScenario(**(base | override))


def _e010_synthetic_evidence(scenario: BaselineScenario, runtime):
    base_at = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    first_generation = int(base_at.timestamp()) // scenario.data_refresh_seconds
    source_revision = runtime.revision.alarm_configuration_revision
    target_revision = runtime.target_revision.alarm_configuration_revision
    tool_revision = runtime.revision.tool_registry_revision
    entries = []
    adoption_commit_ids = []
    restart_commit_ids = []
    generation_six_commit_ids = []
    closed_occurrence_ids = []
    closed_episode_ids = []

    def make_entry(
        *,
        group_index: int,
        revision: str,
        evaluated_at: datetime,
        commit_id: str,
        occurrence_changes=(),
        episode_changes=(),
        assignment_changes=(),
    ):
        return SimpleNamespace(
            record=SimpleNamespace(
                commit=SimpleNamespace(
                    commit_id=commit_id,
                    priority_group=f'perf-group-{group_index:03d}',
                    evaluated_at=evaluated_at.isoformat().replace('+00:00', 'Z'),
                    alarm_configuration_revision=revision,
                    tool_registry_revision=tool_revision,
                ),
                records={
                    'occurrence_changes': list(occurrence_changes),
                    'episode_changes': list(episode_changes),
                    'assignment_changes': list(assignment_changes),
                    'journey_events': [],
                    'evidence_records': [],
                },
            )
        )

    for group_index in range(1, 101):
        alarm_keys = [
            f'perf/alarm_{(group_index - 1) * scenario.effective_priority_group_size + offset:05d}'
            for offset in range(1, scenario.effective_priority_group_size + 1)
        ]
        active_keys = alarm_keys[: scenario.effective_priority_group_size // 2]
        occurrences = [
            {
                'kind': 'STARTED',
                'alarm_key': alarm_key,
                'occurrence_id': f'bootstrap-occ-{group_index:03d}-{index}',
                'started_at': base_at.isoformat().replace('+00:00', 'Z'),
            }
            for index, alarm_key in enumerate(active_keys, start=1)
        ]
        assignments = [
            {
                'kind': 'ASSIGNED',
                'alarm_key': alarm_key,
                'occurrence_id': f'bootstrap-occ-{group_index:03d}-{index}',
                'tool_key': 'perf-tool',
            }
            for index, alarm_key in enumerate(active_keys, start=1)
        ]
        entries.append(
            make_entry(
                group_index=group_index,
                revision=source_revision,
                evaluated_at=base_at,
                commit_id=f'bootstrap-{group_index:03d}',
                occurrence_changes=occurrences,
                episode_changes=(
                    {
                        'kind': 'STARTED',
                        'episode_id': f'bootstrap-episode-{group_index:03d}',
                    },
                ),
                assignment_changes=assignments,
            )
        )

    for generation_index in range(1, 61):
        cohort = (generation_index - 1) % 10
        evaluated_at = base_at + timedelta(
            seconds=65 if generation_index == 6 else generation_index * 10
        )
        revision = source_revision if generation_index <= 5 else target_revision
        for offset in range(10):
            group_index = cohort * 10 + offset + 1
            alarm_start = (group_index - 1) * scenario.effective_priority_group_size + 1
            alarm_keys = [
                f'perf/alarm_{alarm_start + alarm_offset:05d}'
                for alarm_offset in range(scenario.effective_priority_group_size)
            ]
            closing_keys = alarm_keys[: scenario.effective_priority_group_size // 2]
            starting_keys = alarm_keys[scenario.effective_priority_group_size // 2 :]
            occurrences = [
                {
                    'kind': 'CLOSED',
                    'alarm_key': alarm_key,
                    'occurrence_id': f'churn-close-{generation_index:02d}-{group_index:03d}-{index}',
                    'closure_reason': 'condition_normalized',
                }
                for index, alarm_key in enumerate(closing_keys, start=1)
            ] + [
                {
                    'kind': 'STARTED',
                    'alarm_key': alarm_key,
                    'occurrence_id': f'churn-start-{generation_index:02d}-{group_index:03d}-{index}',
                    'started_at': evaluated_at.isoformat().replace('+00:00', 'Z'),
                }
                for index, alarm_key in enumerate(starting_keys, start=1)
            ]
            assignments = [
                {
                    'kind': 'ASSIGNED',
                    'alarm_key': alarm_key,
                    'occurrence_id': f'churn-start-{generation_index:02d}-{group_index:03d}-{index}',
                    'tool_key': 'perf-tool',
                }
                for index, alarm_key in enumerate(starting_keys, start=1)
            ]
            commit_id = f'churn-{generation_index:02d}-{group_index:03d}'
            entries.append(
                make_entry(
                    group_index=group_index,
                    revision=revision,
                    evaluated_at=evaluated_at,
                    commit_id=commit_id,
                    occurrence_changes=occurrences,
                    assignment_changes=assignments,
                )
            )
            if generation_index == 6:
                generation_six_commit_ids.append(commit_id)

    adoption_at = base_at + timedelta(seconds=60)
    restart_at = base_at + timedelta(seconds=65)
    for group_index in range(1, 6):
        alarm_start = (group_index - 1) * scenario.effective_priority_group_size + 1
        reset_alarm_keys = [
            f'perf/alarm_{alarm_start + offset:05d}'
            for offset in range(scenario.effective_priority_group_size // 2)
        ]
        occurrence_changes = []
        for index, alarm_key in enumerate(reset_alarm_keys, start=1):
            occurrence_id = f'reset-old-{group_index:03d}-{index}'
            closed_occurrence_ids.append(occurrence_id)
            occurrence_changes.append(
                {
                    'kind': 'CLOSED',
                    'alarm_key': alarm_key,
                    'occurrence_id': occurrence_id,
                    'closure_reason': 'configuration_reconfigured',
                }
            )
        episode_id = f'reset-old-episode-{group_index:03d}'
        closed_episode_ids.append(episode_id)
        adoption_id = f'adoption-{group_index:03d}'
        adoption_commit_ids.append(adoption_id)
        entries.append(
            make_entry(
                group_index=group_index,
                revision=target_revision,
                evaluated_at=adoption_at,
                commit_id=adoption_id,
                occurrence_changes=occurrence_changes,
                episode_changes=(
                    {
                        'kind': 'CLOSED',
                        'episode_id': episode_id,
                        'closure_reason': 'configuration_terminated',
                    },
                ),
            )
        )

        restart_occurrences = [
            {
                'kind': 'STARTED',
                'alarm_key': alarm_key,
                'occurrence_id': f'reset-new-{group_index:03d}-{index}',
                'started_at': restart_at.isoformat().replace('+00:00', 'Z'),
            }
            for index, alarm_key in enumerate(reset_alarm_keys, start=1)
        ]
        restart_assignments = [
            {
                'kind': 'ASSIGNED',
                'alarm_key': alarm_key,
                'occurrence_id': f'reset-new-{group_index:03d}-{index}',
                'tool_key': 'perf-tool',
            }
            for index, alarm_key in enumerate(reset_alarm_keys, start=1)
        ]
        restart_id = f'restart-{group_index:03d}'
        restart_commit_ids.append(restart_id)
        entries.append(
            make_entry(
                group_index=group_index,
                revision=target_revision,
                evaluated_at=restart_at,
                commit_id=restart_id,
                occurrence_changes=restart_occurrences,
                episode_changes=(
                    {
                        'kind': 'STARTED',
                        'episode_id': f'reset-new-episode-{group_index:03d}',
                    },
                ),
                assignment_changes=restart_assignments,
            )
        )

    snapshots = []
    active_per_group = scenario.effective_priority_group_size // 2
    for group_index in range(1, 101):
        alarms = {}
        for index in range(active_per_group):
            alarm_key = f'perf/alarm_{(group_index - 1) * scenario.effective_priority_group_size + index + 1:05d}'
            alarms[alarm_key] = {
                'occurrence': {
                    'assignments': [{'tool_key': 'perf-tool'}],
                    'pending_assignments': [],
                }
            }
        document = {
            'state_basis': {
                'alarm_configuration_revision': target_revision,
                'tool_registry_revision': tool_revision,
            },
            'episode': {'episode_id': f'final-episode-{group_index:03d}'},
            'alarms': alarms,
        }
        snapshots.append(SimpleNamespace(as_document=lambda value=document: value))

    source_loader = SimpleNamespace(
        first_generation=first_generation,
        churn_generation_count=60,
        churn_group_transition_count=600,
        churn_transition_count=scenario.alarm_count * 10 // 100 * 60,
        technical_hold_started_transition_count=0,
        technical_hold_cleared_transition_count=0,
    )
    samples = [
        SimpleNamespace(
            iteration=iteration,
            revision_origin='source_candidate' if iteration == 1 else 'cache_current',
            adoption_outcome='bootstrapped' if iteration == 1 else 'not_required',
            cycle_executed=True,
            degraded=False,
        )
        for iteration in range(1, 13)
    ]
    samples.append(
        SimpleNamespace(
            iteration=14,
            revision_origin='source_candidate',
            adoption_outcome='adopted',
            cycle_executed=True,
            degraded=False,
        )
    )
    samples.extend(
        SimpleNamespace(
            iteration=iteration,
            revision_origin='cache_current',
            adoption_outcome='not_required',
            cycle_executed=True,
            degraded=False,
        )
        for iteration in range(15, 122)
    )
    owner_a_state = {
        'lease_generation': 1,
        'failed_iteration': 13,
        'failed_iteration_ms': 1200.0,
        'lease_loss_observed': True,
        'journal_aligned_before_loss': True,
        'cache_alarm_revision_before_loss': source_revision,
        'cache_tool_revision_before_loss': tool_revision,
        'cache_replace_count': 1,
        'adoption_commit_ids': adoption_commit_ids,
        'adoption_closed_occurrence_ids': closed_occurrence_ids,
        'adoption_closed_episode_ids': closed_episode_ids,
    }
    owner_b_state = {
        'lease_generation': 2,
        'first_iteration': 14,
        'first_revision_origin': 'source_candidate',
        'first_adoption_outcome': 'adopted',
        'first_cycle_executed': True,
        'first_degraded': False,
        'first_new_commit_ids': [*restart_commit_ids, *generation_six_commit_ids],
        'recovery': {
            'applied_count': 0,
            'skipped_count': 0,
            'discarded_tail_bytes': 0,
        },
        'cache_replace_count': 1,
        'final_cache_alarm_revision': target_revision,
        'final_cache_tool_revision': tool_revision,
    }
    return (
        tuple(entries),
        tuple(snapshots),
        source_loader,
        tuple(samples),
        owner_a_state,
        owner_b_state,
    )


def test_e010_functional_pressure_counts_reset_and_restart_without_duplicate_commit(
    tmp_path: Path,
) -> None:
    scenario = _e010_scenario(alarm_count=400)
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    records, snapshots, source_loader, _, _, _ = _e010_synthetic_evidence(scenario, runtime)

    metrics = _build_functional_pressure_metrics(
        scenario=scenario,
        source_loader=source_loader,
        records=records,
        snapshots=snapshots,
    )
    assert metrics is not None
    assert metrics.functional_integrity_ok is True
    assert metrics.lifecycle_commit_count == 710
    assert metrics.expected_lifecycle_commit_count == 710
    assert metrics.duplicate_lifecycle_commit_count == 0
    assert metrics.occurrence_started_count == 1410
    assert metrics.occurrence_closed_count == 1210
    assert metrics.episode_started_count == 105
    assert metrics.episode_closed_count == 5
    assert metrics.assignment_change_count == 1410


def test_e010_metrics_require_generation_takeover_and_idempotent_adoption_replay(
    tmp_path: Path,
) -> None:
    scenario = _e010_scenario(alarm_count=400)
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    records, snapshots, source_loader, samples, owner_a_state, owner_b_state = (
        _e010_synthetic_evidence(scenario, runtime)
    )
    functional = _build_functional_pressure_metrics(
        scenario=scenario,
        source_loader=source_loader,
        records=records,
        snapshots=snapshots,
    )
    metrics = _build_lease_loss_adoption_pressure_metrics(
        scenario=scenario,
        runtime=runtime,
        records=records,
        snapshots=snapshots,
        samples=samples,
        owner_a_state=owner_a_state,
        owner_b_state=owner_b_state,
        preflight_status='PASS',
        functional_pressure=functional,
    )
    assert metrics is not None
    assert metrics.functional_integrity_ok is True
    assert metrics.owner_a_generation == 1
    assert metrics.owner_b_generation == 2
    assert metrics.owner_a_failed_iteration == 13
    assert metrics.owner_b_first_iteration == 14
    assert metrics.owner_a_adoption_commit_count == 5
    assert metrics.owner_b_replay_adoption_commit_count == 0
    assert metrics.owner_b_post_replay_cache_current_count == 107
    assert metrics.stale_owner_cache_write_count == 0
    assert metrics.configuration_reconfigured_occurrence_count == 10
    assert metrics.restarted_occurrence_count == 10
    assert metrics.occurrence_identity_reuse_count == 0
    assert metrics.source_revision_durable_record_count == 150
    assert metrics.target_revision_durable_record_count == 560
    assert metrics.durable_record_count == 710
    assert metrics.groups_with_7_records == 95
    assert metrics.groups_with_9_records == 5
    assert metrics.target_state_basis_snapshot_count == 100
    assert metrics.final_alarm_count == 200
    assert metrics.final_assignment_count == 200
    assert metrics.final_pending_assignment_count == 0
    assert metrics.open_occurrence_count == 200
    assert metrics.open_episode_count == 100


def _e011_scenario(*, alarm_count: int = 1000) -> BaselineScenario:
    return BaselineScenario(
        test_id='E-011',
        alarm_count=alarm_count,
        duration_seconds=600,
        iteration_period_seconds=5,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        columns_per_alarm=1,
        priority_group_size=10 if alarm_count == 1000 else 4,
        operational_churn_percent=10,
        physical_partition_count=36 if alarm_count >= 36 else alarm_count,
        physical_partition_layout='balanced',
        initial_active_percent=50,
        signal_value=1.0,
        threshold=0.5,
        cache_promotion_failure_at_seconds=60,
    )


def _revision_bundle_from_source(source) -> RuntimeRevisionBundle:
    manifest = source.read_manifest()
    return RuntimeRevisionBundle(
        manifest=manifest,
        alarm_configuration=source.read_alarm_configuration(
            revision=manifest.alarm_configuration_revision
        ),
        tool_registry=source.read_tool_registry(revision=manifest.tool_registry_revision),
    )


def _force_e011_scheduled_target_visible(source, *, elapsed_seconds: int = 61) -> None:
    source.started_monotonic -= elapsed_seconds
    source.base_at -= timedelta(seconds=elapsed_seconds)
    assert source.target_is_visible is True


def test_e011_cache_wrapper_fails_target_before_physical_mutation(tmp_path: Path) -> None:
    scenario = _e011_scenario()
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    assert scenario.has_cache_promotion_failure_pressure is True
    assert scenario.cache_promotion_failure_structural_reset_alarm_count == 50
    assert scenario.cache_promotion_failure_structural_reset_priority_group_count == 5
    assert runtime.target_revision is not None
    assert runtime.cache_promotion_failure_cache is not None

    plan = plan_configuration_adoption(runtime.revision, runtime.target_revision)
    counts = {item: 0 for item in ConfigurationAdoptionDisposition}
    for change in plan.changes:
        counts[change.disposition] += 1
    assert counts[ConfigurationAdoptionDisposition.UNCHANGED] == 950
    assert counts[ConfigurationAdoptionDisposition.STRUCTURAL_RESET] == 50
    assert plan.structural_reset_groups == tuple(f'perf-group-{index:03d}' for index in range(1, 6))

    source = runtime.job.revision_resolver.source
    cache = runtime.cache_promotion_failure_cache
    cache.replace_effective(bundle=_revision_bundle_from_source(source))
    assert cache.load_effective().manifest.alarm_configuration_revision == 'PERF-AC-1'
    assert len(cache.replace_monotonic) == 1

    _force_e011_scheduled_target_visible(source)
    target_bundle = _revision_bundle_from_source(source)
    assert target_bundle.manifest.alarm_configuration_revision == 'PERF-AC-2'
    with pytest.raises(
        InjectedCachePromotionError,
        match='injected target revision cache promotion failure',
    ):
        cache.replace_effective(bundle=target_bundle)
    assert len(cache.target_attempt_monotonic) == 1
    assert len(cache.target_failure_monotonic) == 1
    assert cache.successful_target_replace_count == 0
    assert cache.load_effective().manifest.alarm_configuration_revision == 'PERF-AC-1'

    base_at = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    assert (
        _e011_iteration_as_of(
            schedule_base_at=base_at,
            iteration=1,
            period_seconds=5,
        )
        == base_at
    )
    assert _e011_iteration_as_of(
        schedule_base_at=base_at,
        iteration=13,
        period_seconds=5,
    ) == base_at + timedelta(seconds=60)
    with pytest.raises(
        RuntimeError,
        match='E-011 iteration schedule must resolve to whole-second as_of',
    ):
        _e011_iteration_as_of(
            schedule_base_at=base_at,
            iteration=2,
            period_seconds=0.5,
        )


def test_e011_rejects_invalid_cache_promotion_failure_contract() -> None:
    base = dict(
        test_id='E-011',
        alarm_count=1000,
        duration_seconds=600,
        iteration_period_seconds=5,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        columns_per_alarm=1,
        priority_group_size=10,
        operational_churn_percent=10,
        physical_partition_count=36,
        physical_partition_layout='balanced',
        initial_active_percent=50,
        signal_value=1.0,
        threshold=0.5,
        cache_promotion_failure_at_seconds=60,
    )
    cases = (
        ({'data_profile': 'latest-wide', 'columns_per_alarm': 2}, 'requires data_profile'),
        ({'operational_churn_percent': 9}, 'requires operational churn 10%'),
        ({'initial_active_percent': 100}, 'requires initial_active_percent=50'),
        ({'cache_promotion_failure_at_seconds': 55}, 'must align with data refresh'),
        ({'lease_loss_adoption_at_seconds': 60}, 'must not combine with another pressure'),
    )
    for override, message in cases:
        with pytest.raises(ValueError, match=message):
            BaselineScenario(**(base | override))


def _e011_synthetic_evidence(scenario: BaselineScenario, runtime):
    base_at = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    source_revision = runtime.revision.alarm_configuration_revision
    target_revision = runtime.target_revision.alarm_configuration_revision
    tool_revision = runtime.revision.tool_registry_revision
    entries = []
    last_commit_by_group = {}

    def make_entry(
        *,
        group_index: int,
        revision: str,
        evaluated_at: datetime,
        commit_id: str,
        occurrence_changes=(),
        episode_changes=(),
        assignment_changes=(),
    ):
        group = f'perf-group-{group_index:03d}'
        previous_commit_id = last_commit_by_group.get(group)
        last_commit_by_group[group] = commit_id
        return SimpleNamespace(
            record=SimpleNamespace(
                commit=SimpleNamespace(
                    commit_id=commit_id,
                    previous_commit_id=previous_commit_id,
                    priority_group=group,
                    evaluated_at=evaluated_at.isoformat().replace('+00:00', 'Z'),
                    alarm_configuration_revision=revision,
                    tool_registry_revision=tool_revision,
                ),
                records={
                    'occurrence_changes': list(occurrence_changes),
                    'episode_changes': list(episode_changes),
                    'assignment_changes': list(assignment_changes),
                    'journey_events': [],
                    'evidence_records': [],
                },
            )
        )

    active_per_group = scenario.effective_priority_group_size // 2
    for group_index in range(1, 101):
        alarm_start = (group_index - 1) * scenario.effective_priority_group_size + 1
        active_keys = [
            f'perf/alarm_{alarm_start + offset:05d}' for offset in range(active_per_group)
        ]
        entries.append(
            make_entry(
                group_index=group_index,
                revision=source_revision,
                evaluated_at=base_at,
                commit_id=f'bootstrap-{group_index:03d}',
                occurrence_changes=tuple(
                    {
                        'kind': 'STARTED',
                        'alarm_key': alarm_key,
                        'occurrence_id': f'bootstrap-{group_index:03d}-{index}',
                    }
                    for index, alarm_key in enumerate(active_keys, start=1)
                ),
                episode_changes=(
                    {
                        'kind': 'STARTED',
                        'episode_id': f'episode-{group_index:03d}',
                    },
                ),
                assignment_changes=tuple(
                    {
                        'kind': 'ASSIGNED',
                        'alarm_key': alarm_key,
                        'occurrence_id': f'bootstrap-{group_index:03d}-{index}',
                        'tool_key': 'perf-tool',
                    }
                    for index, alarm_key in enumerate(active_keys, start=1)
                ),
            )
        )

    for generation_index in range(1, 6):
        cohort = generation_index - 1
        evaluated_at = base_at + timedelta(seconds=generation_index * 10)
        for offset in range(10):
            group_index = cohort * 10 + offset + 1
            alarm_start = (group_index - 1) * scenario.effective_priority_group_size + 1
            alarm_keys = [
                f'perf/alarm_{alarm_start + item:05d}'
                for item in range(scenario.effective_priority_group_size)
            ]
            closing_keys = alarm_keys[:active_per_group]
            starting_keys = alarm_keys[active_per_group:]
            entries.append(
                make_entry(
                    group_index=group_index,
                    revision=source_revision,
                    evaluated_at=evaluated_at,
                    commit_id=f'churn-{generation_index:02d}-{group_index:03d}',
                    occurrence_changes=tuple(
                        {
                            'kind': 'CLOSED',
                            'alarm_key': alarm_key,
                            'occurrence_id': f'old-{generation_index:02d}-{group_index:03d}-{index}',
                            'closure_reason': 'condition_normalized',
                        }
                        for index, alarm_key in enumerate(closing_keys, start=1)
                    )
                    + tuple(
                        {
                            'kind': 'STARTED',
                            'alarm_key': alarm_key,
                            'occurrence_id': f'new-{generation_index:02d}-{group_index:03d}-{index}',
                        }
                        for index, alarm_key in enumerate(starting_keys, start=1)
                    ),
                    assignment_changes=tuple(
                        {
                            'kind': 'ASSIGNED',
                            'alarm_key': alarm_key,
                            'occurrence_id': f'new-{generation_index:02d}-{group_index:03d}-{index}',
                            'tool_key': 'perf-tool',
                        }
                        for index, alarm_key in enumerate(starting_keys, start=1)
                    ),
                )
            )

    adoption_at = base_at + timedelta(seconds=60)
    for group_index in range(1, 6):
        alarm_start = (group_index - 1) * scenario.effective_priority_group_size + 1
        reset_keys = [
            f'perf/alarm_{alarm_start + offset:05d}' for offset in range(active_per_group)
        ]
        entries.append(
            make_entry(
                group_index=group_index,
                revision=target_revision,
                evaluated_at=adoption_at,
                commit_id=f'adoption-{group_index:03d}',
                occurrence_changes=tuple(
                    {
                        'kind': 'CLOSED',
                        'alarm_key': alarm_key,
                        'occurrence_id': f'reset-{group_index:03d}-{index}',
                        'closure_reason': 'configuration_reconfigured',
                    }
                    for index, alarm_key in enumerate(reset_keys, start=1)
                ),
                episode_changes=(
                    {
                        'kind': 'CLOSED',
                        'episode_id': f'episode-{group_index:03d}',
                        'closure_reason': 'configuration_terminated',
                    },
                ),
            )
        )

    snapshots = []
    for group_index in range(1, 101):
        if group_index <= 5:
            document = {
                'priority_group': f'perf-group-{group_index:03d}',
                'last_commit_id': last_commit_by_group[f'perf-group-{group_index:03d}'],
                'episode': None,
                'alarms': {},
            }
        else:
            alarm_start = (group_index - 1) * scenario.effective_priority_group_size + 1
            alarms = {
                f'perf/alarm_{alarm_start + offset:05d}': {
                    'occurrence': {
                        'assignments': [{'tool_key': 'perf-tool'}],
                        'pending_assignments': [],
                    }
                }
                for offset in range(active_per_group)
            }
            document = {
                'priority_group': f'perf-group-{group_index:03d}',
                'last_commit_id': last_commit_by_group[f'perf-group-{group_index:03d}'],
                'state_basis': {
                    'alarm_configuration_revision': source_revision,
                    'tool_registry_revision': tool_revision,
                },
                'episode': {'episode_id': f'episode-{group_index:03d}'},
                'alarms': alarms,
            }
        snapshots.append(SimpleNamespace(as_document=lambda value=document: value))

    cache = runtime.cache_promotion_failure_cache
    source = runtime.job.revision_resolver.source
    cache.replace_effective(bundle=_revision_bundle_from_source(source))
    _force_e011_scheduled_target_visible(source)
    target_bundle = _revision_bundle_from_source(source)
    assert target_bundle.manifest.alarm_configuration_revision == 'PERF-AC-2'
    with pytest.raises(InjectedCachePromotionError):
        cache.replace_effective(bundle=target_bundle)
    samples = tuple(SimpleNamespace(iteration=index) for index in range(1, 13))
    failure = InjectedCachePromotionError('injected target revision cache promotion failure')
    return tuple(entries), tuple(snapshots), samples, failure, make_entry


def test_e011_metrics_require_fail_closed_prefix_and_old_physical_cache(tmp_path: Path) -> None:
    scenario = _e011_scenario(alarm_count=400)
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    records, snapshots, samples, failure, _ = _e011_synthetic_evidence(scenario, runtime)
    metrics = _build_cache_promotion_failure_pressure_metrics(
        scenario=scenario,
        runtime=runtime,
        records=records,
        snapshots=snapshots,
        samples=samples,
        failed_iteration=13,
        failed_iteration_ms=1200.0,
        failure=failure,
        durable_materialized_aligned=True,
    )
    assert metrics is not None
    assert metrics.functional_integrity_ok is True
    assert metrics.successful_iteration_count == 12
    assert metrics.failed_iteration == 13
    assert metrics.cache_replace_count == 1
    assert metrics.target_promotion_attempt_count == 1
    assert metrics.target_promotion_failure_count == 1
    assert metrics.successful_target_replace_count == 0
    assert metrics.final_cache_alarm_revision == 'PERF-AC-1'
    assert metrics.source_revision_durable_record_count == 150
    assert metrics.target_revision_durable_record_count == 5
    assert metrics.durable_record_count == 155
    assert metrics.groups_with_1_record == 50
    assert metrics.groups_with_2_records == 45
    assert metrics.groups_with_3_records == 5
    assert metrics.source_state_basis_snapshot_count == 95
    assert metrics.target_state_basis_snapshot_count == 0
    assert metrics.reset_snapshot_count == 5
    assert metrics.reset_empty_snapshot_count == 5
    assert metrics.reset_snapshot_without_state_basis_count == 5
    assert metrics.reset_snapshot_target_last_commit_count == 5
    assert metrics.occurrence_started_count == 300
    assert metrics.occurrence_closed_count == 110
    assert metrics.episode_started_count == 100
    assert metrics.episode_closed_count == 5
    assert metrics.assignment_change_count == 300
    assert metrics.final_alarm_count == 190
    assert metrics.final_assignment_count == 190
    assert metrics.open_occurrence_count == 190
    assert metrics.open_episode_count == 95


def test_e011_metrics_reject_target_operational_continuation(tmp_path: Path) -> None:
    scenario = _e011_scenario(alarm_count=400)
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    records, snapshots, samples, failure, make_entry = _e011_synthetic_evidence(scenario, runtime)
    continued = make_entry(
        group_index=6,
        revision=runtime.target_revision.alarm_configuration_revision,
        evaluated_at=datetime(2026, 8, 30, 12, 1, 5, tzinfo=UTC),
        commit_id='unexpected-target-cycle',
    )
    metrics = _build_cache_promotion_failure_pressure_metrics(
        scenario=scenario,
        runtime=runtime,
        records=(*records, continued),
        snapshots=snapshots,
        samples=samples,
        failed_iteration=13,
        failed_iteration_ms=1200.0,
        failure=failure,
        durable_materialized_aligned=True,
    )
    assert metrics is not None
    assert metrics.unexpected_target_operational_commit_count == 1
    assert metrics.functional_integrity_ok is False

    wrong_reset_document = dict(snapshots[0].as_document())
    wrong_reset_document['last_commit_id'] = 'wrong-target-adoption-commit'
    wrong_reset_snapshot = SimpleNamespace(as_document=lambda value=wrong_reset_document: value)
    wrong_snapshot_metrics = _build_cache_promotion_failure_pressure_metrics(
        scenario=scenario,
        runtime=runtime,
        records=records,
        snapshots=(wrong_reset_snapshot, *snapshots[1:]),
        samples=samples,
        failed_iteration=13,
        failed_iteration_ms=1200.0,
        failure=failure,
        durable_materialized_aligned=True,
    )
    assert wrong_snapshot_metrics is not None
    assert wrong_snapshot_metrics.reset_snapshot_target_last_commit_count == 4
    assert wrong_snapshot_metrics.functional_integrity_ok is False


def _e012_scenario() -> BaselineScenario:
    return BaselineScenario(
        test_id='E-012',
        alarm_count=1000,
        duration_seconds=600,
        iteration_period_seconds=5,
        data_refresh_seconds=10,
        data_profile='latest-narrow',
        columns_per_alarm=1,
        priority_group_size=10,
        operational_churn_percent=0,
        physical_partition_count=36,
        physical_partition_layout='balanced',
        management_action_at_seconds=30,
        management_action_count=480,
        management_action_interval_seconds=1,
        deactivation_decision_at_seconds=60,
        deactivation_decision_count=480,
        deactivation_decision_interval_seconds=1,
        deactivation_window_seconds=900,
        initial_active_percent=100,
        signal_value=1.0,
        threshold=0.5,
        drain_under_workload_at_seconds=300,
    )


def _e012_synthetic_evidence(scenario: BaselineScenario, runtime):
    entries = []
    snapshots = []
    latest_document_by_group = {}
    for group_index in range(1, 101):
        priority_group = f'perf-group-{group_index:03d}'
        record_count = 8 if group_index <= 2 else 7
        previous_commit_id = None
        for record_index in range(1, record_count + 1):
            commit_id = f'e012-{group_index:03d}-{record_index:02d}'
            document = {
                'priority_group': priority_group,
                'last_commit_id': commit_id,
                'state_basis': {
                    'alarm_configuration_revision': 'PERF-AC-1',
                    'tool_registry_revision': 'PERF-TR-1',
                },
                'episode': {'episode_id': f'episode-{group_index:03d}'},
                'alarms': {
                    f'perf/alarm_{(group_index - 1) * 10 + offset + 1:05d}': {}
                    for offset in range(10)
                },
            }
            entries.append(
                SimpleNamespace(
                    record=SimpleNamespace(
                        commit=SimpleNamespace(
                            commit_id=commit_id,
                            previous_commit_id=previous_commit_id,
                            priority_group=priority_group,
                        ),
                        snapshot_after=SimpleNamespace(as_document=lambda value=document: value),
                    )
                )
            )
            previous_commit_id = commit_id
            latest_document_by_group[priority_group] = document
        snapshots.append(
            SimpleNamespace(
                as_document=lambda value=latest_document_by_group[priority_group]: value
            )
        )

    snapshot_documents = tuple(
        sorted(
            __import__('json').dumps(snapshot.as_document(), sort_keys=True, separators=(',', ':'))
            for snapshot in snapshots
        )
    )
    evidence = {
        'durable_record_count': 702,
        'journal_bytes': 8_765_432,
        'durable_head': 'segment-001:8765432',
        'materialized_head': 'segment-001:8765432',
        'aligned': True,
        'snapshot_count': 100,
        'snapshot_documents': snapshot_documents,
        'source_load_count': 61,
        'management_read_batch_count': 61,
        'decision_read_batch_count': 61,
        'management_read_at_count': 0,
        'decision_read_at_count': 0,
        'management_consumed_count': 271,
        'decision_consumed_count': 241,
        'cache_replace_count': 1,
        'management_cursor_byte_offset': 69_376,
        'decision_cursor_byte_offset': 61_696,
        'management_pending_count': 0,
        'decision_pending_count': 0,
        'pending_deactivation_request_count': 30,
    }
    measured = SimpleNamespace(
        stop_requested_iteration=61,
        recorder=SimpleNamespace(samples=tuple(range(61))),
        boundary_evidence=dict(evidence),
        drain_before_evidence=dict(evidence),
        drain_after_evidence=dict(evidence),
        drain_recovery_result=SimpleNamespace(applied_count=0, discarded_tail_bytes=0),
    )
    execution = SimpleNamespace(iteration_count=61, stop_reason='performance_drain_boundary')
    return tuple(entries), tuple(snapshots), measured, execution


def test_e012_scenario_freezes_d009_prefix_and_stop_boundary(tmp_path: Path) -> None:
    scenario = _e012_scenario()
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    assert scenario.has_drain_under_workload_pressure is True
    assert scenario.drain_under_workload_stop_iteration == 61
    assert scenario.drain_under_workload_management_consumed_count == 271
    assert scenario.drain_under_workload_decision_consumed_count == 241
    assert scenario.drain_under_workload_pending_request_count == 30
    assert scenario.drain_under_workload_expected_durable_record_count == 702
    assert isinstance(runtime.input_source, MixedDeactivationInputSource)
    assert runtime.input_source.input_count == 480
    assert runtime.input_source.byte_length == 256
    assert runtime.tracked_revision_cache is not None


def test_e012_scenario_rejects_noncanonical_stop_boundary() -> None:
    with pytest.raises(
        ValueError,
        match='drain-under-workload pressure requires stop at \\+300 seconds',
    ):
        replace(_e012_scenario(), drain_under_workload_at_seconds=299)


def test_e012_metrics_require_no_new_frozen_unit_and_stable_drain(tmp_path: Path) -> None:
    scenario = _e012_scenario()
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    records, snapshots, measured, execution = _e012_synthetic_evidence(scenario, runtime)
    metrics = _build_drain_under_workload_pressure_metrics(
        scenario=scenario,
        runtime=runtime,
        measured=measured,
        execution=execution,
        records=records,
        snapshots=snapshots,
    )
    assert metrics.functional_integrity_ok is True
    assert metrics.stop_iteration == 61
    assert metrics.execution_iteration_count == 61
    assert metrics.management_consumed_count == 271
    assert metrics.decision_consumed_count == 241
    assert metrics.management_cursor_byte_offset == 69_376
    assert metrics.decision_cursor_byte_offset == 61_696
    assert metrics.pending_deactivation_request_count == 30
    assert metrics.future_management_count == 209
    assert metrics.future_decision_count == 239
    assert metrics.durable_record_count_before_drain == 702
    assert metrics.durable_record_count_after_drain == 702
    assert metrics.new_frozen_durable_unit_count == 0
    assert metrics.journal_bytes_unchanged is True
    assert metrics.journal_head_unchanged is True
    assert metrics.snapshot_documents_unchanged is True
    assert metrics.final_snapshot_match_count == 100
    assert metrics.recovery_applied_count == 0
    assert metrics.recovery_discarded_tail_bytes == 0
    assert metrics.post_drain_source_load_count == 0
    assert metrics.post_drain_management_read_count == 0
    assert metrics.post_drain_decision_read_count == 0
    assert metrics.post_drain_cache_replace_count == 0


def test_e012_metrics_reject_post_drain_work_or_snapshot_mutation(tmp_path: Path) -> None:
    scenario = _e012_scenario()
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=tmp_path / 'volume',
        source_path=tmp_path / 'source',
    )
    records, snapshots, measured, execution = _e012_synthetic_evidence(scenario, runtime)
    measured.drain_after_evidence['durable_record_count'] = 703
    measured.drain_after_evidence['source_load_count'] = 62
    measured.drain_after_evidence['management_read_batch_count'] = 62
    measured.drain_after_evidence['cache_replace_count'] = 2
    altered_documents = list(measured.drain_after_evidence['snapshot_documents'])
    altered_documents[0] = altered_documents[0] + 'x'
    measured.drain_after_evidence['snapshot_documents'] = tuple(altered_documents)
    metrics = _build_drain_under_workload_pressure_metrics(
        scenario=scenario,
        runtime=runtime,
        measured=measured,
        execution=execution,
        records=records,
        snapshots=snapshots,
    )
    assert metrics.new_frozen_durable_unit_count == 1
    assert metrics.post_drain_source_load_count == 1
    assert metrics.post_drain_management_read_count == 1
    assert metrics.post_drain_cache_replace_count == 1
    assert metrics.snapshot_documents_unchanged is False
    assert metrics.functional_integrity_ok is False


def _f001_scenario() -> BaselineScenario:
    return BaselineScenario(
        test_id='F-001',
        alarm_count=500,
        duration_seconds=1800,
        iteration_period_seconds=5,
        data_refresh_seconds=10,
        data_profile='shared-latest',
        columns_per_alarm=1,
        physical_partition_count=1,
        physical_partition_layout='balanced',
        initial_active_percent=100,
        soak_warmup_seconds=300,
        soak_window_seconds=300,
    )


def _f001_samples(
    *,
    rss_by_window: tuple[float, float, float, float, float],
    duration_by_window: tuple[float, float, float, float, float] = (
        400.0,
        405.0,
        410.0,
        415.0,
        420.0,
    ),
) -> tuple[IterationSample, ...]:
    samples: list[IterationSample] = []
    for index in range(361):
        iteration = index + 1
        if iteration <= 61:
            rss = 129.0
            duration = 390.0
        else:
            window_index = min((iteration - 62) // 60, 4)
            rss = rss_by_window[window_index]
            duration = duration_by_window[window_index]
        samples.append(
            IterationSample(
                iteration=iteration,
                started_monotonic=float(index * 5),
                start_interval_ms=None if iteration == 1 else 5000.0,
                duration_ms=duration,
                cpu_percent=90.0,
                rss_before_mb=rss,
                rss_after_mb=rss,
                revision_origin='cache_current',
                adoption_outcome='not_required',
                cycle_executed=True,
                degraded=False,
            )
        )
    return tuple(samples)


def _f001_records() -> tuple[SimpleNamespace, ...]:
    records = []
    previous = None
    for index in range(1, 8):
        commit_id = f'f001-{index:02d}'
        records.append(
            SimpleNamespace(
                record=SimpleNamespace(
                    commit=SimpleNamespace(
                        commit_id=commit_id,
                        previous_commit_id=previous,
                        priority_group='perf-group-001',
                    )
                )
            )
        )
        previous = commit_id
    return tuple(records)


def _f001_metrics(samples: tuple[IterationSample, ...]):
    scenario = _f001_scenario()
    metrics = _build_temporal_soak_metrics(
        scenario=scenario,
        records=_f001_records(),
        snapshots=(SimpleNamespace(),),
        samples=samples,
        journal_aligned=True,
        snapshot_alarm_count=500,
        expected_snapshot_alarm_count=500,
    )
    assert metrics is not None
    return metrics


def test_f001_scenario_freezes_duration_isolation_contract() -> None:
    scenario = _f001_scenario()
    assert scenario.has_temporal_soak is True
    assert scenario.soak_window_count == 5
    assert scenario.soak_samples_per_window == 60
    assert scenario.soak_expected_iteration_count == 361
    assert scenario.priority_group_count == 1
    assert scenario.soak_expected_durable_record_count == 7
    assert scenario.data_profile == 'shared-latest'
    assert scenario.physical_partition_count == 1
    assert scenario.has_functional_pressure is False
    assert scenario.has_management_pressure is False
    assert scenario.has_deactivation_decision_pressure is False


def test_f001_scenario_rejects_noncanonical_duration_or_window() -> None:
    with pytest.raises(ValueError, match='F-001 requires duration_seconds=1800'):
        replace(_f001_scenario(), duration_seconds=600)
    with pytest.raises(ValueError, match='soak warmup must align with iteration period'):
        replace(_f001_scenario(), soak_warmup_seconds=302)


def _f002_scenario() -> BaselineScenario:
    return replace(_f001_scenario(), test_id='F-002', alarm_count=1000)


def test_f002_scenario_freezes_target_volume_duration_isolation_contract() -> None:
    scenario = _f002_scenario()
    assert scenario.has_temporal_soak is True
    assert scenario.alarm_count == 1000
    assert scenario.duration_seconds == 1800
    assert scenario.soak_window_count == 5
    assert scenario.soak_samples_per_window == 60
    assert scenario.soak_expected_iteration_count == 361
    assert scenario.priority_group_count == 1
    assert scenario.soak_expected_durable_record_count == 7
    assert scenario.data_profile == 'shared-latest'
    assert scenario.physical_partition_count == 1
    assert scenario.has_functional_pressure is False
    assert scenario.has_management_pressure is False
    assert scenario.has_deactivation_decision_pressure is False


def test_f002_scenario_rejects_noncanonical_target_volume_or_duration() -> None:
    with pytest.raises(ValueError, match='F-002 requires alarm_count=1000'):
        replace(_f002_scenario(), alarm_count=500)
    with pytest.raises(ValueError, match='F-002 requires duration_seconds=1800'):
        replace(_f002_scenario(), duration_seconds=600)


def test_f001_temporal_soak_metrics_are_green_when_windows_are_bounded() -> None:
    metrics = _f001_metrics(_f001_samples(rss_by_window=(130.0, 130.3, 130.2, 130.5, 132.0)))
    assert metrics.actual_iteration_count == 361
    assert metrics.durable_record_count == 7
    assert len(metrics.windows) == 5
    assert all(window.sample_count == 60 for window in metrics.windows)
    assert metrics.rss_w5_w1_ratio == pytest.approx(132.0 / 130.0)
    assert metrics.latency_p95_w5_w1_ratio == pytest.approx(420.0 / 400.0)
    assert metrics.overrun_count == 0
    assert metrics.memory_classification == 'GREEN'
    assert metrics.latency_classification == 'GREEN'
    assert metrics.overall_classification == 'GREEN'
    assert metrics.hard_gate_ok is True


def test_f001_temporal_soak_review_passes_but_hard_drift_fails_report() -> None:
    review_samples = _f001_samples(rss_by_window=(130.0, 132.0, 134.0, 136.0, 139.0))
    review_metrics = _f001_metrics(review_samples)
    assert review_metrics.memory_classification == 'REVIEW'
    assert review_metrics.overall_classification == 'REVIEW'
    assert review_metrics.hard_gate_ok is True

    recorder = PerformanceRecorder(iteration_period_seconds=5)
    recorder.samples = list(review_samples)
    report = recorder.build_report(
        test_id='F-001',
        alarm_count=500,
        planned_duration_seconds=1800,
        actual_duration_seconds=1800,
        data_refresh_seconds=10,
        data_profile='shared-latest',
        columns_per_alarm=1,
        historical_series_per_alarm=0,
        historical_window_minutes=0,
        historical_step_seconds=0,
        historical_points_per_series=0,
        historical_value_count=0,
        physical_partition_count=1,
        physical_partition_column_counts=(1,),
        physical_partition_layout='balanced',
        empty_physical_partition_count=0,
        missing_source_column_count=0,
        synthesized_null_column_count=0,
        source_view_count=1,
        source_column_count=1,
        source_row_count=1,
        source_frame_bytes=140,
        source_numeric_value_count=1,
        latest_source_column_count=1,
        historical_source_column_count=0,
        historical_source_row_count=0,
        source_load_durations_ms=[1.0],
        source_merge_durations_ms=[0.0],
        journal_aligned=True,
        durable_record_count=7,
        snapshot_count=1,
        snapshot_alarm_count=500,
        expected_snapshot_alarm_count=500,
        source_load_count=181,
        temporal_soak=review_metrics,
    )
    assert report.result == 'PASS'
    assert report.performance_class == 'ACCEPTABLE/REVIEW'

    fail_samples = _f001_samples(rss_by_window=(130.0, 134.0, 138.0, 142.0, 146.0))
    fail_metrics = _f001_metrics(fail_samples)
    assert fail_metrics.memory_classification == 'FAIL'
    assert fail_metrics.overall_classification == 'FAIL'
    recorder.samples = list(fail_samples)
    failed_report = recorder.build_report(
        test_id='F-001',
        alarm_count=500,
        planned_duration_seconds=1800,
        actual_duration_seconds=1800,
        data_refresh_seconds=10,
        data_profile='shared-latest',
        columns_per_alarm=1,
        historical_series_per_alarm=0,
        historical_window_minutes=0,
        historical_step_seconds=0,
        historical_points_per_series=0,
        historical_value_count=0,
        physical_partition_count=1,
        physical_partition_column_counts=(1,),
        physical_partition_layout='balanced',
        empty_physical_partition_count=0,
        missing_source_column_count=0,
        synthesized_null_column_count=0,
        source_view_count=1,
        source_column_count=1,
        source_row_count=1,
        source_frame_bytes=140,
        source_numeric_value_count=1,
        latest_source_column_count=1,
        historical_source_column_count=0,
        historical_source_row_count=0,
        source_load_durations_ms=[1.0],
        source_merge_durations_ms=[0.0],
        journal_aligned=True,
        durable_record_count=7,
        snapshot_count=1,
        snapshot_alarm_count=500,
        expected_snapshot_alarm_count=500,
        source_load_count=181,
        temporal_soak=fail_metrics,
    )
    assert failed_report.result == 'FAIL'
    assert failed_report.performance_class == 'INVESTIGATE'
