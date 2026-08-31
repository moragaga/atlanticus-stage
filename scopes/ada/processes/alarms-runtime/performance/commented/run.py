from __future__ import annotations

# Espejo pedagógico: conserva exactamente el comportamiento productivo y explica los contratos relevantes.
import argparse
import json
import shutil
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from ada.processes.alarms_runtime import (
    ConfigurationAdoptionDisposition,
    ConfigurationAdoptionRejectionReason,
    execute_alarm_runtime_job,
    plan_configuration_adoption,
)
from atlanticus.runtime import (
    JobDefinition,
    JobRuntimeContext,
    LeaseOwnershipLostError,
    RuntimeConfiguration,
)
from atlanticus.runtime.lease import ExecutionLease
from atlanticus.state import AtomicJsonStore
from performance.baseline import (
    _F007_PHYSICAL_WARM,
    _F010_PHYSICAL_INTEGRATED,
    _RESCHEDULE_ALARM_REVISION,
    BaselineScenario,
    InjectedCachePromotionError,
    InvertedDeliveryDeactivationInputSource,
    MixedDeactivationInputSource,
    SingleDeactivationDecisionInputSource,
    StaleTargetDeactivationInputSource,
    SustainedDeactivationDecisionInputSource,
    SustainedDeactivationRequestInputSource,
    SustainedManagementInputSource,
    build_baseline_runtime,
)
from performance.f007_physical import (
    CgroupIoCacheSnapshot,
    F007DatasetBank,
    build_f007_physical_source_loader,
)
from performance.metrics import (
    C2RoutingAdoptionPressureMetrics,
    CachePromotionFailurePressureMetrics,
    DeactivationDecisionPressureMetrics,
    DisabledAdoptionPressureMetrics,
    DrainUnderWorkloadPressureMetrics,
    DurableHistoryLookupMetrics,
    FunctionalPressureMetrics,
    InvalidSourceCandidatePressureMetrics,
    InvertedDeactivationDecisionPressureMetrics,
    IterationSample,
    LeaseLossAdoptionPressureMetrics,
    ManagementPressureMetrics,
    MeasuredAlarmRuntimeJobComposition,
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
    TemporalSoakMetrics,
    build_temporal_soak_metrics as build_temporal_soak_metrics_from_samples,
)


def _uses_two_phase_deactivation_runner(scenario: BaselineScenario) -> bool:
    return (
        scenario.has_multi_deactivation_decision_pressure
        and not scenario.has_stale_target_deactivation_pressure
        and not scenario.has_inverted_deactivation_delivery_pressure
        and not scenario.has_mixed_deactivation_pressure
        and not scenario.has_removed_adoption_pressure
    )


def main() -> int:
    args = _parse_args()
    scenario = BaselineScenario(
        test_id=args.test_id,
        alarm_count=args.alarm_count,
        duration_seconds=args.duration_seconds,
        iteration_period_seconds=args.iteration_period_seconds,
        data_refresh_seconds=args.data_refresh_seconds,
        data_profile=args.data_profile,
        columns_per_alarm=args.columns_per_alarm,
        physical_partition_count=args.physical_partition_count,
        physical_partition_layout=args.physical_partition_layout,
        historical_series_per_alarm=args.historical_series_per_alarm,
        historical_window_minutes=args.historical_window_minutes,
        historical_step_seconds=args.historical_step_seconds,
        priority_group_size=args.priority_group_size,
        operational_churn_percent=args.operational_churn_percent,
        technical_hold_churn_percent=args.technical_hold_churn_percent,
        technical_hold_expiry_percent=args.technical_hold_expiry_percent,
        technical_hold_expiry_stagger_seconds=args.technical_hold_expiry_stagger_seconds,
        technical_hold_error_duration_seconds=args.technical_hold_error_duration_seconds,
        initial_error_activation_percent=args.initial_error_activation_percent,
        initial_error_hold_seconds=args.initial_error_hold_seconds,
        initial_error_activation_stagger_seconds=args.initial_error_activation_stagger_seconds,
        fixed_initial_error_percent=args.fixed_initial_error_percent,
        c1_routing_destination_count=args.c1_routing_destination_count,
        c2_routing_delay_seconds=args.c2_routing_delay_seconds,
        c2_reschedule_delay_seconds=args.c2_reschedule_delay_seconds,
        c2_reschedule_phase_a_seconds=args.c2_reschedule_phase_a_seconds,
        c2_remove_destinations_phase_a_seconds=(args.c2_remove_destinations_phase_a_seconds),
        c2_routing_adoption_at_seconds=args.c2_routing_adoption_at_seconds,
        c2_routing_adoption_target_delay_seconds=(args.c2_routing_adoption_target_delay_seconds),
        management_action_at_seconds=args.management_action_at_seconds,
        management_action_count=args.management_action_count,
        management_action_interval_seconds=args.management_action_interval_seconds,
        deactivation_decision_at_seconds=args.deactivation_decision_at_seconds,
        deactivation_decision_count=args.deactivation_decision_count,
        deactivation_decision_interval_seconds=args.deactivation_decision_interval_seconds,
        deactivation_request_delivery_at_seconds=(args.deactivation_request_delivery_at_seconds),
        deactivation_target_removal_at_seconds=args.deactivation_target_removal_at_seconds,
        deactivation_window_seconds=args.deactivation_window_seconds,
        parameter_adoption_at_seconds=args.parameter_adoption_at_seconds,
        parameter_target_threshold=args.parameter_target_threshold,
        disabled_adoption_at_seconds=args.disabled_adoption_at_seconds,
        disabled_alarm_percent=args.disabled_alarm_percent,
        removed_adoption_at_seconds=args.removed_adoption_at_seconds,
        removed_alarm_percent=args.removed_alarm_percent,
        structural_reset_adoption_at_seconds=args.structural_reset_adoption_at_seconds,
        structural_reset_alarm_percent=args.structural_reset_alarm_percent,
        mixed_revision_adoption_at_seconds=args.mixed_revision_adoption_at_seconds,
        mixed_revision_target_threshold=args.mixed_revision_target_threshold,
        mixed_revision_disabled_alarm_percent=args.mixed_revision_disabled_alarm_percent,
        mixed_revision_removed_alarm_percent=args.mixed_revision_removed_alarm_percent,
        mixed_revision_structural_reset_alarm_percent=(
            args.mixed_revision_structural_reset_alarm_percent
        ),
        rejected_candidate_at_seconds=args.rejected_candidate_at_seconds,
        source_unavailable_at_seconds=args.source_unavailable_at_seconds,
        invalid_candidate_at_seconds=args.invalid_candidate_at_seconds,
        lease_loss_adoption_at_seconds=args.lease_loss_adoption_at_seconds,
        cache_promotion_failure_at_seconds=args.cache_promotion_failure_at_seconds,
        drain_under_workload_at_seconds=args.drain_under_workload_at_seconds,
        soak_warmup_seconds=args.soak_warmup_seconds,
        soak_window_seconds=args.soak_window_seconds,
        durable_history_lookup_mode=args.durable_history_lookup_mode,
        initial_active_percent=args.initial_active_percent,
    )
    if args.e010_worker_role is not None:
        return _run_e010_worker(args=args, scenario=scenario)

    run_root = Path(args.work_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    if run_root.exists():
        shutil.rmtree(run_root)
    volume_path = run_root / 'volume'
    source_path = run_root / 'source'
    volume_path.mkdir(parents=True, exist_ok=True)
    source_path.mkdir(parents=True, exist_ok=True)

    if scenario.has_lease_loss_adoption_pressure:
        return _run_lease_loss_adoption(
            args=args,
            scenario=scenario,
            run_root=run_root,
            source_path=source_path,
            output_dir=output_dir,
        )
    if scenario.has_cache_promotion_failure_pressure:
        return _run_cache_promotion_failure(
            scenario=scenario,
            volume_path=volume_path,
            source_path=source_path,
            output_dir=output_dir,
        )
    if scenario.has_drain_under_workload_pressure:
        return _run_drain_under_workload(
            scenario=scenario,
            volume_path=volume_path,
            source_path=source_path,
            output_dir=output_dir,
        )
    if scenario.has_c2_reschedule_pressure:
        return _run_c2_reschedule(
            scenario=scenario,
            volume_path=volume_path,
            source_path=source_path,
            output_dir=output_dir,
        )
    if scenario.has_c2_remove_destinations_pressure:
        return _run_c2_remove_destinations(
            scenario=scenario,
            volume_path=volume_path,
            source_path=source_path,
            output_dir=output_dir,
        )
    if _uses_two_phase_deactivation_runner(scenario):
        return _run_sustained_deactivation_decisions(
            scenario=scenario,
            volume_path=volume_path,
            source_path=source_path,
            output_dir=output_dir,
        )

    # La ruta normal no conoce el banco. Sólo el perfil F-007 valida identidad, mount read-only y compone el loader físico.
    physical_bank: F007DatasetBank | None = None
    physical_loader = None
    physical_as_of: datetime | None = None
    physical_expected_paths: tuple[str, ...] = ()
    # Los perfiles físicos reutilizan el mismo banco; F-010 fija además el as_of a una ventana aceptada del manifiesto.
    if scenario.data_profile in (_F007_PHYSICAL_WARM, _F010_PHYSICAL_INTEGRATED):
        physical_bank = F007DatasetBank.load(
            manifest_path=_required_f007_path(args.f007_manifest, 'f007_manifest'),
            conformance_path=_required_f007_path(args.f007_conformance, 'f007_conformance'),
            input_root=_required_f007_path(args.f007_dataset_root, 'f007_dataset_root'),
            require_read_only=True,
        )
        # F-010 fija el as_of al banco para que 30 minutos de ejecución no cambien las particiones solicitadas.
        if scenario.data_profile == _F010_PHYSICAL_INTEGRATED:
            physical_as_of = physical_bank.window(0).as_of_utc
            physical_expected_paths = (
                physical_bank.latest_path,
                physical_bank.pi_daily_path(0),
            )
        else:
            physical_expected_paths = (physical_bank.latest_path,)
        physical_loader = build_f007_physical_source_loader(
            input_root=physical_bank.input_root,
            fixed_as_of_utc=physical_as_of,
        )
    elif any(
        value is not None
        for value in (args.f007_dataset_root, args.f007_manifest, args.f007_conformance)
    ):
        raise ValueError('physical dataset arguments require a physical data profile')

    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=volume_path,
        source_path=source_path,
        source_loader_override=physical_loader,
    )
    # El prewarm ocurre antes del intervalo medido y dentro del mismo cgroup, por lo que la memoria file-backed sigue siendo observable.
    physical_prewarm_before = None
    physical_prewarm_after = None
    if physical_bank is not None:
        physical_prewarm_before = CgroupIoCacheSnapshot.read()
        runtime.source_loader.prewarm(
            plan=runtime.revision.session.data_plan,
            as_of=physical_as_of or datetime.now(UTC).replace(microsecond=0),
            expected_paths=physical_expected_paths,
        )
        physical_prewarm_after = CgroupIoCacheSnapshot.read()
    recorder = PerformanceRecorder(iteration_period_seconds=scenario.iteration_period_seconds)
    measured = MeasuredAlarmRuntimeJobComposition.wrap(runtime.job, recorder=recorder)
    shutdown_grace = min(10.0, max(2.0, scenario.duration_seconds * 0.1))
    definition = JobDefinition(
        module_name='ada.processes.alarms_runtime.performance',
        service_name='alarms-runtime-performance',
        job_key=f'alarms-runtime-{scenario.test_id.lower()}',
        sleep_seconds=scenario.iteration_period_seconds,
        iteration_timeout_seconds=min(30.0, max(2.0, scenario.duration_seconds / 2)),
        execution_timeout_seconds=(
            scenario.duration_seconds + shutdown_grace + scenario.iteration_period_seconds
        ),
        shutdown_grace_seconds=shutdown_grace,
        lease_timeout_seconds=30.0,
        lease_renew_seconds=10.0,
        lease_wait_seconds=0.0,
        resource_sample_seconds=1.0,
    )
    environ = {
        'ENVIRONMENT': 'local',
        'APPLICATION': 'ada-alarms-runtime-performance',
        'VOLUMEN_PATH': str(volume_path),
        'ATLANTICUS_OBSERVABILITY_FILE_LOGS': 'false',
    }
    execution = execute_alarm_runtime_job(
        definition=definition,
        composition=measured,
        argv=(),
        environ=environ,
    )
    persistence = runtime.job.composition.durability.persistence
    head = persistence.read_head()
    records = persistence.read_durable_records()
    snapshots = persistence.list_snapshots()
    snapshot_alarm_count = sum(len(item.as_document()['alarms']) for item in snapshots)
    expected_snapshot_alarm_count = scenario.expected_snapshot_alarm_count(
        missing_source_columns=runtime.source_loader.missing_source_columns
    )
    functional_pressure = _build_functional_pressure_metrics(
        scenario=scenario,
        source_loader=runtime.source_loader,
        records=records,
        snapshots=snapshots,
    )
    management_pressure = _build_management_pressure_metrics(
        scenario=scenario,
        runtime=runtime,
        records=records,
        snapshots=snapshots,
    )
    parameter_adoption_pressure = _build_parameter_adoption_pressure_metrics(
        scenario=scenario,
        runtime=runtime,
        records=records,
        snapshots=snapshots,
        samples=recorder.samples,
    )
    c2_routing_adoption_pressure = _build_c2_routing_adoption_pressure_metrics(
        scenario=scenario,
        runtime=runtime,
        records=records,
        snapshots=snapshots,
        samples=recorder.samples,
    )
    disabled_adoption_pressure = _build_disabled_adoption_pressure_metrics(
        scenario=scenario,
        runtime=runtime,
        records=records,
        snapshots=snapshots,
        samples=recorder.samples,
    )
    removed_adoption_pressure = _build_removed_adoption_pressure_metrics(
        scenario=scenario,
        runtime=runtime,
        records=records,
        snapshots=snapshots,
        samples=recorder.samples,
    )
    structural_reset_adoption_pressure = _build_structural_reset_adoption_pressure_metrics(
        scenario=scenario,
        runtime=runtime,
        records=records,
        snapshots=snapshots,
        samples=recorder.samples,
    )
    mixed_revision_adoption_pressure = _build_mixed_revision_adoption_pressure_metrics(
        scenario=scenario,
        runtime=runtime,
        records=records,
        snapshots=snapshots,
        samples=recorder.samples,
    )
    rejected_target_pressure = _build_rejected_target_pressure_metrics(
        scenario=scenario,
        runtime=runtime,
        records=records,
        snapshots=snapshots,
        samples=recorder.samples,
    )
    source_unavailable_pressure = _build_source_unavailable_pressure_metrics(
        scenario=scenario,
        runtime=runtime,
        records=records,
        snapshots=snapshots,
        samples=recorder.samples,
    )
    invalid_source_candidate_pressure = _build_invalid_source_candidate_pressure_metrics(
        scenario=scenario,
        runtime=runtime,
        records=records,
        snapshots=snapshots,
        samples=recorder.samples,
    )
    if scenario.has_removed_adoption_pressure:
        deactivation_decision_pressure = None
    elif scenario.has_stale_target_deactivation_pressure:
        deactivation_decision_pressure = _build_stale_target_deactivation_decision_pressure_metrics(
            scenario=scenario,
            runtime=runtime,
            records=records,
            snapshots=snapshots,
        )
    elif scenario.has_mixed_deactivation_pressure:
        deactivation_decision_pressure = _build_mixed_deactivation_decision_pressure_metrics(
            scenario=scenario,
            runtime=runtime,
            records=records,
            snapshots=snapshots,
        )
    elif scenario.has_inverted_deactivation_delivery_pressure:
        deactivation_decision_pressure = _build_inverted_deactivation_decision_pressure_metrics(
            scenario=scenario,
            runtime=runtime,
            records=records,
            snapshots=snapshots,
        )
    else:
        deactivation_decision_pressure = _build_deactivation_decision_pressure_metrics(
            scenario=scenario,
            runtime=runtime,
            records=records,
            snapshots=snapshots,
        )
    source_load_durations_ms = runtime.source_loader.load_durations_ms or []
    source_merge_durations_ms = runtime.source_loader.merge_durations_ms or []
    durable_history_lookup = runtime.composition.build_durable_history_lookup_metrics()
    temporal_soak = _build_temporal_soak_metrics(
        scenario=scenario,
        records=records,
        snapshots=snapshots,
        samples=recorder.samples,
        journal_aligned=head.aligned,
        snapshot_alarm_count=snapshot_alarm_count,
        expected_snapshot_alarm_count=expected_snapshot_alarm_count,
    )
    report = recorder.build_report(
        test_id=scenario.test_id,
        alarm_count=scenario.alarm_count,
        planned_duration_seconds=scenario.duration_seconds,
        actual_duration_seconds=execution.duration_seconds,
        data_refresh_seconds=scenario.data_refresh_seconds,
        data_profile=scenario.data_profile,
        columns_per_alarm=scenario.columns_per_alarm,
        historical_series_per_alarm=scenario.historical_series_per_alarm,
        historical_window_minutes=scenario.historical_window_minutes,
        historical_step_seconds=scenario.historical_step_seconds,
        historical_points_per_series=scenario.historical_points_per_series,
        historical_value_count=runtime.source_loader.historical_value_count,
        physical_partition_count=scenario.physical_partition_count,
        physical_partition_column_counts=(runtime.source_loader.physical_partition_column_counts),
        physical_partition_layout=scenario.physical_partition_layout,
        empty_physical_partition_count=runtime.source_loader.empty_physical_partition_count,
        missing_source_column_count=runtime.source_loader.missing_source_column_count,
        synthesized_null_column_count=runtime.source_loader.synthesized_null_column_count,
        source_view_count=runtime.source_loader.view_count,
        source_column_count=runtime.source_loader.column_count,
        source_row_count=runtime.source_loader.row_count,
        source_frame_bytes=runtime.source_loader.frame_bytes,
        source_numeric_value_count=runtime.source_loader.numeric_value_count,
        latest_source_column_count=runtime.source_loader.latest_column_count,
        historical_source_column_count=runtime.source_loader.historical_column_count,
        historical_source_row_count=runtime.source_loader.historical_row_count,
        source_load_durations_ms=source_load_durations_ms,
        source_merge_durations_ms=source_merge_durations_ms,
        journal_aligned=head.aligned,
        durable_record_count=len(records),
        snapshot_count=len(snapshots),
        snapshot_alarm_count=snapshot_alarm_count,
        expected_snapshot_alarm_count=expected_snapshot_alarm_count,
        source_load_count=runtime.source_loader.load_count,
        durable_history_lookup=durable_history_lookup,
        functional_pressure=functional_pressure,
        management_pressure=management_pressure,
        parameter_adoption_pressure=parameter_adoption_pressure,
        c2_routing_adoption_pressure=c2_routing_adoption_pressure,
        disabled_adoption_pressure=disabled_adoption_pressure,
        removed_adoption_pressure=removed_adoption_pressure,
        structural_reset_adoption_pressure=structural_reset_adoption_pressure,
        mixed_revision_adoption_pressure=mixed_revision_adoption_pressure,
        rejected_target_pressure=rejected_target_pressure,
        source_unavailable_pressure=source_unavailable_pressure,
        invalid_source_candidate_pressure=invalid_source_candidate_pressure,
        temporal_soak=temporal_soak,
        deactivation_decision_pressure=deactivation_decision_pressure,
    )
    recorder.write(output_dir=output_dir, report=report)
    if physical_bank is not None:
        _write_f007_physical_binding(
            output_dir=output_dir,
            bank=physical_bank,
            source_loader=runtime.source_loader,
            prewarm_before=physical_prewarm_before,
            prewarm_after=physical_prewarm_after,
            data_profile=scenario.data_profile,
            fixed_as_of_utc=physical_as_of,
        )
    _write_metadata(
        output_dir=output_dir,
        scenario=scenario,
        run_id=execution.run_id,
        stop_reason=execution.stop_reason,
        source_view_count=runtime.source_loader.view_count,
        source_column_count=runtime.source_loader.column_count,
        source_row_count=runtime.source_loader.row_count,
        source_frame_bytes=runtime.source_loader.frame_bytes,
        source_numeric_value_count=runtime.source_loader.numeric_value_count,
        latest_source_column_count=runtime.source_loader.latest_column_count,
        historical_source_column_count=runtime.source_loader.historical_column_count,
        historical_source_row_count=runtime.source_loader.historical_row_count,
        historical_series_per_alarm=scenario.historical_series_per_alarm,
        historical_window_minutes=scenario.historical_window_minutes,
        historical_step_seconds=scenario.historical_step_seconds,
        historical_points_per_series=scenario.historical_points_per_series,
        historical_value_count=runtime.source_loader.historical_value_count,
        physical_partition_count=scenario.physical_partition_count,
        physical_partition_column_counts=(runtime.source_loader.physical_partition_column_counts),
        physical_partition_layout=scenario.physical_partition_layout,
        empty_physical_partition_count=runtime.source_loader.empty_physical_partition_count,
        missing_source_column_count=runtime.source_loader.missing_source_column_count,
        synthesized_null_column_count=runtime.source_loader.synthesized_null_column_count,
        expected_snapshot_alarm_count=expected_snapshot_alarm_count,
        durable_history_lookup=durable_history_lookup,
        functional_pressure=functional_pressure,
        management_pressure=management_pressure,
        parameter_adoption_pressure=parameter_adoption_pressure,
        c2_routing_adoption_pressure=c2_routing_adoption_pressure,
        disabled_adoption_pressure=disabled_adoption_pressure,
        removed_adoption_pressure=removed_adoption_pressure,
        structural_reset_adoption_pressure=structural_reset_adoption_pressure,
        mixed_revision_adoption_pressure=mixed_revision_adoption_pressure,
        rejected_target_pressure=rejected_target_pressure,
        source_unavailable_pressure=source_unavailable_pressure,
        invalid_source_candidate_pressure=invalid_source_candidate_pressure,
        temporal_soak=temporal_soak,
        deactivation_decision_pressure=deactivation_decision_pressure,
    )
    print(json.dumps(report.as_document(), indent=2, sort_keys=True))
    print(f'Artifacts: {output_dir}')
    return 0 if report.result == 'PASS' else 1


_E010_LEASE_TIMEOUT_SECONDS = 30
_E010_CONTROL_WAIT_SECONDS = 120.0


# Envuelve el fence real del owner A y pausa sólo cuando la adopción ya quedó durable/materializada.
# El owner B toma la generación siguiente antes de liberar la pausa, por lo que el fence subyacente rechaza al owner obsoleto.
class _E010PausingCacheFence:
    def __init__(self, *, underlying, persistence, control_root: Path) -> None:
        self._underlying = underlying
        self._persistence = persistence
        self._control_root = control_root
        self._durable_before_adoption = None
        self.paused = False

    def arm(self, durable_before_adoption) -> None:
        self._durable_before_adoption = durable_before_adoption

    def __call__(self):
        @contextmanager
        def mutation():
            head = self._persistence.read_head()
            if (
                not self.paused
                and self._durable_before_adoption is not None
                and head.aligned
                and head.durable != self._durable_before_adoption
            ):
                self.paused = True
                _e010_write_marker(self._control_root / 'a-paused')
                _e010_wait_for(
                    self._control_root / 'release-a',
                    timeout_seconds=_E010_CONTROL_WAIT_SECONDS,
                )
            with self._underlying():
                yield

        return mutation()


# Orquesta el escenario completo: preflight SMB, dos procesos propietarios, takeover, auditoría y consolidación del reporte.
def _run_lease_loss_adoption(
    *,
    args: argparse.Namespace,
    scenario: BaselineScenario,
    run_root: Path,
    source_path: Path,
    output_dir: Path,
) -> int:
    if args.shared_volume_path is None:
        raise ValueError('lease loss adoption pressure requires --shared-volume-path')
    shared_volume = Path(args.shared_volume_path).expanduser().resolve()
    if not shared_volume.is_dir():
        raise ValueError('shared-volume-path must be an existing directory')

    control_root = run_root / 'e010-control'
    control_root.mkdir(parents=True, exist_ok=True)
    preflight = _run_e010_smb_preflight(shared_volume=shared_volume, control_root=control_root)

    run_id = str(uuid4())
    shared_run_root = shared_volume / '.atlanticus-performance' / scenario.test_id.lower() / run_id
    shared_run_root.mkdir(parents=True, exist_ok=False)
    schedule_started_monotonic = time.perf_counter()
    now_epoch = int(datetime.now(UTC).timestamp())
    aligned_epoch = now_epoch - (now_epoch % scenario.data_refresh_seconds)
    schedule_base_at = datetime.fromtimestamp(aligned_epoch, UTC)
    _e010_atomic_write(
        control_root / 'lease-clock.txt',
        schedule_base_at.isoformat(),
    )
    _e010_write_json(
        control_root / 'schedule.json',
        {
            'run_id': run_id,
            'schedule_started_monotonic': schedule_started_monotonic,
            'schedule_base_at': schedule_base_at.isoformat(),
            'failure_iteration': _e010_expected_failure_iteration(scenario),
            'owner_b_first_iteration': _e010_expected_owner_b_first_iteration(scenario),
            'shared_run_root': str(shared_run_root),
        },
    )

    command_base = [
        sys.executable,
        '-m',
        'performance.run',
        *sys.argv[1:],
        '--e010-control-root',
        str(control_root),
        '--e010-shared-run-root',
        str(shared_run_root),
    ]
    owner_a_log = (control_root / 'owner-a.log').open('w', encoding='utf-8')
    owner_b_log = (control_root / 'owner-b.log').open('w', encoding='utf-8')
    try:
        owner_a = subprocess.Popen(
            [*command_base, '--e010-worker-role', 'owner-a'],
            stdout=owner_a_log,
            stderr=subprocess.STDOUT,
        )
        owner_b = subprocess.Popen(
            [*command_base, '--e010-worker-role', 'owner-b'],
            stdout=owner_b_log,
            stderr=subprocess.STDOUT,
        )
        timeout_seconds = scenario.duration_seconds + 180.0
        a_code = owner_a.wait(timeout=timeout_seconds)
        b_code = owner_b.wait(timeout=timeout_seconds)
    finally:
        owner_a_log.close()
        owner_b_log.close()

    if a_code != 0 or b_code != 0:
        _e010_copy_shared_evidence(shared_run_root=shared_run_root, run_root=run_root)
        raise RuntimeError(
            f'E-010 workers failed: owner-a={a_code}, owner-b={b_code}; logs={control_root}'
        )

    owner_a_state = _e010_read_json(control_root / 'owner-a.json')
    owner_b_state = _e010_read_json(control_root / 'owner-b.json')
    actual_duration_seconds = time.perf_counter() - schedule_started_monotonic
    copied_volume = _e010_copy_shared_evidence(
        shared_run_root=shared_run_root,
        run_root=run_root,
    )

    analysis_runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=copied_volume,
        source_path=source_path,
    )
    persistence = analysis_runtime.composition.durability.persistence
    head = persistence.read_head()
    records = persistence.read_durable_records()
    snapshots = persistence.list_snapshots()
    source_loader = _aggregate_e010_source_loader(owner_a_state, owner_b_state)
    functional_pressure = _build_functional_pressure_metrics(
        scenario=scenario,
        source_loader=source_loader,
        records=records,
        snapshots=snapshots,
    )
    samples = _merge_e010_samples(owner_a_state, owner_b_state)
    recorder = PerformanceRecorder(iteration_period_seconds=scenario.iteration_period_seconds)
    recorder.samples = list(samples)
    recorder.recovery_ms = float(owner_a_state['recovery_ms']) + float(owner_b_state['recovery_ms'])
    recorder.drain_ms = float(owner_b_state['drain_ms'])
    durable_history_lookup = _aggregate_e010_durable_history(owner_a_state, owner_b_state)
    lease_loss_adoption_pressure = _build_lease_loss_adoption_pressure_metrics(
        scenario=scenario,
        runtime=analysis_runtime,
        records=records,
        snapshots=snapshots,
        samples=samples,
        owner_a_state=owner_a_state,
        owner_b_state=owner_b_state,
        preflight_status=str(preflight['status']),
        functional_pressure=functional_pressure,
    )
    snapshot_alarm_count = sum(len(item.as_document()['alarms']) for item in snapshots)
    expected_snapshot_alarm_count = scenario.expected_snapshot_alarm_count(
        missing_source_columns=source_loader.missing_source_columns
    )
    report = recorder.build_report(
        test_id=scenario.test_id,
        alarm_count=scenario.alarm_count,
        planned_duration_seconds=scenario.duration_seconds,
        actual_duration_seconds=actual_duration_seconds,
        data_refresh_seconds=scenario.data_refresh_seconds,
        data_profile=scenario.data_profile,
        columns_per_alarm=scenario.columns_per_alarm,
        historical_series_per_alarm=scenario.historical_series_per_alarm,
        historical_window_minutes=scenario.historical_window_minutes,
        historical_step_seconds=scenario.historical_step_seconds,
        historical_points_per_series=scenario.historical_points_per_series,
        historical_value_count=source_loader.historical_value_count,
        physical_partition_count=scenario.physical_partition_count,
        physical_partition_column_counts=tuple(source_loader.physical_partition_column_counts),
        physical_partition_layout=scenario.physical_partition_layout,
        empty_physical_partition_count=source_loader.empty_physical_partition_count,
        missing_source_column_count=source_loader.missing_source_column_count,
        synthesized_null_column_count=source_loader.synthesized_null_column_count,
        source_view_count=source_loader.view_count,
        source_column_count=source_loader.column_count,
        source_row_count=source_loader.row_count,
        source_frame_bytes=source_loader.frame_bytes,
        source_numeric_value_count=source_loader.numeric_value_count,
        latest_source_column_count=source_loader.latest_column_count,
        historical_source_column_count=source_loader.historical_column_count,
        historical_source_row_count=source_loader.historical_row_count,
        source_load_durations_ms=list(source_loader.load_durations_ms),
        source_merge_durations_ms=list(source_loader.merge_durations_ms),
        journal_aligned=head.aligned,
        durable_record_count=len(records),
        snapshot_count=len(snapshots),
        snapshot_alarm_count=snapshot_alarm_count,
        expected_snapshot_alarm_count=expected_snapshot_alarm_count,
        source_load_count=source_loader.load_count,
        durable_history_lookup=durable_history_lookup,
        functional_pressure=functional_pressure,
        lease_loss_adoption_pressure=lease_loss_adoption_pressure,
    )
    recorder.write(output_dir=output_dir, report=report)
    _write_metadata(
        output_dir=output_dir,
        scenario=scenario,
        run_id=run_id,
        stop_reason='completed_after_lease_takeover',
        source_view_count=source_loader.view_count,
        source_column_count=source_loader.column_count,
        source_row_count=source_loader.row_count,
        source_frame_bytes=source_loader.frame_bytes,
        source_numeric_value_count=source_loader.numeric_value_count,
        latest_source_column_count=source_loader.latest_column_count,
        historical_source_column_count=source_loader.historical_column_count,
        historical_source_row_count=source_loader.historical_row_count,
        historical_series_per_alarm=scenario.historical_series_per_alarm,
        historical_window_minutes=scenario.historical_window_minutes,
        historical_step_seconds=scenario.historical_step_seconds,
        historical_points_per_series=scenario.historical_points_per_series,
        historical_value_count=source_loader.historical_value_count,
        physical_partition_count=scenario.physical_partition_count,
        physical_partition_column_counts=tuple(source_loader.physical_partition_column_counts),
        physical_partition_layout=scenario.physical_partition_layout,
        empty_physical_partition_count=source_loader.empty_physical_partition_count,
        missing_source_column_count=source_loader.missing_source_column_count,
        synthesized_null_column_count=source_loader.synthesized_null_column_count,
        expected_snapshot_alarm_count=expected_snapshot_alarm_count,
        durable_history_lookup=durable_history_lookup,
        functional_pressure=functional_pressure,
        lease_loss_adoption_pressure=lease_loss_adoption_pressure,
        backend='smb-shared-filesystem',
        phase_runs={
            'owner-a': owner_a_state,
            'owner-b': owner_b_state,
            'smb-preflight': preflight,
        },
    )
    _e010_write_json(
        run_root / 'shared-volume-origin.json',
        {
            'shared_run_root': str(shared_run_root),
            'copied_evidence': str(copied_volume),
        },
    )
    try:
        shutil.rmtree(shared_run_root)
    except OSError as error:
        _e010_write_json(
            run_root / 'shared-volume-cleanup-warning.json',
            {'error': str(error), 'shared_run_root': str(shared_run_root)},
        )
    print(json.dumps(report.as_document(), indent=2, sort_keys=True))
    print(f'Artifacts: {output_dir}')
    return 0 if report.result == 'PASS' else 1


def _run_e010_worker(*, args: argparse.Namespace, scenario: BaselineScenario) -> int:
    if not scenario.has_lease_loss_adoption_pressure:
        raise ValueError('E-010 worker requires lease loss adoption pressure')
    if args.e010_control_root is None or args.e010_shared_run_root is None:
        raise ValueError('E-010 worker requires internal control and shared run roots')
    control_root = Path(args.e010_control_root).resolve()
    shared_run_root = Path(args.e010_shared_run_root).resolve()
    source_path = Path(args.work_dir).resolve() / 'source'
    schedule = _e010_read_json(control_root / 'schedule.json')
    if args.e010_worker_role == 'owner-a':
        _run_e010_owner_a(
            scenario=scenario,
            shared_run_root=shared_run_root,
            source_path=source_path,
            control_root=control_root,
            schedule=schedule,
        )
        return 0
    _run_e010_owner_b(
        scenario=scenario,
        shared_run_root=shared_run_root,
        source_path=source_path,
        control_root=control_root,
        schedule=schedule,
    )
    return 0


# Owner A ejecuta AC-1 hasta +60 s, materializa los cinco resets de AC-2 y pierde autoridad al intentar promover cache.
def _run_e010_owner_a(
    *,
    scenario: BaselineScenario,
    shared_run_root: Path,
    source_path: Path,
    control_root: Path,
    schedule: dict[str, object],
) -> None:
    schedule_started = float(schedule['schedule_started_monotonic'])
    schedule_base_at = datetime.fromisoformat(str(schedule['schedule_base_at'])).astimezone(UTC)
    runtime = _build_e010_runtime(
        scenario=scenario,
        volume_path=shared_run_root,
        source_path=source_path,
        schedule_started_monotonic=schedule_started,
        schedule_base_at=schedule_base_at,
    )
    recorder = PerformanceRecorder(iteration_period_seconds=scenario.iteration_period_seconds)
    measured = MeasuredAlarmRuntimeJobComposition.wrap(runtime.job, recorder=recorder)
    definition = _e010_job_definition(scenario)
    context, lease, generation = _e010_acquire_context(
        definition=definition,
        volume_path=shared_run_root,
        control_root=control_root,
        run_id='owner-a',
    )
    pausing_fence = _E010PausingCacheFence(
        underlying=lease.fenced_mutation,
        persistence=runtime.composition.durability.persistence,
        control_root=control_root,
    )
    context._bind_lease_authority(
        generation=generation,
        checker=lease.assert_current,
        fence=pausing_fence,
    )
    measured.recover(context)
    failure_iteration = _e010_expected_failure_iteration(scenario)
    durable_before_adoption = None
    failed_iteration_ms = None
    lease_loss_observed = False
    try:
        for iteration in range(1, failure_iteration + 1):
            _e010_wait_for_slot(
                schedule_started_monotonic=schedule_started,
                iteration=iteration,
                period_seconds=scenario.iteration_period_seconds,
            )
            context._begin_iteration(iteration)
            lease.assert_current()
            if iteration == failure_iteration:
                durable_before_adoption = (
                    runtime.composition.durability.persistence.read_head().durable
                )
                pausing_fence.arm(durable_before_adoption)
                failed_started = time.perf_counter()
                try:
                    measured.iteration(context)
                except LeaseOwnershipLostError:
                    failed_iteration_ms = (time.perf_counter() - failed_started) * 1000
                    lease_loss_observed = True
                    break
                raise RuntimeError('owner A unexpectedly promoted cache after adoption')
            measured.iteration(context)
    finally:
        lease.release(completed=False)

    if not lease_loss_observed or durable_before_adoption is None:
        raise RuntimeError('owner A did not observe the expected lease loss')
    persistence = runtime.composition.durability.persistence
    head = persistence.read_head()
    adoption_entries = persistence.read_durable_records(after=durable_before_adoption)
    cache_bundle = runtime.job.revision_resolver.cache.load_effective()
    cycle_factory = runtime.job.cycle_factory
    state = {
        'run_id': 'owner-a',
        'lease_generation': generation,
        'failed_iteration': failure_iteration,
        'failed_iteration_ms': failed_iteration_ms,
        'lease_loss_observed': lease_loss_observed,
        'journal_aligned_before_loss': head.aligned,
        'cache_alarm_revision_before_loss': (
            None if cache_bundle is None else cache_bundle.manifest.alarm_configuration_revision
        ),
        'cache_tool_revision_before_loss': (
            None if cache_bundle is None else cache_bundle.manifest.tool_registry_revision
        ),
        'cache_replace_count': len(runtime.tracked_revision_cache.replace_monotonic),
        'adoption_commit_ids': [entry.record.commit.commit_id for entry in adoption_entries],
        'adoption_closed_occurrence_ids': _e010_occurrence_ids(
            adoption_entries,
            kind='CLOSED',
            closure_reason='configuration_reconfigured',
        ),
        'adoption_closed_episode_ids': _e010_episode_ids(
            adoption_entries,
            kind='CLOSED',
            closure_reason='configuration_terminated',
        ),
        'samples': [asdict(sample) for sample in recorder.samples],
        'recovery_ms': recorder.recovery_ms,
        'drain_ms': recorder.drain_ms,
        'source_loader': asdict(runtime.source_loader),
        'durable_history_lookup': asdict(
            runtime.composition.build_durable_history_lookup_metrics()
        ),
        'occurrence_count': int(cycle_factory.occurrence_count),
        'episode_count': int(cycle_factory.episode_count),
    }
    _e010_write_json(control_root / 'owner-a.json', state)
    _e010_write_marker(control_root / 'a-rejected')


# Owner B adquiere generation=2, confirma recovery alineado y repite la adopción como no-op durable antes de promover AC-2.
def _run_e010_owner_b(
    *,
    scenario: BaselineScenario,
    shared_run_root: Path,
    source_path: Path,
    control_root: Path,
    schedule: dict[str, object],
) -> None:
    _e010_wait_for(
        control_root / 'a-paused',
        timeout_seconds=scenario.lease_loss_adoption_at_seconds + 60.0,
    )
    schedule_base_at = datetime.fromisoformat(str(schedule['schedule_base_at'])).astimezone(UTC)
    _e010_atomic_write(
        control_root / 'lease-clock.txt',
        (schedule_base_at + timedelta(seconds=_E010_LEASE_TIMEOUT_SECONDS + 1)).isoformat(),
    )
    definition = _e010_job_definition(scenario)
    context, lease, generation = _e010_acquire_context(
        definition=definition,
        volume_path=shared_run_root,
        control_root=control_root,
        run_id='owner-b',
    )
    if generation != 2:
        lease.release(completed=False)
        raise RuntimeError(f'owner B expected lease generation 2, got {generation}')
    _e010_write_marker(control_root / 'b-takeover')
    _e010_write_marker(control_root / 'release-a')
    _e010_wait_for(control_root / 'a-rejected', timeout_seconds=_E010_CONTROL_WAIT_SECONDS)
    owner_a_state = _e010_read_json(control_root / 'owner-a.json')

    schedule_started = float(schedule['schedule_started_monotonic'])
    runtime = _build_e010_runtime(
        scenario=scenario,
        volume_path=shared_run_root,
        source_path=source_path,
        schedule_started_monotonic=schedule_started,
        schedule_base_at=schedule_base_at,
        occurrence_id_start=int(owner_a_state['occurrence_count']),
        episode_id_start=int(owner_a_state['episode_count']),
        source_first_generation=int(owner_a_state['source_loader']['first_generation']),
        source_last_generation=int(owner_a_state['source_loader']['last_generation']),
    )
    recorder = PerformanceRecorder(iteration_period_seconds=scenario.iteration_period_seconds)
    measured = MeasuredAlarmRuntimeJobComposition.wrap(runtime.job, recorder=recorder)
    context._bind_lease_authority(
        generation=generation,
        checker=lease.assert_current,
        fence=lease.fenced_mutation,
    )
    recovery = measured.recover(context)
    first_iteration = _e010_expected_owner_b_first_iteration(scenario)
    first_new_commit_ids: list[str] = []
    first_result = None
    try:
        for iteration in range(first_iteration, _e010_last_scheduled_iteration(scenario) + 1):
            _e010_wait_for_slot(
                schedule_started_monotonic=schedule_started,
                iteration=iteration,
                period_seconds=scenario.iteration_period_seconds,
            )
            context._begin_iteration(iteration)
            lease.assert_current()
            before = runtime.composition.durability.persistence.read_head().durable
            result = measured.iteration(context)
            lease.assert_current()
            if iteration == first_iteration:
                first_result = result
                first_new_commit_ids = [
                    entry.record.commit.commit_id
                    for entry in runtime.composition.durability.persistence.read_durable_records(
                        after=before
                    )
                ]
        measured.drain(context)
    finally:
        lease.release(completed=False)

    if first_result is None:
        raise RuntimeError('owner B did not execute the takeover replay iteration')
    cache_bundle = runtime.job.revision_resolver.cache.load_effective()
    first_entries = _e010_entries_by_commit_ids(
        runtime.composition.durability.persistence.read_durable_records(),
        first_new_commit_ids,
    )
    cycle_factory = runtime.job.cycle_factory
    state = {
        'run_id': 'owner-b',
        'lease_generation': generation,
        'first_iteration': first_iteration,
        'first_revision_origin': first_result.revision_origin.value,
        'first_adoption_outcome': first_result.adoption_outcome.value,
        'first_cycle_executed': first_result.cycle_executed,
        'first_degraded': first_result.degraded,
        'first_new_commit_ids': first_new_commit_ids,
        'first_started_occurrence_ids': _e010_occurrence_ids(first_entries, kind='STARTED'),
        'first_started_episode_ids': _e010_episode_ids(first_entries, kind='STARTED'),
        'recovery': asdict(recovery),
        'cache_replace_count': len(runtime.tracked_revision_cache.replace_monotonic),
        'final_cache_alarm_revision': (
            None if cache_bundle is None else cache_bundle.manifest.alarm_configuration_revision
        ),
        'final_cache_tool_revision': (
            None if cache_bundle is None else cache_bundle.manifest.tool_registry_revision
        ),
        'samples': [asdict(sample) for sample in recorder.samples],
        'recovery_ms': recorder.recovery_ms,
        'drain_ms': recorder.drain_ms,
        'source_loader': asdict(runtime.source_loader),
        'durable_history_lookup': asdict(
            runtime.composition.build_durable_history_lookup_metrics()
        ),
        'occurrence_count': int(cycle_factory.occurrence_count),
        'episode_count': int(cycle_factory.episode_count),
    }
    _e010_write_json(control_root / 'owner-b.json', state)
    _e010_write_marker(control_root / 'b-complete')


def _build_e010_runtime(
    *,
    scenario: BaselineScenario,
    volume_path: Path,
    source_path: Path,
    schedule_started_monotonic: float,
    schedule_base_at: datetime,
    occurrence_id_start: int = 0,
    episode_id_start: int = 0,
    source_first_generation: int | None = None,
    source_last_generation: int | None = None,
):
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=volume_path,
        source_path=source_path,
        occurrence_id_start=occurrence_id_start,
        episode_id_start=episode_id_start,
    )
    revision_source = runtime.job.revision_resolver.source
    if not hasattr(revision_source, 'started_monotonic') or not hasattr(revision_source, 'base_at'):
        raise RuntimeError('E-010 runtime requires a scheduled revision source')
    revision_source.started_monotonic = schedule_started_monotonic
    revision_source.base_at = schedule_base_at
    runtime.job.as_of_provider = lambda context: _e010_iteration_as_of(
        schedule_base_at=schedule_base_at,
        iteration=context.iteration,
        period_seconds=scenario.iteration_period_seconds,
    )
    if source_first_generation is not None:
        runtime.source_loader.first_generation = source_first_generation
    if source_last_generation is not None:
        runtime.source_loader.last_generation = source_last_generation
    if runtime.tracked_revision_cache is None:
        raise RuntimeError('E-010 runtime requires tracked revision cache')
    return runtime


def _e010_job_definition(scenario: BaselineScenario) -> JobDefinition:
    return JobDefinition(
        module_name='ada.processes.alarms_runtime.performance',
        service_name='alarms-runtime-performance',
        job_key=f'alarms-runtime-{scenario.test_id.lower()}',
        sleep_seconds=scenario.iteration_period_seconds,
        iteration_timeout_seconds=30.0,
        execution_timeout_seconds=scenario.duration_seconds + 120.0,
        shutdown_grace_seconds=10.0,
        lease_timeout_seconds=float(_E010_LEASE_TIMEOUT_SECONDS),
        lease_renew_seconds=10.0,
        lease_wait_seconds=0.0,
        lease_poll_seconds=0.05,
        resource_sample_seconds=1.0,
    )


def _e010_acquire_context(
    *,
    definition: JobDefinition,
    volume_path: Path,
    control_root: Path,
    run_id: str,
) -> tuple[JobRuntimeContext, ExecutionLease, int]:
    configuration = RuntimeConfiguration.from_sources(
        environ={
            'ENVIRONMENT': 'local',
            'APPLICATION': 'ada-alarms-runtime-performance',
            'VOLUMEN_PATH': str(volume_path),
            'ATLANTICUS_OBSERVABILITY_FILE_LOGS': 'false',
        }
    )
    context = JobRuntimeContext.create(
        definition=definition,
        configuration=configuration,
        run_id=run_id,
        correlation_id=f'{run_id}-correlation',
    )
    lease = ExecutionLease(
        volume_path=volume_path,
        application=configuration.application,
        service_name=definition.service_name,
        job_key=definition.job_key,
        module_name=definition.module_name,
        run_id=run_id,
        lease_timeout_seconds=definition.lease_timeout_seconds,
        renewal_seconds=definition.lease_renew_seconds,
        wait_seconds=0.0,
        poll_seconds=definition.lease_poll_seconds,
        wall_clock=lambda: _e010_read_lease_clock(control_root),
    )
    acquisition = lease.acquire()
    if not acquisition.acquired or acquisition.generation is None:
        raise RuntimeError(f'{run_id} could not acquire E-010 execution lease')
    return context, lease, acquisition.generation


def _e010_iteration_as_of(
    *,
    schedule_base_at: datetime,
    iteration: int,
    period_seconds: float,
) -> datetime:
    elapsed_seconds = max(0.0, (iteration - 1) * period_seconds)
    if not elapsed_seconds.is_integer():
        raise RuntimeError('E-010 iteration schedule must resolve to whole-second as_of')
    return schedule_base_at + timedelta(seconds=int(elapsed_seconds))


def _e010_expected_failure_iteration(scenario: BaselineScenario) -> int:
    return int(scenario.lease_loss_adoption_at_seconds / scenario.iteration_period_seconds) + 1


def _e010_expected_owner_b_first_iteration(scenario: BaselineScenario) -> int:
    return _e010_expected_failure_iteration(scenario) + 1


def _e010_last_scheduled_iteration(scenario: BaselineScenario) -> int:
    return int(scenario.duration_seconds / scenario.iteration_period_seconds) + 1


def _e010_wait_for_slot(
    *,
    schedule_started_monotonic: float,
    iteration: int,
    period_seconds: float,
) -> None:
    target = schedule_started_monotonic + (iteration - 1) * period_seconds
    while True:
        remaining = target - time.perf_counter()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.1))


def _run_e010_smb_preflight(*, shared_volume: Path, control_root: Path) -> dict[str, object]:
    probe_id = f'e010-{uuid4().hex}'
    probe_path = Path(__file__).resolve().parent.parent / 'tests' / 'smb_fencing_probe.py'
    result = subprocess.run(
        [
            sys.executable,
            str(probe_path),
            'orchestrate',
            '--shared-volume',
            str(shared_volume),
            '--probe-id',
            probe_id,
            '--scenario',
            'post-durable',
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60.0,
    )
    (control_root / 'smb-preflight.log').write_text(
        result.stdout + result.stderr,
        encoding='utf-8',
    )
    if result.returncode != 0:
        raise RuntimeError(
            'E-010 SMB fencing preflight failed; workload was not executed. '
            f'See {control_root / "smb-preflight.log"}'
        )
    return {
        'status': 'PASS',
        'scenario': 'post-durable',
        'probe_id': probe_id,
    }


def _aggregate_e010_source_loader(
    owner_a_state: dict[str, object],
    owner_b_state: dict[str, object],
) -> SimpleNamespace:
    a = dict(owner_a_state['source_loader'])
    b = dict(owner_b_state['source_loader'])
    merged = dict(a)
    additive = (
        'load_count',
        'churn_generation_count',
        'churn_group_transition_count',
        'churn_transition_count',
        'technical_hold_started_transition_count',
        'technical_hold_cleared_transition_count',
        'technical_hold_expired_transition_count',
        'technical_hold_expired_group_transition_count',
        'technical_hold_reappearance_transition_count',
        'technical_hold_reappearance_group_transition_count',
        'initial_error_activation_transition_count',
        'initial_error_activation_group_transition_count',
    )
    for key in additive:
        merged[key] = int(a.get(key, 0)) + int(b.get(key, 0))
    merged['first_generation'] = a.get('first_generation')
    merged['last_generation'] = b.get('last_generation')
    merged['load_durations_ms'] = [*a.get('load_durations_ms', []), *b.get('load_durations_ms', [])]
    merged['merge_durations_ms'] = [
        *a.get('merge_durations_ms', []),
        *b.get('merge_durations_ms', []),
    ]
    merged['physical_partition_column_counts'] = tuple(
        a.get('physical_partition_column_counts', ())
    )
    merged['missing_source_columns'] = tuple(a.get('missing_source_columns', ()))
    return SimpleNamespace(**merged)


def _aggregate_e010_durable_history(
    owner_a_state: dict[str, object],
    owner_b_state: dict[str, object],
) -> DurableHistoryLookupMetrics:
    a = dict(owner_a_state['durable_history_lookup'])
    b = dict(owner_b_state['durable_history_lookup'])
    return DurableHistoryLookupMetrics(
        mode=str(a['mode']),
        cycle_count=int(a['cycle_count']) + int(b['cycle_count']),
        lookup_call_count=int(a['lookup_call_count']) + int(b['lookup_call_count']),
        lookup_total_ms=float(a['lookup_total_ms']) + float(b['lookup_total_ms']),
        durable_record_scan_count=int(a['durable_record_scan_count'])
        + int(b['durable_record_scan_count']),
        durable_record_scan_total_ms=float(a['durable_record_scan_total_ms'])
        + float(b['durable_record_scan_total_ms']),
        durable_record_entries_seen=int(a['durable_record_entries_seen'])
        + int(b['durable_record_entries_seen']),
        index_build_count=int(a['index_build_count']) + int(b['index_build_count']),
        index_build_total_ms=float(a['index_build_total_ms']) + float(b['index_build_total_ms']),
    )


def _merge_e010_samples(
    owner_a_state: dict[str, object],
    owner_b_state: dict[str, object],
) -> tuple[IterationSample, ...]:
    documents = [*owner_a_state['samples'], *owner_b_state['samples']]
    ordered = sorted(
        (IterationSample(**document) for document in documents), key=lambda item: item.iteration
    )
    merged: list[IterationSample] = []
    previous_started = None
    for sample in ordered:
        interval_ms = (
            None
            if previous_started is None
            else (sample.started_monotonic - previous_started) * 1000
        )
        merged.append(replace(sample, start_interval_ms=interval_ms))
        previous_started = sample.started_monotonic
    return tuple(merged)


def _e010_occurrence_ids(entries, *, kind: str, closure_reason: str | None = None) -> list[str]:
    values: list[str] = []
    for entry in entries:
        for change in entry.record.records.get('occurrence_changes', []):
            if change.get('kind') != kind:
                continue
            if closure_reason is not None and change.get('closure_reason') != closure_reason:
                continue
            value = change.get('occurrence_id')
            if isinstance(value, str):
                values.append(value)
    return values


def _e010_episode_ids(entries, *, kind: str, closure_reason: str | None = None) -> list[str]:
    values: list[str] = []
    for entry in entries:
        for change in entry.record.records.get('episode_changes', []):
            if change.get('kind') != kind:
                continue
            if closure_reason is not None and change.get('closure_reason') != closure_reason:
                continue
            value = change.get('episode_id')
            if isinstance(value, str):
                values.append(value)
    return values


def _e010_entries_by_commit_ids(entries, commit_ids: list[str]):
    selected = set(commit_ids)
    return tuple(entry for entry in entries if entry.record.commit.commit_id in selected)


def _e010_copy_shared_evidence(*, shared_run_root: Path, run_root: Path) -> Path:
    destination = run_root / 'shared-volume-final'
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(shared_run_root, destination)
    return destination


def _e010_read_lease_clock(control_root: Path) -> datetime:
    value = datetime.fromisoformat((control_root / 'lease-clock.txt').read_text(encoding='utf-8'))
    if value.tzinfo is None or value.utcoffset() is None:
        raise RuntimeError('E-010 lease clock must be timezone-aware')
    return value.astimezone(UTC)


def _e010_write_json(path: Path, document: dict[str, object]) -> None:
    _e010_atomic_write(path, json.dumps(document, indent=2, sort_keys=True) + '\n')


def _e010_read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise RuntimeError(f'E-010 control document must be an object: {path}')
    return value


def _e010_write_marker(path: Path) -> None:
    _e010_atomic_write(path, 'ready\n')


def _e010_atomic_write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.{uuid4().hex}.tmp')
    temporary.write_text(value, encoding='utf-8')
    temporary.replace(path)


def _e010_wait_for(path: Path, *, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if path.is_file():
            return
        time.sleep(0.05)
    raise TimeoutError(f'timed out waiting for E-010 control marker: {path}')


# Runner E-011: ejecuta sólo hasta la falla controlada del cache; no drena ni
# continúa con AC1 una vez que los resets AC2 ya son durable/materialized.
# Wrapper de medición E-012. No cambia el producto: deja que la iteration #61 termine
# y recién después solicita stop al Job Runtime. La misma composición captura evidencia
# inmediatamente antes y después del drain real para demostrar que no aparece trabajo nuevo.
class _E012DrainBoundaryMeasuredComposition(MeasuredAlarmRuntimeJobComposition):
    __slots__ = (
        'scenario',
        'stop_iteration',
        'schedule_base_at',
        'schedule_started_monotonic',
        'stop_requested_iteration',
        'boundary_evidence',
        'drain_before_evidence',
        'drain_after_evidence',
        'drain_recovery_result',
    )

    def __init__(self, *, scenario: BaselineScenario, stop_iteration: int, **kwargs) -> None:
        super().__init__(**kwargs)
        self.scenario = scenario
        self.stop_iteration = stop_iteration
        self.schedule_base_at: datetime | None = None
        self.schedule_started_monotonic: float | None = None
        self.stop_requested_iteration: int | None = None
        self.boundary_evidence: dict[str, object] | None = None
        self.drain_before_evidence: dict[str, object] | None = None
        self.drain_after_evidence: dict[str, object] | None = None
        self.drain_recovery_result = None

    @classmethod
    def wrap_e012(
        cls,
        composition,
        *,
        recorder: PerformanceRecorder,
        scenario: BaselineScenario,
        stop_iteration: int,
    ):
        return cls(
            recorder=recorder,
            scenario=scenario,
            stop_iteration=stop_iteration,
            composition=composition.composition,
            revision_resolver=composition.revision_resolver,
            adoption_executor=composition.adoption_executor,
            input_consumer=composition.input_consumer,
            iteration_source_loader=composition.iteration_source_loader,
            cycle_factory=composition.cycle_factory,
            as_of_provider=composition.as_of_provider,
        )

    def recover(self, context: JobRuntimeContext):
        # Anclamos los dos relojes del source al final del recovery productivo. Esto evita
        # que lease/observabilidad consuman parte del segundo lógico cero y desplacen 271/241.
        result = super().recover(context)
        source = self.input_consumer.source
        if not isinstance(source, MixedDeactivationInputSource):
            raise RuntimeError(
                'drain-under-workload runtime requires mixed deactivation input source'
            )
        now_epoch = int(datetime.now(UTC).timestamp())
        aligned_epoch = now_epoch - (now_epoch % self.scenario.data_refresh_seconds)
        schedule_base_at = datetime.fromtimestamp(aligned_epoch, UTC)
        schedule_started_monotonic = time.perf_counter()
        source.started_monotonic = schedule_started_monotonic
        source.base_at = schedule_base_at
        source.first_request_created_at = schedule_base_at + timedelta(
            seconds=self.scenario.management_action_at_seconds
        )
        source.first_decision_decided_at = schedule_base_at + timedelta(
            seconds=self.scenario.deactivation_decision_at_seconds
        )
        source.management_hour_bucket = source.first_request_created_at.strftime('%Y-%m-%dT%HZ')
        source.decision_hour_bucket = source.first_decision_decided_at.strftime('%Y-%m-%dT%HZ')
        self.schedule_base_at = schedule_base_at
        self.schedule_started_monotonic = schedule_started_monotonic
        self.as_of_provider = lambda iteration_context: _e011_iteration_as_of(
            schedule_base_at=schedule_base_at,
            iteration=iteration_context.iteration,
            period_seconds=self.scenario.iteration_period_seconds,
        )
        return result

    def iteration(self, context: JobRuntimeContext):
        result = super().iteration(context)
        if context.iteration == self.stop_iteration:
            self.stop_requested_iteration = context.iteration
            self.boundary_evidence = _e012_capture_runtime_evidence(self)
            context.request_stop('performance_drain_boundary')
        return result

    def drain(self, context: JobRuntimeContext):
        self.drain_before_evidence = _e012_capture_runtime_evidence(self)
        result = super().drain(context)
        self.drain_recovery_result = result
        self.drain_after_evidence = _e012_capture_runtime_evidence(self)
        return result


# Captura una fotografía comparable de toda actividad que DRAINING tiene prohibido
# iniciar: WAL/head, snapshots, lecturas de source, cursores, pendientes y cache replace.
def _e012_capture_runtime_evidence(composition) -> dict[str, object]:
    persistence = composition.composition.durability.persistence
    head = persistence.read_head()
    source = composition.input_consumer.source
    snapshots = persistence.list_snapshots()
    state = (
        AtomicJsonStore(root_path=persistence.paths.alarms_root).read(
            'runtime/state/consumers/management.json'
        )
        or {}
    )
    management = state.get('management') if isinstance(state, dict) else None
    decisions = state.get('decisions') if isinstance(state, dict) else None
    management_cursor = management.get('cursor') if isinstance(management, dict) else None
    decision_cursor = decisions.get('cursor') if isinstance(decisions, dict) else None
    management_pending = management.get('pending') if isinstance(management, dict) else None
    decision_pending = decisions.get('pending') if isinstance(decisions, dict) else None
    pending_request_ids = (
        state.get('pending_deactivation_request_ids') if isinstance(state, dict) else None
    )
    cache = composition.revision_resolver.cache
    return {
        'durable_record_count': len(persistence.read_durable_records()),
        'journal_bytes': _e012_journal_bytes(persistence.paths),
        'durable_head': _e012_head_position(head.durable),
        'materialized_head': _e012_head_position(head.materialized),
        'aligned': head.aligned,
        'snapshot_count': len(snapshots),
        'snapshot_documents': _e012_snapshot_documents(snapshots),
        'source_load_count': composition.iteration_source_loader.load_count,
        'management_read_batch_count': len(getattr(source, 'management_read_batch_sizes', [])),
        'decision_read_batch_count': len(getattr(source, 'decision_read_batch_sizes', [])),
        'management_read_at_count': int(getattr(source, 'management_read_at_count', 0)),
        'decision_read_at_count': int(getattr(source, 'decision_read_at_count', 0)),
        'management_consumed_count': sum(getattr(source, 'management_read_batch_sizes', [])),
        'decision_consumed_count': sum(getattr(source, 'decision_read_batch_sizes', [])),
        'cache_replace_count': len(getattr(cache, 'replace_monotonic', [])),
        'management_cursor_byte_offset': (
            management_cursor.get('byte_offset') if isinstance(management_cursor, dict) else None
        ),
        'decision_cursor_byte_offset': (
            decision_cursor.get('byte_offset') if isinstance(decision_cursor, dict) else None
        ),
        'management_pending_count': (
            len(management_pending) if isinstance(management_pending, list) else -1
        ),
        'decision_pending_count': (
            len(decision_pending) if isinstance(decision_pending, list) else -1
        ),
        'pending_deactivation_request_count': (
            len(pending_request_ids) if isinstance(pending_request_ids, list) else -1
        ),
    }


def _e012_head_position(position) -> str | None:
    if position is None:
        return None
    return f'{position.segment_id}:{position.byte_offset}'


def _e012_journal_bytes(paths) -> int:
    return sum(
        path.stat().st_size
        for root in (paths.journal_open_root, paths.journal_sealed_root)
        if root.exists()
        for path in root.rglob('*.jsonl')
    )


def _e012_snapshot_documents(snapshots) -> tuple[str, ...]:
    return tuple(
        sorted(
            json.dumps(snapshot.as_document(), sort_keys=True, separators=(',', ':'))
            for snapshot in snapshots
        )
    )


# Ejecuta E-012 mediante el Job Runtime real, no mediante un loop alternativo del harness.
# El status de ejecución puede reflejar stop cooperativo, mientras la adjudicación PASS/GREEN
# depende de los invariantes de frontera recopilados abajo.
def _run_drain_under_workload(
    *,
    scenario: BaselineScenario,
    volume_path: Path,
    source_path: Path,
    output_dir: Path,
) -> int:
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=volume_path,
        source_path=source_path,
    )
    if not isinstance(runtime.input_source, MixedDeactivationInputSource):
        raise RuntimeError('drain-under-workload runtime requires mixed deactivation input source')
    if runtime.tracked_revision_cache is None:
        raise RuntimeError('drain-under-workload runtime requires tracked revision cache')

    recorder = PerformanceRecorder(iteration_period_seconds=scenario.iteration_period_seconds)
    measured = _E012DrainBoundaryMeasuredComposition.wrap_e012(
        runtime.job,
        recorder=recorder,
        scenario=scenario,
        stop_iteration=scenario.drain_under_workload_stop_iteration,
    )
    definition = _e012_job_definition(scenario)
    environ = {
        'ENVIRONMENT': 'local',
        'APPLICATION': 'ada-alarms-runtime-performance',
        'VOLUMEN_PATH': str(volume_path),
        'ATLANTICUS_OBSERVABILITY_FILE_LOGS': 'false',
    }
    execution = execute_alarm_runtime_job(
        definition=definition,
        composition=measured,
        argv=(),
        environ=environ,
    )
    persistence = runtime.composition.durability.persistence
    head = persistence.read_head()
    records = persistence.read_durable_records()
    snapshots = persistence.list_snapshots()
    snapshot_alarm_count = sum(len(item.as_document()['alarms']) for item in snapshots)
    drain_pressure = _build_drain_under_workload_pressure_metrics(
        scenario=scenario,
        runtime=runtime,
        measured=measured,
        execution=execution,
        records=records,
        snapshots=snapshots,
    )
    expected_snapshot_alarm_count = scenario.alarm_count
    source_load_durations_ms = runtime.source_loader.load_durations_ms or []
    source_merge_durations_ms = runtime.source_loader.merge_durations_ms or []
    durable_history_lookup = runtime.composition.build_durable_history_lookup_metrics()
    report = recorder.build_report(
        test_id=scenario.test_id,
        alarm_count=scenario.alarm_count,
        planned_duration_seconds=scenario.duration_seconds,
        actual_duration_seconds=execution.duration_seconds,
        data_refresh_seconds=scenario.data_refresh_seconds,
        data_profile=scenario.data_profile,
        columns_per_alarm=scenario.columns_per_alarm,
        historical_series_per_alarm=scenario.historical_series_per_alarm,
        historical_window_minutes=scenario.historical_window_minutes,
        historical_step_seconds=scenario.historical_step_seconds,
        historical_points_per_series=scenario.historical_points_per_series,
        historical_value_count=runtime.source_loader.historical_value_count,
        physical_partition_count=scenario.physical_partition_count,
        physical_partition_column_counts=runtime.source_loader.physical_partition_column_counts,
        physical_partition_layout=scenario.physical_partition_layout,
        empty_physical_partition_count=runtime.source_loader.empty_physical_partition_count,
        missing_source_column_count=runtime.source_loader.missing_source_column_count,
        synthesized_null_column_count=runtime.source_loader.synthesized_null_column_count,
        source_view_count=runtime.source_loader.view_count,
        source_column_count=runtime.source_loader.column_count,
        source_row_count=runtime.source_loader.row_count,
        source_frame_bytes=runtime.source_loader.frame_bytes,
        source_numeric_value_count=runtime.source_loader.numeric_value_count,
        latest_source_column_count=runtime.source_loader.latest_column_count,
        historical_source_column_count=runtime.source_loader.historical_column_count,
        historical_source_row_count=runtime.source_loader.historical_row_count,
        source_load_durations_ms=source_load_durations_ms,
        source_merge_durations_ms=source_merge_durations_ms,
        journal_aligned=head.aligned,
        durable_record_count=len(records),
        snapshot_count=len(snapshots),
        snapshot_alarm_count=snapshot_alarm_count,
        expected_snapshot_alarm_count=expected_snapshot_alarm_count,
        source_load_count=runtime.source_loader.load_count,
        durable_history_lookup=durable_history_lookup,
        drain_under_workload_pressure=drain_pressure,
    )
    recorder.write(output_dir=output_dir, report=report)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / 'drain.json').write_text(
        json.dumps(
            {
                'execution_iteration_count': execution.iteration_count,
                'execution_stop_reason': execution.stop_reason,
                'stop_requested_iteration': measured.stop_requested_iteration,
                'boundary': _e012_public_evidence(measured.boundary_evidence),
                'drain_before': _e012_public_evidence(measured.drain_before_evidence),
                'drain_after': _e012_public_evidence(measured.drain_after_evidence),
                'recovery_applied_count': drain_pressure.recovery_applied_count,
                'recovery_discarded_tail_bytes': drain_pressure.recovery_discarded_tail_bytes,
            },
            indent=2,
            sort_keys=True,
        )
        + '\n',
        encoding='utf-8',
    )
    _write_metadata(
        output_dir=output_dir,
        scenario=scenario,
        run_id=execution.run_id,
        stop_reason=execution.stop_reason,
        source_view_count=runtime.source_loader.view_count,
        source_column_count=runtime.source_loader.column_count,
        source_row_count=runtime.source_loader.row_count,
        source_frame_bytes=runtime.source_loader.frame_bytes,
        source_numeric_value_count=runtime.source_loader.numeric_value_count,
        latest_source_column_count=runtime.source_loader.latest_column_count,
        historical_source_column_count=runtime.source_loader.historical_column_count,
        historical_source_row_count=runtime.source_loader.historical_row_count,
        historical_series_per_alarm=scenario.historical_series_per_alarm,
        historical_window_minutes=scenario.historical_window_minutes,
        historical_step_seconds=scenario.historical_step_seconds,
        historical_points_per_series=scenario.historical_points_per_series,
        historical_value_count=runtime.source_loader.historical_value_count,
        physical_partition_count=scenario.physical_partition_count,
        physical_partition_column_counts=runtime.source_loader.physical_partition_column_counts,
        physical_partition_layout=scenario.physical_partition_layout,
        empty_physical_partition_count=runtime.source_loader.empty_physical_partition_count,
        missing_source_column_count=runtime.source_loader.missing_source_column_count,
        synthesized_null_column_count=runtime.source_loader.synthesized_null_column_count,
        expected_snapshot_alarm_count=expected_snapshot_alarm_count,
        durable_history_lookup=durable_history_lookup,
        functional_pressure=None,
        drain_under_workload_pressure=drain_pressure,
    )
    print(json.dumps(report.as_document(), indent=2, sort_keys=True))
    print(f'Artifacts: {output_dir}')
    return 0 if report.result == 'PASS' else 1


def _e012_job_definition(scenario: BaselineScenario) -> JobDefinition:
    return JobDefinition(
        module_name='ada.processes.alarms_runtime.performance',
        service_name='alarms-runtime-performance',
        job_key=f'alarms-runtime-{scenario.test_id.lower()}',
        sleep_seconds=scenario.iteration_period_seconds,
        iteration_timeout_seconds=30.0,
        execution_timeout_seconds=scenario.duration_seconds + 120.0,
        shutdown_grace_seconds=30.0,
        lease_timeout_seconds=scenario.duration_seconds + 120.0,
        lease_renew_seconds=60.0,
        lease_wait_seconds=0.0,
        lease_poll_seconds=0.05,
        resource_sample_seconds=1.0,
    )


def _e012_public_evidence(evidence: dict[str, object] | None) -> dict[str, object] | None:
    if evidence is None:
        return None
    return {key: value for key, value in evidence.items() if key != 'snapshot_documents'}


# Adjudicador E-012: exige el prefijo exacto de D-009 y además compara evidencia
# pre/post drain. Cualquier commit, lectura, cache replace o mutación de snapshot posterior
# a la frontera invalida el run aunque durable/materialized terminen alineados.
def _build_drain_under_workload_pressure_metrics(
    *,
    scenario: BaselineScenario,
    runtime,
    measured: _E012DrainBoundaryMeasuredComposition,
    execution,
    records,
    snapshots,
) -> DrainUnderWorkloadPressureMetrics:
    source = runtime.input_source
    if not isinstance(source, MixedDeactivationInputSource):
        raise RuntimeError('drain-under-workload metrics require mixed deactivation input source')
    before = measured.drain_before_evidence
    after = measured.drain_after_evidence
    boundary = measured.boundary_evidence
    if boundary is None or before is None or after is None:
        raise RuntimeError('drain-under-workload boundary evidence is incomplete')
    recovery = measured.drain_recovery_result
    if recovery is None:
        raise RuntimeError('drain-under-workload recovery evidence is missing')

    expected_management = scenario.drain_under_workload_management_consumed_count
    expected_decisions = scenario.drain_under_workload_decision_consumed_count
    expected_pending_requests = scenario.drain_under_workload_pending_request_count
    expected_management_cursor = expected_management * source.byte_length
    expected_decision_cursor = expected_decisions * source.byte_length
    management_consumed = int(after['management_consumed_count'])
    decision_consumed = int(after['decision_consumed_count'])
    future_management = source.input_count - management_consumed
    future_decisions = source.input_count - decision_consumed

    commit_ids: list[str] = []
    last_commit_by_group: dict[str, str] = {}
    chain_mismatches = 0
    latest_snapshot_by_group: dict[str, dict[str, object]] = {}
    for entry in records:
        record = entry.record
        commit = record.commit
        commit_ids.append(commit.commit_id)
        expected_previous = last_commit_by_group.get(commit.priority_group)
        if commit.previous_commit_id != expected_previous:
            chain_mismatches += 1
        last_commit_by_group[commit.priority_group] = commit.commit_id
        latest_snapshot_by_group[commit.priority_group] = record.snapshot_after.as_document()
    duplicate_commit_ids = len(commit_ids) - len(set(commit_ids))
    final_snapshot_match_count = 0
    for snapshot in snapshots:
        document = snapshot.as_document()
        priority_group = document.get('priority_group')
        if (
            isinstance(priority_group, str)
            and latest_snapshot_by_group.get(priority_group) == document
        ):
            final_snapshot_match_count += 1

    post_source_load = int(after['source_load_count']) - int(before['source_load_count'])
    post_management_reads = int(after['management_read_batch_count']) - int(
        before['management_read_batch_count']
    )
    post_decision_reads = int(after['decision_read_batch_count']) - int(
        before['decision_read_batch_count']
    )
    post_cache_replaces = int(after['cache_replace_count']) - int(before['cache_replace_count'])
    new_frozen_units = int(after['durable_record_count']) - int(before['durable_record_count'])
    snapshots_unchanged = before['snapshot_documents'] == after['snapshot_documents']
    journal_head_unchanged = (
        before['durable_head'] == after['durable_head']
        and before['materialized_head'] == after['materialized_head']
    )
    journal_bytes_unchanged = before['journal_bytes'] == after['journal_bytes']

    functional_integrity_ok = (
        measured.stop_requested_iteration == scenario.drain_under_workload_stop_iteration
        and execution.iteration_count == scenario.drain_under_workload_stop_iteration
        and execution.stop_reason == 'performance_drain_boundary'
        and len(measured.recorder.samples) == scenario.drain_under_workload_stop_iteration
        and management_consumed == expected_management
        and decision_consumed == expected_decisions
        and after['management_cursor_byte_offset'] == expected_management_cursor
        and after['decision_cursor_byte_offset'] == expected_decision_cursor
        and after['management_pending_count'] == 0
        and after['decision_pending_count'] == 0
        and after['pending_deactivation_request_count'] == expected_pending_requests
        and future_management == source.input_count - expected_management
        and future_decisions == source.input_count - expected_decisions
        and before['durable_record_count']
        == scenario.drain_under_workload_expected_durable_record_count
        and after['durable_record_count']
        == scenario.drain_under_workload_expected_durable_record_count
        and new_frozen_units == 0
        and journal_bytes_unchanged
        and journal_head_unchanged
        and before['aligned'] is True
        and after['aligned'] is True
        and before['snapshot_count'] == scenario.priority_group_count
        and after['snapshot_count'] == scenario.priority_group_count
        and snapshots_unchanged
        and final_snapshot_match_count == scenario.priority_group_count
        and post_source_load == 0
        and post_management_reads == 0
        and post_decision_reads == 0
        and int(after['management_read_at_count']) == int(before['management_read_at_count'])
        and int(after['decision_read_at_count']) == int(before['decision_read_at_count'])
        and post_cache_replaces == 0
        and int(before['cache_replace_count']) == 1
        and recovery.applied_count == 0
        and recovery.discarded_tail_bytes == 0
        and duplicate_commit_ids == 0
        and chain_mismatches == 0
    )

    return DrainUnderWorkloadPressureMetrics(
        stop_at_seconds=scenario.drain_under_workload_at_seconds,
        stop_iteration=measured.stop_requested_iteration or 0,
        expected_stop_iteration=scenario.drain_under_workload_stop_iteration,
        execution_iteration_count=execution.iteration_count,
        execution_stop_reason=execution.stop_reason,
        management_consumed_count=management_consumed,
        expected_management_consumed_count=expected_management,
        decision_consumed_count=decision_consumed,
        expected_decision_consumed_count=expected_decisions,
        management_cursor_byte_offset=after['management_cursor_byte_offset'],
        expected_management_cursor_byte_offset=expected_management_cursor,
        decision_cursor_byte_offset=after['decision_cursor_byte_offset'],
        expected_decision_cursor_byte_offset=expected_decision_cursor,
        management_pending_count=int(after['management_pending_count']),
        decision_pending_count=int(after['decision_pending_count']),
        pending_deactivation_request_count=int(after['pending_deactivation_request_count']),
        expected_pending_deactivation_request_count=expected_pending_requests,
        future_management_count=future_management,
        expected_future_management_count=source.input_count - expected_management,
        future_decision_count=future_decisions,
        expected_future_decision_count=source.input_count - expected_decisions,
        durable_record_count_before_drain=int(before['durable_record_count']),
        durable_record_count_after_drain=int(after['durable_record_count']),
        expected_durable_record_count=scenario.drain_under_workload_expected_durable_record_count,
        journal_bytes_before_drain=int(before['journal_bytes']),
        journal_bytes_after_drain=int(after['journal_bytes']),
        journal_bytes_unchanged=journal_bytes_unchanged,
        durable_head_before_drain=before['durable_head'],
        durable_head_after_drain=after['durable_head'],
        materialized_head_before_drain=before['materialized_head'],
        materialized_head_after_drain=after['materialized_head'],
        journal_head_unchanged=journal_head_unchanged,
        durable_materialized_aligned_before_drain=bool(before['aligned']),
        durable_materialized_aligned_after_drain=bool(after['aligned']),
        snapshot_count_before_drain=int(before['snapshot_count']),
        snapshot_count_after_drain=int(after['snapshot_count']),
        snapshot_documents_unchanged=snapshots_unchanged,
        final_snapshot_match_count=final_snapshot_match_count,
        expected_final_snapshot_match_count=scenario.priority_group_count,
        source_load_count_before_drain=int(before['source_load_count']),
        source_load_count_after_drain=int(after['source_load_count']),
        management_read_batch_count_before_drain=int(before['management_read_batch_count']),
        management_read_batch_count_after_drain=int(after['management_read_batch_count']),
        decision_read_batch_count_before_drain=int(before['decision_read_batch_count']),
        decision_read_batch_count_after_drain=int(after['decision_read_batch_count']),
        management_read_at_count_before_drain=int(before['management_read_at_count']),
        management_read_at_count_after_drain=int(after['management_read_at_count']),
        decision_read_at_count_before_drain=int(before['decision_read_at_count']),
        decision_read_at_count_after_drain=int(after['decision_read_at_count']),
        cache_replace_count_before_drain=int(before['cache_replace_count']),
        cache_replace_count_after_drain=int(after['cache_replace_count']),
        recovery_applied_count=recovery.applied_count,
        recovery_discarded_tail_bytes=recovery.discarded_tail_bytes,
        duplicate_commit_id_count=duplicate_commit_ids,
        commit_chain_mismatch_count=chain_mismatches,
        new_frozen_durable_unit_count=new_frozen_units,
        post_drain_source_load_count=post_source_load,
        post_drain_management_read_count=post_management_reads,
        post_drain_decision_read_count=post_decision_reads,
        post_drain_cache_replace_count=post_cache_replaces,
        functional_integrity_ok=functional_integrity_ok,
    )


def _run_cache_promotion_failure(
    *,
    scenario: BaselineScenario,
    volume_path: Path,
    source_path: Path,
    output_dir: Path,
) -> int:
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=volume_path,
        source_path=source_path,
    )
    if runtime.cache_promotion_failure_cache is None:
        raise RuntimeError('cache promotion failure runtime requires injected cache wrapper')
    revision_source = runtime.job.revision_resolver.source
    if not hasattr(revision_source, 'started_monotonic') or not hasattr(revision_source, 'base_at'):
        raise RuntimeError('cache promotion failure runtime requires a scheduled revision source')

    now_epoch = int(datetime.now(UTC).timestamp())
    aligned_epoch = now_epoch - (now_epoch % scenario.data_refresh_seconds)
    schedule_base_at = datetime.fromtimestamp(aligned_epoch, UTC)
    schedule_started_monotonic = time.perf_counter()
    revision_source.started_monotonic = schedule_started_monotonic
    revision_source.base_at = schedule_base_at
    runtime.job.as_of_provider = lambda context: _e011_iteration_as_of(
        schedule_base_at=schedule_base_at,
        iteration=context.iteration,
        period_seconds=scenario.iteration_period_seconds,
    )

    recorder = PerformanceRecorder(iteration_period_seconds=scenario.iteration_period_seconds)
    measured = MeasuredAlarmRuntimeJobComposition.wrap(runtime.job, recorder=recorder)
    definition = _e011_job_definition(scenario)
    run_id = str(uuid4())
    context, lease = _e011_acquire_context(
        definition=definition,
        volume_path=volume_path,
        run_id=run_id,
    )
    measured.recover(context)

    failed_iteration = _e011_expected_failure_iteration(scenario)
    failed_iteration_ms = None
    failure: InjectedCachePromotionError | None = None
    execution_started = schedule_started_monotonic
    try:
        for iteration in range(1, failed_iteration + 1):
            _e010_wait_for_slot(
                schedule_started_monotonic=schedule_started_monotonic,
                iteration=iteration,
                period_seconds=scenario.iteration_period_seconds,
            )
            context._begin_iteration(iteration)
            lease.assert_current()
            if iteration == failed_iteration:
                failed_started = time.perf_counter()
                try:
                    measured.iteration(context)
                except InjectedCachePromotionError as error:
                    failed_iteration_ms = (time.perf_counter() - failed_started) * 1000
                    failure = error
                    break
                raise RuntimeError('cache promotion failure injection did not stop iteration')
            measured.iteration(context)
            lease.assert_current()
    finally:
        lease.release(completed=False)
    actual_duration_seconds = time.perf_counter() - execution_started

    if failure is None:
        raise RuntimeError('cache promotion failure pressure did not observe injected failure')

    persistence = runtime.composition.durability.persistence
    head = persistence.read_head()
    records = persistence.read_durable_records()
    snapshots = persistence.list_snapshots()
    snapshot_alarm_count = sum(len(item.as_document()['alarms']) for item in snapshots)
    cache_pressure = _build_cache_promotion_failure_pressure_metrics(
        scenario=scenario,
        runtime=runtime,
        records=records,
        snapshots=snapshots,
        samples=recorder.samples,
        failed_iteration=failed_iteration,
        failed_iteration_ms=failed_iteration_ms,
        failure=failure,
        durable_materialized_aligned=head.aligned,
    )
    expected_snapshot_alarm_count = cache_pressure.expected_final_alarm_count
    source_load_durations_ms = runtime.source_loader.load_durations_ms or []
    source_merge_durations_ms = runtime.source_loader.merge_durations_ms or []
    durable_history_lookup = runtime.composition.build_durable_history_lookup_metrics()
    report = recorder.build_report(
        test_id=scenario.test_id,
        alarm_count=scenario.alarm_count,
        planned_duration_seconds=scenario.duration_seconds,
        actual_duration_seconds=actual_duration_seconds,
        data_refresh_seconds=scenario.data_refresh_seconds,
        data_profile=scenario.data_profile,
        columns_per_alarm=scenario.columns_per_alarm,
        historical_series_per_alarm=scenario.historical_series_per_alarm,
        historical_window_minutes=scenario.historical_window_minutes,
        historical_step_seconds=scenario.historical_step_seconds,
        historical_points_per_series=scenario.historical_points_per_series,
        historical_value_count=runtime.source_loader.historical_value_count,
        physical_partition_count=scenario.physical_partition_count,
        physical_partition_column_counts=runtime.source_loader.physical_partition_column_counts,
        physical_partition_layout=scenario.physical_partition_layout,
        empty_physical_partition_count=runtime.source_loader.empty_physical_partition_count,
        missing_source_column_count=runtime.source_loader.missing_source_column_count,
        synthesized_null_column_count=runtime.source_loader.synthesized_null_column_count,
        source_view_count=runtime.source_loader.view_count,
        source_column_count=runtime.source_loader.column_count,
        source_row_count=runtime.source_loader.row_count,
        source_frame_bytes=runtime.source_loader.frame_bytes,
        source_numeric_value_count=runtime.source_loader.numeric_value_count,
        latest_source_column_count=runtime.source_loader.latest_column_count,
        historical_source_column_count=runtime.source_loader.historical_column_count,
        historical_source_row_count=runtime.source_loader.historical_row_count,
        source_load_durations_ms=source_load_durations_ms,
        source_merge_durations_ms=source_merge_durations_ms,
        journal_aligned=head.aligned,
        durable_record_count=len(records),
        snapshot_count=len(snapshots),
        snapshot_alarm_count=snapshot_alarm_count,
        expected_snapshot_alarm_count=expected_snapshot_alarm_count,
        source_load_count=runtime.source_loader.load_count,
        durable_history_lookup=durable_history_lookup,
        cache_promotion_failure_pressure=cache_pressure,
    )
    recorder.write(output_dir=output_dir, report=report)
    failure_document = {
        'failed_iteration': failed_iteration,
        'failed_iteration_ms': failed_iteration_ms,
        'exception_type': type(failure).__name__,
        'exception_message': str(failure),
        'target_promotion_attempt_count': len(
            runtime.cache_promotion_failure_cache.target_attempt_monotonic
        ),
        'target_promotion_failure_count': len(
            runtime.cache_promotion_failure_cache.target_failure_monotonic
        ),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / 'failure.json').write_text(
        json.dumps(failure_document, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    _write_metadata(
        output_dir=output_dir,
        scenario=scenario,
        run_id=run_id,
        stop_reason='expected_cache_promotion_failure',
        source_view_count=runtime.source_loader.view_count,
        source_column_count=runtime.source_loader.column_count,
        source_row_count=runtime.source_loader.row_count,
        source_frame_bytes=runtime.source_loader.frame_bytes,
        source_numeric_value_count=runtime.source_loader.numeric_value_count,
        latest_source_column_count=runtime.source_loader.latest_column_count,
        historical_source_column_count=runtime.source_loader.historical_column_count,
        historical_source_row_count=runtime.source_loader.historical_row_count,
        historical_series_per_alarm=scenario.historical_series_per_alarm,
        historical_window_minutes=scenario.historical_window_minutes,
        historical_step_seconds=scenario.historical_step_seconds,
        historical_points_per_series=scenario.historical_points_per_series,
        historical_value_count=runtime.source_loader.historical_value_count,
        physical_partition_count=scenario.physical_partition_count,
        physical_partition_column_counts=runtime.source_loader.physical_partition_column_counts,
        physical_partition_layout=scenario.physical_partition_layout,
        empty_physical_partition_count=runtime.source_loader.empty_physical_partition_count,
        missing_source_column_count=runtime.source_loader.missing_source_column_count,
        synthesized_null_column_count=runtime.source_loader.synthesized_null_column_count,
        expected_snapshot_alarm_count=expected_snapshot_alarm_count,
        durable_history_lookup=durable_history_lookup,
        functional_pressure=None,
        cache_promotion_failure_pressure=cache_pressure,
    )
    print(json.dumps(report.as_document(), indent=2, sort_keys=True))
    print(f'Artifacts: {output_dir}')
    return 0 if report.result == 'PASS' else 1


def _e011_job_definition(scenario: BaselineScenario) -> JobDefinition:
    return JobDefinition(
        module_name='ada.processes.alarms_runtime.performance',
        service_name='alarms-runtime-performance',
        job_key=f'alarms-runtime-{scenario.test_id.lower()}',
        sleep_seconds=scenario.iteration_period_seconds,
        iteration_timeout_seconds=30.0,
        execution_timeout_seconds=scenario.duration_seconds + 120.0,
        shutdown_grace_seconds=10.0,
        lease_timeout_seconds=scenario.duration_seconds + 120.0,
        lease_renew_seconds=60.0,
        lease_wait_seconds=0.0,
        lease_poll_seconds=0.05,
        resource_sample_seconds=1.0,
    )


def _e011_acquire_context(
    *,
    definition: JobDefinition,
    volume_path: Path,
    run_id: str,
) -> tuple[JobRuntimeContext, ExecutionLease]:
    configuration = RuntimeConfiguration.from_sources(
        environ={
            'ENVIRONMENT': 'local',
            'APPLICATION': 'ada-alarms-runtime-performance',
            'VOLUMEN_PATH': str(volume_path),
            'ATLANTICUS_OBSERVABILITY_FILE_LOGS': 'false',
        }
    )
    context = JobRuntimeContext.create(
        definition=definition,
        configuration=configuration,
        run_id=run_id,
        correlation_id=f'{run_id}-correlation',
    )
    lease = ExecutionLease(
        volume_path=volume_path,
        application=configuration.application,
        service_name=definition.service_name,
        job_key=definition.job_key,
        module_name=definition.module_name,
        run_id=run_id,
        lease_timeout_seconds=definition.lease_timeout_seconds,
        renewal_seconds=definition.lease_renew_seconds,
        wait_seconds=0.0,
        poll_seconds=definition.lease_poll_seconds,
        wall_clock=lambda: datetime.now(UTC),
    )
    acquisition = lease.acquire()
    if not acquisition.acquired or acquisition.generation is None:
        raise RuntimeError('E-011 could not acquire single-owner execution lease')
    context._bind_lease_authority(
        generation=acquisition.generation,
        checker=lease.assert_current,
        fence=lease.fenced_mutation,
    )
    return context, lease


def _e011_iteration_as_of(
    *,
    schedule_base_at: datetime,
    iteration: int,
    period_seconds: float,
) -> datetime:
    elapsed_seconds = max(0.0, (iteration - 1) * period_seconds)
    if not elapsed_seconds.is_integer():
        raise RuntimeError('E-011 iteration schedule must resolve to whole-second as_of')
    return schedule_base_at + timedelta(seconds=int(elapsed_seconds))


def _e011_expected_failure_iteration(scenario: BaselineScenario) -> int:
    return int(scenario.cache_promotion_failure_at_seconds / scenario.iteration_period_seconds) + 1


# Adjudicación E-011: compara el prefijo durable exacto contra el contrato
# congelado y exige evidencia explícita de fail-closed.
def _build_cache_promotion_failure_pressure_metrics(
    *,
    scenario: BaselineScenario,
    runtime,
    records,
    snapshots,
    samples,
    failed_iteration: int | None,
    failed_iteration_ms: float | None,
    failure: BaseException | None,
    durable_materialized_aligned: bool,
) -> CachePromotionFailurePressureMetrics | None:
    if not scenario.has_cache_promotion_failure_pressure:
        return None
    target_revision = runtime.target_revision
    cache = runtime.cache_promotion_failure_cache
    if target_revision is None or cache is None:
        raise RuntimeError('cache promotion failure metrics require target revision and cache')

    plan = plan_configuration_adoption(runtime.revision, target_revision)
    dispositions = {item: 0 for item in ConfigurationAdoptionDisposition}
    for change in plan.changes:
        dispositions[change.disposition] += 1
    reset_alarm_keys = {
        change.identity.canonical_key
        for change in plan.changes
        if change.disposition is ConfigurationAdoptionDisposition.STRUCTURAL_RESET
    }
    reset_groups = set(plan.structural_reset_groups)
    source_revision = runtime.revision.alarm_configuration_revision
    source_tool_revision = runtime.revision.tool_registry_revision
    target_revision_key = target_revision.alarm_configuration_revision
    target_tool_revision = target_revision.tool_registry_revision

    source_revision_durable_record_count = 0
    target_revision_durable_record_count = 0
    group_record_counts: dict[str, int] = {}
    commit_ids: list[str] = []
    last_commit_by_group: dict[str, str] = {}
    target_reset_commit_by_group: dict[str, str] = {}
    commit_chain_mismatch_count = 0
    unexpected_target_operational_commit_count = 0
    configuration_reconfigured_occurrence_count = 0
    configuration_terminated_episode_count = 0
    occurrence_started_count = 0
    occurrence_closed_count = 0
    episode_started_count = 0
    episode_closed_count = 0
    assignment_change_count = 0

    for entry in records:
        record = entry.record
        commit = record.commit
        commit_ids.append(commit.commit_id)
        expected_previous = last_commit_by_group.get(commit.priority_group)
        if commit.previous_commit_id != expected_previous:
            commit_chain_mismatch_count += 1
        last_commit_by_group[commit.priority_group] = commit.commit_id
        group_record_counts[commit.priority_group] = (
            group_record_counts.get(commit.priority_group, 0) + 1
        )
        is_source = (
            commit.alarm_configuration_revision == source_revision
            and commit.tool_registry_revision == source_tool_revision
        )
        is_target = (
            commit.alarm_configuration_revision == target_revision_key
            and commit.tool_registry_revision == target_tool_revision
        )
        source_revision_durable_record_count += int(is_source)
        target_revision_durable_record_count += int(is_target)
        has_configuration_reset = False
        for change in record.records.get('occurrence_changes', []):
            kind = change.get('kind')
            occurrence_started_count += int(kind == 'STARTED')
            occurrence_closed_count += int(kind == 'CLOSED')
            if (
                kind == 'CLOSED'
                and str(change.get('closure_reason')).strip().lower()
                == 'configuration_reconfigured'
                and change.get('alarm_key') in reset_alarm_keys
            ):
                configuration_reconfigured_occurrence_count += 1
                has_configuration_reset = True
        for change in record.records.get('episode_changes', []):
            kind = change.get('kind')
            episode_started_count += int(kind == 'STARTED')
            episode_closed_count += int(kind == 'CLOSED')
            if (
                kind == 'CLOSED'
                and str(change.get('closure_reason')).strip().lower() == 'configuration_terminated'
            ):
                configuration_terminated_episode_count += 1
        assignment_change_count += sum(
            change.get('kind') == 'ASSIGNED'
            for change in record.records.get('assignment_changes', [])
        )
        if is_target and has_configuration_reset:
            target_reset_commit_by_group[commit.priority_group] = commit.commit_id
        if is_target and not has_configuration_reset:
            unexpected_target_operational_commit_count += 1

    duplicate_commit_id_count = len(commit_ids) - len(set(commit_ids))
    groups_with_1_record = sum(count == 1 for count in group_record_counts.values())
    groups_with_2_records = sum(count == 2 for count in group_record_counts.values())
    groups_with_3_records = sum(count == 3 for count in group_record_counts.values())

    source_state_basis_snapshot_count = 0
    target_state_basis_snapshot_count = 0
    reset_snapshot_count = 0
    reset_empty_snapshot_count = 0
    reset_snapshot_without_state_basis_count = 0
    reset_snapshot_target_last_commit_count = 0
    final_alarm_count = 0
    final_assignment_count = 0
    final_pending_assignment_count = 0
    open_occurrence_count = 0
    open_episode_count = 0
    for snapshot in snapshots:
        document = snapshot.as_document()
        basis = document.get('state_basis')
        basis_key = None
        if isinstance(basis, dict):
            basis_key = (
                basis.get('alarm_configuration_revision'),
                basis.get('tool_registry_revision'),
            )
        source_state_basis_snapshot_count += int(
            basis_key == (source_revision, source_tool_revision)
        )
        target_state_basis_snapshot_count += int(
            basis_key == (target_revision_key, target_tool_revision)
        )
        alarms = document.get('alarms', {})
        priority_group = document.get('priority_group')
        if priority_group in reset_groups:
            reset_snapshot_count += 1
            reset_empty_snapshot_count += int(not alarms and document.get('episode') is None)
            reset_snapshot_without_state_basis_count += int(basis is None)
            expected_last_commit_id = target_reset_commit_by_group.get(priority_group)
            reset_snapshot_target_last_commit_count += int(
                expected_last_commit_id is not None
                and document.get('last_commit_id') == expected_last_commit_id
            )
        open_episode_count += int(document.get('episode') is not None)
        final_alarm_count += len(alarms)
        for alarm in alarms.values():
            occurrence = alarm.get('occurrence')
            if occurrence is None:
                continue
            open_occurrence_count += 1
            final_assignment_count += len(occurrence.get('assignments', []))
            final_pending_assignment_count += len(occurrence.get('pending_assignments', []))

    cache_bundle = cache.load_effective()
    final_cache_alarm_revision = (
        None if cache_bundle is None else cache_bundle.manifest.alarm_configuration_revision
    )
    final_cache_tool_revision = (
        None if cache_bundle is None else cache_bundle.manifest.tool_registry_revision
    )
    expected_failed_iteration = _e011_expected_failure_iteration(scenario)
    expected_successful_iteration_count = expected_failed_iteration - 1
    failed_iteration_sample_count = sum(
        sample.iteration == expected_failed_iteration for sample in samples
    )
    expected_reset_alarm_count = scenario.cache_promotion_failure_structural_reset_alarm_count
    expected_reset_group_count = (
        scenario.cache_promotion_failure_structural_reset_priority_group_count
    )
    active_reset_alarm_count = expected_reset_alarm_count * scenario.initial_active_percent // 100
    churn_group_count = (
        scenario.alarm_count
        * scenario.operational_churn_percent
        // 100
        // scenario.effective_priority_group_size
    )
    source_churn_generation_count = max(
        0, scenario.cache_promotion_failure_at_seconds // scenario.data_refresh_seconds - 1
    )
    expected_source_revision_durable_record_count = (
        scenario.priority_group_count + source_churn_generation_count * churn_group_count
    )
    expected_target_revision_durable_record_count = expected_reset_group_count
    expected_durable_record_count = (
        expected_source_revision_durable_record_count
        + expected_target_revision_durable_record_count
    )
    expected_groups_with_3_records = expected_reset_group_count
    expected_groups_with_2_records = source_churn_generation_count * churn_group_count - (
        expected_reset_group_count
    )
    expected_groups_with_1_record = (
        scenario.priority_group_count
        - expected_groups_with_2_records
        - expected_groups_with_3_records
    )
    expected_occurrence_started_count = scenario.initial_active_alarm_count + (
        source_churn_generation_count
        * scenario.alarm_count
        * scenario.operational_churn_percent
        // 100
        // 2
    )
    expected_occurrence_closed_count = (
        source_churn_generation_count
        * scenario.alarm_count
        * scenario.operational_churn_percent
        // 100
        // 2
        + active_reset_alarm_count
    )
    expected_episode_started_count = scenario.priority_group_count
    expected_episode_closed_count = expected_reset_group_count
    expected_assignment_change_count = expected_occurrence_started_count
    expected_final_alarm_count = scenario.initial_active_alarm_count - active_reset_alarm_count
    expected_final_assignment_count = expected_final_alarm_count
    expected_open_occurrence_count = expected_final_alarm_count
    expected_open_episode_count = scenario.priority_group_count - expected_reset_group_count
    expected_source_state_basis_snapshot_count = (
        scenario.priority_group_count - expected_reset_group_count
    )
    expected_target_state_basis_snapshot_count = 0
    expected_reset_snapshot_count = expected_reset_group_count
    expected_reset_empty_snapshot_count = expected_reset_group_count
    expected_reset_snapshot_without_state_basis_count = expected_reset_group_count
    expected_reset_snapshot_target_last_commit_count = expected_reset_group_count

    functional_integrity_ok = (
        plan.is_adoptable
        and len(plan.changes) == scenario.alarm_count
        and dispositions[ConfigurationAdoptionDisposition.UNCHANGED]
        == scenario.alarm_count - expected_reset_alarm_count
        and dispositions[ConfigurationAdoptionDisposition.STRUCTURAL_RESET]
        == expected_reset_alarm_count
        and dispositions[ConfigurationAdoptionDisposition.COMPATIBLE] == 0
        and dispositions[ConfigurationAdoptionDisposition.DISABLED] == 0
        and dispositions[ConfigurationAdoptionDisposition.REMOVED] == 0
        and dispositions[ConfigurationAdoptionDisposition.REJECTED] == 0
        and len(reset_groups) == expected_reset_group_count
        and failed_iteration == expected_failed_iteration
        and len(samples) == expected_successful_iteration_count
        and failed_iteration_sample_count == 0
        and failure is not None
        and type(failure).__name__ == 'InjectedCachePromotionError'
        and str(failure) == 'injected target revision cache promotion failure'
        and len(cache.replace_monotonic) == 1
        and len(cache.target_attempt_monotonic) == 1
        and len(cache.target_failure_monotonic) == 1
        and cache.successful_target_replace_count == 0
        and final_cache_alarm_revision == source_revision
        and final_cache_tool_revision == source_tool_revision
        and source_revision_durable_record_count == expected_source_revision_durable_record_count
        and target_revision_durable_record_count == expected_target_revision_durable_record_count
        and len(records) == expected_durable_record_count
        and duplicate_commit_id_count == 0
        and commit_chain_mismatch_count == 0
        and unexpected_target_operational_commit_count == 0
        and groups_with_1_record == expected_groups_with_1_record
        and groups_with_2_records == expected_groups_with_2_records
        and groups_with_3_records == expected_groups_with_3_records
        and source_state_basis_snapshot_count == expected_source_state_basis_snapshot_count
        and target_state_basis_snapshot_count == expected_target_state_basis_snapshot_count
        and reset_snapshot_count == expected_reset_snapshot_count
        and reset_empty_snapshot_count == expected_reset_empty_snapshot_count
        and reset_snapshot_without_state_basis_count
        == expected_reset_snapshot_without_state_basis_count
        and reset_snapshot_target_last_commit_count
        == expected_reset_snapshot_target_last_commit_count
        and configuration_reconfigured_occurrence_count == active_reset_alarm_count
        and configuration_terminated_episode_count == expected_reset_group_count
        and occurrence_started_count == expected_occurrence_started_count
        and occurrence_closed_count == expected_occurrence_closed_count
        and episode_started_count == expected_episode_started_count
        and episode_closed_count == expected_episode_closed_count
        and assignment_change_count == expected_assignment_change_count
        and final_alarm_count == expected_final_alarm_count
        and final_assignment_count == expected_final_assignment_count
        and final_pending_assignment_count == 0
        and open_occurrence_count == expected_open_occurrence_count
        and open_episode_count == expected_open_episode_count
        and durable_materialized_aligned
    )

    return CachePromotionFailurePressureMetrics(
        failure_at_seconds=scenario.cache_promotion_failure_at_seconds,
        structural_reset_alarm_percent=5,
        source_revision=source_revision,
        target_revision=target_revision_key,
        plan_change_count=len(plan.changes),
        unchanged_change_count=dispositions[ConfigurationAdoptionDisposition.UNCHANGED],
        structural_reset_change_count=dispositions[
            ConfigurationAdoptionDisposition.STRUCTURAL_RESET
        ],
        expected_structural_reset_change_count=expected_reset_alarm_count,
        structural_reset_group_count=len(reset_groups),
        expected_structural_reset_group_count=expected_reset_group_count,
        failed_iteration=failed_iteration,
        expected_failed_iteration=expected_failed_iteration,
        failed_iteration_ms=failed_iteration_ms,
        successful_iteration_count=len(samples),
        expected_successful_iteration_count=expected_successful_iteration_count,
        failed_iteration_sample_count=failed_iteration_sample_count,
        exception_type=None if failure is None else type(failure).__name__,
        exception_message=None if failure is None else str(failure),
        cache_replace_count=len(cache.replace_monotonic),
        target_promotion_attempt_count=len(cache.target_attempt_monotonic),
        target_promotion_failure_count=len(cache.target_failure_monotonic),
        successful_target_replace_count=cache.successful_target_replace_count,
        final_cache_alarm_revision=final_cache_alarm_revision,
        final_cache_tool_revision=final_cache_tool_revision,
        source_revision_durable_record_count=source_revision_durable_record_count,
        expected_source_revision_durable_record_count=(
            expected_source_revision_durable_record_count
        ),
        target_revision_durable_record_count=target_revision_durable_record_count,
        expected_target_revision_durable_record_count=(
            expected_target_revision_durable_record_count
        ),
        durable_record_count=len(records),
        expected_durable_record_count=expected_durable_record_count,
        duplicate_commit_id_count=duplicate_commit_id_count,
        commit_chain_mismatch_count=commit_chain_mismatch_count,
        unexpected_target_operational_commit_count=unexpected_target_operational_commit_count,
        groups_with_1_record=groups_with_1_record,
        expected_groups_with_1_record=expected_groups_with_1_record,
        groups_with_2_records=groups_with_2_records,
        expected_groups_with_2_records=expected_groups_with_2_records,
        groups_with_3_records=groups_with_3_records,
        expected_groups_with_3_records=expected_groups_with_3_records,
        source_state_basis_snapshot_count=source_state_basis_snapshot_count,
        expected_source_state_basis_snapshot_count=(expected_source_state_basis_snapshot_count),
        target_state_basis_snapshot_count=target_state_basis_snapshot_count,
        expected_target_state_basis_snapshot_count=(expected_target_state_basis_snapshot_count),
        reset_snapshot_count=reset_snapshot_count,
        expected_reset_snapshot_count=expected_reset_snapshot_count,
        reset_empty_snapshot_count=reset_empty_snapshot_count,
        expected_reset_empty_snapshot_count=expected_reset_empty_snapshot_count,
        reset_snapshot_without_state_basis_count=(reset_snapshot_without_state_basis_count),
        expected_reset_snapshot_without_state_basis_count=(
            expected_reset_snapshot_without_state_basis_count
        ),
        reset_snapshot_target_last_commit_count=(reset_snapshot_target_last_commit_count),
        expected_reset_snapshot_target_last_commit_count=(
            expected_reset_snapshot_target_last_commit_count
        ),
        configuration_reconfigured_occurrence_count=(configuration_reconfigured_occurrence_count),
        expected_configuration_reconfigured_occurrence_count=active_reset_alarm_count,
        configuration_terminated_episode_count=configuration_terminated_episode_count,
        expected_configuration_terminated_episode_count=expected_reset_group_count,
        occurrence_started_count=occurrence_started_count,
        expected_occurrence_started_count=expected_occurrence_started_count,
        occurrence_closed_count=occurrence_closed_count,
        expected_occurrence_closed_count=expected_occurrence_closed_count,
        episode_started_count=episode_started_count,
        expected_episode_started_count=expected_episode_started_count,
        episode_closed_count=episode_closed_count,
        expected_episode_closed_count=expected_episode_closed_count,
        assignment_change_count=assignment_change_count,
        expected_assignment_change_count=expected_assignment_change_count,
        final_alarm_count=final_alarm_count,
        expected_final_alarm_count=expected_final_alarm_count,
        final_assignment_count=final_assignment_count,
        expected_final_assignment_count=expected_final_assignment_count,
        final_pending_assignment_count=final_pending_assignment_count,
        open_occurrence_count=open_occurrence_count,
        expected_open_occurrence_count=expected_open_occurrence_count,
        open_episode_count=open_episode_count,
        expected_open_episode_count=expected_open_episode_count,
        durable_materialized_aligned=durable_materialized_aligned,
        functional_integrity_ok=functional_integrity_ok,
    )


def _run_c2_reschedule(
    *,
    scenario: BaselineScenario,
    volume_path: Path,
    source_path: Path,
    output_dir: Path,
) -> int:
    phase_a_runtime, phase_a_recorder, phase_a_execution = _execute_phase(
        scenario=scenario,
        volume_path=volume_path,
        source_path=source_path,
        phase_key='phase-a',
        phase_duration_seconds=float(scenario.c2_reschedule_phase_a_seconds),
    )
    phase_b_scenario = replace(
        scenario,
        c2_routing_delay_seconds=scenario.c2_reschedule_delay_seconds,
        c2_reschedule_delay_seconds=(),
        c2_reschedule_phase_a_seconds=0,
    )
    phase_b_runtime, phase_b_recorder, phase_b_execution = _execute_phase(
        scenario=phase_b_scenario,
        volume_path=volume_path,
        source_path=source_path,
        phase_key='phase-b',
        phase_duration_seconds=scenario.c2_phase_b_duration_seconds,
        alarm_configuration_revision=_RESCHEDULE_ALARM_REVISION,
        additional_revisions=(phase_a_runtime.revision,),
        occurrence_id_start=scenario.alarm_count,
        episode_id_start=scenario.priority_group_count,
    )

    persistence = phase_b_runtime.job.composition.durability.persistence
    head = persistence.read_head()
    records = persistence.read_durable_records()
    snapshots = persistence.list_snapshots()
    snapshot_alarm_count = sum(len(item.as_document()['alarms']) for item in snapshots)
    expected_snapshot_alarm_count = scenario.expected_snapshot_alarm_count(
        missing_source_columns=phase_b_runtime.source_loader.missing_source_columns
    )
    functional_pressure = _build_functional_pressure_metrics(
        scenario=scenario,
        source_loader=phase_a_runtime.source_loader,
        records=records,
        snapshots=snapshots,
    )
    recorder = _combine_recorders(
        scenario.iteration_period_seconds,
        phase_a_recorder,
        phase_b_recorder,
    )
    source_load_durations_ms = [
        *phase_a_runtime.source_loader.load_durations_ms,
        *phase_b_runtime.source_loader.load_durations_ms,
    ]
    source_merge_durations_ms = [
        *phase_a_runtime.source_loader.merge_durations_ms,
        *phase_b_runtime.source_loader.merge_durations_ms,
    ]
    durable_history_lookup = _combine_durable_history_lookup_metrics(
        phase_a_runtime.composition.build_durable_history_lookup_metrics(),
        phase_b_runtime.composition.build_durable_history_lookup_metrics(),
    )
    actual_duration_seconds = (
        phase_a_execution.duration_seconds + phase_b_execution.duration_seconds
    )
    report = recorder.build_report(
        test_id=scenario.test_id,
        alarm_count=scenario.alarm_count,
        planned_duration_seconds=scenario.duration_seconds,
        actual_duration_seconds=actual_duration_seconds,
        data_refresh_seconds=scenario.data_refresh_seconds,
        data_profile=scenario.data_profile,
        columns_per_alarm=scenario.columns_per_alarm,
        historical_series_per_alarm=scenario.historical_series_per_alarm,
        historical_window_minutes=scenario.historical_window_minutes,
        historical_step_seconds=scenario.historical_step_seconds,
        historical_points_per_series=scenario.historical_points_per_series,
        historical_value_count=phase_b_runtime.source_loader.historical_value_count,
        physical_partition_count=scenario.physical_partition_count,
        physical_partition_column_counts=(
            phase_b_runtime.source_loader.physical_partition_column_counts
        ),
        physical_partition_layout=scenario.physical_partition_layout,
        empty_physical_partition_count=(
            phase_b_runtime.source_loader.empty_physical_partition_count
        ),
        missing_source_column_count=phase_b_runtime.source_loader.missing_source_column_count,
        synthesized_null_column_count=(phase_b_runtime.source_loader.synthesized_null_column_count),
        source_view_count=phase_b_runtime.source_loader.view_count,
        source_column_count=phase_b_runtime.source_loader.column_count,
        source_row_count=phase_b_runtime.source_loader.row_count,
        source_frame_bytes=phase_b_runtime.source_loader.frame_bytes,
        source_numeric_value_count=phase_b_runtime.source_loader.numeric_value_count,
        latest_source_column_count=phase_b_runtime.source_loader.latest_column_count,
        historical_source_column_count=phase_b_runtime.source_loader.historical_column_count,
        historical_source_row_count=phase_b_runtime.source_loader.historical_row_count,
        source_load_durations_ms=source_load_durations_ms,
        source_merge_durations_ms=source_merge_durations_ms,
        journal_aligned=head.aligned,
        durable_record_count=len(records),
        snapshot_count=len(snapshots),
        snapshot_alarm_count=snapshot_alarm_count,
        expected_snapshot_alarm_count=expected_snapshot_alarm_count,
        source_load_count=(
            phase_a_runtime.source_loader.load_count + phase_b_runtime.source_loader.load_count
        ),
        durable_history_lookup=durable_history_lookup,
        functional_pressure=functional_pressure,
    )
    recorder.write(output_dir=output_dir, report=report)
    phase_runs = {
        'phase_a': {
            'run_id': phase_a_execution.run_id,
            'alarm_configuration_revision': phase_a_runtime.revision.alarm_configuration_revision,
            'planned_duration_seconds': float(scenario.c2_reschedule_phase_a_seconds),
            'actual_duration_seconds': phase_a_execution.duration_seconds,
            'stop_reason': phase_a_execution.stop_reason,
        },
        'phase_b': {
            'run_id': phase_b_execution.run_id,
            'alarm_configuration_revision': phase_b_runtime.revision.alarm_configuration_revision,
            'planned_duration_seconds': scenario.c2_phase_b_duration_seconds,
            'actual_duration_seconds': phase_b_execution.duration_seconds,
            'stop_reason': phase_b_execution.stop_reason,
        },
    }
    _write_metadata(
        output_dir=output_dir,
        scenario=scenario,
        run_id=phase_b_execution.run_id,
        stop_reason=(
            f'phase_a={phase_a_execution.stop_reason};phase_b={phase_b_execution.stop_reason}'
        ),
        source_view_count=phase_b_runtime.source_loader.view_count,
        source_column_count=phase_b_runtime.source_loader.column_count,
        source_row_count=phase_b_runtime.source_loader.row_count,
        source_frame_bytes=phase_b_runtime.source_loader.frame_bytes,
        source_numeric_value_count=phase_b_runtime.source_loader.numeric_value_count,
        latest_source_column_count=phase_b_runtime.source_loader.latest_column_count,
        historical_source_column_count=phase_b_runtime.source_loader.historical_column_count,
        historical_source_row_count=phase_b_runtime.source_loader.historical_row_count,
        historical_series_per_alarm=scenario.historical_series_per_alarm,
        historical_window_minutes=scenario.historical_window_minutes,
        historical_step_seconds=scenario.historical_step_seconds,
        historical_points_per_series=scenario.historical_points_per_series,
        historical_value_count=phase_b_runtime.source_loader.historical_value_count,
        physical_partition_count=scenario.physical_partition_count,
        physical_partition_column_counts=(
            phase_b_runtime.source_loader.physical_partition_column_counts
        ),
        physical_partition_layout=scenario.physical_partition_layout,
        empty_physical_partition_count=(
            phase_b_runtime.source_loader.empty_physical_partition_count
        ),
        missing_source_column_count=phase_b_runtime.source_loader.missing_source_column_count,
        synthesized_null_column_count=(phase_b_runtime.source_loader.synthesized_null_column_count),
        expected_snapshot_alarm_count=expected_snapshot_alarm_count,
        durable_history_lookup=durable_history_lookup,
        functional_pressure=functional_pressure,
        phase_runs=phase_runs,
    )
    (output_dir / 'phases.json').write_text(
        json.dumps(phase_runs, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    print(json.dumps(report.as_document(), indent=2, sort_keys=True))
    print(f'Artifacts: {output_dir}')
    return 0 if report.result == 'PASS' else 1


def _run_c2_remove_destinations(
    *,
    scenario: BaselineScenario,
    volume_path: Path,
    source_path: Path,
    output_dir: Path,
) -> int:
    phase_a_runtime, phase_a_recorder, phase_a_execution = _execute_phase(
        scenario=scenario,
        volume_path=volume_path,
        source_path=source_path,
        phase_key='phase-a',
        phase_duration_seconds=float(scenario.c2_remove_destinations_phase_a_seconds),
    )
    phase_b_scenario = replace(
        scenario,
        c2_remove_destinations_phase_a_seconds=0,
        c2_remove_destinations_target=True,
    )
    phase_b_runtime, phase_b_recorder, phase_b_execution = _execute_phase(
        scenario=phase_b_scenario,
        volume_path=volume_path,
        source_path=source_path,
        phase_key='phase-b',
        phase_duration_seconds=scenario.c2_phase_b_duration_seconds,
        alarm_configuration_revision=_RESCHEDULE_ALARM_REVISION,
        additional_revisions=(phase_a_runtime.revision,),
        occurrence_id_start=scenario.alarm_count,
        episode_id_start=scenario.priority_group_count,
    )

    persistence = phase_b_runtime.job.composition.durability.persistence
    head = persistence.read_head()
    records = persistence.read_durable_records()
    snapshots = persistence.list_snapshots()
    snapshot_alarm_count = sum(len(item.as_document()['alarms']) for item in snapshots)
    expected_snapshot_alarm_count = scenario.expected_snapshot_alarm_count(
        missing_source_columns=phase_b_runtime.source_loader.missing_source_columns
    )
    functional_pressure = _build_functional_pressure_metrics(
        scenario=scenario,
        source_loader=phase_a_runtime.source_loader,
        records=records,
        snapshots=snapshots,
    )
    recorder = _combine_recorders(
        scenario.iteration_period_seconds,
        phase_a_recorder,
        phase_b_recorder,
    )
    source_load_durations_ms = [
        *phase_a_runtime.source_loader.load_durations_ms,
        *phase_b_runtime.source_loader.load_durations_ms,
    ]
    source_merge_durations_ms = [
        *phase_a_runtime.source_loader.merge_durations_ms,
        *phase_b_runtime.source_loader.merge_durations_ms,
    ]
    durable_history_lookup = _combine_durable_history_lookup_metrics(
        phase_a_runtime.composition.build_durable_history_lookup_metrics(),
        phase_b_runtime.composition.build_durable_history_lookup_metrics(),
    )
    actual_duration_seconds = (
        phase_a_execution.duration_seconds + phase_b_execution.duration_seconds
    )
    report = recorder.build_report(
        test_id=scenario.test_id,
        alarm_count=scenario.alarm_count,
        planned_duration_seconds=scenario.duration_seconds,
        actual_duration_seconds=actual_duration_seconds,
        data_refresh_seconds=scenario.data_refresh_seconds,
        data_profile=scenario.data_profile,
        columns_per_alarm=scenario.columns_per_alarm,
        historical_series_per_alarm=scenario.historical_series_per_alarm,
        historical_window_minutes=scenario.historical_window_minutes,
        historical_step_seconds=scenario.historical_step_seconds,
        historical_points_per_series=scenario.historical_points_per_series,
        historical_value_count=phase_b_runtime.source_loader.historical_value_count,
        physical_partition_count=scenario.physical_partition_count,
        physical_partition_column_counts=phase_b_runtime.source_loader.physical_partition_column_counts,
        physical_partition_layout=scenario.physical_partition_layout,
        empty_physical_partition_count=phase_b_runtime.source_loader.empty_physical_partition_count,
        missing_source_column_count=phase_b_runtime.source_loader.missing_source_column_count,
        synthesized_null_column_count=phase_b_runtime.source_loader.synthesized_null_column_count,
        source_view_count=phase_b_runtime.source_loader.view_count,
        source_column_count=phase_b_runtime.source_loader.column_count,
        source_row_count=phase_b_runtime.source_loader.row_count,
        source_frame_bytes=phase_b_runtime.source_loader.frame_bytes,
        source_numeric_value_count=phase_b_runtime.source_loader.numeric_value_count,
        latest_source_column_count=phase_b_runtime.source_loader.latest_column_count,
        historical_source_column_count=phase_b_runtime.source_loader.historical_column_count,
        historical_source_row_count=phase_b_runtime.source_loader.historical_row_count,
        source_load_durations_ms=source_load_durations_ms,
        source_merge_durations_ms=source_merge_durations_ms,
        journal_aligned=head.aligned,
        durable_record_count=len(records),
        snapshot_count=len(snapshots),
        snapshot_alarm_count=snapshot_alarm_count,
        expected_snapshot_alarm_count=expected_snapshot_alarm_count,
        source_load_count=phase_a_runtime.source_loader.load_count
        + phase_b_runtime.source_loader.load_count,
        durable_history_lookup=durable_history_lookup,
        functional_pressure=functional_pressure,
    )
    recorder.write(output_dir=output_dir, report=report)
    phase_runs = {
        'phase_a': {
            'run_id': phase_a_execution.run_id,
            'alarm_configuration_revision': phase_a_runtime.revision.alarm_configuration_revision,
            'planned_duration_seconds': float(scenario.c2_remove_destinations_phase_a_seconds),
            'actual_duration_seconds': phase_a_execution.duration_seconds,
            'stop_reason': phase_a_execution.stop_reason,
        },
        'phase_b': {
            'run_id': phase_b_execution.run_id,
            'alarm_configuration_revision': phase_b_runtime.revision.alarm_configuration_revision,
            'planned_duration_seconds': scenario.c2_phase_b_duration_seconds,
            'actual_duration_seconds': phase_b_execution.duration_seconds,
            'stop_reason': phase_b_execution.stop_reason,
        },
    }
    _write_metadata(
        output_dir=output_dir,
        scenario=scenario,
        run_id=phase_b_execution.run_id,
        stop_reason=f'phase_a={phase_a_execution.stop_reason};phase_b={phase_b_execution.stop_reason}',
        source_view_count=phase_b_runtime.source_loader.view_count,
        source_column_count=phase_b_runtime.source_loader.column_count,
        source_row_count=phase_b_runtime.source_loader.row_count,
        source_frame_bytes=phase_b_runtime.source_loader.frame_bytes,
        source_numeric_value_count=phase_b_runtime.source_loader.numeric_value_count,
        latest_source_column_count=phase_b_runtime.source_loader.latest_column_count,
        historical_source_column_count=phase_b_runtime.source_loader.historical_column_count,
        historical_source_row_count=phase_b_runtime.source_loader.historical_row_count,
        historical_series_per_alarm=scenario.historical_series_per_alarm,
        historical_window_minutes=scenario.historical_window_minutes,
        historical_step_seconds=scenario.historical_step_seconds,
        historical_points_per_series=scenario.historical_points_per_series,
        historical_value_count=phase_b_runtime.source_loader.historical_value_count,
        physical_partition_count=scenario.physical_partition_count,
        physical_partition_column_counts=phase_b_runtime.source_loader.physical_partition_column_counts,
        physical_partition_layout=scenario.physical_partition_layout,
        empty_physical_partition_count=phase_b_runtime.source_loader.empty_physical_partition_count,
        missing_source_column_count=phase_b_runtime.source_loader.missing_source_column_count,
        synthesized_null_column_count=phase_b_runtime.source_loader.synthesized_null_column_count,
        expected_snapshot_alarm_count=expected_snapshot_alarm_count,
        durable_history_lookup=durable_history_lookup,
        functional_pressure=functional_pressure,
        phase_runs=phase_runs,
    )
    (output_dir / 'phases.json').write_text(
        json.dumps(phase_runs, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    print(json.dumps(report.as_document(), indent=2, sort_keys=True))
    print(f'Artifacts: {output_dir}')
    return 0 if report.result == 'PASS' else 1


def _run_sustained_deactivation_decisions(
    *,
    scenario: BaselineScenario,
    volume_path: Path,
    source_path: Path,
    output_dir: Path,
) -> int:
    phase_duration_seconds = scenario.deactivation_phase_duration_seconds
    phase_a_runtime, phase_a_recorder, phase_a_execution = _execute_phase(
        scenario=scenario,
        volume_path=volume_path,
        source_path=source_path,
        phase_key='phase-a-requests',
        phase_duration_seconds=phase_duration_seconds,
        deactivation_phase='requests',
    )
    phase_a_store = AtomicJsonStore(
        root_path=phase_a_runtime.composition.durability.persistence.paths.alarms_root
    )
    phase_a_state = phase_a_store.read('runtime/state/consumers/management.json') or {}

    phase_b_runtime, phase_b_recorder, phase_b_execution = _execute_phase(
        scenario=scenario,
        volume_path=volume_path,
        source_path=source_path,
        phase_key='phase-b-decisions',
        phase_duration_seconds=phase_duration_seconds,
        occurrence_id_start=scenario.alarm_count,
        episode_id_start=scenario.priority_group_count,
        deactivation_phase='decisions',
    )

    persistence = phase_b_runtime.job.composition.durability.persistence
    head = persistence.read_head()
    records = persistence.read_durable_records()
    snapshots = persistence.list_snapshots()
    snapshot_alarm_count = sum(len(item.as_document()['alarms']) for item in snapshots)
    expected_snapshot_alarm_count = scenario.expected_snapshot_alarm_count(
        missing_source_columns=phase_b_runtime.source_loader.missing_source_columns
    )
    deactivation_decision_pressure = _build_sustained_deactivation_decision_pressure_metrics(
        scenario=scenario,
        phase_a_runtime=phase_a_runtime,
        phase_b_runtime=phase_b_runtime,
        phase_a_state=phase_a_state,
        records=records,
        snapshots=snapshots,
    )
    recorder = _combine_recorders(
        scenario.iteration_period_seconds,
        phase_a_recorder,
        phase_b_recorder,
    )
    source_load_durations_ms = [
        *phase_a_runtime.source_loader.load_durations_ms,
        *phase_b_runtime.source_loader.load_durations_ms,
    ]
    source_merge_durations_ms = [
        *phase_a_runtime.source_loader.merge_durations_ms,
        *phase_b_runtime.source_loader.merge_durations_ms,
    ]
    durable_history_lookup = _combine_durable_history_lookup_metrics(
        phase_a_runtime.composition.build_durable_history_lookup_metrics(),
        phase_b_runtime.composition.build_durable_history_lookup_metrics(),
    )
    actual_duration_seconds = (
        phase_a_execution.duration_seconds + phase_b_execution.duration_seconds
    )
    report = recorder.build_report(
        test_id=scenario.test_id,
        alarm_count=scenario.alarm_count,
        planned_duration_seconds=scenario.duration_seconds,
        actual_duration_seconds=actual_duration_seconds,
        data_refresh_seconds=scenario.data_refresh_seconds,
        data_profile=scenario.data_profile,
        columns_per_alarm=scenario.columns_per_alarm,
        historical_series_per_alarm=scenario.historical_series_per_alarm,
        historical_window_minutes=scenario.historical_window_minutes,
        historical_step_seconds=scenario.historical_step_seconds,
        historical_points_per_series=scenario.historical_points_per_series,
        historical_value_count=phase_b_runtime.source_loader.historical_value_count,
        physical_partition_count=scenario.physical_partition_count,
        physical_partition_column_counts=(
            phase_b_runtime.source_loader.physical_partition_column_counts
        ),
        physical_partition_layout=scenario.physical_partition_layout,
        empty_physical_partition_count=(
            phase_b_runtime.source_loader.empty_physical_partition_count
        ),
        missing_source_column_count=phase_b_runtime.source_loader.missing_source_column_count,
        synthesized_null_column_count=(phase_b_runtime.source_loader.synthesized_null_column_count),
        source_view_count=phase_b_runtime.source_loader.view_count,
        source_column_count=phase_b_runtime.source_loader.column_count,
        source_row_count=phase_b_runtime.source_loader.row_count,
        source_frame_bytes=phase_b_runtime.source_loader.frame_bytes,
        source_numeric_value_count=phase_b_runtime.source_loader.numeric_value_count,
        latest_source_column_count=phase_b_runtime.source_loader.latest_column_count,
        historical_source_column_count=phase_b_runtime.source_loader.historical_column_count,
        historical_source_row_count=phase_b_runtime.source_loader.historical_row_count,
        source_load_durations_ms=source_load_durations_ms,
        source_merge_durations_ms=source_merge_durations_ms,
        journal_aligned=head.aligned,
        durable_record_count=len(records),
        snapshot_count=len(snapshots),
        snapshot_alarm_count=snapshot_alarm_count,
        expected_snapshot_alarm_count=expected_snapshot_alarm_count,
        source_load_count=(
            phase_a_runtime.source_loader.load_count + phase_b_runtime.source_loader.load_count
        ),
        durable_history_lookup=durable_history_lookup,
        deactivation_decision_pressure=deactivation_decision_pressure,
    )
    recorder.write(output_dir=output_dir, report=report)
    phase_runs = {
        'phase_a': {
            'run_id': phase_a_execution.run_id,
            'role': 'canonical-deactivation-request-setup',
            'planned_duration_seconds': phase_duration_seconds,
            'actual_duration_seconds': phase_a_execution.duration_seconds,
            'stop_reason': phase_a_execution.stop_reason,
            'pending_request_count': deactivation_decision_pressure.phase_a_pending_request_count,
        },
        'phase_b': {
            'run_id': phase_b_execution.run_id,
            'role': 'sustained-approved-decisions',
            'planned_duration_seconds': phase_duration_seconds,
            'actual_duration_seconds': phase_b_execution.duration_seconds,
            'stop_reason': phase_b_execution.stop_reason,
        },
    }
    _write_metadata(
        output_dir=output_dir,
        scenario=scenario,
        run_id=phase_b_execution.run_id,
        stop_reason=(
            f'phase_a={phase_a_execution.stop_reason};phase_b={phase_b_execution.stop_reason}'
        ),
        source_view_count=phase_b_runtime.source_loader.view_count,
        source_column_count=phase_b_runtime.source_loader.column_count,
        source_row_count=phase_b_runtime.source_loader.row_count,
        source_frame_bytes=phase_b_runtime.source_loader.frame_bytes,
        source_numeric_value_count=phase_b_runtime.source_loader.numeric_value_count,
        latest_source_column_count=phase_b_runtime.source_loader.latest_column_count,
        historical_source_column_count=phase_b_runtime.source_loader.historical_column_count,
        historical_source_row_count=phase_b_runtime.source_loader.historical_row_count,
        historical_series_per_alarm=scenario.historical_series_per_alarm,
        historical_window_minutes=scenario.historical_window_minutes,
        historical_step_seconds=scenario.historical_step_seconds,
        historical_points_per_series=scenario.historical_points_per_series,
        historical_value_count=phase_b_runtime.source_loader.historical_value_count,
        physical_partition_count=scenario.physical_partition_count,
        physical_partition_column_counts=(
            phase_b_runtime.source_loader.physical_partition_column_counts
        ),
        physical_partition_layout=scenario.physical_partition_layout,
        empty_physical_partition_count=(
            phase_b_runtime.source_loader.empty_physical_partition_count
        ),
        missing_source_column_count=phase_b_runtime.source_loader.missing_source_column_count,
        synthesized_null_column_count=(phase_b_runtime.source_loader.synthesized_null_column_count),
        expected_snapshot_alarm_count=expected_snapshot_alarm_count,
        durable_history_lookup=durable_history_lookup,
        functional_pressure=None,
        deactivation_decision_pressure=deactivation_decision_pressure,
        phase_runs=phase_runs,
    )
    (output_dir / 'phases.json').write_text(
        json.dumps(phase_runs, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    print(json.dumps(report.as_document(), indent=2, sort_keys=True))
    print(f'Artifacts: {output_dir}')
    return 0 if report.result == 'PASS' else 1


def _execute_phase(
    *,
    scenario: BaselineScenario,
    volume_path: Path,
    source_path: Path,
    phase_key: str,
    phase_duration_seconds: float,
    alarm_configuration_revision: str = 'PERF-AC-1',
    additional_revisions=(),
    occurrence_id_start: int = 0,
    episode_id_start: int = 0,
    deactivation_phase: str | None = None,
):
    runtime = build_baseline_runtime(
        scenario=scenario,
        volume_path=volume_path,
        source_path=source_path,
        alarm_configuration_revision=alarm_configuration_revision,
        additional_revisions=additional_revisions,
        occurrence_id_start=occurrence_id_start,
        episode_id_start=episode_id_start,
        deactivation_phase=deactivation_phase,
    )
    recorder = PerformanceRecorder(iteration_period_seconds=scenario.iteration_period_seconds)
    measured = MeasuredAlarmRuntimeJobComposition.wrap(runtime.job, recorder=recorder)
    shutdown_grace = min(10.0, max(2.0, phase_duration_seconds * 0.1))
    definition = JobDefinition(
        module_name='ada.processes.alarms_runtime.performance',
        service_name='alarms-runtime-performance',
        job_key=f'alarms-runtime-{scenario.test_id.lower()}-{phase_key}',
        sleep_seconds=scenario.iteration_period_seconds,
        iteration_timeout_seconds=min(30.0, max(2.0, phase_duration_seconds / 2)),
        execution_timeout_seconds=(
            phase_duration_seconds + shutdown_grace + scenario.iteration_period_seconds
        ),
        shutdown_grace_seconds=shutdown_grace,
        lease_timeout_seconds=30.0,
        lease_renew_seconds=10.0,
        lease_wait_seconds=0.0,
        resource_sample_seconds=1.0,
    )
    environ = {
        'ENVIRONMENT': 'local',
        'APPLICATION': 'ada-alarms-runtime-performance',
        'VOLUMEN_PATH': str(volume_path),
        'ATLANTICUS_OBSERVABILITY_FILE_LOGS': 'false',
    }
    execution = execute_alarm_runtime_job(
        definition=definition,
        composition=measured,
        argv=(),
        environ=environ,
    )
    return runtime, recorder, execution


def _combine_recorders(
    iteration_period_seconds: float,
    *recorders: PerformanceRecorder,
) -> PerformanceRecorder:
    combined = PerformanceRecorder(iteration_period_seconds=iteration_period_seconds)
    iteration_offset = 0
    for recorder in recorders:
        for index, sample in enumerate(recorder.samples, start=1):
            combined.samples.append(
                replace(
                    sample,
                    iteration=iteration_offset + index,
                    start_interval_ms=(None if index == 1 else sample.start_interval_ms),
                )
            )
        iteration_offset += len(recorder.samples)
        combined.recovery_ms += recorder.recovery_ms
        combined.drain_ms += recorder.drain_ms
    return combined


def _combine_durable_history_lookup_metrics(
    *metrics: DurableHistoryLookupMetrics,
) -> DurableHistoryLookupMetrics:
    modes = {item.mode for item in metrics}
    if len(modes) != 1:
        raise ValueError('durable history lookup modes must match across phases')
    return DurableHistoryLookupMetrics(
        mode=next(iter(modes)),
        cycle_count=sum(item.cycle_count for item in metrics),
        lookup_call_count=sum(item.lookup_call_count for item in metrics),
        lookup_total_ms=sum(item.lookup_total_ms for item in metrics),
        durable_record_scan_count=sum(item.durable_record_scan_count for item in metrics),
        durable_record_scan_total_ms=sum(item.durable_record_scan_total_ms for item in metrics),
        durable_record_entries_seen=sum(item.durable_record_entries_seen for item in metrics),
        index_build_count=sum(item.index_build_count for item in metrics),
        index_build_total_ms=sum(item.index_build_total_ms for item in metrics),
    )


def _required_f007_path(value: str | None, field_name: str) -> Path:
    if value is None or not value.strip():
        raise ValueError(f'{field_name} is required for a physical data profile')
    return Path(value).resolve()


# Persistimos evidencia del binding/prewarm sin volver a hashear los Parquet, evitando contaminar FIRST_TOUCH.
def _write_f007_physical_binding(
    *,
    output_dir: Path,
    bank: F007DatasetBank,
    source_loader,
    prewarm_before: CgroupIoCacheSnapshot | None,
    prewarm_after: CgroupIoCacheSnapshot | None,
    data_profile: str,
    fixed_as_of_utc: datetime | None,
) -> None:
    if prewarm_before is None or prewarm_after is None:
        raise RuntimeError('F-007 physical prewarm telemetry is missing')
    document = {
        'dataset_bank_id': bank.dataset_bank_id,
        'aggregate_sha256': bank.aggregate_sha256,
        'bank_sha256': bank.bank_sha256,
        'input_root': str(bank.input_root),
        'read_only': True,
        'profile': 'WARM_FIXED'
        if data_profile == _F007_PHYSICAL_WARM
        else 'F010_PHYSICAL_INTEGRATED',
        'data_profile': data_profile,
        'fixed_as_of_utc': None
        if fixed_as_of_utc is None
        else fixed_as_of_utc.isoformat().replace('+00:00', 'Z'),
        'physical_signal_pool_size': bank.physical_signal_pool_size,
        'prewarm_duration_ms': source_loader.prewarm_duration_ms,
        'prewarm_paths': list(source_loader.prewarm_paths),
        'prewarm_cgroup': {
            'memory_current_before': prewarm_before.memory_current,
            'memory_current_after': prewarm_after.memory_current,
            'memory_anon_after': prewarm_after.memory_anon,
            'memory_file_after': prewarm_after.memory_file,
            'memory_active_file_after': prewarm_after.memory_active_file,
            'memory_inactive_file_after': prewarm_after.memory_inactive_file,
            'io_read_bytes_delta': max(
                0, prewarm_after.io_read_bytes - prewarm_before.io_read_bytes
            ),
            'io_read_operations_delta': max(
                0, prewarm_after.io_read_operations - prewarm_before.io_read_operations
            ),
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / 'f007-physical-binding.json').write_text(
        json.dumps(document, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('--test-id', default='A-001')
    parser.add_argument('--alarm-count', type=int, default=100)
    parser.add_argument('--duration-seconds', type=float, default=120.0)
    parser.add_argument('--iteration-period-seconds', type=float, default=5.0)
    parser.add_argument('--data-refresh-seconds', type=int, default=10)
    parser.add_argument(
        '--data-profile',
        choices=(
            'shared-latest',
            'latest-narrow',
            'latest-wide',
            'latest-historical',
            'f007-physical-warm',
            'f010-physical-integrated',
        ),
        default='shared-latest',
    )
    parser.add_argument('--columns-per-alarm', type=int, default=1)
    parser.add_argument('--historical-series-per-alarm', type=int, default=0)
    parser.add_argument('--historical-window-minutes', type=int, default=0)
    parser.add_argument('--historical-step-seconds', type=int, default=0)
    # Estos argumentos son obligatorios sólo para f007-physical-warm y apuntan a los mounts Docker aceptados.
    parser.add_argument('--f007-dataset-root')
    parser.add_argument('--f007-manifest')
    parser.add_argument('--f007-conformance')
    parser.add_argument('--priority-group-size', type=int, default=0)
    parser.add_argument('--operational-churn-percent', type=int, default=0)
    parser.add_argument('--technical-hold-churn-percent', type=int, default=0)
    parser.add_argument('--technical-hold-expiry-percent', type=int, default=0)
    parser.add_argument('--technical-hold-expiry-stagger-seconds', type=int, default=0)
    parser.add_argument('--technical-hold-error-duration-seconds', type=int, default=0)
    parser.add_argument('--initial-error-activation-percent', type=int, default=0)
    parser.add_argument('--initial-error-hold-seconds', type=int, default=0)
    parser.add_argument('--initial-error-activation-stagger-seconds', type=int, default=0)
    parser.add_argument('--fixed-initial-error-percent', type=int, default=0)
    parser.add_argument('--c1-routing-destination-count', type=int, default=0)
    parser.add_argument(
        '--c2-routing-delay-seconds',
        type=_parse_int_tuple,
        default=(),
    )
    parser.add_argument(
        '--c2-reschedule-delay-seconds',
        type=_parse_int_tuple,
        default=(),
    )
    parser.add_argument('--c2-reschedule-phase-a-seconds', type=int, default=0)
    parser.add_argument('--c2-remove-destinations-phase-a-seconds', type=int, default=0)
    parser.add_argument('--c2-routing-adoption-at-seconds', type=int, default=0)
    parser.add_argument(
        '--c2-routing-adoption-target-delay-seconds',
        type=_parse_int_tuple,
        default=(),
    )
    parser.add_argument('--management-action-at-seconds', type=int, default=0)
    parser.add_argument('--management-action-count', type=int, default=0)
    parser.add_argument('--management-action-interval-seconds', type=int, default=0)
    parser.add_argument('--deactivation-decision-at-seconds', type=int, default=0)
    parser.add_argument('--deactivation-decision-count', type=int, default=0)
    parser.add_argument('--deactivation-decision-interval-seconds', type=int, default=0)
    parser.add_argument('--deactivation-request-delivery-at-seconds', type=int, default=0)
    parser.add_argument('--deactivation-target-removal-at-seconds', type=int, default=0)
    parser.add_argument('--deactivation-window-seconds', type=int, default=0)
    parser.add_argument('--parameter-adoption-at-seconds', type=int, default=0)
    parser.add_argument('--parameter-target-threshold', type=float, default=None)
    parser.add_argument('--disabled-adoption-at-seconds', type=int, default=0)
    parser.add_argument('--disabled-alarm-percent', type=int, default=0)
    parser.add_argument('--removed-adoption-at-seconds', type=int, default=0)
    parser.add_argument('--removed-alarm-percent', type=int, default=0)
    parser.add_argument('--structural-reset-adoption-at-seconds', type=int, default=0)
    parser.add_argument('--structural-reset-alarm-percent', type=int, default=0)
    parser.add_argument('--mixed-revision-adoption-at-seconds', type=int, default=0)
    parser.add_argument('--mixed-revision-target-threshold', type=float, default=None)
    parser.add_argument('--mixed-revision-disabled-alarm-percent', type=int, default=0)
    parser.add_argument('--mixed-revision-removed-alarm-percent', type=int, default=0)
    parser.add_argument(
        '--mixed-revision-structural-reset-alarm-percent',
        type=int,
        default=0,
    )
    parser.add_argument('--rejected-candidate-at-seconds', type=int, default=0)
    parser.add_argument('--source-unavailable-at-seconds', type=int, default=0)
    parser.add_argument('--invalid-candidate-at-seconds', type=int, default=0)
    parser.add_argument('--lease-loss-adoption-at-seconds', type=int, default=0)
    parser.add_argument('--cache-promotion-failure-at-seconds', type=int, default=0)
    parser.add_argument('--drain-under-workload-at-seconds', type=int, default=0)
    parser.add_argument('--soak-warmup-seconds', type=int, default=0)
    parser.add_argument('--soak-window-seconds', type=int, default=0)
    parser.add_argument('--shared-volume-path', default=None)
    parser.add_argument(
        '--e010-worker-role',
        choices=('owner-a', 'owner-b'),
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument('--e010-control-root', default=None, help=argparse.SUPPRESS)
    parser.add_argument('--e010-shared-run-root', default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        '--durable-history-lookup-mode',
        choices=('baseline', 'indexed'),
        default='baseline',
    )
    parser.add_argument('--initial-active-percent', type=int, default=100)
    parser.add_argument('--physical-partition-count', type=int, default=1)
    parser.add_argument(
        '--physical-partition-layout',
        choices=('balanced', 'skewed', 'mixed'),
        default='balanced',
    )
    parser.add_argument('--work-dir', required=True)
    parser.add_argument('--output-dir', required=True)
    return parser.parse_args()


def _parse_int_tuple(value: str) -> tuple[int, ...]:
    if not isinstance(value, str):
        raise argparse.ArgumentTypeError('routing delays must be comma-separated integers')
    stripped = value.strip()
    if not stripped:
        return ()
    try:
        return tuple(int(part.strip()) for part in stripped.split(','))
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            'routing delays must be comma-separated integers'
        ) from error


# Este adaptador añade la geometría durable al análisis temporal. La ventana de
# tendencias se calcula en metrics.py y aquí sólo reconstruimos identidad/cadena WAL.
def _build_temporal_soak_metrics(
    *,
    scenario: BaselineScenario,
    records,
    snapshots,
    samples: list[IterationSample] | tuple[IterationSample, ...],
    journal_aligned: bool,
    snapshot_alarm_count: int,
    expected_snapshot_alarm_count: int,
) -> TemporalSoakMetrics | None:
    if not scenario.has_temporal_soak:
        return None
    commit_ids: list[str] = []
    last_commit_by_group: dict[str, str] = {}
    chain_mismatches = 0
    for entry in records:
        commit = entry.record.commit
        commit_ids.append(commit.commit_id)
        expected_previous = last_commit_by_group.get(commit.priority_group)
        if commit.previous_commit_id != expected_previous:
            chain_mismatches += 1
        last_commit_by_group[commit.priority_group] = commit.commit_id
    duplicate_commit_ids = len(commit_ids) - len(set(commit_ids))
    return build_temporal_soak_metrics_from_samples(
        samples=samples,
        warmup_seconds=scenario.soak_warmup_seconds,
        window_seconds=scenario.soak_window_seconds,
        iteration_period_seconds=scenario.iteration_period_seconds,
        expected_window_count=scenario.soak_window_count,
        expected_samples_per_window=scenario.soak_samples_per_window,
        expected_iteration_count=scenario.soak_expected_iteration_count,
        durable_record_count=len(records),
        expected_durable_record_count=scenario.soak_expected_durable_record_count,
        duplicate_commit_id_count=duplicate_commit_ids,
        commit_chain_mismatch_count=chain_mismatches,
        journal_aligned=journal_aligned,
        snapshot_count=len(snapshots),
        expected_snapshot_count=scenario.priority_group_count,
        snapshot_alarm_count=snapshot_alarm_count,
        expected_snapshot_alarm_count=expected_snapshot_alarm_count,
    )


def _write_metadata(
    *,
    output_dir: Path,
    scenario: BaselineScenario,
    run_id: str,
    stop_reason: str,
    source_view_count: int,
    source_column_count: int,
    source_row_count: int,
    source_frame_bytes: int,
    source_numeric_value_count: int,
    latest_source_column_count: int,
    historical_source_column_count: int,
    historical_source_row_count: int,
    historical_series_per_alarm: int,
    historical_window_minutes: int,
    historical_step_seconds: int,
    historical_points_per_series: int,
    historical_value_count: int,
    physical_partition_count: int,
    physical_partition_column_counts: tuple[int, ...],
    physical_partition_layout: str,
    empty_physical_partition_count: int,
    missing_source_column_count: int,
    synthesized_null_column_count: int,
    expected_snapshot_alarm_count: int,
    durable_history_lookup: DurableHistoryLookupMetrics,
    functional_pressure: FunctionalPressureMetrics | None,
    management_pressure: ManagementPressureMetrics
    | SustainedManagementPressureMetrics
    | None = None,
    parameter_adoption_pressure: ParameterAdoptionPressureMetrics | None = None,
    c2_routing_adoption_pressure: C2RoutingAdoptionPressureMetrics | None = None,
    disabled_adoption_pressure: DisabledAdoptionPressureMetrics | None = None,
    removed_adoption_pressure: RemovedAdoptionPressureMetrics | None = None,
    structural_reset_adoption_pressure: StructuralResetAdoptionPressureMetrics | None = None,
    mixed_revision_adoption_pressure: MixedRevisionAdoptionPressureMetrics | None = None,
    rejected_target_pressure: RejectedTargetPressureMetrics | None = None,
    source_unavailable_pressure: SourceUnavailablePressureMetrics | None = None,
    invalid_source_candidate_pressure: InvalidSourceCandidatePressureMetrics | None = None,
    lease_loss_adoption_pressure: LeaseLossAdoptionPressureMetrics | None = None,
    cache_promotion_failure_pressure: CachePromotionFailurePressureMetrics | None = None,
    drain_under_workload_pressure: DrainUnderWorkloadPressureMetrics | None = None,
    temporal_soak: TemporalSoakMetrics | None = None,
    deactivation_decision_pressure: (
        DeactivationDecisionPressureMetrics
        | SustainedDeactivationDecisionPressureMetrics
        | InvertedDeactivationDecisionPressureMetrics
        | MixedDeactivationDecisionPressureMetrics
        | None
    ) = None,
    phase_runs: dict[str, dict[str, object]] | None = None,
    backend: str = 'local-filesystem',
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    document = {
        'test_id': scenario.test_id,
        'run_id': run_id,
        'recorded_at': datetime.now(UTC).isoformat().replace('+00:00', 'Z'),
        'host': socket.gethostname(),
        'stage_commit': _git_commit(),
        'stop_reason': stop_reason,
        'scenario': {
            'alarm_count': scenario.alarm_count,
            'duration_seconds': scenario.duration_seconds,
            'iteration_period_seconds': scenario.iteration_period_seconds,
            'data_refresh_seconds': scenario.data_refresh_seconds,
            'data_profile': scenario.data_profile,
            'columns_per_alarm': scenario.columns_per_alarm,
            'historical_series_per_alarm': historical_series_per_alarm,
            'historical_window_minutes': historical_window_minutes,
            'historical_step_seconds': historical_step_seconds,
            'historical_points_per_series': historical_points_per_series,
            'historical_value_count': historical_value_count,
            'physical_partition_count': physical_partition_count,
            'physical_partition_column_counts': list(physical_partition_column_counts),
            'physical_partition_layout': physical_partition_layout,
            'empty_physical_partition_count': empty_physical_partition_count,
            'missing_source_column_count': missing_source_column_count,
            'synthesized_null_column_count': synthesized_null_column_count,
            'expected_snapshot_alarm_count': expected_snapshot_alarm_count,
            'priority_group_size': scenario.priority_group_size,
            'priority_group_count': scenario.priority_group_count,
            'operational_churn_percent': scenario.operational_churn_percent,
            'technical_hold_churn_percent': scenario.technical_hold_churn_percent,
            'technical_hold_expiry_percent': scenario.technical_hold_expiry_percent,
            'technical_hold_expiry_stagger_seconds': scenario.technical_hold_expiry_stagger_seconds,
            'technical_hold_error_duration_seconds': scenario.technical_hold_error_duration_seconds,
            'initial_error_activation_percent': scenario.initial_error_activation_percent,
            'initial_error_hold_seconds': scenario.initial_error_hold_seconds,
            'initial_error_activation_stagger_seconds': scenario.initial_error_activation_stagger_seconds,
            'fixed_initial_error_percent': scenario.fixed_initial_error_percent,
            'c1_routing_destination_count': scenario.c1_routing_destination_count,
            'c2_routing_delay_seconds': list(scenario.c2_routing_delay_seconds),
            'c2_reschedule_delay_seconds': list(scenario.c2_reschedule_delay_seconds),
            'c2_reschedule_phase_a_seconds': scenario.c2_reschedule_phase_a_seconds,
            'c2_remove_destinations_phase_a_seconds': (
                scenario.c2_remove_destinations_phase_a_seconds
            ),
            'c2_routing_adoption_at_seconds': scenario.c2_routing_adoption_at_seconds,
            'c2_routing_adoption_target_delay_seconds': list(
                scenario.c2_routing_adoption_target_delay_seconds
            ),
            'management_action_at_seconds': scenario.management_action_at_seconds,
            'management_action_count': scenario.effective_management_action_count,
            'management_action_interval_seconds': scenario.management_action_interval_seconds,
            'management_arrival_mode': scenario.management_arrival_mode,
            'management_last_action_at_seconds': scenario.management_last_action_at_seconds,
            'deactivation_decision_at_seconds': scenario.deactivation_decision_at_seconds,
            'deactivation_decision_count': scenario.effective_deactivation_decision_count,
            'deactivation_decision_interval_seconds': (
                scenario.deactivation_decision_interval_seconds
            ),
            'deactivation_request_delivery_at_seconds': (
                scenario.deactivation_request_delivery_at_seconds
            ),
            'deactivation_decision_last_at_seconds': (
                scenario.deactivation_decision_last_at_seconds
            ),
            'deactivation_phase_duration_seconds': (scenario.deactivation_phase_duration_seconds),
            'deactivation_window_seconds': scenario.deactivation_window_seconds,
            'parameter_adoption_at_seconds': scenario.parameter_adoption_at_seconds,
            'parameter_target_threshold': scenario.parameter_target_threshold,
            'disabled_adoption_at_seconds': scenario.disabled_adoption_at_seconds,
            'disabled_alarm_percent': scenario.disabled_alarm_percent,
            'removed_adoption_at_seconds': scenario.removed_adoption_at_seconds,
            'removed_alarm_percent': scenario.removed_alarm_percent,
            'structural_reset_adoption_at_seconds': scenario.structural_reset_adoption_at_seconds,
            'structural_reset_alarm_percent': scenario.structural_reset_alarm_percent,
            'mixed_revision_adoption_at_seconds': scenario.mixed_revision_adoption_at_seconds,
            'mixed_revision_target_threshold': scenario.mixed_revision_target_threshold,
            'mixed_revision_disabled_alarm_percent': (
                scenario.mixed_revision_disabled_alarm_percent
            ),
            'mixed_revision_removed_alarm_percent': (scenario.mixed_revision_removed_alarm_percent),
            'mixed_revision_structural_reset_alarm_percent': (
                scenario.mixed_revision_structural_reset_alarm_percent
            ),
            'rejected_candidate_at_seconds': scenario.rejected_candidate_at_seconds,
            'source_unavailable_at_seconds': scenario.source_unavailable_at_seconds,
            'invalid_candidate_at_seconds': scenario.invalid_candidate_at_seconds,
            'lease_loss_adoption_at_seconds': scenario.lease_loss_adoption_at_seconds,
            'cache_promotion_failure_at_seconds': scenario.cache_promotion_failure_at_seconds,
            'drain_under_workload_at_seconds': scenario.drain_under_workload_at_seconds,
            'soak_warmup_seconds': scenario.soak_warmup_seconds,
            'soak_window_seconds': scenario.soak_window_seconds,
            'routing_criticality': scenario.routing_criticality,
            'durable_history_lookup_mode': scenario.durable_history_lookup_mode,
            'initial_active_percent': scenario.initial_active_percent,
            'source_view_count': source_view_count,
            'source_column_count': source_column_count,
            'source_row_count': source_row_count,
            'source_frame_bytes': source_frame_bytes,
            'source_numeric_value_count': source_numeric_value_count,
            'latest_source_column_count': latest_source_column_count,
            'historical_source_column_count': historical_source_column_count,
            'historical_source_row_count': historical_source_row_count,
            'backend': backend,
        },
        'durable_history_lookup': asdict(durable_history_lookup),
        'functional_pressure': None if functional_pressure is None else asdict(functional_pressure),
        'management_pressure': (
            None if management_pressure is None else asdict(management_pressure)
        ),
        'parameter_adoption_pressure': (
            None if parameter_adoption_pressure is None else asdict(parameter_adoption_pressure)
        ),
        'c2_routing_adoption_pressure': (
            None if c2_routing_adoption_pressure is None else asdict(c2_routing_adoption_pressure)
        ),
        'disabled_adoption_pressure': (
            None if disabled_adoption_pressure is None else asdict(disabled_adoption_pressure)
        ),
        'removed_adoption_pressure': (
            None if removed_adoption_pressure is None else asdict(removed_adoption_pressure)
        ),
        'structural_reset_adoption_pressure': (
            None
            if structural_reset_adoption_pressure is None
            else asdict(structural_reset_adoption_pressure)
        ),
        'mixed_revision_adoption_pressure': (
            None
            if mixed_revision_adoption_pressure is None
            else asdict(mixed_revision_adoption_pressure)
        ),
        'rejected_target_pressure': (
            None if rejected_target_pressure is None else asdict(rejected_target_pressure)
        ),
        'source_unavailable_pressure': (
            None if source_unavailable_pressure is None else asdict(source_unavailable_pressure)
        ),
        'invalid_source_candidate_pressure': (
            None
            if invalid_source_candidate_pressure is None
            else asdict(invalid_source_candidate_pressure)
        ),
        'lease_loss_adoption_pressure': (
            None if lease_loss_adoption_pressure is None else asdict(lease_loss_adoption_pressure)
        ),
        'cache_promotion_failure_pressure': (
            None
            if cache_promotion_failure_pressure is None
            else asdict(cache_promotion_failure_pressure)
        ),
        'drain_under_workload_pressure': (
            None if drain_under_workload_pressure is None else asdict(drain_under_workload_pressure)
        ),
        'temporal_soak': None if temporal_soak is None else asdict(temporal_soak),
        'deactivation_decision_pressure': (
            None
            if deactivation_decision_pressure is None
            else asdict(deactivation_decision_pressure)
        ),
        'phase_runs': phase_runs,
    }
    (output_dir / 'metadata.json').write_text(
        json.dumps(document, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            check=True,
            capture_output=True,
            text=True,
        )
    except OSError, subprocess.CalledProcessError:
        return None
    return result.stdout.strip() or None


def _build_sustained_management_pressure_metrics(
    *,
    scenario: BaselineScenario,
    runtime,
    records,
    snapshots=(),
) -> SustainedManagementPressureMetrics:
    source = runtime.input_source
    consumer = runtime.input_consumer
    expected_count = scenario.effective_management_action_count
    if not isinstance(source, SustainedManagementInputSource):
        return SustainedManagementPressureMetrics(
            action_at_seconds=scenario.management_action_at_seconds,
            action_count=expected_count,
            action_interval_seconds=scenario.management_action_interval_seconds,
            arrival_mode=scenario.management_arrival_mode,
            first_input_id='PERF-M-000001',
            last_input_id=f'PERF-M-{expected_count:06d}',
            input_receipt_count=0,
            expected_input_receipt_count=expected_count,
            effective_receipt_count=0,
            expected_effective_receipt_count=expected_count,
            management_effect_started_count=0,
            expected_management_effect_started_count=expected_count,
            management_effect_cleared_count=0,
            expected_management_effect_cleared_count=0,
            management_commit_count=0,
            deactivation_request_count=0,
            expected_deactivation_request_count=0,
            lost_input_count=expected_count,
            duplicate_receipt_count=0,
            unique_target_count=0,
            expected_unique_target_count=expected_count,
            consumer_cursor_byte_offset=None,
            expected_consumer_cursor_byte_offset=expected_count * 256,
            consumer_pending_count=-1,
            expected_consumer_pending_count=0,
            consumer_pending_high_water_count=0,
            max_batch_size=0,
            nonempty_batch_count=0,
            nonempty_batch_sizes=(),
            first_nonempty_batch_size=0,
            expected_first_nonempty_batch_size=(
                expected_count if scenario.has_burst_management_pressure else None
            ),
            fully_absorbed_in_first_eligible_iteration=(
                False if scenario.has_burst_management_pressure else None
            ),
            snapshot_management_effect_count=0,
            expected_snapshot_management_effect_count=expected_count,
            occurrence_identity_mismatch_count=expected_count,
            receipt_before_cursor_checked_count=0,
            receipt_before_cursor_advance_ok=False,
            input_to_receipt_p50_ms=0.0,
            input_to_receipt_p95_ms=0.0,
            input_to_receipt_p99_ms=0.0,
            input_to_receipt_max_ms=0.0,
            durable_record_count=len(records),
            functional_integrity_ok=False,
        )

    expected_input_ids = set(source.input_ids)
    expected_effect_ids = {f'PERF-ME-{input_id}' for input_id in expected_input_ids}
    receipts = []
    effects = []
    deactivation_requests = []
    management_commit_count = 0
    for entry in records:
        payload = entry.record.records
        matching_receipts = [
            item
            for item in payload.get('input_receipts', [])
            if item.get('input_kind') == 'MANAGEMENT' and item.get('input_id') in expected_input_ids
        ]
        if matching_receipts:
            management_commit_count += 1
            receipts.extend(matching_receipts)
        effects.extend(
            item
            for item in payload.get('management_effects', [])
            if item.get('effect_id') in expected_effect_ids
        )
        deactivation_requests.extend(payload.get('deactivation_requests', []))

    receipt_input_ids = [str(item.get('input_id')) for item in receipts]
    unique_receipt_ids = set(receipt_input_ids)
    lost_input_count = len(expected_input_ids - unique_receipt_ids)
    duplicate_receipt_count = len(receipt_input_ids) - len(unique_receipt_ids)
    effective_receipt_count = sum(item.get('outcome') == 'EFFECTIVE' for item in receipts)
    management_effect_started_count = sum(item.get('kind') == 'STARTED' for item in effects)
    management_effect_cleared_count = sum(item.get('kind') == 'CLEARED' for item in effects)

    store = AtomicJsonStore(root_path=runtime.composition.durability.persistence.paths.alarms_root)
    state = store.read('runtime/state/consumers/management.json') or {}
    management_state = state.get('management') if isinstance(state, dict) else None
    cursor_value = management_state.get('cursor') if isinstance(management_state, dict) else None
    pending_value = management_state.get('pending') if isinstance(management_state, dict) else None
    cursor_byte_offset = cursor_value.get('byte_offset') if isinstance(cursor_value, dict) else None
    consumer_pending_count = len(pending_value) if isinstance(pending_value, list) else -1

    final_alarms: dict[str, dict] = {}
    for snapshot in snapshots:
        final_alarms.update(snapshot.as_document()['alarms'])
    snapshot_management_effect_count = 0
    occurrence_identity_mismatch_count = 0
    for target_alarm_key, target_occurrence_id in source.target_occurrence_ids.items():
        alarm = final_alarms.get(target_alarm_key)
        if not isinstance(alarm, dict):
            occurrence_identity_mismatch_count += 1
            continue
        occurrence = alarm.get('occurrence')
        if (
            not isinstance(occurrence, dict)
            or occurrence.get('occurrence_id') != target_occurrence_id
        ):
            occurrence_identity_mismatch_count += 1
        if alarm.get('management_effect') is not None:
            snapshot_management_effect_count += 1

    latencies = list(consumer.sustained_management_input_to_receipt_ms.values())
    expected_cursor = source.expected_final_cursor.byte_offset
    batch_sizes = tuple(consumer.management_receipt_batch_sizes)
    first_nonempty_batch_size = batch_sizes[0] if batch_sizes else 0
    expected_first_nonempty_batch_size = (
        expected_count if scenario.has_burst_management_pressure else None
    )
    fully_absorbed_in_first_eligible_iteration = (
        first_nonempty_batch_size == expected_count
        if scenario.has_burst_management_pressure
        else None
    )
    receipt_before_cursor_advance_ok = bool(
        consumer.receipt_before_cursor_advance_ok
        and consumer.receipt_before_cursor_checked_count == expected_count
    )
    functional_integrity_ok = (
        len(receipts) == expected_count
        and effective_receipt_count == expected_count
        and management_effect_started_count == expected_count
        and management_effect_cleared_count == 0
        and len(deactivation_requests) == 0
        and lost_input_count == 0
        and duplicate_receipt_count == 0
        and len(source.target_occurrence_ids) == expected_count
        and cursor_byte_offset == expected_cursor
        and consumer_pending_count == 0
        and snapshot_management_effect_count == expected_count
        and occurrence_identity_mismatch_count == 0
        and receipt_before_cursor_advance_ok
        and len(latencies) == expected_count
        and all(value >= 0 for value in latencies)
        and (
            not scenario.has_burst_management_pressure
            or fully_absorbed_in_first_eligible_iteration is True
        )
    )
    return SustainedManagementPressureMetrics(
        action_at_seconds=scenario.management_action_at_seconds,
        action_count=expected_count,
        action_interval_seconds=scenario.management_action_interval_seconds,
        arrival_mode=scenario.management_arrival_mode,
        first_input_id='PERF-M-000001',
        last_input_id=f'PERF-M-{expected_count:06d}',
        input_receipt_count=len(receipts),
        expected_input_receipt_count=expected_count,
        effective_receipt_count=effective_receipt_count,
        expected_effective_receipt_count=expected_count,
        management_effect_started_count=management_effect_started_count,
        expected_management_effect_started_count=expected_count,
        management_effect_cleared_count=management_effect_cleared_count,
        expected_management_effect_cleared_count=0,
        management_commit_count=management_commit_count,
        deactivation_request_count=len(deactivation_requests),
        expected_deactivation_request_count=0,
        lost_input_count=lost_input_count,
        duplicate_receipt_count=duplicate_receipt_count,
        unique_target_count=len(source.target_occurrence_ids),
        expected_unique_target_count=expected_count,
        consumer_cursor_byte_offset=cursor_byte_offset,
        expected_consumer_cursor_byte_offset=expected_cursor,
        consumer_pending_count=consumer_pending_count,
        expected_consumer_pending_count=0,
        consumer_pending_high_water_count=consumer.management_pending_high_water_count,
        max_batch_size=max(batch_sizes, default=0),
        nonempty_batch_count=len(batch_sizes),
        nonempty_batch_sizes=batch_sizes,
        first_nonempty_batch_size=first_nonempty_batch_size,
        expected_first_nonempty_batch_size=expected_first_nonempty_batch_size,
        fully_absorbed_in_first_eligible_iteration=(fully_absorbed_in_first_eligible_iteration),
        snapshot_management_effect_count=snapshot_management_effect_count,
        expected_snapshot_management_effect_count=expected_count,
        occurrence_identity_mismatch_count=occurrence_identity_mismatch_count,
        receipt_before_cursor_checked_count=consumer.receipt_before_cursor_checked_count,
        receipt_before_cursor_advance_ok=receipt_before_cursor_advance_ok,
        input_to_receipt_p50_ms=_percentile_values(latencies, 50),
        input_to_receipt_p95_ms=_percentile_values(latencies, 95),
        input_to_receipt_p99_ms=_percentile_values(latencies, 99),
        input_to_receipt_max_ms=max(latencies, default=0.0),
        durable_record_count=len(records),
        functional_integrity_ok=functional_integrity_ok,
    )


def _percentile_values(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percentile / 100
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _build_deactivation_decision_pressure_metrics(
    *,
    scenario: BaselineScenario,
    runtime,
    records,
    snapshots=(),
) -> DeactivationDecisionPressureMetrics | None:
    if not scenario.has_deactivation_decision_pressure:
        return None
    source = runtime.input_source
    consumer = runtime.input_consumer
    if not isinstance(source, SingleDeactivationDecisionInputSource):
        raise RuntimeError('deactivation decision pressure requires its canonical input source')

    target_alarm_key = source.target_identity.canonical_key
    request_receipts = []
    decision_receipts = []
    deactivation_requests = []
    management_effects = []
    deactivation_effects = []
    target_group_durable_record_count = 0
    for entry in records:
        payload = entry.record.records
        request_receipts.extend(
            item
            for item in payload.get('input_receipts', [])
            if item.get('input_kind') == 'DEACTIVATION_REQUEST'
            and item.get('input_id') == source.management_input_id
        )
        decision_receipts.extend(
            item
            for item in payload.get('input_receipts', [])
            if item.get('input_kind') == 'DEACTIVATION_DECISION'
            and item.get('input_id') == source.decision_id
        )
        deactivation_requests.extend(
            item
            for item in payload.get('deactivation_requests', [])
            if item.get('request_id') == source.request_id
        )
        management_effects.extend(
            item
            for item in payload.get('management_effects', [])
            if item.get('alarm_key') == target_alarm_key
        )
        deactivation_effects.extend(
            item
            for item in payload.get('deactivation_effects', [])
            if item.get('alarm_key') == target_alarm_key
        )
        if entry.record.commit.priority_group == source.target_priority_group:
            target_group_durable_record_count += 1

    pending_approval_receipt_count = sum(
        item.get('outcome') == 'PENDING_APPROVAL' for item in request_receipts
    )
    applied_decision_receipt_count = sum(
        item.get('outcome') == 'APPLIED' for item in decision_receipts
    )
    approval_required_request_count = sum(
        item.get('approval_required') is True for item in deactivation_requests
    )
    management_effect_started_count = sum(
        item.get('kind') == 'STARTED' for item in management_effects
    )
    deactivation_effect_started_count = sum(
        item.get('kind') == 'STARTED' for item in deactivation_effects
    )
    deactivation_effect_cleared_count = sum(
        item.get('kind') == 'CLEARED' for item in deactivation_effects
    )

    store = AtomicJsonStore(root_path=runtime.composition.durability.persistence.paths.alarms_root)
    state = store.read('runtime/state/consumers/management.json') or {}
    management_state = state.get('management') if isinstance(state, dict) else None
    decision_state = state.get('decisions') if isinstance(state, dict) else None
    management_cursor = (
        management_state.get('cursor') if isinstance(management_state, dict) else None
    )
    decision_cursor = decision_state.get('cursor') if isinstance(decision_state, dict) else None
    management_pending = (
        management_state.get('pending') if isinstance(management_state, dict) else None
    )
    decision_pending = decision_state.get('pending') if isinstance(decision_state, dict) else None
    pending_request_ids = (
        state.get('pending_deactivation_request_ids') if isinstance(state, dict) else None
    )
    management_cursor_byte_offset = (
        management_cursor.get('byte_offset') if isinstance(management_cursor, dict) else None
    )
    decision_cursor_byte_offset = (
        decision_cursor.get('byte_offset') if isinstance(decision_cursor, dict) else None
    )
    management_pending_count = (
        len(management_pending) if isinstance(management_pending, list) else -1
    )
    decision_pending_count = len(decision_pending) if isinstance(decision_pending, list) else -1
    pending_request_count = (
        len(pending_request_ids) if isinstance(pending_request_ids, list) else -1
    )

    snapshot_management_effect_count = 0
    snapshot_deactivation_effect_count = 0
    final_occurrence_id = None
    snapshot_deactivation_effect = None
    for snapshot in snapshots:
        alarm = snapshot.as_document()['alarms'].get(target_alarm_key)
        if not isinstance(alarm, dict):
            continue
        occurrence = alarm.get('occurrence')
        if isinstance(occurrence, dict):
            final_occurrence_id = occurrence.get('occurrence_id')
        if alarm.get('management_effect') is not None:
            snapshot_management_effect_count += 1
        if isinstance(alarm.get('deactivation_effect'), dict):
            snapshot_deactivation_effect_count += 1
            snapshot_deactivation_effect = alarm['deactivation_effect']

    request_document = deactivation_requests[0] if len(deactivation_requests) == 1 else None
    effect_started = next(
        (item for item in deactivation_effects if item.get('kind') == 'STARTED'),
        None,
    )
    expected_requested_at = source.request_created_at.isoformat().replace('+00:00', 'Z')
    expected_decided_at = source.decision_decided_at.isoformat().replace('+00:00', 'Z')
    expected_effective_until = source.effective_until.isoformat().replace('+00:00', 'Z')
    request_occurrence_identity_mismatch_count = int(
        request_document is None
        or request_document.get('source_occurrence_id') != source.target_occurrence_id
        or request_document.get('source_management_input_id') != source.management_input_id
        or request_document.get('alarm_key') != target_alarm_key
        or request_document.get('requested_at') != expected_requested_at
        or request_document.get('effective_until') != expected_effective_until
    )
    final_occurrence_identity_mismatch_count = int(
        final_occurrence_id != source.target_occurrence_id
    )
    expected_management_effect_id = f'PERF-ME-{source.management_input_id}'
    expected_deactivation_effect_id = f'PERF-DE-{source.request_id}'
    effect_window_preserved_ok = bool(
        effect_started is not None
        and effect_started.get('effect_id') == expected_deactivation_effect_id
        and effect_started.get('effective_from') == expected_decided_at
        and effect_started.get('effective_until') == expected_effective_until
        and isinstance(snapshot_deactivation_effect, dict)
        and snapshot_deactivation_effect.get('effect_id') == expected_deactivation_effect_id
        and snapshot_deactivation_effect.get('effective_from') == expected_decided_at
        and snapshot_deactivation_effect.get('effective_until') == expected_effective_until
    )
    remaining_window_seconds = None
    if effect_started is not None:
        effective_from = _parse_report_timestamp(effect_started.get('effective_from'))
        effective_until = _parse_report_timestamp(effect_started.get('effective_until'))
        if effective_from is not None and effective_until is not None:
            remaining_window_seconds = (effective_until - effective_from).total_seconds()
    expected_remaining_window_seconds = float(
        scenario.deactivation_window_seconds
        - (scenario.deactivation_decision_at_seconds - scenario.management_action_at_seconds)
    )
    expected_total_durable_record_count = scenario.priority_group_count + 2
    request_before_decision_ok = bool(consumer.request_durable_before_decision_exposure)
    decision_input_to_receipt_ms = consumer.decision_input_to_receipt_ms

    functional_integrity_ok = (
        len(request_receipts) == 1
        and pending_approval_receipt_count == 1
        and len(deactivation_requests) == 1
        and approval_required_request_count == 1
        and len(decision_receipts) == 1
        and applied_decision_receipt_count == 1
        and management_effect_started_count == 1
        and all(
            item.get('effect_id') == expected_management_effect_id
            for item in management_effects
            if item.get('kind') == 'STARTED'
        )
        and deactivation_effect_started_count == 1
        and deactivation_effect_cleared_count == 0
        and management_cursor_byte_offset == source.management_cursor.byte_offset
        and decision_cursor_byte_offset == source.decision_cursor.byte_offset
        and management_pending_count == 0
        and decision_pending_count == 0
        and pending_request_count == 0
        and consumer.pending_request_high_water_count == 1
        and consumer.decision_pending_high_water_count == 0
        and snapshot_management_effect_count == 1
        and snapshot_deactivation_effect_count == 1
        and request_occurrence_identity_mismatch_count == 0
        and final_occurrence_identity_mismatch_count == 0
        and request_before_decision_ok
        and source.target_visible_while_pending
        and consumer.request_receipt_before_cursor_advance_ok
        and consumer.decision_receipt_before_cursor_advance_ok
        and effect_window_preserved_ok
        and remaining_window_seconds == expected_remaining_window_seconds
        and consumer.request_receipt_commit_id is not None
        and consumer.decision_receipt_commit_id is not None
        and decision_input_to_receipt_ms is not None
        and decision_input_to_receipt_ms >= 0
        and target_group_durable_record_count == 3
        and len(records) == expected_total_durable_record_count
    )
    return DeactivationDecisionPressureMetrics(
        request_at_seconds=scenario.management_action_at_seconds,
        decision_at_seconds=scenario.deactivation_decision_at_seconds,
        deactivation_window_seconds=scenario.deactivation_window_seconds,
        management_input_id=source.management_input_id,
        request_id=source.request_id,
        decision_id=source.decision_id,
        target_alarm_key=target_alarm_key,
        target_occurrence_id=source.target_occurrence_id or '',
        request_receipt_count=len(request_receipts),
        expected_request_receipt_count=1,
        pending_approval_receipt_count=pending_approval_receipt_count,
        expected_pending_approval_receipt_count=1,
        deactivation_request_count=len(deactivation_requests),
        expected_deactivation_request_count=1,
        approval_required_request_count=approval_required_request_count,
        expected_approval_required_request_count=1,
        decision_receipt_count=len(decision_receipts),
        expected_decision_receipt_count=1,
        applied_decision_receipt_count=applied_decision_receipt_count,
        expected_applied_decision_receipt_count=1,
        management_effect_started_count=management_effect_started_count,
        expected_management_effect_started_count=1,
        deactivation_effect_started_count=deactivation_effect_started_count,
        expected_deactivation_effect_started_count=1,
        deactivation_effect_cleared_count=deactivation_effect_cleared_count,
        expected_deactivation_effect_cleared_count=0,
        management_cursor_byte_offset=management_cursor_byte_offset,
        expected_management_cursor_byte_offset=source.management_cursor.byte_offset,
        decision_cursor_byte_offset=decision_cursor_byte_offset,
        expected_decision_cursor_byte_offset=source.decision_cursor.byte_offset,
        management_pending_count=management_pending_count,
        decision_pending_count=decision_pending_count,
        pending_request_count=pending_request_count,
        pending_request_high_water_count=consumer.pending_request_high_water_count,
        decision_pending_high_water_count=consumer.decision_pending_high_water_count,
        snapshot_management_effect_count=snapshot_management_effect_count,
        snapshot_deactivation_effect_count=snapshot_deactivation_effect_count,
        request_occurrence_identity_mismatch_count=request_occurrence_identity_mismatch_count,
        final_occurrence_identity_mismatch_count=final_occurrence_identity_mismatch_count,
        request_before_decision_ok=request_before_decision_ok,
        target_visible_while_pending_ok=source.target_visible_while_pending,
        request_receipt_before_management_cursor_ok=(
            consumer.request_receipt_before_cursor_advance_ok
        ),
        decision_receipt_before_decision_cursor_ok=(
            consumer.decision_receipt_before_cursor_advance_ok
        ),
        effect_window_preserved_ok=effect_window_preserved_ok,
        remaining_window_seconds=remaining_window_seconds,
        expected_remaining_window_seconds=expected_remaining_window_seconds,
        request_receipt_commit_id=consumer.request_receipt_commit_id,
        decision_receipt_commit_id=consumer.decision_receipt_commit_id,
        decision_input_to_receipt_ms=decision_input_to_receipt_ms,
        target_group_durable_record_count=target_group_durable_record_count,
        expected_target_group_durable_record_count=3,
        total_durable_record_count=len(records),
        expected_total_durable_record_count=expected_total_durable_record_count,
        functional_integrity_ok=functional_integrity_ok,
    )


def _build_inverted_deactivation_decision_pressure_metrics(
    *,
    scenario: BaselineScenario,
    runtime,
    records,
    snapshots=(),
) -> InvertedDeactivationDecisionPressureMetrics:
    source = runtime.input_source
    consumer = runtime.input_consumer
    if not isinstance(source, InvertedDeliveryDeactivationInputSource):
        raise RuntimeError('inverted deactivation metrics require inverted delivery source')

    expected_count = source.input_count
    expected_input_ids = set(source.input_ids)
    expected_request_ids = set(source.request_ids)
    expected_decision_ids = set(source.decision_ids)
    expected_management_effect_ids = {f'PERF-ME-{input_id}' for input_id in expected_input_ids}
    expected_deactivation_effect_ids = {
        f'PERF-DE-{request_id}' for request_id in expected_request_ids
    }

    request_receipts = []
    decision_receipts = []
    request_documents = []
    management_effects = []
    deactivation_effects = []
    decision_receipt_entries: dict[str, list[tuple[object, list[dict[str, object]]]]] = {}
    for entry in records:
        payload = entry.record.records
        entry_effects = [
            item
            for item in payload.get('deactivation_effects', [])
            if item.get('effect_id') in expected_deactivation_effect_ids
        ]
        for receipt in payload.get('input_receipts', []):
            input_kind = receipt.get('input_kind')
            input_id = receipt.get('input_id')
            if input_kind == 'DEACTIVATION_REQUEST' and input_id in expected_input_ids:
                request_receipts.append(receipt)
            elif input_kind == 'DEACTIVATION_DECISION' and input_id in expected_decision_ids:
                decision_receipts.append(receipt)
                decision_receipt_entries.setdefault(str(input_id), []).append(
                    (entry, entry_effects)
                )
        request_documents.extend(
            item
            for item in payload.get('deactivation_requests', [])
            if item.get('request_id') in expected_request_ids
        )
        management_effects.extend(
            item
            for item in payload.get('management_effects', [])
            if item.get('effect_id') in expected_management_effect_ids
        )
        deactivation_effects.extend(entry_effects)

    request_receipt_ids = [str(item.get('input_id')) for item in request_receipts]
    decision_receipt_ids = [str(item.get('input_id')) for item in decision_receipts]
    request_receipt_set = set(request_receipt_ids)
    decision_receipt_set = set(decision_receipt_ids)
    lost_request_input_count = len(expected_input_ids - request_receipt_set)
    duplicate_request_receipt_count = len(request_receipt_ids) - len(request_receipt_set)
    lost_decision_input_count = len(expected_decision_ids - decision_receipt_set)
    duplicate_decision_receipt_count = len(decision_receipt_ids) - len(decision_receipt_set)

    pending_approval_receipt_count = sum(
        item.get('outcome') == 'PENDING_APPROVAL' for item in request_receipts
    )
    applied_decision_receipt_count = sum(
        item.get('outcome') == 'APPLIED' for item in decision_receipts
    )
    management_effect_started_count = sum(
        item.get('kind') == 'STARTED' for item in management_effects
    )
    deactivation_effect_started_count = sum(
        item.get('kind') == 'STARTED' for item in deactivation_effects
    )
    deactivation_effect_cleared_count = sum(
        item.get('kind') == 'CLEARED' for item in deactivation_effects
    )

    requests_by_id: dict[str, list[dict[str, object]]] = {}
    for request in request_documents:
        requests_by_id.setdefault(str(request.get('request_id')), []).append(request)
    effects_by_id: dict[str, list[dict[str, object]]] = {}
    for effect in deactivation_effects:
        effects_by_id.setdefault(str(effect.get('effect_id')), []).append(effect)

    final_occurrences: dict[str, object] = {}
    final_alarms: dict[str, dict[str, object]] = {}
    for snapshot in snapshots:
        for alarm_key, alarm in snapshot.as_document()['alarms'].items():
            if not isinstance(alarm, dict):
                continue
            final_alarms[alarm_key] = alarm
            occurrence = alarm.get('occurrence')
            if isinstance(occurrence, dict):
                final_occurrences[alarm_key] = occurrence.get('occurrence_id')

    wrong_decision_request_correlation_count = 0
    request_occurrence_identity_mismatch_count = 0
    final_occurrence_identity_mismatch_count = 0
    effect_window_mismatch_count = 0
    remaining_windows: list[float] = []
    expected_target_keys: set[str] = set()
    expected_requested_at = source.request_created_at.isoformat().replace('+00:00', 'Z')
    expected_decided_at = source.decision_decided_at.isoformat().replace('+00:00', 'Z')
    expected_effective_until = source.effective_until.isoformat().replace('+00:00', 'Z')

    for index in range(expected_count):
        input_id = source.input_ids[index]
        request_id = source.request_ids[index]
        decision_id = source.decision_ids[index]
        expected_effect_id = f'PERF-DE-{request_id}'
        identity, _priority_group = source.target_for_input(index)
        alarm_key = identity.canonical_key
        expected_target_keys.add(alarm_key)
        expected_occurrence_id = source.target_occurrence_ids.get(alarm_key)

        request_candidates = requests_by_id.get(request_id, [])
        request = request_candidates[0] if len(request_candidates) == 1 else None
        if (
            request is None
            or request.get('source_management_input_id') != input_id
            or request.get('alarm_key') != alarm_key
            or request.get('source_occurrence_id') != expected_occurrence_id
            or request.get('requested_at') != expected_requested_at
            or request.get('effective_until') != expected_effective_until
            or request.get('approval_required') is not True
        ):
            request_occurrence_identity_mismatch_count += 1
        if final_occurrences.get(alarm_key) != expected_occurrence_id:
            final_occurrence_identity_mismatch_count += 1

        effect_candidates = [
            item
            for item in effects_by_id.get(expected_effect_id, [])
            if item.get('kind') == 'STARTED'
        ]
        effect = effect_candidates[0] if len(effect_candidates) == 1 else None
        if (
            effect is None
            or request is None
            or effect.get('alarm_key') != alarm_key
            or effect.get('kind') != 'STARTED'
            or effect.get('effective_from') != expected_decided_at
            or effect.get('effective_until') != request.get('effective_until')
        ):
            effect_window_mismatch_count += 1
        else:
            effective_from = _parse_report_timestamp(effect.get('effective_from'))
            effective_until = _parse_report_timestamp(effect.get('effective_until'))
            if effective_from is not None and effective_until is not None:
                remaining_windows.append((effective_until - effective_from).total_seconds())

        receipt_entries = decision_receipt_entries.get(decision_id, [])
        if len(receipt_entries) != 1:
            wrong_decision_request_correlation_count += 1
        else:
            _entry, entry_effects = receipt_entries[0]
            matching = [
                item
                for item in entry_effects
                if item.get('effect_id') == expected_effect_id
                and item.get('alarm_key') == alarm_key
            ]
            if len(matching) != 1:
                wrong_decision_request_correlation_count += 1

    snapshot_management_effect_count = sum(
        isinstance(final_alarms.get(alarm_key), dict)
        and final_alarms[alarm_key].get('management_effect') is not None
        for alarm_key in expected_target_keys
    )
    snapshot_deactivation_effect_count = sum(
        isinstance(final_alarms.get(alarm_key), dict)
        and isinstance(final_alarms[alarm_key].get('deactivation_effect'), dict)
        for alarm_key in expected_target_keys
    )

    store = AtomicJsonStore(root_path=runtime.composition.durability.persistence.paths.alarms_root)
    final_state = store.read('runtime/state/consumers/management.json') or {}
    final_management = final_state.get('management') if isinstance(final_state, dict) else None
    final_decisions = final_state.get('decisions') if isinstance(final_state, dict) else None
    final_management_cursor = (
        final_management.get('cursor') if isinstance(final_management, dict) else None
    )
    final_decision_cursor = (
        final_decisions.get('cursor') if isinstance(final_decisions, dict) else None
    )
    final_management_pending = (
        final_management.get('pending') if isinstance(final_management, dict) else None
    )
    final_decision_pending = (
        final_decisions.get('pending') if isinstance(final_decisions, dict) else None
    )
    final_pending_request_ids = (
        final_state.get('pending_deactivation_request_ids')
        if isinstance(final_state, dict)
        else None
    )
    final_management_cursor_byte_offset = (
        final_management_cursor.get('byte_offset')
        if isinstance(final_management_cursor, dict)
        else None
    )
    final_decision_cursor_byte_offset = (
        final_decision_cursor.get('byte_offset')
        if isinstance(final_decision_cursor, dict)
        else None
    )
    final_management_pending_count = (
        len(final_management_pending) if isinstance(final_management_pending, list) else -1
    )
    final_decision_pending_count = (
        len(final_decision_pending) if isinstance(final_decision_pending, list) else -1
    )
    final_pending_request_count = (
        len(final_pending_request_ids) if isinstance(final_pending_request_ids, list) else -1
    )

    decision_latencies = [
        (confirmed_at - source.decision_visible_monotonic_by_input_id[decision_id]) * 1000
        for decision_id, confirmed_at in (
            consumer.inverted_decision_receipt_confirmed_monotonic_by_input_id.items()
        )
        if decision_id in source.decision_visible_monotonic_by_input_id
    ]
    receipt_batches = tuple(
        size for size in consumer.inverted_decision_receipt_batch_sizes if size > 0
    )
    expected_cursor_byte_offset = expected_count * source.byte_length
    expected_durable_record_count = scenario.priority_group_count + expected_count * 2

    early_pending_state_ok = (
        consumer.inverted_early_decision_cursor_byte_offset == expected_cursor_byte_offset
        and consumer.inverted_early_decision_pending_count == expected_count
        and consumer.inverted_early_decision_receipt_count == 0
    )
    post_request_pending_state_ok = (
        consumer.inverted_post_request_management_cursor_byte_offset == expected_cursor_byte_offset
        and consumer.inverted_post_request_decision_pending_count == expected_count
        and consumer.inverted_post_request_pending_request_count == expected_count
        and consumer.inverted_post_request_decision_receipt_count == 0
    )
    final_replay_state_ok = (
        consumer.inverted_final_resolved_observed
        and final_management_cursor_byte_offset == expected_cursor_byte_offset
        and final_decision_cursor_byte_offset == expected_cursor_byte_offset
        and final_management_pending_count == 0
        and final_decision_pending_count == 0
        and final_pending_request_count == 0
    )

    functional_integrity_ok = (
        len(request_receipts) == expected_count
        and pending_approval_receipt_count == expected_count
        and len(request_documents) == expected_count
        and len(decision_receipts) == expected_count
        and applied_decision_receipt_count == expected_count
        and management_effect_started_count == expected_count
        and deactivation_effect_started_count == expected_count
        and deactivation_effect_cleared_count == 0
        and lost_request_input_count == 0
        and duplicate_request_receipt_count == 0
        and lost_decision_input_count == 0
        and duplicate_decision_receipt_count == 0
        and wrong_decision_request_correlation_count == 0
        and request_occurrence_identity_mismatch_count == 0
        and final_occurrence_identity_mismatch_count == 0
        and effect_window_mismatch_count == 0
        and snapshot_management_effect_count == expected_count
        and snapshot_deactivation_effect_count == expected_count
        and early_pending_state_ok
        and post_request_pending_state_ok
        and final_replay_state_ok
        and consumer.decision_pending_high_water_count == expected_count
        and consumer.pending_request_high_water_count == expected_count
        and sum(source.decision_read_batch_sizes) == expected_count
        and source.decision_read_at_count >= expected_count * 2
        and receipt_batches == (expected_count,)
        and len(decision_latencies) == expected_count
        and all(value >= 0 for value in decision_latencies)
        and len(records) == expected_durable_record_count
    )

    return InvertedDeactivationDecisionPressureMetrics(
        request_logical_at_seconds=scenario.management_action_at_seconds,
        decision_delivery_at_seconds=scenario.deactivation_decision_at_seconds,
        request_delivery_at_seconds=scenario.deactivation_request_delivery_at_seconds,
        input_count=expected_count,
        deactivation_window_seconds=scenario.deactivation_window_seconds,
        request_receipt_count=len(request_receipts),
        expected_request_receipt_count=expected_count,
        pending_approval_receipt_count=pending_approval_receipt_count,
        decision_receipt_count=len(decision_receipts),
        expected_decision_receipt_count=expected_count,
        applied_decision_receipt_count=applied_decision_receipt_count,
        management_effect_started_count=management_effect_started_count,
        deactivation_effect_started_count=deactivation_effect_started_count,
        deactivation_effect_cleared_count=deactivation_effect_cleared_count,
        lost_request_input_count=lost_request_input_count,
        duplicate_request_receipt_count=duplicate_request_receipt_count,
        lost_decision_input_count=lost_decision_input_count,
        duplicate_decision_receipt_count=duplicate_decision_receipt_count,
        wrong_decision_request_correlation_count=wrong_decision_request_correlation_count,
        request_occurrence_identity_mismatch_count=request_occurrence_identity_mismatch_count,
        final_occurrence_identity_mismatch_count=final_occurrence_identity_mismatch_count,
        effect_window_mismatch_count=effect_window_mismatch_count,
        early_decision_cursor_byte_offset=(consumer.inverted_early_decision_cursor_byte_offset),
        expected_cursor_byte_offset=expected_cursor_byte_offset,
        early_decision_pending_count=consumer.inverted_early_decision_pending_count,
        early_decision_receipt_count=consumer.inverted_early_decision_receipt_count,
        post_request_management_cursor_byte_offset=(
            consumer.inverted_post_request_management_cursor_byte_offset
        ),
        post_request_decision_pending_count=(consumer.inverted_post_request_decision_pending_count),
        post_request_pending_request_count=(consumer.inverted_post_request_pending_request_count),
        post_request_decision_receipt_count=(consumer.inverted_post_request_decision_receipt_count),
        final_management_cursor_byte_offset=final_management_cursor_byte_offset,
        final_decision_cursor_byte_offset=final_decision_cursor_byte_offset,
        final_management_pending_count=final_management_pending_count,
        final_decision_pending_count=final_decision_pending_count,
        final_pending_request_count=final_pending_request_count,
        decision_pending_high_water_count=consumer.decision_pending_high_water_count,
        pending_request_high_water_count=consumer.pending_request_high_water_count,
        decision_fresh_record_count=sum(source.decision_read_batch_sizes),
        decision_pending_read_count=source.decision_read_at_count,
        decision_receipt_nonempty_batch_sizes=receipt_batches,
        decision_delivery_to_receipt_p50_ms=_percentile_values(decision_latencies, 50),
        decision_delivery_to_receipt_p95_ms=_percentile_values(decision_latencies, 95),
        decision_delivery_to_receipt_p99_ms=_percentile_values(decision_latencies, 99),
        decision_delivery_to_receipt_max_ms=max(decision_latencies, default=0.0),
        remaining_window_min_seconds=min(remaining_windows, default=None),
        remaining_window_max_seconds=max(remaining_windows, default=None),
        snapshot_management_effect_count=snapshot_management_effect_count,
        snapshot_deactivation_effect_count=snapshot_deactivation_effect_count,
        durable_record_count=len(records),
        expected_durable_record_count=expected_durable_record_count,
        early_pending_state_ok=early_pending_state_ok,
        post_request_pending_state_ok=post_request_pending_state_ok,
        final_replay_state_ok=final_replay_state_ok,
        functional_integrity_ok=functional_integrity_ok,
    )


def _build_c2_routing_adoption_pressure_metrics(
    *,
    scenario: BaselineScenario,
    runtime,
    records,
    snapshots,
    samples,
) -> C2RoutingAdoptionPressureMetrics | None:
    if not scenario.has_c2_routing_adoption_pressure:
        return None
    target_revision = runtime.target_revision
    if target_revision is None:
        raise RuntimeError('C2 routing adoption runtime requires a target revision')
    plan = plan_configuration_adoption(runtime.revision, target_revision)
    disposition_counts = {item: 0 for item in ConfigurationAdoptionDisposition}
    for change in plan.changes:
        disposition_counts[change.disposition] += 1

    adoption_samples = [
        sample
        for sample in samples
        if sample.revision_origin == 'source_candidate' and sample.adoption_outcome == 'adopted'
    ]
    adoption_sample = adoption_samples[0] if len(adoption_samples) == 1 else None
    post_adoption_cache_current_iteration_count = 0
    if adoption_sample is not None:
        post_adoption_cache_current_iteration_count = sum(
            sample.iteration > adoption_sample.iteration
            and sample.revision_origin == 'cache_current'
            and sample.adoption_outcome == 'not_required'
            for sample in samples
        )

    cache_bundle = runtime.job.revision_resolver.cache.load_effective()
    effective_cache_revision = (
        None if cache_bundle is None else cache_bundle.manifest.alarm_configuration_revision
    )
    source_revision = runtime.revision.alarm_configuration_revision
    target_revision_key = target_revision.alarm_configuration_revision
    source_revision_durable_record_count = sum(
        entry.record.commit.alarm_configuration_revision == source_revision for entry in records
    )
    target_revision_durable_record_count = sum(
        entry.record.commit.alarm_configuration_revision == target_revision_key for entry in records
    )

    occurrence_ids: dict[str, str] = {}
    adoption_commit_count = 0
    adoption_assigned_count = 0
    adoption_rescheduled_count = 0
    occurrence_identity_mismatch_count = 0
    for entry in records:
        payload = entry.record.records
        for change in payload.get('occurrence_changes', []):
            if change.get('kind') == 'STARTED':
                occurrence_ids[str(change.get('alarm_key'))] = str(change.get('occurrence_id'))
        assignment_changes = payload.get('assignment_changes', [])
        has_rescheduled = any(change.get('kind') == 'RESCHEDULED' for change in assignment_changes)
        if (
            entry.record.commit.alarm_configuration_revision == target_revision_key
            and has_rescheduled
        ):
            adoption_commit_count += 1
            adoption_assigned_count += sum(
                change.get('kind') == 'ASSIGNED' for change in assignment_changes
            )
            adoption_rescheduled_count += sum(
                change.get('kind') == 'RESCHEDULED' for change in assignment_changes
            )
        for change in assignment_changes:
            alarm_key = str(change.get('alarm_key'))
            expected_occurrence_id = occurrence_ids.get(alarm_key)
            if (
                expected_occurrence_id is not None
                and change.get('occurrence_id') != expected_occurrence_id
            ):
                occurrence_identity_mismatch_count += 1

    source_state_basis_snapshot_count = 0
    target_state_basis_snapshot_count = 0
    final_assignment_count = 0
    final_pending_assignment_count = 0
    open_occurrence_count = 0
    for snapshot in snapshots:
        document = snapshot.as_document()
        basis = document.get('state_basis')
        if isinstance(basis, dict):
            revision = basis.get('alarm_configuration_revision')
            if revision == source_revision:
                source_state_basis_snapshot_count += 1
            if revision == target_revision_key:
                target_state_basis_snapshot_count += 1
        for alarm in document['alarms'].values():
            occurrence = alarm.get('occurrence')
            if occurrence is None:
                continue
            open_occurrence_count += 1
            final_assignment_count += len(occurrence.get('assignments', []))
            final_pending_assignment_count += len(occurrence.get('pending_assignments', []))

    source_delays = scenario.c2_routing_delay_seconds
    target_delays = scenario.c2_routing_adoption_target_delay_seconds
    adoption_at = scenario.c2_routing_adoption_at_seconds
    expected_promoted_destinations = sum(
        source_delay > adoption_at and target_delay <= adoption_at
        for source_delay, target_delay in zip(source_delays, target_delays, strict=True)
    )
    expected_rescheduled_destinations = sum(
        source_delay > adoption_at and target_delay > adoption_at and source_delay != target_delay
        for source_delay, target_delay in zip(source_delays, target_delays, strict=True)
    )
    source_wave_count = sum(delay < adoption_at for delay in source_delays)
    target_wave_count = sum(delay > adoption_at for delay in target_delays)
    expected_adoption_commit_count = scenario.priority_group_count
    expected_adoption_assigned_count = scenario.alarm_count * expected_promoted_destinations
    expected_adoption_rescheduled_count = scenario.alarm_count * expected_rescheduled_destinations
    expected_source_revision_durable_record_count = scenario.priority_group_count * (
        1 + source_wave_count
    )
    expected_target_revision_durable_record_count = scenario.priority_group_count * (
        1 + target_wave_count
    )
    expected_durable_record_count = (
        expected_source_revision_durable_record_count
        + expected_target_revision_durable_record_count
    )
    expected_final_assignment_count = scenario.alarm_count * (len(target_delays) + 1)
    functional_integrity_ok = (
        plan.is_adoptable
        and len(plan.changes) == scenario.alarm_count
        and disposition_counts[ConfigurationAdoptionDisposition.COMPATIBLE] == scenario.alarm_count
        and disposition_counts[ConfigurationAdoptionDisposition.UNCHANGED] == 0
        and disposition_counts[ConfigurationAdoptionDisposition.STRUCTURAL_RESET] == 0
        and disposition_counts[ConfigurationAdoptionDisposition.DISABLED] == 0
        and disposition_counts[ConfigurationAdoptionDisposition.REMOVED] == 0
        and disposition_counts[ConfigurationAdoptionDisposition.REJECTED] == 0
        and len(target_revision.session.identities) == scenario.alarm_count
        and effective_cache_revision == target_revision_key
        and len(adoption_samples) == 1
        and adoption_sample is not None
        and not adoption_sample.cycle_executed
        and post_adoption_cache_current_iteration_count > 0
        and adoption_commit_count == expected_adoption_commit_count
        and adoption_assigned_count == expected_adoption_assigned_count
        and adoption_rescheduled_count == expected_adoption_rescheduled_count
        and source_revision_durable_record_count == expected_source_revision_durable_record_count
        and target_revision_durable_record_count == expected_target_revision_durable_record_count
        and len(records) == expected_durable_record_count
        and source_state_basis_snapshot_count == 0
        and target_state_basis_snapshot_count == scenario.priority_group_count
        and final_assignment_count == expected_final_assignment_count
        and final_pending_assignment_count == 0
        and open_occurrence_count == scenario.alarm_count
        and occurrence_identity_mismatch_count == 0
    )
    return C2RoutingAdoptionPressureMetrics(
        adoption_at_seconds=adoption_at,
        source_revision=source_revision,
        target_revision=target_revision_key,
        source_delay_seconds=source_delays,
        target_delay_seconds=target_delays,
        plan_change_count=len(plan.changes),
        compatible_change_count=disposition_counts[ConfigurationAdoptionDisposition.COMPATIBLE],
        unchanged_change_count=disposition_counts[ConfigurationAdoptionDisposition.UNCHANGED],
        structural_reset_change_count=disposition_counts[
            ConfigurationAdoptionDisposition.STRUCTURAL_RESET
        ],
        disabled_change_count=disposition_counts[ConfigurationAdoptionDisposition.DISABLED],
        removed_change_count=disposition_counts[ConfigurationAdoptionDisposition.REMOVED],
        rejected_change_count=disposition_counts[ConfigurationAdoptionDisposition.REJECTED],
        target_runtime_alarm_count=len(target_revision.session.identities),
        expected_target_runtime_alarm_count=scenario.alarm_count,
        effective_cache_revision=effective_cache_revision,
        adoption_iteration_count=len(adoption_samples),
        adoption_iteration=None if adoption_sample is None else adoption_sample.iteration,
        adoption_iteration_ms=None if adoption_sample is None else adoption_sample.duration_ms,
        adoption_iteration_cpu_percent=(
            None if adoption_sample is None else adoption_sample.cpu_percent
        ),
        adoption_cycle_executed=(
            False if adoption_sample is None else adoption_sample.cycle_executed
        ),
        post_adoption_cache_current_iteration_count=post_adoption_cache_current_iteration_count,
        adoption_commit_count=adoption_commit_count,
        expected_adoption_commit_count=expected_adoption_commit_count,
        adoption_assigned_count=adoption_assigned_count,
        expected_adoption_assigned_count=expected_adoption_assigned_count,
        adoption_rescheduled_count=adoption_rescheduled_count,
        expected_adoption_rescheduled_count=expected_adoption_rescheduled_count,
        source_revision_durable_record_count=source_revision_durable_record_count,
        expected_source_revision_durable_record_count=expected_source_revision_durable_record_count,
        target_revision_durable_record_count=target_revision_durable_record_count,
        expected_target_revision_durable_record_count=expected_target_revision_durable_record_count,
        durable_record_count=len(records),
        expected_durable_record_count=expected_durable_record_count,
        source_state_basis_snapshot_count=source_state_basis_snapshot_count,
        target_state_basis_snapshot_count=target_state_basis_snapshot_count,
        final_assignment_count=final_assignment_count,
        expected_final_assignment_count=expected_final_assignment_count,
        final_pending_assignment_count=final_pending_assignment_count,
        open_occurrence_count=open_occurrence_count,
        expected_open_occurrence_count=scenario.alarm_count,
        occurrence_identity_mismatch_count=occurrence_identity_mismatch_count,
        functional_integrity_ok=functional_integrity_ok,
    )


def _build_disabled_adoption_pressure_metrics(
    *,
    scenario: BaselineScenario,
    runtime,
    records,
    snapshots,
    samples,
) -> DisabledAdoptionPressureMetrics | None:
    if not scenario.has_disabled_adoption_pressure:
        return None
    target_revision = runtime.target_revision
    if target_revision is None:
        raise RuntimeError('disabled adoption runtime requires a target revision')
    plan = plan_configuration_adoption(runtime.revision, target_revision)
    disposition_counts = {item: 0 for item in ConfigurationAdoptionDisposition}
    for change in plan.changes:
        disposition_counts[change.disposition] += 1

    adoption_samples = [
        sample
        for sample in samples
        if sample.revision_origin == 'source_candidate' and sample.adoption_outcome == 'adopted'
    ]
    adoption_sample = adoption_samples[0] if len(adoption_samples) == 1 else None
    post_adoption_cache_current_iteration_count = 0
    if adoption_sample is not None:
        post_adoption_cache_current_iteration_count = sum(
            sample.iteration > adoption_sample.iteration
            and sample.revision_origin == 'cache_current'
            and sample.adoption_outcome == 'not_required'
            for sample in samples
        )

    cache_bundle = runtime.job.revision_resolver.cache.load_effective()
    effective_cache_revision = (
        None if cache_bundle is None else cache_bundle.manifest.alarm_configuration_revision
    )
    source_revision = runtime.revision.alarm_configuration_revision
    target_revision_key = target_revision.alarm_configuration_revision
    source_revision_durable_record_count = sum(
        entry.record.commit.alarm_configuration_revision == source_revision for entry in records
    )
    target_revision_durable_record_count = sum(
        entry.record.commit.alarm_configuration_revision == target_revision_key for entry in records
    )

    occurrence_ids: dict[str, str] = {}
    adoption_commit_count = 0
    configuration_disabled_occurrence_count = 0
    configuration_removed_occurrence_count = 0
    occurrence_identity_mismatch_count = 0
    for entry in records:
        payload = entry.record.records
        has_disabled_closure = False
        for change in payload.get('occurrence_changes', []):
            alarm_key = str(change.get('alarm_key'))
            kind = change.get('kind')
            if kind == 'STARTED':
                occurrence_ids[alarm_key] = str(change.get('occurrence_id'))
                continue
            if kind != 'CLOSED':
                continue
            closure_reason = str(change.get('closure_reason')).strip().lower()
            if closure_reason == 'configuration_disabled':
                configuration_disabled_occurrence_count += 1
                has_disabled_closure = True
            if closure_reason == 'configuration_removed':
                configuration_removed_occurrence_count += 1
            expected_occurrence_id = occurrence_ids.get(alarm_key)
            if (
                expected_occurrence_id is not None
                and change.get('occurrence_id') != expected_occurrence_id
            ):
                occurrence_identity_mismatch_count += 1
        if (
            entry.record.commit.alarm_configuration_revision == target_revision_key
            and has_disabled_closure
        ):
            adoption_commit_count += 1

    source_state_basis_snapshot_count = 0
    target_state_basis_snapshot_count = 0
    final_alarm_count = 0
    final_assignment_count = 0
    open_occurrence_count = 0
    open_episode_count = 0
    disabled_target_present_count = 0
    disabled_alarm_keys = {
        identity.canonical_key
        for identity in target_revision.defined_alarm_identities
        if identity not in target_revision.session.identities
    }
    for snapshot in snapshots:
        document = snapshot.as_document()
        basis = document.get('state_basis')
        if isinstance(basis, dict):
            revision = basis.get('alarm_configuration_revision')
            if revision == source_revision:
                source_state_basis_snapshot_count += 1
            if revision == target_revision_key:
                target_state_basis_snapshot_count += 1
        if document.get('episode') is not None:
            open_episode_count += 1
        alarms = document['alarms']
        final_alarm_count += len(alarms)
        disabled_target_present_count += sum(key in disabled_alarm_keys for key in alarms)
        for alarm in alarms.values():
            occurrence = alarm.get('occurrence')
            if occurrence is None:
                continue
            open_occurrence_count += 1
            final_assignment_count += len(occurrence.get('assignments', []))

    expected_disabled_change_count = scenario.disabled_alarm_count
    expected_target_runtime_alarm_count = scenario.alarm_count - expected_disabled_change_count
    expected_adoption_commit_count = scenario.priority_group_count
    expected_source_revision_durable_record_count = scenario.priority_group_count
    expected_target_revision_durable_record_count = scenario.priority_group_count
    expected_durable_record_count = (
        expected_source_revision_durable_record_count
        + expected_target_revision_durable_record_count
    )
    functional_integrity_ok = (
        plan.is_adoptable
        and len(plan.changes) == scenario.alarm_count
        and disposition_counts[ConfigurationAdoptionDisposition.COMPATIBLE] == 0
        and disposition_counts[ConfigurationAdoptionDisposition.UNCHANGED]
        == scenario.alarm_count - expected_disabled_change_count
        and disposition_counts[ConfigurationAdoptionDisposition.STRUCTURAL_RESET] == 0
        and disposition_counts[ConfigurationAdoptionDisposition.DISABLED]
        == expected_disabled_change_count
        and disposition_counts[ConfigurationAdoptionDisposition.REMOVED] == 0
        and disposition_counts[ConfigurationAdoptionDisposition.REJECTED] == 0
        and len(target_revision.defined_alarm_identities) == scenario.alarm_count
        and len(target_revision.session.identities) == expected_target_runtime_alarm_count
        and effective_cache_revision == target_revision_key
        and len(adoption_samples) == 1
        and adoption_sample is not None
        and not adoption_sample.cycle_executed
        and post_adoption_cache_current_iteration_count > 0
        and adoption_commit_count == expected_adoption_commit_count
        and configuration_disabled_occurrence_count == expected_disabled_change_count
        and configuration_removed_occurrence_count == 0
        and occurrence_identity_mismatch_count == 0
        and source_revision_durable_record_count == expected_source_revision_durable_record_count
        and target_revision_durable_record_count == expected_target_revision_durable_record_count
        and len(records) == expected_durable_record_count
        and source_state_basis_snapshot_count == 0
        and target_state_basis_snapshot_count == scenario.priority_group_count
        and final_alarm_count == expected_target_runtime_alarm_count
        and final_assignment_count == expected_target_runtime_alarm_count
        and open_occurrence_count == expected_target_runtime_alarm_count
        and open_episode_count == scenario.priority_group_count
        and disabled_target_present_count == 0
    )
    return DisabledAdoptionPressureMetrics(
        adoption_at_seconds=scenario.disabled_adoption_at_seconds,
        disabled_alarm_percent=scenario.disabled_alarm_percent,
        source_revision=source_revision,
        target_revision=target_revision_key,
        plan_change_count=len(plan.changes),
        compatible_change_count=disposition_counts[ConfigurationAdoptionDisposition.COMPATIBLE],
        unchanged_change_count=disposition_counts[ConfigurationAdoptionDisposition.UNCHANGED],
        structural_reset_change_count=disposition_counts[
            ConfigurationAdoptionDisposition.STRUCTURAL_RESET
        ],
        disabled_change_count=disposition_counts[ConfigurationAdoptionDisposition.DISABLED],
        expected_disabled_change_count=expected_disabled_change_count,
        removed_change_count=disposition_counts[ConfigurationAdoptionDisposition.REMOVED],
        rejected_change_count=disposition_counts[ConfigurationAdoptionDisposition.REJECTED],
        target_defined_alarm_count=len(target_revision.defined_alarm_identities),
        expected_target_defined_alarm_count=scenario.alarm_count,
        target_runtime_alarm_count=len(target_revision.session.identities),
        expected_target_runtime_alarm_count=expected_target_runtime_alarm_count,
        effective_cache_revision=effective_cache_revision,
        adoption_iteration_count=len(adoption_samples),
        adoption_iteration=None if adoption_sample is None else adoption_sample.iteration,
        adoption_iteration_ms=None if adoption_sample is None else adoption_sample.duration_ms,
        adoption_iteration_cpu_percent=(
            None if adoption_sample is None else adoption_sample.cpu_percent
        ),
        adoption_cycle_executed=(
            False if adoption_sample is None else adoption_sample.cycle_executed
        ),
        post_adoption_cache_current_iteration_count=post_adoption_cache_current_iteration_count,
        adoption_commit_count=adoption_commit_count,
        expected_adoption_commit_count=expected_adoption_commit_count,
        configuration_disabled_occurrence_count=configuration_disabled_occurrence_count,
        expected_configuration_disabled_occurrence_count=expected_disabled_change_count,
        configuration_removed_occurrence_count=configuration_removed_occurrence_count,
        occurrence_identity_mismatch_count=occurrence_identity_mismatch_count,
        source_revision_durable_record_count=source_revision_durable_record_count,
        expected_source_revision_durable_record_count=expected_source_revision_durable_record_count,
        target_revision_durable_record_count=target_revision_durable_record_count,
        expected_target_revision_durable_record_count=expected_target_revision_durable_record_count,
        durable_record_count=len(records),
        expected_durable_record_count=expected_durable_record_count,
        source_state_basis_snapshot_count=source_state_basis_snapshot_count,
        target_state_basis_snapshot_count=target_state_basis_snapshot_count,
        final_alarm_count=final_alarm_count,
        expected_final_alarm_count=expected_target_runtime_alarm_count,
        final_assignment_count=final_assignment_count,
        expected_final_assignment_count=expected_target_runtime_alarm_count,
        open_occurrence_count=open_occurrence_count,
        expected_open_occurrence_count=expected_target_runtime_alarm_count,
        open_episode_count=open_episode_count,
        expected_open_episode_count=scenario.priority_group_count,
        disabled_target_present_count=disabled_target_present_count,
        functional_integrity_ok=functional_integrity_ok,
    )


def _build_removed_adoption_pressure_metrics(
    *,
    scenario: BaselineScenario,
    runtime,
    records,
    snapshots,
    samples,
) -> RemovedAdoptionPressureMetrics | None:
    if not scenario.has_removed_adoption_pressure:
        return None
    source = runtime.input_source
    target_revision = runtime.target_revision
    if not isinstance(source, StaleTargetDeactivationInputSource):
        raise RuntimeError('removed adoption requires the burst deactivation input source')
    if target_revision is None:
        raise RuntimeError('removed adoption requires a target revision')

    plan = plan_configuration_adoption(runtime.revision, target_revision)
    disposition_counts = {item: 0 for item in ConfigurationAdoptionDisposition}
    for change in plan.changes:
        disposition_counts[change.disposition] += 1

    adoption_samples = tuple(
        sample
        for sample in samples
        if sample.revision_origin == 'source_candidate' and sample.adoption_outcome == 'adopted'
    )
    adoption_sample = adoption_samples[0] if len(adoption_samples) == 1 else None
    post_adoption_cache_current_iteration_count = 0
    if adoption_sample is not None:
        post_adoption_cache_current_iteration_count = sum(
            sample.iteration > adoption_sample.iteration
            and sample.revision_origin == 'cache_current'
            and sample.adoption_outcome == 'not_required'
            for sample in samples
        )

    cache_bundle = runtime.job.revision_resolver.cache.load_effective()
    effective_cache_revision = (
        None if cache_bundle is None else cache_bundle.manifest.alarm_configuration_revision
    )
    source_revision = runtime.revision.alarm_configuration_revision
    target_revision_key = target_revision.alarm_configuration_revision
    source_revision_durable_record_count = sum(
        entry.record.commit.alarm_configuration_revision == source_revision for entry in records
    )
    target_revision_durable_record_count = sum(
        entry.record.commit.alarm_configuration_revision == target_revision_key for entry in records
    )

    expected_target_keys = {
        source.target_for_input(index)[0].canonical_key
        for index in range(scenario.removed_alarm_count)
    }
    expected_input_ids = set(source.input_ids)
    expected_request_ids = set(source.request_ids)
    expected_decision_ids = set(source.decision_ids)

    occurrence_ids: dict[str, str] = {}
    request_receipts: list[dict[str, object]] = []
    decision_receipts: list[dict[str, object]] = []
    requests: list[dict[str, object]] = []
    management_effects: list[dict[str, object]] = []
    deactivation_effects: list[dict[str, object]] = []
    adoption_commit_count = 0
    configuration_removed_occurrence_count = 0
    configuration_disabled_occurrence_count = 0
    management_effect_cleared_count = 0
    occurrence_identity_mismatch_count = 0

    for entry in records:
        payload = entry.record.records
        request_receipts.extend(
            item
            for item in payload.get('input_receipts', [])
            if item.get('input_kind') == 'DEACTIVATION_REQUEST'
            and item.get('input_id') in expected_input_ids
        )
        decision_receipts.extend(
            item
            for item in payload.get('input_receipts', [])
            if item.get('input_kind') == 'DEACTIVATION_DECISION'
            and item.get('input_id') in expected_decision_ids
        )
        requests.extend(
            item
            for item in payload.get('deactivation_requests', [])
            if item.get('request_id') in expected_request_ids
        )
        management_effects.extend(
            item
            for item in payload.get('management_effects', [])
            if item.get('alarm_key') in expected_target_keys
        )
        deactivation_effects.extend(
            item
            for item in payload.get('deactivation_effects', [])
            if item.get('alarm_key') in expected_target_keys
        )

        has_removed_closure = False
        for change in payload.get('occurrence_changes', []):
            alarm_key = str(change.get('alarm_key'))
            kind = change.get('kind')
            if kind == 'STARTED':
                occurrence_ids[alarm_key] = str(change.get('occurrence_id'))
                continue
            if kind != 'CLOSED' or alarm_key not in expected_target_keys:
                continue
            closure_reason = str(change.get('closure_reason')).strip().lower()
            if closure_reason == 'configuration_removed':
                configuration_removed_occurrence_count += 1
                has_removed_closure = True
            if closure_reason == 'configuration_disabled':
                configuration_disabled_occurrence_count += 1
            expected_occurrence_id = occurrence_ids.get(alarm_key)
            if (
                expected_occurrence_id is not None
                and change.get('occurrence_id') != expected_occurrence_id
            ):
                occurrence_identity_mismatch_count += 1
        if (
            entry.record.commit.alarm_configuration_revision == target_revision_key
            and has_removed_closure
        ):
            adoption_commit_count += 1

    pending_approval_receipt_count = sum(
        item.get('outcome') == 'PENDING_APPROVAL' for item in request_receipts
    )
    applied_decision_receipt_count = sum(
        item.get('outcome') == 'APPLIED' for item in decision_receipts
    )
    management_effect_started_count = sum(
        item.get('kind') == 'STARTED' for item in management_effects
    )
    management_effect_cleared_count = sum(
        item.get('kind') == 'CLEARED' for item in management_effects
    )
    deactivation_effect_started_count = sum(
        item.get('kind') == 'STARTED' for item in deactivation_effects
    )
    deactivation_effect_cleared_count = sum(
        item.get('kind') == 'CLEARED' for item in deactivation_effects
    )

    requests_by_id: dict[str, list[dict[str, object]]] = {}
    for request in requests:
        requests_by_id.setdefault(str(request.get('request_id')), []).append(request)
    effects_by_id: dict[str, list[dict[str, object]]] = {}
    for effect in deactivation_effects:
        effects_by_id.setdefault(str(effect.get('effect_id')), []).append(effect)
    control_plane_correlation_mismatch_count = 0
    for index in range(scenario.removed_alarm_count):
        identity, _priority_group = source.target_for_input(index)
        alarm_key = identity.canonical_key
        input_id = source.input_ids[index]
        request_id = source.request_ids[index]
        expected_occurrence_id = source.target_occurrence_ids.get(alarm_key)
        request_candidates = requests_by_id.get(request_id, [])
        request = request_candidates[0] if len(request_candidates) == 1 else None
        if (
            request is None
            or request.get('alarm_key') != alarm_key
            or request.get('source_management_input_id') != input_id
            or request.get('source_occurrence_id') != expected_occurrence_id
            or request.get('approval_required') is not True
        ):
            control_plane_correlation_mismatch_count += 1
        expected_effect_id = f'PERF-DE-{request_id}'
        effect_candidates = effects_by_id.get(expected_effect_id, [])
        started_effects = [item for item in effect_candidates if item.get('kind') == 'STARTED']
        if len(started_effects) != 1 or started_effects[0].get('alarm_key') != alarm_key:
            control_plane_correlation_mismatch_count += 1

    source_state_basis_snapshot_count = 0
    target_state_basis_snapshot_count = 0
    final_alarm_state_count = 0
    final_assignment_count = 0
    open_occurrence_count = 0
    open_episode_count = 0
    orphan_deactivation_state_count = 0
    orphan_occurrence_count = 0
    orphan_management_effect_count = 0
    for snapshot in snapshots:
        document = snapshot.as_document()
        basis = document.get('state_basis')
        if isinstance(basis, dict):
            revision = basis.get('alarm_configuration_revision')
            if revision == source_revision:
                source_state_basis_snapshot_count += 1
            if revision == target_revision_key:
                target_state_basis_snapshot_count += 1
        if document.get('episode') is not None:
            open_episode_count += 1
        alarms = document['alarms']
        final_alarm_state_count += len(alarms)
        for alarm_key, alarm in alarms.items():
            occurrence = alarm.get('occurrence')
            if isinstance(occurrence, dict):
                open_occurrence_count += 1
                final_assignment_count += len(occurrence.get('assignments', []))
            if alarm_key not in expected_target_keys:
                continue
            if occurrence is not None:
                orphan_occurrence_count += 1
            if alarm.get('management_effect') is not None:
                orphan_management_effect_count += 1
            if (
                occurrence is None
                and alarm.get('management_effect') is None
                and isinstance(alarm.get('deactivation_effect'), dict)
            ):
                orphan_deactivation_state_count += 1

    store = AtomicJsonStore(root_path=runtime.composition.durability.persistence.paths.alarms_root)
    final_state = store.read('runtime/state/consumers/management.json') or {}
    final_management = final_state.get('management') if isinstance(final_state, dict) else None
    final_decisions = final_state.get('decisions') if isinstance(final_state, dict) else None
    management_cursor = (
        final_management.get('cursor') if isinstance(final_management, dict) else None
    )
    decision_cursor = final_decisions.get('cursor') if isinstance(final_decisions, dict) else None
    management_pending = (
        final_management.get('pending') if isinstance(final_management, dict) else None
    )
    decision_pending = final_decisions.get('pending') if isinstance(final_decisions, dict) else None
    pending_requests = (
        final_state.get('pending_deactivation_request_ids')
        if isinstance(final_state, dict)
        else None
    )
    management_cursor_byte_offset = (
        management_cursor.get('byte_offset') if isinstance(management_cursor, dict) else None
    )
    decision_cursor_byte_offset = (
        decision_cursor.get('byte_offset') if isinstance(decision_cursor, dict) else None
    )
    management_pending_count = (
        len(management_pending) if isinstance(management_pending, list) else -1
    )
    decision_pending_count = len(decision_pending) if isinstance(decision_pending, list) else -1
    pending_deactivation_request_count = (
        len(pending_requests) if isinstance(pending_requests, list) else -1
    )

    expected_removed_change_count = scenario.removed_alarm_count
    expected_target_runtime_alarm_count = scenario.alarm_count - expected_removed_change_count
    expected_cursor_byte_offset = expected_removed_change_count * source.byte_length
    expected_adoption_commit_count = scenario.priority_group_count
    expected_source_revision_durable_record_count = scenario.priority_group_count * 3
    expected_target_revision_durable_record_count = scenario.priority_group_count
    expected_durable_record_count = (
        expected_source_revision_durable_record_count
        + expected_target_revision_durable_record_count
    )
    functional_integrity_ok = (
        plan.is_adoptable
        and len(plan.changes) == scenario.alarm_count
        and disposition_counts[ConfigurationAdoptionDisposition.COMPATIBLE] == 0
        and disposition_counts[ConfigurationAdoptionDisposition.UNCHANGED]
        == expected_target_runtime_alarm_count
        and disposition_counts[ConfigurationAdoptionDisposition.STRUCTURAL_RESET] == 0
        and disposition_counts[ConfigurationAdoptionDisposition.DISABLED] == 0
        and disposition_counts[ConfigurationAdoptionDisposition.REMOVED]
        == expected_removed_change_count
        and disposition_counts[ConfigurationAdoptionDisposition.REJECTED] == 0
        and len(target_revision.defined_alarm_identities) == expected_target_runtime_alarm_count
        and len(target_revision.session.identities) == expected_target_runtime_alarm_count
        and effective_cache_revision == target_revision_key
        and len(adoption_samples) == 1
        and adoption_sample is not None
        and not adoption_sample.cycle_executed
        and post_adoption_cache_current_iteration_count > 0
        and len(request_receipts) == expected_removed_change_count
        and pending_approval_receipt_count == expected_removed_change_count
        and len(requests) == expected_removed_change_count
        and management_effect_started_count == expected_removed_change_count
        and len(decision_receipts) == expected_removed_change_count
        and applied_decision_receipt_count == expected_removed_change_count
        and deactivation_effect_started_count == expected_removed_change_count
        and deactivation_effect_cleared_count == 0
        and adoption_commit_count == expected_adoption_commit_count
        and configuration_removed_occurrence_count == expected_removed_change_count
        and configuration_disabled_occurrence_count == 0
        and management_effect_cleared_count == expected_removed_change_count
        and occurrence_identity_mismatch_count == 0
        and control_plane_correlation_mismatch_count == 0
        and source_revision_durable_record_count == expected_source_revision_durable_record_count
        and target_revision_durable_record_count == expected_target_revision_durable_record_count
        and len(records) == expected_durable_record_count
        and source_state_basis_snapshot_count == 0
        and target_state_basis_snapshot_count == scenario.priority_group_count
        and final_alarm_state_count == scenario.alarm_count
        and final_assignment_count == expected_target_runtime_alarm_count
        and open_occurrence_count == expected_target_runtime_alarm_count
        and open_episode_count == scenario.priority_group_count
        and orphan_deactivation_state_count == expected_removed_change_count
        and orphan_occurrence_count == 0
        and orphan_management_effect_count == 0
        and management_cursor_byte_offset == expected_cursor_byte_offset
        and decision_cursor_byte_offset == expected_cursor_byte_offset
        and management_pending_count == 0
        and decision_pending_count == 0
        and pending_deactivation_request_count == 0
    )
    return RemovedAdoptionPressureMetrics(
        adoption_at_seconds=scenario.removed_adoption_at_seconds,
        removed_alarm_percent=scenario.removed_alarm_percent,
        request_at_seconds=scenario.management_action_at_seconds,
        decision_at_seconds=scenario.deactivation_decision_at_seconds,
        deactivation_window_seconds=scenario.deactivation_window_seconds,
        source_revision=source_revision,
        target_revision=target_revision_key,
        plan_change_count=len(plan.changes),
        compatible_change_count=disposition_counts[ConfigurationAdoptionDisposition.COMPATIBLE],
        unchanged_change_count=disposition_counts[ConfigurationAdoptionDisposition.UNCHANGED],
        structural_reset_change_count=disposition_counts[
            ConfigurationAdoptionDisposition.STRUCTURAL_RESET
        ],
        disabled_change_count=disposition_counts[ConfigurationAdoptionDisposition.DISABLED],
        removed_change_count=disposition_counts[ConfigurationAdoptionDisposition.REMOVED],
        expected_removed_change_count=expected_removed_change_count,
        rejected_change_count=disposition_counts[ConfigurationAdoptionDisposition.REJECTED],
        target_defined_alarm_count=len(target_revision.defined_alarm_identities),
        expected_target_defined_alarm_count=expected_target_runtime_alarm_count,
        target_runtime_alarm_count=len(target_revision.session.identities),
        expected_target_runtime_alarm_count=expected_target_runtime_alarm_count,
        effective_cache_revision=effective_cache_revision,
        adoption_iteration_count=len(adoption_samples),
        adoption_iteration=None if adoption_sample is None else adoption_sample.iteration,
        adoption_iteration_ms=None if adoption_sample is None else adoption_sample.duration_ms,
        adoption_iteration_cpu_percent=(
            None if adoption_sample is None else adoption_sample.cpu_percent
        ),
        adoption_cycle_executed=False
        if adoption_sample is None
        else adoption_sample.cycle_executed,
        post_adoption_cache_current_iteration_count=post_adoption_cache_current_iteration_count,
        request_receipt_count=len(request_receipts),
        pending_approval_receipt_count=pending_approval_receipt_count,
        deactivation_request_count=len(requests),
        management_effect_started_count=management_effect_started_count,
        decision_receipt_count=len(decision_receipts),
        applied_decision_receipt_count=applied_decision_receipt_count,
        deactivation_effect_started_count=deactivation_effect_started_count,
        deactivation_effect_cleared_count=deactivation_effect_cleared_count,
        adoption_commit_count=adoption_commit_count,
        expected_adoption_commit_count=expected_adoption_commit_count,
        configuration_removed_occurrence_count=configuration_removed_occurrence_count,
        expected_configuration_removed_occurrence_count=expected_removed_change_count,
        configuration_disabled_occurrence_count=configuration_disabled_occurrence_count,
        management_effect_cleared_count=management_effect_cleared_count,
        occurrence_identity_mismatch_count=occurrence_identity_mismatch_count,
        control_plane_correlation_mismatch_count=control_plane_correlation_mismatch_count,
        source_revision_durable_record_count=source_revision_durable_record_count,
        expected_source_revision_durable_record_count=expected_source_revision_durable_record_count,
        target_revision_durable_record_count=target_revision_durable_record_count,
        expected_target_revision_durable_record_count=expected_target_revision_durable_record_count,
        durable_record_count=len(records),
        expected_durable_record_count=expected_durable_record_count,
        source_state_basis_snapshot_count=source_state_basis_snapshot_count,
        target_state_basis_snapshot_count=target_state_basis_snapshot_count,
        final_alarm_state_count=final_alarm_state_count,
        expected_final_alarm_state_count=scenario.alarm_count,
        final_assignment_count=final_assignment_count,
        expected_final_assignment_count=expected_target_runtime_alarm_count,
        open_occurrence_count=open_occurrence_count,
        expected_open_occurrence_count=expected_target_runtime_alarm_count,
        open_episode_count=open_episode_count,
        expected_open_episode_count=scenario.priority_group_count,
        orphan_deactivation_state_count=orphan_deactivation_state_count,
        expected_orphan_deactivation_state_count=expected_removed_change_count,
        orphan_occurrence_count=orphan_occurrence_count,
        orphan_management_effect_count=orphan_management_effect_count,
        management_cursor_byte_offset=management_cursor_byte_offset,
        decision_cursor_byte_offset=decision_cursor_byte_offset,
        management_pending_count=management_pending_count,
        decision_pending_count=decision_pending_count,
        pending_deactivation_request_count=pending_deactivation_request_count,
        functional_integrity_ok=functional_integrity_ok,
    )


def _build_structural_reset_adoption_pressure_metrics(
    *,
    scenario: BaselineScenario,
    runtime,
    records,
    snapshots,
    samples,
) -> StructuralResetAdoptionPressureMetrics | None:
    if not scenario.has_structural_reset_adoption_pressure:
        return None
    target_revision = runtime.target_revision
    if target_revision is None:
        raise RuntimeError('structural reset adoption runtime requires a target revision')
    plan = plan_configuration_adoption(runtime.revision, target_revision)
    disposition_counts = {item: 0 for item in ConfigurationAdoptionDisposition}
    for change in plan.changes:
        disposition_counts[change.disposition] += 1

    adoption_samples = [
        sample
        for sample in samples
        if sample.revision_origin == 'source_candidate' and sample.adoption_outcome == 'adopted'
    ]
    adoption_sample = adoption_samples[0] if len(adoption_samples) == 1 else None
    immediate_next_sample = None
    if adoption_sample is not None:
        immediate_next_sample = next(
            (sample for sample in samples if sample.iteration == adoption_sample.iteration + 1),
            None,
        )

    cache_bundle = runtime.job.revision_resolver.cache.load_effective()
    effective_cache_revision = (
        None if cache_bundle is None else cache_bundle.manifest.alarm_configuration_revision
    )
    source_revision = runtime.revision.alarm_configuration_revision
    target_revision_key = target_revision.alarm_configuration_revision
    source_revision_durable_record_count = sum(
        entry.record.commit.alarm_configuration_revision == source_revision for entry in records
    )
    target_revision_durable_record_count = sum(
        entry.record.commit.alarm_configuration_revision == target_revision_key for entry in records
    )

    structural_reset_alarm_keys = {
        change.identity.canonical_key
        for change in plan.changes
        if change.disposition is ConfigurationAdoptionDisposition.STRUCTURAL_RESET
    }
    structural_reset_groups = set(plan.structural_reset_groups)

    initial_occurrence_ids: dict[str, str] = {}
    adoption_commit_count = 0
    next_cycle_commit_count = 0
    configuration_reconfigured_occurrence_count = 0
    configuration_terminated_episode_count = 0
    restarted_occurrence_count = 0
    restarted_episode_count = 0
    occurrence_identity_reuse_count = 0

    for entry in records:
        payload = entry.record.records
        occurrence_changes = payload.get('occurrence_changes', [])
        episode_changes = payload.get('episode_changes', [])
        is_target_revision = entry.record.commit.alarm_configuration_revision == target_revision_key
        has_reset_closure = False
        has_restart = False

        for change in occurrence_changes:
            alarm_key = str(change.get('alarm_key'))
            kind = change.get('kind')
            if kind == 'STARTED':
                occurrence_id = str(change.get('occurrence_id'))
                if not is_target_revision:
                    initial_occurrence_ids[alarm_key] = occurrence_id
                elif alarm_key in structural_reset_alarm_keys:
                    restarted_occurrence_count += 1
                    has_restart = True
                    if initial_occurrence_ids.get(alarm_key) == occurrence_id:
                        occurrence_identity_reuse_count += 1
                continue
            if kind != 'CLOSED':
                continue
            closure_reason = str(change.get('closure_reason')).strip().lower()
            if closure_reason != 'configuration_reconfigured':
                continue
            if alarm_key in structural_reset_alarm_keys:
                configuration_reconfigured_occurrence_count += 1
                has_reset_closure = True
                expected_occurrence_id = initial_occurrence_ids.get(alarm_key)
                if (
                    expected_occurrence_id is not None
                    and change.get('occurrence_id') != expected_occurrence_id
                ):
                    occurrence_identity_reuse_count += 1

        for change in episode_changes:
            kind = change.get('kind')
            closure_reason = str(change.get('closure_reason')).strip().lower()
            if kind == 'CLOSED' and closure_reason == 'configuration_terminated':
                configuration_terminated_episode_count += 1
            if is_target_revision and kind == 'STARTED':
                restarted_episode_count += 1

        if is_target_revision and has_reset_closure:
            adoption_commit_count += 1
        if is_target_revision and has_restart:
            next_cycle_commit_count += 1

    source_state_basis_snapshot_count = 0
    target_state_basis_snapshot_count = 0
    final_alarm_count = 0
    final_assignment_count = 0
    open_occurrence_count = 0
    open_episode_count = 0
    for snapshot in snapshots:
        document = snapshot.as_document()
        basis = document.get('state_basis')
        if isinstance(basis, dict):
            revision = basis.get('alarm_configuration_revision')
            if revision == source_revision:
                source_state_basis_snapshot_count += 1
            if revision == target_revision_key:
                target_state_basis_snapshot_count += 1
        if document.get('episode') is not None:
            open_episode_count += 1
        alarms = document['alarms']
        final_alarm_count += len(alarms)
        for alarm in alarms.values():
            occurrence = alarm.get('occurrence')
            if occurrence is None:
                continue
            open_occurrence_count += 1
            final_assignment_count += len(occurrence.get('assignments', []))

    expected_structural_reset_change_count = scenario.structural_reset_alarm_count
    expected_structural_reset_group_count = scenario.structural_reset_priority_group_count
    expected_source_revision_durable_record_count = scenario.priority_group_count
    expected_target_revision_durable_record_count = expected_structural_reset_group_count * 2
    expected_durable_record_count = (
        expected_source_revision_durable_record_count
        + expected_target_revision_durable_record_count
    )
    expected_source_state_basis_snapshot_count = (
        scenario.priority_group_count - expected_structural_reset_group_count
    )
    expected_target_state_basis_snapshot_count = expected_structural_reset_group_count

    functional_integrity_ok = (
        plan.is_adoptable
        and len(plan.changes) == scenario.alarm_count
        and disposition_counts[ConfigurationAdoptionDisposition.COMPATIBLE] == 0
        and disposition_counts[ConfigurationAdoptionDisposition.UNCHANGED]
        == scenario.alarm_count - expected_structural_reset_change_count
        and disposition_counts[ConfigurationAdoptionDisposition.STRUCTURAL_RESET]
        == expected_structural_reset_change_count
        and len(structural_reset_groups) == expected_structural_reset_group_count
        and disposition_counts[ConfigurationAdoptionDisposition.DISABLED] == 0
        and disposition_counts[ConfigurationAdoptionDisposition.REMOVED] == 0
        and disposition_counts[ConfigurationAdoptionDisposition.REJECTED] == 0
        and len(target_revision.defined_alarm_identities) == scenario.alarm_count
        and len(target_revision.session.identities) == scenario.alarm_count
        and effective_cache_revision == target_revision_key
        and len(adoption_samples) == 1
        and adoption_sample is not None
        and not adoption_sample.cycle_executed
        and immediate_next_sample is not None
        and immediate_next_sample.revision_origin == 'cache_current'
        and immediate_next_sample.adoption_outcome == 'not_required'
        and immediate_next_sample.cycle_executed
        and adoption_commit_count == expected_structural_reset_group_count
        and next_cycle_commit_count == expected_structural_reset_group_count
        and configuration_reconfigured_occurrence_count == expected_structural_reset_change_count
        and configuration_terminated_episode_count == expected_structural_reset_group_count
        and restarted_occurrence_count == expected_structural_reset_change_count
        and restarted_episode_count == expected_structural_reset_group_count
        and occurrence_identity_reuse_count == 0
        and source_revision_durable_record_count == expected_source_revision_durable_record_count
        and target_revision_durable_record_count == expected_target_revision_durable_record_count
        and len(records) == expected_durable_record_count
        and source_state_basis_snapshot_count == expected_source_state_basis_snapshot_count
        and target_state_basis_snapshot_count == expected_target_state_basis_snapshot_count
        and final_alarm_count == scenario.alarm_count
        and final_assignment_count == scenario.alarm_count
        and open_occurrence_count == scenario.alarm_count
        and open_episode_count == scenario.priority_group_count
    )
    return StructuralResetAdoptionPressureMetrics(
        adoption_at_seconds=scenario.structural_reset_adoption_at_seconds,
        structural_reset_alarm_percent=scenario.structural_reset_alarm_percent,
        source_revision=source_revision,
        target_revision=target_revision_key,
        plan_change_count=len(plan.changes),
        compatible_change_count=disposition_counts[ConfigurationAdoptionDisposition.COMPATIBLE],
        unchanged_change_count=disposition_counts[ConfigurationAdoptionDisposition.UNCHANGED],
        structural_reset_change_count=disposition_counts[
            ConfigurationAdoptionDisposition.STRUCTURAL_RESET
        ],
        expected_structural_reset_change_count=expected_structural_reset_change_count,
        structural_reset_group_count=len(structural_reset_groups),
        expected_structural_reset_group_count=expected_structural_reset_group_count,
        disabled_change_count=disposition_counts[ConfigurationAdoptionDisposition.DISABLED],
        removed_change_count=disposition_counts[ConfigurationAdoptionDisposition.REMOVED],
        rejected_change_count=disposition_counts[ConfigurationAdoptionDisposition.REJECTED],
        effective_cache_revision=effective_cache_revision,
        adoption_iteration_count=len(adoption_samples),
        adoption_iteration=None if adoption_sample is None else adoption_sample.iteration,
        adoption_iteration_ms=None if adoption_sample is None else adoption_sample.duration_ms,
        adoption_iteration_cpu_percent=(
            None if adoption_sample is None else adoption_sample.cpu_percent
        ),
        adoption_cycle_executed=(
            False if adoption_sample is None else adoption_sample.cycle_executed
        ),
        immediate_next_iteration=(
            None if immediate_next_sample is None else immediate_next_sample.iteration
        ),
        immediate_next_iteration_cycle_executed=(
            False if immediate_next_sample is None else immediate_next_sample.cycle_executed
        ),
        immediate_next_iteration_cache_current=(
            False
            if immediate_next_sample is None
            else immediate_next_sample.revision_origin == 'cache_current'
            and immediate_next_sample.adoption_outcome == 'not_required'
        ),
        adoption_commit_count=adoption_commit_count,
        expected_adoption_commit_count=expected_structural_reset_group_count,
        next_cycle_commit_count=next_cycle_commit_count,
        expected_next_cycle_commit_count=expected_structural_reset_group_count,
        configuration_reconfigured_occurrence_count=(configuration_reconfigured_occurrence_count),
        expected_configuration_reconfigured_occurrence_count=(
            expected_structural_reset_change_count
        ),
        configuration_terminated_episode_count=configuration_terminated_episode_count,
        expected_configuration_terminated_episode_count=(expected_structural_reset_group_count),
        restarted_occurrence_count=restarted_occurrence_count,
        expected_restarted_occurrence_count=expected_structural_reset_change_count,
        restarted_episode_count=restarted_episode_count,
        expected_restarted_episode_count=expected_structural_reset_group_count,
        occurrence_identity_reuse_count=occurrence_identity_reuse_count,
        source_revision_durable_record_count=source_revision_durable_record_count,
        expected_source_revision_durable_record_count=(
            expected_source_revision_durable_record_count
        ),
        target_revision_durable_record_count=target_revision_durable_record_count,
        expected_target_revision_durable_record_count=(
            expected_target_revision_durable_record_count
        ),
        durable_record_count=len(records),
        expected_durable_record_count=expected_durable_record_count,
        source_state_basis_snapshot_count=source_state_basis_snapshot_count,
        expected_source_state_basis_snapshot_count=(expected_source_state_basis_snapshot_count),
        target_state_basis_snapshot_count=target_state_basis_snapshot_count,
        expected_target_state_basis_snapshot_count=(expected_target_state_basis_snapshot_count),
        final_alarm_count=final_alarm_count,
        expected_final_alarm_count=scenario.alarm_count,
        final_assignment_count=final_assignment_count,
        expected_final_assignment_count=scenario.alarm_count,
        open_occurrence_count=open_occurrence_count,
        expected_open_occurrence_count=scenario.alarm_count,
        open_episode_count=open_episode_count,
        expected_open_episode_count=scenario.priority_group_count,
        functional_integrity_ok=functional_integrity_ok,
    )


def _build_rejected_target_pressure_metrics(
    *,
    scenario: BaselineScenario,
    runtime,
    records,
    snapshots,
    samples,
) -> RejectedTargetPressureMetrics | None:
    if not scenario.has_rejected_candidate_pressure:
        return None
    target_revision = runtime.target_revision
    if target_revision is None:
        raise RuntimeError('rejected target runtime requires a target revision')

    plan = plan_configuration_adoption(runtime.revision, target_revision)
    disposition_counts = {item: 0 for item in ConfigurationAdoptionDisposition}
    for change in plan.changes:
        disposition_counts[change.disposition] += 1
    rejected_changes = tuple(
        change
        for change in plan.changes
        if change.disposition is ConfigurationAdoptionDisposition.REJECTED
    )
    rejected_change = rejected_changes[0] if len(rejected_changes) == 1 else None
    priority_group_changed_rejection_count = sum(
        change.rejection_reason is ConfigurationAdoptionRejectionReason.PRIORITY_GROUP_CHANGED
        for change in rejected_changes
    )
    rejected_alarm_key = '' if rejected_change is None else rejected_change.identity.alarm_key
    rejected_target_plan = (
        None if rejected_change is None else target_revision.plan_for(rejected_change.identity)
    )
    rejected_target_priority_group = (
        '' if rejected_target_plan is None else rejected_target_plan.priority_group
    )

    rejected_samples = [
        sample
        for sample in samples
        if sample.revision_origin == 'source_candidate' and sample.adoption_outcome == 'rejected'
    ]
    first_rejected_sample = rejected_samples[0] if rejected_samples else None
    expected_first_rejected_iteration = (
        int(scenario.rejected_candidate_at_seconds / scenario.iteration_period_seconds) + 1
    )
    post_candidate_non_rejected_iteration_count = 0
    if first_rejected_sample is not None:
        post_candidate_non_rejected_iteration_count = sum(
            sample.iteration >= first_rejected_sample.iteration
            and not (
                sample.revision_origin == 'source_candidate'
                and sample.adoption_outcome == 'rejected'
            )
            for sample in samples
        )
    degraded_rejected_iteration_count = sum(sample.degraded for sample in rejected_samples)
    rejected_cycle_executed_count = sum(sample.cycle_executed for sample in rejected_samples)

    cache_bundle = runtime.job.revision_resolver.cache.load_effective()
    effective_cache_revision = (
        None if cache_bundle is None else cache_bundle.manifest.alarm_configuration_revision
    )
    source_revision = runtime.revision.alarm_configuration_revision
    target_revision_key = target_revision.alarm_configuration_revision
    source_revision_durable_record_count = sum(
        entry.record.commit.alarm_configuration_revision == source_revision for entry in records
    )
    target_revision_durable_record_count = sum(
        entry.record.commit.alarm_configuration_revision == target_revision_key for entry in records
    )

    source_state_basis_snapshot_count = 0
    target_state_basis_snapshot_count = 0
    rejected_priority_group_materialized_count = 0
    final_alarm_count = 0
    final_assignment_count = 0
    open_occurrence_count = 0
    open_episode_count = 0
    for snapshot in snapshots:
        document = snapshot.as_document()
        basis = document.get('state_basis')
        if isinstance(basis, dict):
            revision = basis.get('alarm_configuration_revision')
            if revision == source_revision:
                source_state_basis_snapshot_count += 1
            if revision == target_revision_key:
                target_state_basis_snapshot_count += 1
        if document.get('priority_group') == rejected_target_priority_group:
            rejected_priority_group_materialized_count += 1
        if document.get('episode') is not None:
            open_episode_count += 1
        alarms = document.get('alarms', {})
        final_alarm_count += len(alarms)
        for alarm in alarms.values():
            occurrence = alarm.get('occurrence')
            if occurrence is None:
                continue
            open_occurrence_count += 1
            final_assignment_count += len(occurrence.get('assignments', {}))

    expected_source_revision_durable_record_count = scenario.priority_group_count
    expected_target_revision_durable_record_count = 0
    expected_durable_record_count = scenario.priority_group_count
    expected_rejected_change_count = 1
    expected_final_alarm_count = scenario.alarm_count
    expected_final_assignment_count = scenario.alarm_count
    expected_open_occurrence_count = scenario.alarm_count
    expected_open_episode_count = scenario.priority_group_count
    expected_rejected_iteration_count = sum(
        sample.iteration >= expected_first_rejected_iteration for sample in samples
    )

    functional_integrity_ok = (
        not plan.is_adoptable
        and len(plan.changes) == scenario.alarm_count
        and disposition_counts[ConfigurationAdoptionDisposition.UNCHANGED]
        == scenario.alarm_count - 1
        and disposition_counts[ConfigurationAdoptionDisposition.COMPATIBLE] == 0
        and disposition_counts[ConfigurationAdoptionDisposition.STRUCTURAL_RESET] == 0
        and disposition_counts[ConfigurationAdoptionDisposition.DISABLED] == 0
        and disposition_counts[ConfigurationAdoptionDisposition.REMOVED] == 0
        and disposition_counts[ConfigurationAdoptionDisposition.REJECTED]
        == expected_rejected_change_count
        and priority_group_changed_rejection_count == expected_rejected_change_count
        and len(target_revision.defined_alarm_identities) == scenario.alarm_count
        and len(target_revision.session.identities) == scenario.alarm_count
        and effective_cache_revision == source_revision
        and first_rejected_sample is not None
        and first_rejected_sample.iteration == expected_first_rejected_iteration
        and len(rejected_samples) == expected_rejected_iteration_count
        and degraded_rejected_iteration_count == len(rejected_samples)
        and rejected_cycle_executed_count == len(rejected_samples)
        and post_candidate_non_rejected_iteration_count == 0
        and source_revision_durable_record_count == expected_source_revision_durable_record_count
        and target_revision_durable_record_count == expected_target_revision_durable_record_count
        and len(records) == expected_durable_record_count
        and source_state_basis_snapshot_count == scenario.priority_group_count
        and target_state_basis_snapshot_count == 0
        and rejected_priority_group_materialized_count == 0
        and final_alarm_count == expected_final_alarm_count
        and final_assignment_count == expected_final_assignment_count
        and open_occurrence_count == expected_open_occurrence_count
        and open_episode_count == expected_open_episode_count
    )

    return RejectedTargetPressureMetrics(
        candidate_at_seconds=scenario.rejected_candidate_at_seconds,
        source_revision=source_revision,
        target_revision=target_revision_key,
        rejected_alarm_key=rejected_alarm_key,
        rejected_target_priority_group=rejected_target_priority_group,
        plan_change_count=len(plan.changes),
        unchanged_change_count=disposition_counts[ConfigurationAdoptionDisposition.UNCHANGED],
        compatible_change_count=disposition_counts[ConfigurationAdoptionDisposition.COMPATIBLE],
        structural_reset_change_count=disposition_counts[
            ConfigurationAdoptionDisposition.STRUCTURAL_RESET
        ],
        disabled_change_count=disposition_counts[ConfigurationAdoptionDisposition.DISABLED],
        removed_change_count=disposition_counts[ConfigurationAdoptionDisposition.REMOVED],
        rejected_change_count=disposition_counts[ConfigurationAdoptionDisposition.REJECTED],
        expected_rejected_change_count=expected_rejected_change_count,
        priority_group_changed_rejection_count=priority_group_changed_rejection_count,
        plan_adoptable=plan.is_adoptable,
        effective_cache_revision=effective_cache_revision,
        first_rejected_iteration=(
            None if first_rejected_sample is None else first_rejected_sample.iteration
        ),
        expected_first_rejected_iteration=expected_first_rejected_iteration,
        rejected_iteration_count=len(rejected_samples),
        degraded_rejected_iteration_count=degraded_rejected_iteration_count,
        rejected_cycle_executed_count=rejected_cycle_executed_count,
        post_candidate_non_rejected_iteration_count=post_candidate_non_rejected_iteration_count,
        target_revision_durable_record_count=target_revision_durable_record_count,
        expected_target_revision_durable_record_count=(
            expected_target_revision_durable_record_count
        ),
        source_revision_durable_record_count=source_revision_durable_record_count,
        expected_source_revision_durable_record_count=(
            expected_source_revision_durable_record_count
        ),
        durable_record_count=len(records),
        expected_durable_record_count=expected_durable_record_count,
        source_state_basis_snapshot_count=source_state_basis_snapshot_count,
        target_state_basis_snapshot_count=target_state_basis_snapshot_count,
        rejected_priority_group_materialized_count=rejected_priority_group_materialized_count,
        final_alarm_count=final_alarm_count,
        expected_final_alarm_count=expected_final_alarm_count,
        final_assignment_count=final_assignment_count,
        expected_final_assignment_count=expected_final_assignment_count,
        open_occurrence_count=open_occurrence_count,
        expected_open_occurrence_count=expected_open_occurrence_count,
        open_episode_count=open_episode_count,
        expected_open_episode_count=expected_open_episode_count,
        functional_integrity_ok=functional_integrity_ok,
    )


# Reconstruye el hard gate de E-008 a partir de samples, cache, WAL y snapshots.
# No infiere continuidad sólo por el resultado global: cada iteración posterior al fallo debe cumplirla.
def _build_source_unavailable_pressure_metrics(
    *,
    scenario: BaselineScenario,
    runtime,
    records,
    snapshots,
    samples,
) -> SourceUnavailablePressureMetrics | None:
    if not scenario.has_source_unavailable_pressure:
        return None
    source = runtime.source_unavailable_revision_source
    cache = runtime.tracked_revision_cache
    if source is None or cache is None:
        raise RuntimeError('source unavailable pressure runtime instrumentation is missing')

    expected_first_fallback_iteration = (
        int(scenario.source_unavailable_at_seconds / scenario.iteration_period_seconds) + 1
    )
    fallback_samples = [
        sample
        for sample in samples
        if sample.revision_origin == 'cache_fallback' and sample.adoption_outcome == 'not_required'
    ]
    first_fallback_sample = fallback_samples[0] if fallback_samples else None
    expected_fallback_iteration_count = max(0, len(samples) - expected_first_fallback_iteration + 1)
    post_failure_samples = [
        sample for sample in samples if sample.iteration >= expected_first_fallback_iteration
    ]
    post_failure_non_fallback_iteration_count = sum(
        not (
            sample.revision_origin == 'cache_fallback'
            and sample.adoption_outcome == 'not_required'
            and sample.cycle_executed
            and sample.degraded
        )
        for sample in post_failure_samples
    )
    degraded_fallback_iteration_count = sum(sample.degraded for sample in fallback_samples)
    fallback_cycle_executed_count = sum(sample.cycle_executed for sample in fallback_samples)
    not_required_fallback_iteration_count = sum(
        sample.adoption_outcome == 'not_required' for sample in fallback_samples
    )

    cache_bundle = cache.load_effective()
    effective_cache_alarm_revision = (
        None if cache_bundle is None else cache_bundle.manifest.alarm_configuration_revision
    )
    effective_cache_tool_revision = (
        None if cache_bundle is None else cache_bundle.manifest.tool_registry_revision
    )
    failure_monotonic = source.started_monotonic + scenario.source_unavailable_at_seconds
    post_failure_cache_replace_count = sum(
        replaced_at >= failure_monotonic for replaced_at in cache.replace_monotonic
    )

    source_alarm_revision = runtime.revision.alarm_configuration_revision
    source_tool_revision = runtime.revision.tool_registry_revision
    source_revision_durable_record_count = sum(
        entry.record.commit.alarm_configuration_revision == source_alarm_revision
        and entry.record.commit.tool_registry_revision == source_tool_revision
        for entry in records
    )
    unexpected_revision_durable_record_count = len(records) - source_revision_durable_record_count
    source_state_basis_snapshot_count = 0
    unexpected_state_basis_snapshot_count = 0
    for snapshot in snapshots:
        basis = snapshot.as_document()['state_basis']
        if (
            basis['alarm_configuration_revision'] == source_alarm_revision
            and basis['tool_registry_revision'] == source_tool_revision
        ):
            source_state_basis_snapshot_count += 1
        else:
            unexpected_state_basis_snapshot_count += 1

    functional_integrity_ok = (
        first_fallback_sample is not None
        and first_fallback_sample.iteration == expected_first_fallback_iteration
        and len(fallback_samples) == expected_fallback_iteration_count
        and degraded_fallback_iteration_count == len(fallback_samples)
        and fallback_cycle_executed_count == len(fallback_samples)
        and not_required_fallback_iteration_count == len(fallback_samples)
        and post_failure_non_fallback_iteration_count == 0
        and source.manifest_success_count == expected_first_fallback_iteration - 1
        and source.manifest_failure_count == len(fallback_samples)
        and len(cache.replace_monotonic) == 1
        and post_failure_cache_replace_count == 0
        and effective_cache_alarm_revision == source_alarm_revision
        and effective_cache_tool_revision == source_tool_revision
        and source_revision_durable_record_count == len(records)
        and unexpected_revision_durable_record_count == 0
        and source_state_basis_snapshot_count == len(snapshots)
        and unexpected_state_basis_snapshot_count == 0
    )

    return SourceUnavailablePressureMetrics(
        unavailable_at_seconds=scenario.source_unavailable_at_seconds,
        source_alarm_revision=source_alarm_revision,
        source_tool_revision=source_tool_revision,
        first_fallback_iteration=(
            None if first_fallback_sample is None else first_fallback_sample.iteration
        ),
        expected_first_fallback_iteration=expected_first_fallback_iteration,
        fallback_iteration_count=len(fallback_samples),
        expected_fallback_iteration_count=expected_fallback_iteration_count,
        degraded_fallback_iteration_count=degraded_fallback_iteration_count,
        fallback_cycle_executed_count=fallback_cycle_executed_count,
        not_required_fallback_iteration_count=not_required_fallback_iteration_count,
        post_failure_non_fallback_iteration_count=post_failure_non_fallback_iteration_count,
        manifest_success_count=source.manifest_success_count,
        manifest_failure_count=source.manifest_failure_count,
        cache_replace_count=len(cache.replace_monotonic),
        post_failure_cache_replace_count=post_failure_cache_replace_count,
        effective_cache_alarm_revision=effective_cache_alarm_revision,
        effective_cache_tool_revision=effective_cache_tool_revision,
        source_revision_durable_record_count=source_revision_durable_record_count,
        unexpected_revision_durable_record_count=unexpected_revision_durable_record_count,
        source_state_basis_snapshot_count=source_state_basis_snapshot_count,
        unexpected_state_basis_snapshot_count=unexpected_state_basis_snapshot_count,
        functional_integrity_ok=functional_integrity_ok,
    )


# Reconstruye el hard gate de E-009 sin inferir la causa sólo desde CACHE_FALLBACK.
# Reconstruye E-010 desde WAL, snapshots y estados de ambos owners; no confía sólo en los contadores del runner.
def _build_lease_loss_adoption_pressure_metrics(
    *,
    scenario: BaselineScenario,
    runtime,
    records,
    snapshots,
    samples,
    owner_a_state: dict[str, object],
    owner_b_state: dict[str, object],
    preflight_status: str,
    functional_pressure: FunctionalPressureMetrics | None,
) -> LeaseLossAdoptionPressureMetrics | None:
    if not scenario.has_lease_loss_adoption_pressure:
        return None
    target_revision = runtime.target_revision
    if target_revision is None:
        raise RuntimeError('lease loss adoption runtime requires a target revision')

    plan = plan_configuration_adoption(runtime.revision, target_revision)
    disposition_counts = {item: 0 for item in ConfigurationAdoptionDisposition}
    for change in plan.changes:
        disposition_counts[change.disposition] += 1
    reset_alarm_keys = {
        change.identity.canonical_key
        for change in plan.changes
        if change.disposition is ConfigurationAdoptionDisposition.STRUCTURAL_RESET
    }
    reset_groups = set(plan.structural_reset_groups)

    source_revision = runtime.revision.alarm_configuration_revision
    source_tool_revision = runtime.revision.tool_registry_revision
    target_revision_key = target_revision.alarm_configuration_revision
    target_tool_revision = target_revision.tool_registry_revision
    source_revision_durable_record_count = sum(
        entry.record.commit.alarm_configuration_revision == source_revision
        and entry.record.commit.tool_registry_revision == source_tool_revision
        for entry in records
    )
    target_revision_durable_record_count = sum(
        entry.record.commit.alarm_configuration_revision == target_revision_key
        and entry.record.commit.tool_registry_revision == target_tool_revision
        for entry in records
    )
    group_record_counts: dict[str, int] = {}
    configuration_reconfigured_occurrence_count = 0
    configuration_terminated_episode_count = 0
    for entry in records:
        record = entry.record
        group_record_counts[record.commit.priority_group] = (
            group_record_counts.get(record.commit.priority_group, 0) + 1
        )
        for change in record.records.get('occurrence_changes', []):
            if (
                change.get('kind') == 'CLOSED'
                and str(change.get('closure_reason')).strip().lower()
                == 'configuration_reconfigured'
                and change.get('alarm_key') in reset_alarm_keys
            ):
                configuration_reconfigured_occurrence_count += 1
        for change in record.records.get('episode_changes', []):
            if (
                change.get('kind') == 'CLOSED'
                and str(change.get('closure_reason')).strip().lower() == 'configuration_terminated'
            ):
                configuration_terminated_episode_count += 1

    owner_a_adoption_commit_ids = list(owner_a_state.get('adoption_commit_ids', []))
    owner_b_first_commit_ids = list(owner_b_state.get('first_new_commit_ids', []))
    owner_b_first_entries = _e010_entries_by_commit_ids(records, owner_b_first_commit_ids)
    owner_b_replay_adoption_commit_count = sum(
        any(
            change.get('kind') == 'CLOSED'
            and str(change.get('closure_reason')).strip().lower() == 'configuration_reconfigured'
            for change in entry.record.records.get('occurrence_changes', [])
        )
        for entry in owner_b_first_entries
    )
    restarted_occurrence_ids = [
        str(change['occurrence_id'])
        for entry in owner_b_first_entries
        for change in entry.record.records.get('occurrence_changes', [])
        if change.get('kind') == 'STARTED'
        and change.get('alarm_key') in reset_alarm_keys
        and isinstance(change.get('occurrence_id'), str)
    ]
    restarted_episode_ids = [
        str(change['episode_id'])
        for entry in owner_b_first_entries
        for change in entry.record.records.get('episode_changes', [])
        if change.get('kind') == 'STARTED'
        and entry.record.commit.priority_group in reset_groups
        and isinstance(change.get('episode_id'), str)
    ]
    owner_a_closed_occurrence_ids = set(owner_a_state.get('adoption_closed_occurrence_ids', []))
    owner_a_closed_episode_ids = set(owner_a_state.get('adoption_closed_episode_ids', []))
    restarted_occurrence_count = len(restarted_occurrence_ids)
    restarted_episode_count = len(restarted_episode_ids)
    occurrence_identity_reuse_count = len(
        owner_a_closed_occurrence_ids.intersection(restarted_occurrence_ids)
    )
    episode_identity_reuse_count = len(
        owner_a_closed_episode_ids.intersection(restarted_episode_ids)
    )

    target_state_basis_snapshot_count = 0
    final_alarm_count = 0
    final_assignment_count = 0
    final_pending_assignment_count = 0
    open_occurrence_count = 0
    open_episode_count = 0
    for snapshot in snapshots:
        document = snapshot.as_document()
        basis = document.get('state_basis')
        if isinstance(basis, dict) and (
            basis.get('alarm_configuration_revision'),
            basis.get('tool_registry_revision'),
        ) == (target_revision_key, target_tool_revision):
            target_state_basis_snapshot_count += 1
        if document.get('episode') is not None:
            open_episode_count += 1
        alarms = document['alarms']
        final_alarm_count += len(alarms)
        for alarm in alarms.values():
            occurrence = alarm.get('occurrence')
            if occurrence is None:
                continue
            open_occurrence_count += 1
            final_assignment_count += len(occurrence.get('assignments', []))
            final_pending_assignment_count += len(occurrence.get('pending_assignments', []))

    expected_owner_a_failed_iteration = _e010_expected_failure_iteration(scenario)
    expected_owner_b_first_iteration = _e010_expected_owner_b_first_iteration(scenario)
    expected_owner_b_post_replay_cache_current_count = max(
        0, _e010_last_scheduled_iteration(scenario) - expected_owner_b_first_iteration
    )
    owner_b_post_replay_samples = [
        sample for sample in samples if sample.iteration > expected_owner_b_first_iteration
    ]
    owner_b_post_replay_cache_current_count = sum(
        sample.revision_origin == 'cache_current'
        and sample.adoption_outcome == 'not_required'
        and sample.cycle_executed
        and not sample.degraded
        for sample in owner_b_post_replay_samples
    )

    owner_a_cache_replace_count = int(owner_a_state.get('cache_replace_count', 0))
    owner_a_post_adoption_cache_replace_count = max(0, owner_a_cache_replace_count - 1)
    stale_owner_cache_write_count = owner_a_post_adoption_cache_replace_count
    owner_b_recovery = dict(owner_b_state.get('recovery', {}))
    expected_structural_reset_change_count = scenario.lease_loss_structural_reset_alarm_count
    expected_structural_reset_group_count = (
        scenario.lease_loss_structural_reset_priority_group_count
    )
    active_reset_alarm_count = (
        expected_structural_reset_change_count * scenario.initial_active_percent // 100
    )
    churn_group_count = (
        scenario.alarm_count
        * scenario.operational_churn_percent
        // 100
        // scenario.effective_priority_group_size
    )
    source_churn_generation_count = max(
        0, scenario.lease_loss_adoption_at_seconds // scenario.data_refresh_seconds - 1
    )
    expected_source_revision_durable_record_count = (
        scenario.priority_group_count + source_churn_generation_count * churn_group_count
    )
    baseline_churn_generation_count = scenario.duration_seconds // scenario.data_refresh_seconds
    expected_durable_record_count = (
        scenario.priority_group_count
        + baseline_churn_generation_count * churn_group_count
        + expected_structural_reset_group_count * 2
    )
    expected_target_revision_durable_record_count = (
        expected_durable_record_count - expected_source_revision_durable_record_count
    )
    baseline_records_per_group = 1 + (
        baseline_churn_generation_count * scenario.operational_churn_percent // 100
    )
    expected_groups_with_9_records = expected_structural_reset_group_count
    expected_groups_with_7_records = (
        scenario.priority_group_count - expected_structural_reset_group_count
    )
    groups_with_7_records = sum(
        count == baseline_records_per_group for count in group_record_counts.values()
    )
    groups_with_9_records = sum(
        count == baseline_records_per_group + 2 for count in group_record_counts.values()
    )
    expected_final_alarm_count = scenario.initial_active_alarm_count
    expected_final_assignment_count = expected_final_alarm_count
    expected_open_occurrence_count = expected_final_alarm_count
    expected_open_episode_count = scenario.priority_group_count

    first_owner_b_sample = next(
        (sample for sample in samples if sample.iteration == expected_owner_b_first_iteration),
        None,
    )
    functional_integrity_ok = (
        plan.is_adoptable
        and len(plan.changes) == scenario.alarm_count
        and disposition_counts[ConfigurationAdoptionDisposition.UNCHANGED]
        == scenario.alarm_count - expected_structural_reset_change_count
        and disposition_counts[ConfigurationAdoptionDisposition.STRUCTURAL_RESET]
        == expected_structural_reset_change_count
        and disposition_counts[ConfigurationAdoptionDisposition.COMPATIBLE] == 0
        and disposition_counts[ConfigurationAdoptionDisposition.DISABLED] == 0
        and disposition_counts[ConfigurationAdoptionDisposition.REMOVED] == 0
        and disposition_counts[ConfigurationAdoptionDisposition.REJECTED] == 0
        and len(reset_groups) == expected_structural_reset_group_count
        and preflight_status == 'PASS'
        and int(owner_a_state.get('lease_generation', 0)) == 1
        and int(owner_b_state.get('lease_generation', 0)) == 2
        and int(owner_a_state.get('failed_iteration', 0)) == expected_owner_a_failed_iteration
        and int(owner_b_state.get('first_iteration', 0)) == expected_owner_b_first_iteration
        and bool(owner_a_state.get('lease_loss_observed'))
        and bool(owner_a_state.get('journal_aligned_before_loss'))
        and owner_a_state.get('cache_alarm_revision_before_loss') == source_revision
        and owner_a_state.get('cache_tool_revision_before_loss') == source_tool_revision
        and owner_a_cache_replace_count == 1
        and owner_a_post_adoption_cache_replace_count == 0
        and len(owner_a_adoption_commit_ids) == expected_structural_reset_group_count
        and int(owner_b_recovery.get('applied_count', -1)) == 0
        and int(owner_b_recovery.get('skipped_count', -1)) == 0
        and int(owner_b_recovery.get('discarded_tail_bytes', -1)) == 0
        and owner_b_state.get('first_revision_origin') == 'source_candidate'
        and owner_b_state.get('first_adoption_outcome') == 'adopted'
        and bool(owner_b_state.get('first_cycle_executed'))
        and not bool(owner_b_state.get('first_degraded'))
        and first_owner_b_sample is not None
        and first_owner_b_sample.revision_origin == 'source_candidate'
        and first_owner_b_sample.adoption_outcome == 'adopted'
        and first_owner_b_sample.cycle_executed
        and not first_owner_b_sample.degraded
        and len(owner_b_post_replay_samples) == expected_owner_b_post_replay_cache_current_count
        and owner_b_post_replay_cache_current_count
        == expected_owner_b_post_replay_cache_current_count
        and owner_b_replay_adoption_commit_count == 0
        and int(owner_b_state.get('cache_replace_count', 0)) == 1
        and stale_owner_cache_write_count == 0
        and owner_b_state.get('final_cache_alarm_revision') == target_revision_key
        and owner_b_state.get('final_cache_tool_revision') == target_tool_revision
        and configuration_reconfigured_occurrence_count == active_reset_alarm_count
        and configuration_terminated_episode_count == expected_structural_reset_group_count
        and restarted_occurrence_count == active_reset_alarm_count
        and restarted_episode_count == expected_structural_reset_group_count
        and occurrence_identity_reuse_count == 0
        and episode_identity_reuse_count == 0
        and source_revision_durable_record_count == expected_source_revision_durable_record_count
        and target_revision_durable_record_count == expected_target_revision_durable_record_count
        and len(records) == expected_durable_record_count
        and groups_with_7_records == expected_groups_with_7_records
        and groups_with_9_records == expected_groups_with_9_records
        and target_state_basis_snapshot_count == scenario.priority_group_count
        and final_alarm_count == expected_final_alarm_count
        and final_assignment_count == expected_final_assignment_count
        and final_pending_assignment_count == 0
        and open_occurrence_count == expected_open_occurrence_count
        and open_episode_count == expected_open_episode_count
        and functional_pressure is not None
        and functional_pressure.functional_integrity_ok
    )

    return LeaseLossAdoptionPressureMetrics(
        adoption_at_seconds=scenario.lease_loss_adoption_at_seconds,
        structural_reset_alarm_percent=5,
        source_revision=source_revision,
        target_revision=target_revision_key,
        plan_change_count=len(plan.changes),
        unchanged_change_count=disposition_counts[ConfigurationAdoptionDisposition.UNCHANGED],
        structural_reset_change_count=disposition_counts[
            ConfigurationAdoptionDisposition.STRUCTURAL_RESET
        ],
        expected_structural_reset_change_count=expected_structural_reset_change_count,
        structural_reset_group_count=len(reset_groups),
        expected_structural_reset_group_count=expected_structural_reset_group_count,
        preflight_status=preflight_status,
        owner_a_generation=int(owner_a_state.get('lease_generation', 0)) or None,
        owner_b_generation=int(owner_b_state.get('lease_generation', 0)) or None,
        owner_a_failed_iteration=int(owner_a_state.get('failed_iteration', 0)) or None,
        expected_owner_a_failed_iteration=expected_owner_a_failed_iteration,
        owner_b_first_iteration=int(owner_b_state.get('first_iteration', 0)) or None,
        expected_owner_b_first_iteration=expected_owner_b_first_iteration,
        owner_a_failed_iteration_ms=(
            None
            if owner_a_state.get('failed_iteration_ms') is None
            else float(owner_a_state['failed_iteration_ms'])
        ),
        owner_a_lease_loss_observed=bool(owner_a_state.get('lease_loss_observed')),
        owner_a_journal_aligned_before_loss=bool(owner_a_state.get('journal_aligned_before_loss')),
        owner_a_cache_alarm_revision_before_loss=owner_a_state.get(
            'cache_alarm_revision_before_loss'
        ),
        owner_a_cache_replace_count=owner_a_cache_replace_count,
        owner_a_post_adoption_cache_replace_count=owner_a_post_adoption_cache_replace_count,
        owner_a_adoption_commit_count=len(owner_a_adoption_commit_ids),
        expected_owner_a_adoption_commit_count=expected_structural_reset_group_count,
        owner_b_recovery_applied_count=int(owner_b_recovery.get('applied_count', 0)),
        owner_b_recovery_skipped_count=int(owner_b_recovery.get('skipped_count', 0)),
        owner_b_recovery_discarded_tail_bytes=int(owner_b_recovery.get('discarded_tail_bytes', 0)),
        owner_b_first_revision_origin=owner_b_state.get('first_revision_origin'),
        owner_b_first_adoption_outcome=owner_b_state.get('first_adoption_outcome'),
        owner_b_first_cycle_executed=bool(owner_b_state.get('first_cycle_executed')),
        owner_b_first_degraded=bool(owner_b_state.get('first_degraded')),
        owner_b_post_replay_cache_current_count=owner_b_post_replay_cache_current_count,
        expected_owner_b_post_replay_cache_current_count=(
            expected_owner_b_post_replay_cache_current_count
        ),
        owner_b_replay_adoption_commit_count=owner_b_replay_adoption_commit_count,
        owner_b_cache_replace_count=int(owner_b_state.get('cache_replace_count', 0)),
        stale_owner_cache_write_count=stale_owner_cache_write_count,
        final_cache_alarm_revision=owner_b_state.get('final_cache_alarm_revision'),
        final_cache_tool_revision=owner_b_state.get('final_cache_tool_revision'),
        configuration_reconfigured_occurrence_count=(configuration_reconfigured_occurrence_count),
        expected_configuration_reconfigured_occurrence_count=active_reset_alarm_count,
        configuration_terminated_episode_count=configuration_terminated_episode_count,
        expected_configuration_terminated_episode_count=expected_structural_reset_group_count,
        restarted_occurrence_count=restarted_occurrence_count,
        expected_restarted_occurrence_count=active_reset_alarm_count,
        restarted_episode_count=restarted_episode_count,
        expected_restarted_episode_count=expected_structural_reset_group_count,
        occurrence_identity_reuse_count=occurrence_identity_reuse_count,
        episode_identity_reuse_count=episode_identity_reuse_count,
        source_revision_durable_record_count=source_revision_durable_record_count,
        expected_source_revision_durable_record_count=(
            expected_source_revision_durable_record_count
        ),
        target_revision_durable_record_count=target_revision_durable_record_count,
        expected_target_revision_durable_record_count=(
            expected_target_revision_durable_record_count
        ),
        durable_record_count=len(records),
        expected_durable_record_count=expected_durable_record_count,
        groups_with_7_records=groups_with_7_records,
        expected_groups_with_7_records=expected_groups_with_7_records,
        groups_with_9_records=groups_with_9_records,
        expected_groups_with_9_records=expected_groups_with_9_records,
        target_state_basis_snapshot_count=target_state_basis_snapshot_count,
        expected_target_state_basis_snapshot_count=scenario.priority_group_count,
        final_alarm_count=final_alarm_count,
        final_assignment_count=final_assignment_count,
        final_pending_assignment_count=final_pending_assignment_count,
        open_occurrence_count=open_occurrence_count,
        open_episode_count=open_episode_count,
        functional_integrity_ok=functional_integrity_ok,
    )


def _build_invalid_source_candidate_pressure_metrics(
    *,
    scenario: BaselineScenario,
    runtime,
    records,
    snapshots,
    samples,
) -> InvalidSourceCandidatePressureMetrics | None:
    if not scenario.has_invalid_source_candidate_pressure:
        return None
    source = runtime.invalid_candidate_revision_source
    decoder = runtime.invalid_candidate_revision_decoder
    cache = runtime.tracked_revision_cache
    if source is None or decoder is None or cache is None:
        raise RuntimeError('invalid candidate pressure runtime instrumentation is missing')

    expected_first_fallback_iteration = (
        int(scenario.invalid_candidate_at_seconds / scenario.iteration_period_seconds) + 1
    )
    fallback_samples = [
        sample
        for sample in samples
        if sample.revision_origin == 'cache_fallback' and sample.adoption_outcome == 'not_required'
    ]
    first_fallback_sample = fallback_samples[0] if fallback_samples else None
    expected_fallback_iteration_count = max(0, len(samples) - expected_first_fallback_iteration + 1)
    post_candidate_samples = [
        sample for sample in samples if sample.iteration >= expected_first_fallback_iteration
    ]
    post_candidate_non_fallback_iteration_count = sum(
        not (
            sample.revision_origin == 'cache_fallback'
            and sample.adoption_outcome == 'not_required'
            and sample.cycle_executed
            and sample.degraded
        )
        for sample in post_candidate_samples
    )
    post_candidate_source_candidate_iteration_count = sum(
        sample.revision_origin == 'source_candidate' for sample in post_candidate_samples
    )
    post_candidate_rejected_iteration_count = sum(
        sample.adoption_outcome == 'rejected' for sample in post_candidate_samples
    )
    degraded_fallback_iteration_count = sum(sample.degraded for sample in fallback_samples)
    fallback_cycle_executed_count = sum(sample.cycle_executed for sample in fallback_samples)
    not_required_fallback_iteration_count = sum(
        sample.adoption_outcome == 'not_required' for sample in fallback_samples
    )

    cache_bundle = cache.load_effective()
    effective_cache_alarm_revision = (
        None if cache_bundle is None else cache_bundle.manifest.alarm_configuration_revision
    )
    effective_cache_tool_revision = (
        None if cache_bundle is None else cache_bundle.manifest.tool_registry_revision
    )
    candidate_monotonic = source.started_monotonic + scenario.invalid_candidate_at_seconds
    post_candidate_cache_replace_count = sum(
        replaced_at >= candidate_monotonic for replaced_at in cache.replace_monotonic
    )

    source_alarm_revision = runtime.revision.alarm_configuration_revision
    source_tool_revision = runtime.revision.tool_registry_revision
    candidate_alarm_revision = source.candidate_alarm_revision
    candidate_tool_revision = source_tool_revision
    source_revision_durable_record_count = sum(
        entry.record.commit.alarm_configuration_revision == source_alarm_revision
        and entry.record.commit.tool_registry_revision == source_tool_revision
        for entry in records
    )
    candidate_revision_durable_record_count = sum(
        entry.record.commit.alarm_configuration_revision == candidate_alarm_revision
        and entry.record.commit.tool_registry_revision == candidate_tool_revision
        for entry in records
    )
    unexpected_revision_durable_record_count = (
        len(records)
        - source_revision_durable_record_count
        - candidate_revision_durable_record_count
    )
    source_state_basis_snapshot_count = 0
    candidate_state_basis_snapshot_count = 0
    unexpected_state_basis_snapshot_count = 0
    for snapshot in snapshots:
        basis = snapshot.as_document()['state_basis']
        revision_key = (
            basis['alarm_configuration_revision'],
            basis['tool_registry_revision'],
        )
        if revision_key == (source_alarm_revision, source_tool_revision):
            source_state_basis_snapshot_count += 1
        elif revision_key == (candidate_alarm_revision, candidate_tool_revision):
            candidate_state_basis_snapshot_count += 1
        else:
            unexpected_state_basis_snapshot_count += 1

    functional_integrity_ok = (
        first_fallback_sample is not None
        and first_fallback_sample.iteration == expected_first_fallback_iteration
        and len(fallback_samples) == expected_fallback_iteration_count
        and degraded_fallback_iteration_count == len(fallback_samples)
        and fallback_cycle_executed_count == len(fallback_samples)
        and not_required_fallback_iteration_count == len(fallback_samples)
        and post_candidate_non_fallback_iteration_count == 0
        and post_candidate_source_candidate_iteration_count == 0
        and post_candidate_rejected_iteration_count == 0
        and source.manifest_success_count == len(samples)
        and source.manifest_failure_count == 0
        and source.candidate_manifest_count == len(fallback_samples)
        and source.candidate_alarm_document_read_count == len(fallback_samples)
        and source.candidate_tool_document_read_count == len(fallback_samples)
        and decoder.contract_failure_count == len(fallback_samples)
        and len(cache.replace_monotonic) == 1
        and post_candidate_cache_replace_count == 0
        and effective_cache_alarm_revision == source_alarm_revision
        and effective_cache_tool_revision == source_tool_revision
        and source_revision_durable_record_count == len(records)
        and candidate_revision_durable_record_count == 0
        and unexpected_revision_durable_record_count == 0
        and source_state_basis_snapshot_count == len(snapshots)
        and candidate_state_basis_snapshot_count == 0
        and unexpected_state_basis_snapshot_count == 0
    )

    return InvalidSourceCandidatePressureMetrics(
        invalid_at_seconds=scenario.invalid_candidate_at_seconds,
        source_alarm_revision=source_alarm_revision,
        source_tool_revision=source_tool_revision,
        candidate_alarm_revision=candidate_alarm_revision,
        candidate_tool_revision=candidate_tool_revision,
        invalid_alarm_document_revision=source.invalid_alarm_document_revision,
        first_fallback_iteration=(
            None if first_fallback_sample is None else first_fallback_sample.iteration
        ),
        expected_first_fallback_iteration=expected_first_fallback_iteration,
        fallback_iteration_count=len(fallback_samples),
        expected_fallback_iteration_count=expected_fallback_iteration_count,
        degraded_fallback_iteration_count=degraded_fallback_iteration_count,
        fallback_cycle_executed_count=fallback_cycle_executed_count,
        not_required_fallback_iteration_count=not_required_fallback_iteration_count,
        post_candidate_non_fallback_iteration_count=post_candidate_non_fallback_iteration_count,
        post_candidate_source_candidate_iteration_count=(
            post_candidate_source_candidate_iteration_count
        ),
        post_candidate_rejected_iteration_count=post_candidate_rejected_iteration_count,
        manifest_success_count=source.manifest_success_count,
        manifest_failure_count=source.manifest_failure_count,
        candidate_manifest_count=source.candidate_manifest_count,
        candidate_alarm_document_read_count=source.candidate_alarm_document_read_count,
        candidate_tool_document_read_count=source.candidate_tool_document_read_count,
        candidate_contract_failure_count=decoder.contract_failure_count,
        cache_replace_count=len(cache.replace_monotonic),
        post_candidate_cache_replace_count=post_candidate_cache_replace_count,
        effective_cache_alarm_revision=effective_cache_alarm_revision,
        effective_cache_tool_revision=effective_cache_tool_revision,
        source_revision_durable_record_count=source_revision_durable_record_count,
        candidate_revision_durable_record_count=candidate_revision_durable_record_count,
        unexpected_revision_durable_record_count=unexpected_revision_durable_record_count,
        source_state_basis_snapshot_count=source_state_basis_snapshot_count,
        candidate_state_basis_snapshot_count=candidate_state_basis_snapshot_count,
        unexpected_state_basis_snapshot_count=unexpected_state_basis_snapshot_count,
        functional_integrity_ok=functional_integrity_ok,
    )


def _build_mixed_revision_adoption_pressure_metrics(
    *,
    scenario: BaselineScenario,
    runtime,
    records,
    snapshots,
    samples,
) -> MixedRevisionAdoptionPressureMetrics | None:
    if not scenario.has_mixed_revision_adoption_pressure:
        return None
    target_revision = runtime.target_revision
    if target_revision is None:
        raise RuntimeError('mixed revision adoption runtime requires a target revision')
    if scenario.mixed_revision_target_threshold is None:
        raise RuntimeError('mixed revision adoption metrics require target threshold')

    plan = plan_configuration_adoption(runtime.revision, target_revision)
    disposition_counts = {item: 0 for item in ConfigurationAdoptionDisposition}
    for change in plan.changes:
        disposition_counts[change.disposition] += 1

    adoption_samples = [
        sample
        for sample in samples
        if sample.revision_origin == 'source_candidate' and sample.adoption_outcome == 'adopted'
    ]
    adoption_sample = adoption_samples[0] if len(adoption_samples) == 1 else None
    immediate_next_sample = None
    if adoption_sample is not None:
        immediate_next_sample = next(
            (sample for sample in samples if sample.iteration == adoption_sample.iteration + 1),
            None,
        )

    cache_bundle = runtime.job.revision_resolver.cache.load_effective()
    effective_cache_revision = (
        None if cache_bundle is None else cache_bundle.manifest.alarm_configuration_revision
    )
    source_revision = runtime.revision.alarm_configuration_revision
    target_revision_key = target_revision.alarm_configuration_revision
    source_revision_durable_record_count = sum(
        entry.record.commit.alarm_configuration_revision == source_revision for entry in records
    )
    target_revision_durable_record_count = sum(
        entry.record.commit.alarm_configuration_revision == target_revision_key for entry in records
    )

    reset_alarm_keys = {
        change.identity.canonical_key
        for change in plan.changes
        if change.disposition is ConfigurationAdoptionDisposition.STRUCTURAL_RESET
    }
    disabled_alarm_keys = {
        change.identity.canonical_key
        for change in plan.changes
        if change.disposition is ConfigurationAdoptionDisposition.DISABLED
    }
    removed_alarm_keys = {
        change.identity.canonical_key
        for change in plan.changes
        if change.disposition is ConfigurationAdoptionDisposition.REMOVED
    }
    reset_groups = set(plan.structural_reset_groups)

    def alarm_group(alarm_key: str) -> str:
        alarm_number = int(alarm_key.rsplit('_', 1)[1])
        group_number = (alarm_number - 1) // scenario.effective_priority_group_size + 1
        return f'perf-group-{group_number:03d}'

    disabled_groups = {alarm_group(key) for key in disabled_alarm_keys}
    removed_groups = {alarm_group(key) for key in removed_alarm_keys}
    touched_groups = reset_groups | disabled_groups | removed_groups
    disabled_removed_overlap_group_count = len(disabled_groups & removed_groups)

    initial_occurrence_ids: dict[str, str] = {}
    initial_episode_ids: dict[str, str] = {}
    adoption_commit_count = 0
    next_cycle_commit_count = 0
    configuration_reconfigured_occurrence_count = 0
    configuration_disabled_occurrence_count = 0
    configuration_removed_occurrence_count = 0
    configuration_terminated_episode_count = 0
    restarted_occurrence_count = 0
    restarted_episode_count = 0
    occurrence_identity_reuse_count = 0
    episode_identity_reuse_count = 0
    records_per_group: dict[str, int] = {}

    for entry in records:
        priority_group = entry.record.commit.priority_group
        records_per_group[priority_group] = records_per_group.get(priority_group, 0) + 1
        payload = entry.record.records
        occurrence_changes = payload.get('occurrence_changes', [])
        episode_changes = payload.get('episode_changes', [])
        is_target_revision = entry.record.commit.alarm_configuration_revision == target_revision_key
        has_adoption_change = False
        has_restart = False

        for change in occurrence_changes:
            alarm_key = str(change.get('alarm_key'))
            kind = change.get('kind')
            if kind == 'STARTED':
                occurrence_id = str(change.get('occurrence_id'))
                if not is_target_revision:
                    initial_occurrence_ids[alarm_key] = occurrence_id
                elif alarm_key in reset_alarm_keys:
                    restarted_occurrence_count += 1
                    has_restart = True
                    if initial_occurrence_ids.get(alarm_key) == occurrence_id:
                        occurrence_identity_reuse_count += 1
                continue
            if kind != 'CLOSED':
                continue
            closure_reason = str(change.get('closure_reason')).strip().lower()
            expected_occurrence_id = initial_occurrence_ids.get(alarm_key)
            if (
                expected_occurrence_id is not None
                and change.get('occurrence_id') != expected_occurrence_id
            ):
                occurrence_identity_reuse_count += 1
            if closure_reason == 'configuration_reconfigured' and alarm_key in reset_alarm_keys:
                configuration_reconfigured_occurrence_count += 1
                has_adoption_change = True
            elif closure_reason == 'configuration_disabled' and alarm_key in disabled_alarm_keys:
                configuration_disabled_occurrence_count += 1
                has_adoption_change = True
            elif closure_reason == 'configuration_removed' and alarm_key in removed_alarm_keys:
                configuration_removed_occurrence_count += 1
                has_adoption_change = True

        for change in episode_changes:
            kind = change.get('kind')
            if kind == 'STARTED':
                episode_id = str(change.get('episode_id'))
                if not is_target_revision:
                    initial_episode_ids[priority_group] = episode_id
                elif priority_group in reset_groups:
                    restarted_episode_count += 1
                    has_restart = True
                    if initial_episode_ids.get(priority_group) == episode_id:
                        episode_identity_reuse_count += 1
            elif (
                kind == 'CLOSED'
                and str(change.get('closure_reason')).strip().lower() == 'configuration_terminated'
                and priority_group in reset_groups
            ):
                configuration_terminated_episode_count += 1
                has_adoption_change = True

        if is_target_revision and has_adoption_change:
            adoption_commit_count += 1
        if is_target_revision and has_restart:
            next_cycle_commit_count += 1

    source_state_basis_snapshot_count = 0
    target_state_basis_snapshot_count = 0
    final_alarm_count = 0
    final_assignment_count = 0
    open_occurrence_count = 0
    open_episode_count = 0
    for snapshot in snapshots:
        document = snapshot.as_document()
        basis = document.get('state_basis')
        if isinstance(basis, dict):
            revision = basis.get('alarm_configuration_revision')
            if revision == source_revision:
                source_state_basis_snapshot_count += 1
            if revision == target_revision_key:
                target_state_basis_snapshot_count += 1
        if document.get('episode') is not None:
            open_episode_count += 1
        alarms = document['alarms']
        final_alarm_count += len(alarms)
        for alarm in alarms.values():
            occurrence = alarm.get('occurrence')
            if occurrence is None:
                continue
            open_occurrence_count += 1
            final_assignment_count += len(occurrence.get('assignments', []))

    expected_compatible_change_count = scenario.mixed_revision_compatible_alarm_count
    expected_disabled_change_count = scenario.mixed_revision_disabled_alarm_count
    expected_removed_change_count = scenario.mixed_revision_removed_alarm_count
    expected_structural_reset_change_count = scenario.mixed_revision_structural_reset_alarm_count
    expected_structural_reset_group_count = (
        scenario.mixed_revision_structural_reset_priority_group_count
    )
    expected_target_defined_alarm_count = scenario.alarm_count - expected_removed_change_count
    expected_target_runtime_alarm_count = (
        scenario.alarm_count - expected_disabled_change_count - expected_removed_change_count
    )
    expected_touched_priority_group_count = scenario.priority_group_count
    expected_overlap_group_count = scenario.mixed_revision_disabled_removed_overlap_group_count
    expected_adoption_commit_count = scenario.priority_group_count
    expected_next_cycle_commit_count = expected_structural_reset_group_count
    expected_source_revision_durable_record_count = scenario.priority_group_count
    expected_target_revision_durable_record_count = (
        expected_adoption_commit_count + expected_next_cycle_commit_count
    )
    expected_durable_record_count = (
        expected_source_revision_durable_record_count
        + expected_target_revision_durable_record_count
    )
    expected_groups_with_three_records = expected_structural_reset_group_count
    expected_groups_with_two_records = (
        scenario.priority_group_count - expected_groups_with_three_records
    )
    groups_with_two_records = sum(count == 2 for count in records_per_group.values())
    groups_with_three_records = sum(count == 3 for count in records_per_group.values())

    functional_integrity_ok = (
        plan.is_adoptable
        and len(plan.changes) == scenario.alarm_count
        and disposition_counts[ConfigurationAdoptionDisposition.COMPATIBLE]
        == expected_compatible_change_count
        and disposition_counts[ConfigurationAdoptionDisposition.UNCHANGED] == 0
        and disposition_counts[ConfigurationAdoptionDisposition.STRUCTURAL_RESET]
        == expected_structural_reset_change_count
        and disposition_counts[ConfigurationAdoptionDisposition.DISABLED]
        == expected_disabled_change_count
        and disposition_counts[ConfigurationAdoptionDisposition.REMOVED]
        == expected_removed_change_count
        and disposition_counts[ConfigurationAdoptionDisposition.REJECTED] == 0
        and len(reset_groups) == expected_structural_reset_group_count
        and len(touched_groups) == expected_touched_priority_group_count
        and disabled_removed_overlap_group_count == expected_overlap_group_count
        and len(target_revision.defined_alarm_identities) == expected_target_defined_alarm_count
        and len(target_revision.session.identities) == expected_target_runtime_alarm_count
        and {float(entry.parameters['threshold']) for entry in target_revision.session.entries}
        == {float(scenario.mixed_revision_target_threshold)}
        and effective_cache_revision == target_revision_key
        and len(adoption_samples) == 1
        and adoption_sample is not None
        and not adoption_sample.cycle_executed
        and immediate_next_sample is not None
        and immediate_next_sample.revision_origin == 'cache_current'
        and immediate_next_sample.adoption_outcome == 'not_required'
        and immediate_next_sample.cycle_executed
        and adoption_commit_count == expected_adoption_commit_count
        and next_cycle_commit_count == expected_next_cycle_commit_count
        and configuration_reconfigured_occurrence_count == expected_structural_reset_change_count
        and configuration_disabled_occurrence_count == expected_disabled_change_count
        and configuration_removed_occurrence_count == expected_removed_change_count
        and configuration_terminated_episode_count == expected_structural_reset_group_count
        and restarted_occurrence_count == expected_structural_reset_change_count
        and restarted_episode_count == expected_structural_reset_group_count
        and occurrence_identity_reuse_count == 0
        and episode_identity_reuse_count == 0
        and source_revision_durable_record_count == expected_source_revision_durable_record_count
        and target_revision_durable_record_count == expected_target_revision_durable_record_count
        and len(records) == expected_durable_record_count
        and groups_with_two_records == expected_groups_with_two_records
        and groups_with_three_records == expected_groups_with_three_records
        and source_state_basis_snapshot_count == 0
        and target_state_basis_snapshot_count == scenario.priority_group_count
        and final_alarm_count == expected_target_runtime_alarm_count
        and final_assignment_count == expected_target_runtime_alarm_count
        and open_occurrence_count == expected_target_runtime_alarm_count
        and open_episode_count == scenario.priority_group_count
    )

    return MixedRevisionAdoptionPressureMetrics(
        adoption_at_seconds=scenario.mixed_revision_adoption_at_seconds,
        target_threshold=float(scenario.mixed_revision_target_threshold),
        disabled_alarm_percent=scenario.mixed_revision_disabled_alarm_percent,
        removed_alarm_percent=scenario.mixed_revision_removed_alarm_percent,
        structural_reset_alarm_percent=scenario.mixed_revision_structural_reset_alarm_percent,
        source_revision=source_revision,
        target_revision=target_revision_key,
        plan_change_count=len(plan.changes),
        compatible_change_count=disposition_counts[ConfigurationAdoptionDisposition.COMPATIBLE],
        expected_compatible_change_count=expected_compatible_change_count,
        unchanged_change_count=disposition_counts[ConfigurationAdoptionDisposition.UNCHANGED],
        structural_reset_change_count=disposition_counts[
            ConfigurationAdoptionDisposition.STRUCTURAL_RESET
        ],
        expected_structural_reset_change_count=expected_structural_reset_change_count,
        structural_reset_group_count=len(reset_groups),
        expected_structural_reset_group_count=expected_structural_reset_group_count,
        disabled_change_count=disposition_counts[ConfigurationAdoptionDisposition.DISABLED],
        expected_disabled_change_count=expected_disabled_change_count,
        removed_change_count=disposition_counts[ConfigurationAdoptionDisposition.REMOVED],
        expected_removed_change_count=expected_removed_change_count,
        rejected_change_count=disposition_counts[ConfigurationAdoptionDisposition.REJECTED],
        target_defined_alarm_count=len(target_revision.defined_alarm_identities),
        expected_target_defined_alarm_count=expected_target_defined_alarm_count,
        target_runtime_alarm_count=len(target_revision.session.identities),
        expected_target_runtime_alarm_count=expected_target_runtime_alarm_count,
        touched_priority_group_count=len(touched_groups),
        expected_touched_priority_group_count=expected_touched_priority_group_count,
        disabled_removed_overlap_group_count=disabled_removed_overlap_group_count,
        expected_disabled_removed_overlap_group_count=expected_overlap_group_count,
        effective_cache_revision=effective_cache_revision,
        adoption_iteration_count=len(adoption_samples),
        adoption_iteration=None if adoption_sample is None else adoption_sample.iteration,
        adoption_iteration_ms=None if adoption_sample is None else adoption_sample.duration_ms,
        adoption_iteration_cpu_percent=(
            None if adoption_sample is None else adoption_sample.cpu_percent
        ),
        adoption_cycle_executed=(
            False if adoption_sample is None else adoption_sample.cycle_executed
        ),
        immediate_next_iteration=(
            None if immediate_next_sample is None else immediate_next_sample.iteration
        ),
        immediate_next_iteration_cycle_executed=(
            False if immediate_next_sample is None else immediate_next_sample.cycle_executed
        ),
        immediate_next_iteration_cache_current=(
            False
            if immediate_next_sample is None
            else immediate_next_sample.revision_origin == 'cache_current'
            and immediate_next_sample.adoption_outcome == 'not_required'
        ),
        immediate_next_start_interval_ms=(
            None if immediate_next_sample is None else immediate_next_sample.start_interval_ms
        ),
        immediate_next_duration_ms=(
            None if immediate_next_sample is None else immediate_next_sample.duration_ms
        ),
        adoption_commit_count=adoption_commit_count,
        expected_adoption_commit_count=expected_adoption_commit_count,
        next_cycle_commit_count=next_cycle_commit_count,
        expected_next_cycle_commit_count=expected_next_cycle_commit_count,
        configuration_reconfigured_occurrence_count=configuration_reconfigured_occurrence_count,
        expected_configuration_reconfigured_occurrence_count=expected_structural_reset_change_count,
        configuration_disabled_occurrence_count=configuration_disabled_occurrence_count,
        expected_configuration_disabled_occurrence_count=expected_disabled_change_count,
        configuration_removed_occurrence_count=configuration_removed_occurrence_count,
        expected_configuration_removed_occurrence_count=expected_removed_change_count,
        configuration_terminated_episode_count=configuration_terminated_episode_count,
        expected_configuration_terminated_episode_count=expected_structural_reset_group_count,
        restarted_occurrence_count=restarted_occurrence_count,
        expected_restarted_occurrence_count=expected_structural_reset_change_count,
        restarted_episode_count=restarted_episode_count,
        expected_restarted_episode_count=expected_structural_reset_group_count,
        occurrence_identity_reuse_count=occurrence_identity_reuse_count,
        episode_identity_reuse_count=episode_identity_reuse_count,
        source_revision_durable_record_count=source_revision_durable_record_count,
        expected_source_revision_durable_record_count=expected_source_revision_durable_record_count,
        target_revision_durable_record_count=target_revision_durable_record_count,
        expected_target_revision_durable_record_count=expected_target_revision_durable_record_count,
        durable_record_count=len(records),
        expected_durable_record_count=expected_durable_record_count,
        groups_with_two_records=groups_with_two_records,
        expected_groups_with_two_records=expected_groups_with_two_records,
        groups_with_three_records=groups_with_three_records,
        expected_groups_with_three_records=expected_groups_with_three_records,
        source_state_basis_snapshot_count=source_state_basis_snapshot_count,
        expected_source_state_basis_snapshot_count=0,
        target_state_basis_snapshot_count=target_state_basis_snapshot_count,
        expected_target_state_basis_snapshot_count=scenario.priority_group_count,
        final_alarm_count=final_alarm_count,
        expected_final_alarm_count=expected_target_runtime_alarm_count,
        final_assignment_count=final_assignment_count,
        expected_final_assignment_count=expected_target_runtime_alarm_count,
        open_occurrence_count=open_occurrence_count,
        expected_open_occurrence_count=expected_target_runtime_alarm_count,
        open_episode_count=open_episode_count,
        expected_open_episode_count=scenario.priority_group_count,
        functional_integrity_ok=functional_integrity_ok,
    )


def _build_parameter_adoption_pressure_metrics(
    *,
    scenario: BaselineScenario,
    runtime,
    records,
    snapshots,
    samples,
) -> ParameterAdoptionPressureMetrics | None:
    if not scenario.has_parameter_adoption_pressure:
        return None
    target_revision = runtime.target_revision
    if target_revision is None or scenario.parameter_target_threshold is None:
        raise RuntimeError('parameter adoption runtime requires a target revision')
    plan = plan_configuration_adoption(runtime.revision, target_revision)
    disposition_counts = {item: 0 for item in ConfigurationAdoptionDisposition}
    for change in plan.changes:
        disposition_counts[change.disposition] += 1

    adoption_samples = [
        sample
        for sample in samples
        if sample.revision_origin == 'source_candidate' and sample.adoption_outcome == 'adopted'
    ]
    adoption_sample = adoption_samples[0] if len(adoption_samples) == 1 else None
    post_adoption_cache_current_iteration_count = 0
    if adoption_sample is not None:
        post_adoption_cache_current_iteration_count = sum(
            sample.iteration > adoption_sample.iteration
            and sample.revision_origin == 'cache_current'
            and sample.adoption_outcome == 'not_required'
            for sample in samples
        )

    cache_bundle = runtime.job.revision_resolver.cache.load_effective()
    effective_cache_revision = (
        None if cache_bundle is None else cache_bundle.manifest.alarm_configuration_revision
    )
    source_revision = runtime.revision.alarm_configuration_revision
    target_revision_key = target_revision.alarm_configuration_revision
    source_revision_durable_record_count = sum(
        entry.record.commit.alarm_configuration_revision == source_revision for entry in records
    )
    target_revision_durable_record_count = sum(
        entry.record.commit.alarm_configuration_revision == target_revision_key for entry in records
    )
    source_state_basis_snapshot_count = 0
    target_state_basis_snapshot_count = 0
    open_occurrence_count = 0
    for snapshot in snapshots:
        document = snapshot.as_document()
        basis = document.get('state_basis')
        if isinstance(basis, dict):
            if basis.get('alarm_configuration_revision') == source_revision:
                source_state_basis_snapshot_count += 1
            if basis.get('alarm_configuration_revision') == target_revision_key:
                target_state_basis_snapshot_count += 1
        open_occurrence_count += sum(
            alarm.get('occurrence') is not None for alarm in document['alarms'].values()
        )
    target_threshold = float(scenario.parameter_target_threshold)
    target_threshold_alarm_count = sum(
        float(entry.parameters['threshold']) == target_threshold
        for entry in target_revision.session.entries
    )
    # En F-010 la adopción convive con commits de Management/Decisions, por lo que no reutilizamos la geometría durable exclusiva de E-001.
    # La qualification conserva las invariantes semánticas, pero admite evidencia durable posterior a la adopción.
    integrated_f010 = scenario.test_id == 'F-010'
    expected_durable_record_count = (
        len(records) if integrated_f010 else scenario.priority_group_count
    )
    expected_runtime_alarm_count = scenario.alarm_count
    adoption_integrity_ok = (
        plan.is_adoptable
        and len(plan.changes) == scenario.alarm_count
        and disposition_counts[ConfigurationAdoptionDisposition.COMPATIBLE] == scenario.alarm_count
        and disposition_counts[ConfigurationAdoptionDisposition.UNCHANGED] == 0
        and disposition_counts[ConfigurationAdoptionDisposition.STRUCTURAL_RESET] == 0
        and disposition_counts[ConfigurationAdoptionDisposition.DISABLED] == 0
        and disposition_counts[ConfigurationAdoptionDisposition.REMOVED] == 0
        and disposition_counts[ConfigurationAdoptionDisposition.REJECTED] == 0
        and len(target_revision.session.identities) == expected_runtime_alarm_count
        and target_threshold_alarm_count == expected_runtime_alarm_count
        and effective_cache_revision == target_revision_key
        and len(adoption_samples) == 1
        and adoption_sample is not None
        and adoption_sample.cycle_executed
        and post_adoption_cache_current_iteration_count > 0
        and open_occurrence_count == scenario.alarm_count
    )
    if integrated_f010:
        functional_integrity_ok = (
            adoption_integrity_ok
            and source_revision_durable_record_count > 0
            and target_revision_durable_record_count > 0
            and target_state_basis_snapshot_count > 0
        )
    else:
        functional_integrity_ok = (
            adoption_integrity_ok
            and source_revision_durable_record_count == expected_durable_record_count
            and target_revision_durable_record_count == 0
            and len(records) == expected_durable_record_count
            and source_state_basis_snapshot_count == scenario.priority_group_count
            and target_state_basis_snapshot_count == 0
        )
    return ParameterAdoptionPressureMetrics(
        adoption_at_seconds=scenario.parameter_adoption_at_seconds,
        source_revision=source_revision,
        target_revision=target_revision_key,
        source_threshold=float(scenario.threshold),
        target_threshold=target_threshold,
        plan_change_count=len(plan.changes),
        compatible_change_count=disposition_counts[ConfigurationAdoptionDisposition.COMPATIBLE],
        unchanged_change_count=disposition_counts[ConfigurationAdoptionDisposition.UNCHANGED],
        structural_reset_change_count=disposition_counts[
            ConfigurationAdoptionDisposition.STRUCTURAL_RESET
        ],
        disabled_change_count=disposition_counts[ConfigurationAdoptionDisposition.DISABLED],
        removed_change_count=disposition_counts[ConfigurationAdoptionDisposition.REMOVED],
        rejected_change_count=disposition_counts[ConfigurationAdoptionDisposition.REJECTED],
        target_runtime_alarm_count=len(target_revision.session.identities),
        expected_target_runtime_alarm_count=expected_runtime_alarm_count,
        target_threshold_alarm_count=target_threshold_alarm_count,
        effective_cache_revision=effective_cache_revision,
        adoption_iteration_count=len(adoption_samples),
        adoption_iteration=None if adoption_sample is None else adoption_sample.iteration,
        adoption_iteration_ms=None if adoption_sample is None else adoption_sample.duration_ms,
        adoption_iteration_cpu_percent=(
            None if adoption_sample is None else adoption_sample.cpu_percent
        ),
        adoption_cycle_executed=(
            False if adoption_sample is None else adoption_sample.cycle_executed
        ),
        post_adoption_cache_current_iteration_count=post_adoption_cache_current_iteration_count,
        source_revision_durable_record_count=source_revision_durable_record_count,
        target_revision_durable_record_count=target_revision_durable_record_count,
        durable_record_count=len(records),
        expected_durable_record_count=expected_durable_record_count,
        source_state_basis_snapshot_count=source_state_basis_snapshot_count,
        target_state_basis_snapshot_count=target_state_basis_snapshot_count,
        open_occurrence_count=open_occurrence_count,
        expected_open_occurrence_count=scenario.alarm_count,
        functional_integrity_ok=functional_integrity_ok,
    )


def _build_stale_target_deactivation_decision_pressure_metrics(
    *,
    scenario: BaselineScenario,
    runtime,
    records,
    snapshots=(),
) -> StaleTargetDeactivationDecisionPressureMetrics:
    source = runtime.input_source
    consumer = runtime.input_consumer
    if not isinstance(source, StaleTargetDeactivationInputSource):
        raise RuntimeError('stale-target metrics require stale-target input source')
    if runtime.target_revision is None:
        raise RuntimeError('stale-target metrics require target revision')

    expected_count = source.input_count
    expected_input_ids = set(source.input_ids)
    expected_request_ids = set(source.request_ids)
    expected_decision_ids = set(source.decision_ids)
    expected_management_effect_ids = {f'PERF-ME-{input_id}' for input_id in expected_input_ids}
    expected_target_keys = {
        source.target_for_input(index)[0].canonical_key for index in range(expected_count)
    }

    request_receipts = []
    decision_receipts = []
    request_documents = []
    management_effects = []
    deactivation_effects = []
    removed_occurrences = []
    for entry in records:
        payload = entry.record.records
        for receipt in payload.get('input_receipts', []):
            input_kind = receipt.get('input_kind')
            input_id = receipt.get('input_id')
            if input_kind == 'DEACTIVATION_REQUEST' and input_id in expected_input_ids:
                request_receipts.append(receipt)
            elif input_kind == 'DEACTIVATION_DECISION' and input_id in expected_decision_ids:
                decision_receipts.append(receipt)
        request_documents.extend(
            item
            for item in payload.get('deactivation_requests', [])
            if item.get('request_id') in expected_request_ids
        )
        management_effects.extend(
            item
            for item in payload.get('management_effects', [])
            if item.get('effect_id') in expected_management_effect_ids
        )
        deactivation_effects.extend(
            item
            for item in payload.get('deactivation_effects', [])
            if item.get('alarm_key') in expected_target_keys
        )
        removed_occurrences.extend(
            item
            for item in payload.get('occurrence_changes', [])
            if item.get('alarm_key') in expected_target_keys
            and item.get('kind') == 'CLOSED'
            and str(item.get('closure_reason')).strip().lower() == 'configuration_removed'
        )

    request_receipt_ids = [str(item.get('input_id')) for item in request_receipts]
    decision_receipt_ids = [str(item.get('input_id')) for item in decision_receipts]
    request_receipt_set = set(request_receipt_ids)
    decision_receipt_set = set(decision_receipt_ids)
    lost_request_input_count = len(expected_input_ids - request_receipt_set)
    duplicate_request_receipt_count = len(request_receipt_ids) - len(request_receipt_set)
    lost_decision_input_count = len(expected_decision_ids - decision_receipt_set)
    duplicate_decision_receipt_count = len(decision_receipt_ids) - len(decision_receipt_set)

    pending_approval_receipt_count = sum(
        item.get('outcome') == 'PENDING_APPROVAL' for item in request_receipts
    )
    stale_target_receipt_count = sum(
        item.get('outcome') == 'STALE_TARGET' for item in decision_receipts
    )
    applied_decision_receipt_count = sum(
        item.get('outcome') == 'APPLIED' for item in decision_receipts
    )
    approval_required_request_count = sum(
        item.get('approval_required') is True for item in request_documents
    )
    management_effect_started_count = sum(
        item.get('kind') == 'STARTED' for item in management_effects
    )
    management_effect_cleared_count = sum(
        item.get('kind') == 'CLEARED' for item in management_effects
    )
    deactivation_effect_started_count = sum(
        item.get('kind') == 'STARTED' for item in deactivation_effects
    )
    deactivation_effect_cleared_count = sum(
        item.get('kind') == 'CLEARED' for item in deactivation_effects
    )

    requests_by_id: dict[str, list[dict[str, object]]] = {}
    for request in request_documents:
        requests_by_id.setdefault(str(request.get('request_id')), []).append(request)
    stale_events_by_decision: dict[str, list[tuple[dict[str, object], str | None]]] = {}
    for entry in records:
        payload = entry.record.records
        entry_decision_receipts = [
            item
            for item in payload.get('input_receipts', [])
            if item.get('input_kind') == 'DEACTIVATION_DECISION'
            and item.get('input_id') in expected_decision_ids
        ]
        entry_stale_events = [
            item
            for item in payload.get('journey_events', [])
            if item.get('alarm_key') in expected_target_keys
            and item.get('event_key') == 'deactivation_decision_stale_target'
        ]
        if len(entry_decision_receipts) == 1 and len(entry_stale_events) == 1:
            decision_id = str(entry_decision_receipts[0].get('input_id'))
            priority_group = getattr(entry.record.commit, 'priority_group', None)
            stale_events_by_decision.setdefault(decision_id, []).append(
                (entry_stale_events[0], priority_group)
            )

    wrong_decision_request_correlation_count = 0
    request_occurrence_identity_mismatch_count = 0
    stale_target_occurrence_mismatch_count = 0
    for index in range(expected_count):
        input_id = source.input_ids[index]
        request_id = source.request_ids[index]
        decision_id = source.decision_ids[index]
        identity, priority_group = source.target_for_input(index)
        alarm_key = identity.canonical_key
        expected_occurrence_id = source.target_occurrence_ids.get(alarm_key)
        request_candidates = requests_by_id.get(request_id, [])
        request = request_candidates[0] if len(request_candidates) == 1 else None
        expected_requested_at = source.request_created_at.isoformat().replace('+00:00', 'Z')
        expected_effective_until = source.effective_until.isoformat().replace('+00:00', 'Z')
        if (
            request is None
            or request.get('source_management_input_id') != input_id
            or request.get('alarm_key') != alarm_key
            or request.get('source_occurrence_id') != expected_occurrence_id
            or request.get('requested_at') != expected_requested_at
            or request.get('effective_until') != expected_effective_until
            or request.get('approval_required') is not True
        ):
            request_occurrence_identity_mismatch_count += 1
        events = stale_events_by_decision.get(decision_id, [])
        if len(events) != 1:
            wrong_decision_request_correlation_count += 1
        else:
            event, event_priority_group = events[0]
            if (
                event.get('alarm_key') != alarm_key
                or event.get('occurrence_id') != expected_occurrence_id
                or event_priority_group != priority_group
            ):
                stale_target_occurrence_mismatch_count += 1

    final_alarms: dict[str, dict[str, object]] = {}
    for snapshot in snapshots:
        for alarm_key, alarm in snapshot.as_document()['alarms'].items():
            if isinstance(alarm, dict):
                final_alarms[alarm_key] = alarm
    final_removed_target_present_count = sum(
        alarm_key in final_alarms for alarm_key in expected_target_keys
    )

    store = AtomicJsonStore(root_path=runtime.composition.durability.persistence.paths.alarms_root)
    final_state = store.read('runtime/state/consumers/management.json') or {}
    final_management = final_state.get('management') if isinstance(final_state, dict) else None
    final_decisions = final_state.get('decisions') if isinstance(final_state, dict) else None
    final_management_cursor = (
        final_management.get('cursor') if isinstance(final_management, dict) else None
    )
    final_decision_cursor = (
        final_decisions.get('cursor') if isinstance(final_decisions, dict) else None
    )
    final_management_pending = (
        final_management.get('pending') if isinstance(final_management, dict) else None
    )
    final_decision_pending = (
        final_decisions.get('pending') if isinstance(final_decisions, dict) else None
    )
    final_pending_request_ids = (
        final_state.get('pending_deactivation_request_ids')
        if isinstance(final_state, dict)
        else None
    )
    final_management_cursor_byte_offset = (
        final_management_cursor.get('byte_offset')
        if isinstance(final_management_cursor, dict)
        else None
    )
    final_decision_cursor_byte_offset = (
        final_decision_cursor.get('byte_offset')
        if isinstance(final_decision_cursor, dict)
        else None
    )
    final_management_pending_count = (
        len(final_management_pending) if isinstance(final_management_pending, list) else -1
    )
    final_decision_pending_count = (
        len(final_decision_pending) if isinstance(final_decision_pending, list) else -1
    )
    final_pending_request_count = (
        len(final_pending_request_ids) if isinstance(final_pending_request_ids, list) else -1
    )

    request_latencies = [
        (confirmed_at - source.management_visible_monotonic_by_input_id[input_id]) * 1000
        for input_id, confirmed_at in (
            consumer.stale_request_receipt_confirmed_monotonic_by_input_id.items()
        )
        if input_id in source.management_visible_monotonic_by_input_id
    ]
    decision_latencies = [
        (confirmed_at - source.decision_visible_monotonic_by_input_id[decision_id]) * 1000
        for decision_id, confirmed_at in (
            consumer.stale_decision_receipt_confirmed_monotonic_by_input_id.items()
        )
        if decision_id in source.decision_visible_monotonic_by_input_id
    ]
    request_receipt_batches = tuple(
        size for size in consumer.stale_request_receipt_batch_sizes if size > 0
    )
    decision_receipt_batches = tuple(
        size for size in consumer.stale_decision_receipt_batch_sizes if size > 0
    )
    expected_cursor_byte_offset = expected_count * source.byte_length
    expected_target_runtime_alarm_count = scenario.alarm_count - expected_count
    target_runtime_alarm_count = len(runtime.target_revision.session.entries)
    expected_durable_record_count = (
        scenario.priority_group_count
        + expected_count
        + scenario.priority_group_count
        + expected_count
    )
    remaining_window_seconds = (source.effective_until - source.decision_decided_at).total_seconds()

    functional_integrity_ok = (
        len(request_receipts) == expected_count
        and pending_approval_receipt_count == expected_count
        and len(request_documents) == expected_count
        and approval_required_request_count == expected_count
        and len(decision_receipts) == expected_count
        and stale_target_receipt_count == expected_count
        and applied_decision_receipt_count == 0
        and management_effect_started_count == expected_count
        and management_effect_cleared_count == expected_count
        and len(removed_occurrences) == expected_count
        and deactivation_effect_started_count == 0
        and deactivation_effect_cleared_count == 0
        and lost_request_input_count == 0
        and duplicate_request_receipt_count == 0
        and lost_decision_input_count == 0
        and duplicate_decision_receipt_count == 0
        and wrong_decision_request_correlation_count == 0
        and request_occurrence_identity_mismatch_count == 0
        and stale_target_occurrence_mismatch_count == 0
        and final_removed_target_present_count == 0
        and final_management_cursor_byte_offset == expected_cursor_byte_offset
        and final_decision_cursor_byte_offset == expected_cursor_byte_offset
        and final_management_pending_count == 0
        and final_decision_pending_count == 0
        and final_pending_request_count == 0
        and consumer.management_pending_high_water_count == 0
        and consumer.decision_pending_high_water_count == 0
        and sum(source.management_read_batch_sizes) == expected_count
        and sum(source.decision_read_batch_sizes) == expected_count
        and source.management_read_at_count == 0
        and source.decision_read_at_count == 0
        and request_receipt_batches == (expected_count,)
        and decision_receipt_batches == (expected_count,)
        and len(request_latencies) == expected_count
        and len(decision_latencies) == expected_count
        and all(value >= 0 for value in request_latencies)
        and all(value >= 0 for value in decision_latencies)
        and target_runtime_alarm_count == expected_target_runtime_alarm_count
        and len(records) == expected_durable_record_count
    )

    return StaleTargetDeactivationDecisionPressureMetrics(
        request_at_seconds=scenario.management_action_at_seconds,
        removal_at_seconds=scenario.deactivation_target_removal_at_seconds,
        decision_at_seconds=scenario.deactivation_decision_at_seconds,
        input_count=expected_count,
        deactivation_window_seconds=scenario.deactivation_window_seconds,
        source_revision=runtime.revision.alarm_configuration_revision,
        target_revision=runtime.target_revision.alarm_configuration_revision,
        request_receipt_count=len(request_receipts),
        pending_approval_receipt_count=pending_approval_receipt_count,
        deactivation_request_count=len(request_documents),
        approval_required_request_count=approval_required_request_count,
        decision_receipt_count=len(decision_receipts),
        stale_target_receipt_count=stale_target_receipt_count,
        applied_decision_receipt_count=applied_decision_receipt_count,
        management_effect_started_count=management_effect_started_count,
        management_effect_cleared_count=management_effect_cleared_count,
        configuration_removed_occurrence_count=len(removed_occurrences),
        deactivation_effect_started_count=deactivation_effect_started_count,
        deactivation_effect_cleared_count=deactivation_effect_cleared_count,
        lost_request_input_count=lost_request_input_count,
        duplicate_request_receipt_count=duplicate_request_receipt_count,
        lost_decision_input_count=lost_decision_input_count,
        duplicate_decision_receipt_count=duplicate_decision_receipt_count,
        wrong_decision_request_correlation_count=wrong_decision_request_correlation_count,
        request_occurrence_identity_mismatch_count=request_occurrence_identity_mismatch_count,
        stale_target_occurrence_mismatch_count=stale_target_occurrence_mismatch_count,
        final_removed_target_present_count=final_removed_target_present_count,
        final_management_cursor_byte_offset=final_management_cursor_byte_offset,
        expected_management_cursor_byte_offset=expected_cursor_byte_offset,
        final_decision_cursor_byte_offset=final_decision_cursor_byte_offset,
        expected_decision_cursor_byte_offset=expected_cursor_byte_offset,
        final_management_pending_count=final_management_pending_count,
        final_decision_pending_count=final_decision_pending_count,
        final_pending_request_count=final_pending_request_count,
        management_pending_high_water_count=consumer.management_pending_high_water_count,
        decision_pending_high_water_count=consumer.decision_pending_high_water_count,
        pending_request_high_water_count=consumer.pending_request_high_water_count,
        management_fresh_record_count=sum(source.management_read_batch_sizes),
        decision_fresh_record_count=sum(source.decision_read_batch_sizes),
        management_pending_read_count=source.management_read_at_count,
        decision_pending_read_count=source.decision_read_at_count,
        request_receipt_nonempty_batch_sizes=request_receipt_batches,
        decision_receipt_nonempty_batch_sizes=decision_receipt_batches,
        request_input_to_receipt_p50_ms=_percentile_values(request_latencies, 50),
        request_input_to_receipt_p95_ms=_percentile_values(request_latencies, 95),
        request_input_to_receipt_p99_ms=_percentile_values(request_latencies, 99),
        request_input_to_receipt_max_ms=max(request_latencies, default=0.0),
        decision_input_to_receipt_p50_ms=_percentile_values(decision_latencies, 50),
        decision_input_to_receipt_p95_ms=_percentile_values(decision_latencies, 95),
        decision_input_to_receipt_p99_ms=_percentile_values(decision_latencies, 99),
        decision_input_to_receipt_max_ms=max(decision_latencies, default=0.0),
        remaining_window_seconds=remaining_window_seconds,
        target_runtime_alarm_count=target_runtime_alarm_count,
        expected_target_runtime_alarm_count=expected_target_runtime_alarm_count,
        durable_record_count=len(records),
        expected_durable_record_count=expected_durable_record_count,
        functional_integrity_ok=functional_integrity_ok,
    )


# F-010 dura lo suficiente para que cada efecto de desactivación genere STARTED y luego CLEARED.
# El auditor selecciona el STARTED para validar su ventana y exige el lifecycle exacto esperado.
def _select_mixed_deactivation_started_effect(
    effects: list[dict[str, object]],
    *,
    expect_cleared: bool,
) -> tuple[dict[str, object] | None, bool]:
    started = [item for item in effects if item.get('kind') == 'STARTED']
    cleared = [item for item in effects if item.get('kind') == 'CLEARED']
    if len(started) != 1:
        return None, False
    if expect_cleared:
        return started[0], len(effects) == 2 and len(cleared) == 1
    return started[0], len(effects) == 1 and not cleared


def _build_mixed_deactivation_decision_pressure_metrics(
    *,
    scenario: BaselineScenario,
    runtime,
    records,
    snapshots=(),
) -> MixedDeactivationDecisionPressureMetrics:
    source = runtime.input_source
    consumer = runtime.input_consumer
    if not isinstance(source, MixedDeactivationInputSource):
        raise RuntimeError('mixed deactivation metrics require mixed input source')

    expected_count = source.input_count
    expected_input_ids = set(source.input_ids)
    expected_request_ids = set(source.request_ids)
    expected_decision_ids = set(source.decision_ids)
    expected_management_effect_ids = {f'PERF-ME-{input_id}' for input_id in expected_input_ids}
    expected_deactivation_effect_ids = {
        f'PERF-DE-{request_id}' for request_id in expected_request_ids
    }

    request_receipts = []
    decision_receipts = []
    request_documents = []
    management_effects = []
    deactivation_effects = []
    decision_receipt_entries: dict[str, list[tuple[object, list[dict[str, object]]]]] = {}
    for entry in records:
        payload = entry.record.records
        entry_effects = [
            item
            for item in payload.get('deactivation_effects', [])
            if item.get('effect_id') in expected_deactivation_effect_ids
        ]
        for receipt in payload.get('input_receipts', []):
            input_kind = receipt.get('input_kind')
            input_id = receipt.get('input_id')
            if input_kind == 'DEACTIVATION_REQUEST' and input_id in expected_input_ids:
                request_receipts.append(receipt)
            elif input_kind == 'DEACTIVATION_DECISION' and input_id in expected_decision_ids:
                decision_receipts.append(receipt)
                decision_receipt_entries.setdefault(str(input_id), []).append(
                    (entry, entry_effects)
                )
        request_documents.extend(
            item
            for item in payload.get('deactivation_requests', [])
            if item.get('request_id') in expected_request_ids
        )
        management_effects.extend(
            item
            for item in payload.get('management_effects', [])
            if item.get('effect_id') in expected_management_effect_ids
        )
        deactivation_effects.extend(entry_effects)

    request_receipt_ids = [str(item.get('input_id')) for item in request_receipts]
    decision_receipt_ids = [str(item.get('input_id')) for item in decision_receipts]
    request_receipt_set = set(request_receipt_ids)
    decision_receipt_set = set(decision_receipt_ids)
    lost_request_input_count = len(expected_input_ids - request_receipt_set)
    duplicate_request_receipt_count = len(request_receipt_ids) - len(request_receipt_set)
    lost_decision_input_count = len(expected_decision_ids - decision_receipt_set)
    duplicate_decision_receipt_count = len(decision_receipt_ids) - len(decision_receipt_set)

    pending_approval_receipt_count = sum(
        item.get('outcome') == 'PENDING_APPROVAL' for item in request_receipts
    )
    applied_decision_receipt_count = sum(
        item.get('outcome') == 'APPLIED' for item in decision_receipts
    )
    approval_required_request_count = sum(
        item.get('approval_required') is True for item in request_documents
    )
    management_effect_started_count = sum(
        item.get('kind') == 'STARTED' for item in management_effects
    )
    deactivation_effect_started_count = sum(
        item.get('kind') == 'STARTED' for item in deactivation_effects
    )
    deactivation_effect_cleared_count = sum(
        item.get('kind') == 'CLEARED' for item in deactivation_effects
    )

    requests_by_id: dict[str, list[dict[str, object]]] = {}
    for request in request_documents:
        requests_by_id.setdefault(str(request.get('request_id')), []).append(request)
    effects_by_id: dict[str, list[dict[str, object]]] = {}
    for effect in deactivation_effects:
        effects_by_id.setdefault(str(effect.get('effect_id')), []).append(effect)

    final_occurrences: dict[str, object] = {}
    final_alarms: dict[str, dict[str, object]] = {}
    for snapshot in snapshots:
        for alarm_key, alarm in snapshot.as_document()['alarms'].items():
            if not isinstance(alarm, dict):
                continue
            final_alarms[alarm_key] = alarm
            occurrence = alarm.get('occurrence')
            if isinstance(occurrence, dict):
                final_occurrences[alarm_key] = occurrence.get('occurrence_id')

    wrong_decision_request_correlation_count = 0
    request_occurrence_identity_mismatch_count = 0
    final_occurrence_identity_mismatch_count = 0
    effect_window_mismatch_count = 0
    remaining_windows: list[float] = []
    expected_target_keys: set[str] = set()
    integrated_f010 = scenario.test_id == 'F-010'

    for index in range(expected_count):
        input_id = source.input_ids[index]
        request_id = source.request_ids[index]
        decision_id = source.decision_ids[index]
        expected_effect_id = f'PERF-DE-{request_id}'
        identity, _priority_group = source.target_for_input(index)
        alarm_key = identity.canonical_key
        expected_target_keys.add(alarm_key)
        expected_occurrence_id = source.target_occurrence_ids.get(alarm_key)
        expected_requested_at = (
            source.request_created_at_for(index).isoformat().replace('+00:00', 'Z')
        )
        expected_decided_at = (
            source.decision_decided_at_for(index).isoformat().replace('+00:00', 'Z')
        )
        expected_effective_until = (
            source.effective_until_for(index).isoformat().replace('+00:00', 'Z')
        )

        request_candidates = requests_by_id.get(request_id, [])
        request = request_candidates[0] if len(request_candidates) == 1 else None
        if (
            request is None
            or request.get('source_management_input_id') != input_id
            or request.get('alarm_key') != alarm_key
            or request.get('source_occurrence_id') != expected_occurrence_id
            or request.get('requested_at') != expected_requested_at
            or request.get('effective_until') != expected_effective_until
            or request.get('approval_required') is not True
        ):
            request_occurrence_identity_mismatch_count += 1
        if final_occurrences.get(alarm_key) != expected_occurrence_id:
            final_occurrence_identity_mismatch_count += 1

        effect_candidates = effects_by_id.get(expected_effect_id, [])
        effect, effect_lifecycle_ok = _select_mixed_deactivation_started_effect(
            effect_candidates,
            expect_cleared=integrated_f010,
        )
        if (
            not effect_lifecycle_ok
            or effect is None
            or request is None
            or any(item.get('alarm_key') != alarm_key for item in effect_candidates)
            or effect.get('alarm_key') != alarm_key
            or effect.get('kind') != 'STARTED'
            or effect.get('effective_from') != expected_decided_at
            or effect.get('effective_until') != request.get('effective_until')
        ):
            effect_window_mismatch_count += 1
        else:
            effective_from = _parse_report_timestamp(effect.get('effective_from'))
            effective_until = _parse_report_timestamp(effect.get('effective_until'))
            if effective_from is not None and effective_until is not None:
                remaining_windows.append((effective_until - effective_from).total_seconds())

        receipt_entries = decision_receipt_entries.get(decision_id, [])
        if len(receipt_entries) != 1:
            wrong_decision_request_correlation_count += 1
        else:
            _entry, entry_effects = receipt_entries[0]
            matching = [
                item
                for item in entry_effects
                if item.get('effect_id') == expected_effect_id
                and item.get('alarm_key') == alarm_key
            ]
            if len(matching) != 1:
                wrong_decision_request_correlation_count += 1

    unique_target_count = len(expected_target_keys)
    snapshot_management_effect_count = sum(
        isinstance(final_alarms.get(alarm_key), dict)
        and final_alarms[alarm_key].get('management_effect') is not None
        for alarm_key in expected_target_keys
    )
    snapshot_deactivation_effect_count = sum(
        isinstance(final_alarms.get(alarm_key), dict)
        and isinstance(final_alarms[alarm_key].get('deactivation_effect'), dict)
        for alarm_key in expected_target_keys
    )

    store = AtomicJsonStore(root_path=runtime.composition.durability.persistence.paths.alarms_root)
    final_state = store.read('runtime/state/consumers/management.json') or {}
    final_management = final_state.get('management') if isinstance(final_state, dict) else None
    final_decisions = final_state.get('decisions') if isinstance(final_state, dict) else None
    final_management_cursor = (
        final_management.get('cursor') if isinstance(final_management, dict) else None
    )
    final_decision_cursor = (
        final_decisions.get('cursor') if isinstance(final_decisions, dict) else None
    )
    final_management_pending = (
        final_management.get('pending') if isinstance(final_management, dict) else None
    )
    final_decision_pending = (
        final_decisions.get('pending') if isinstance(final_decisions, dict) else None
    )
    final_pending_request_ids = (
        final_state.get('pending_deactivation_request_ids')
        if isinstance(final_state, dict)
        else None
    )
    final_management_cursor_byte_offset = (
        final_management_cursor.get('byte_offset')
        if isinstance(final_management_cursor, dict)
        else None
    )
    final_decision_cursor_byte_offset = (
        final_decision_cursor.get('byte_offset')
        if isinstance(final_decision_cursor, dict)
        else None
    )
    final_management_pending_count = (
        len(final_management_pending) if isinstance(final_management_pending, list) else -1
    )
    final_decision_pending_count = (
        len(final_decision_pending) if isinstance(final_decision_pending, list) else -1
    )
    final_pending_request_count = (
        len(final_pending_request_ids) if isinstance(final_pending_request_ids, list) else -1
    )

    request_latencies = [
        (confirmed_at - source.management_visible_monotonic_by_input_id[input_id]) * 1000
        for input_id, confirmed_at in (
            consumer.mixed_request_receipt_confirmed_monotonic_by_input_id.items()
        )
        if input_id in source.management_visible_monotonic_by_input_id
    ]
    decision_latencies = [
        (confirmed_at - source.decision_visible_monotonic_by_input_id[decision_id]) * 1000
        for decision_id, confirmed_at in (
            consumer.mixed_decision_receipt_confirmed_monotonic_by_input_id.items()
        )
        if decision_id in source.decision_visible_monotonic_by_input_id
    ]
    request_receipt_batches = tuple(
        size for size in consumer.mixed_request_receipt_batch_sizes if size > 0
    )
    decision_receipt_batches = tuple(
        size for size in consumer.mixed_decision_receipt_batch_sizes if size > 0
    )
    management_source_batches = tuple(
        size for size in source.management_read_batch_sizes if size > 0
    )
    decision_source_batches = tuple(size for size in source.decision_read_batch_sizes if size > 0)
    mixed_receipt_cycle_count = sum(
        request_count > 0 and decision_count > 0
        for request_count, decision_count in consumer.mixed_receipt_cycle_batches
    )
    expected_cursor_byte_offset = expected_count * source.byte_length
    management_fresh_record_count = sum(source.management_read_batch_sizes)
    decision_fresh_record_count = sum(source.decision_read_batch_sizes)

    # En el tail de 30 minutos las ventanas de 900 s expiran; los 480 efectos deben registrar STARTED y CLEARED.
    expected_cleared_count = expected_count if integrated_f010 else 0
    snapshot_effects_ok = (
        True
        if integrated_f010
        else snapshot_management_effect_count == expected_count
        and snapshot_deactivation_effect_count == expected_count
    )

    functional_integrity_ok = (
        len(request_receipts) == expected_count
        and pending_approval_receipt_count == expected_count
        and len(request_documents) == expected_count
        and approval_required_request_count == expected_count
        and len(decision_receipts) == expected_count
        and applied_decision_receipt_count == expected_count
        and management_effect_started_count == expected_count
        and deactivation_effect_started_count == expected_count
        and deactivation_effect_cleared_count == expected_cleared_count
        and lost_request_input_count == 0
        and duplicate_request_receipt_count == 0
        and lost_decision_input_count == 0
        and duplicate_decision_receipt_count == 0
        and unique_target_count == expected_count
        and wrong_decision_request_correlation_count == 0
        and request_occurrence_identity_mismatch_count == 0
        and final_occurrence_identity_mismatch_count == 0
        and effect_window_mismatch_count == 0
        and snapshot_effects_ok
        and final_management_cursor_byte_offset == expected_cursor_byte_offset
        and final_decision_cursor_byte_offset == expected_cursor_byte_offset
        and final_management_pending_count == 0
        and final_decision_pending_count == 0
        and final_pending_request_count == 0
        and management_fresh_record_count == expected_count
        and decision_fresh_record_count == expected_count
        and len(request_latencies) == expected_count
        and len(decision_latencies) == expected_count
        and all(value >= 0 for value in request_latencies)
        and all(value >= 0 for value in decision_latencies)
    )

    return MixedDeactivationDecisionPressureMetrics(
        request_at_seconds=scenario.management_action_at_seconds,
        request_count=expected_count,
        request_interval_seconds=scenario.management_action_interval_seconds,
        request_last_at_seconds=scenario.management_last_action_at_seconds,
        decision_at_seconds=scenario.deactivation_decision_at_seconds,
        decision_count=expected_count,
        decision_interval_seconds=scenario.deactivation_decision_interval_seconds,
        decision_last_at_seconds=scenario.deactivation_decision_last_at_seconds,
        decision_lag_seconds=(
            scenario.deactivation_decision_at_seconds - scenario.management_action_at_seconds
        ),
        deactivation_window_seconds=scenario.deactivation_window_seconds,
        request_receipt_count=len(request_receipts),
        expected_request_receipt_count=expected_count,
        pending_approval_receipt_count=pending_approval_receipt_count,
        deactivation_request_count=len(request_documents),
        approval_required_request_count=approval_required_request_count,
        decision_receipt_count=len(decision_receipts),
        expected_decision_receipt_count=expected_count,
        applied_decision_receipt_count=applied_decision_receipt_count,
        management_effect_started_count=management_effect_started_count,
        deactivation_effect_started_count=deactivation_effect_started_count,
        deactivation_effect_cleared_count=deactivation_effect_cleared_count,
        lost_request_input_count=lost_request_input_count,
        duplicate_request_receipt_count=duplicate_request_receipt_count,
        lost_decision_input_count=lost_decision_input_count,
        duplicate_decision_receipt_count=duplicate_decision_receipt_count,
        unique_target_count=unique_target_count,
        wrong_decision_request_correlation_count=wrong_decision_request_correlation_count,
        request_occurrence_identity_mismatch_count=request_occurrence_identity_mismatch_count,
        final_occurrence_identity_mismatch_count=final_occurrence_identity_mismatch_count,
        effect_window_mismatch_count=effect_window_mismatch_count,
        snapshot_management_effect_count=snapshot_management_effect_count,
        snapshot_deactivation_effect_count=snapshot_deactivation_effect_count,
        final_management_cursor_byte_offset=final_management_cursor_byte_offset,
        expected_management_cursor_byte_offset=expected_cursor_byte_offset,
        final_decision_cursor_byte_offset=final_decision_cursor_byte_offset,
        expected_decision_cursor_byte_offset=expected_cursor_byte_offset,
        final_management_pending_count=final_management_pending_count,
        final_decision_pending_count=final_decision_pending_count,
        final_pending_request_count=final_pending_request_count,
        management_pending_high_water_count=consumer.management_pending_high_water_count,
        decision_pending_high_water_count=consumer.decision_pending_high_water_count,
        pending_request_high_water_count=consumer.pending_request_high_water_count,
        management_fresh_record_count=management_fresh_record_count,
        decision_fresh_record_count=decision_fresh_record_count,
        management_pending_read_count=source.management_read_at_count,
        decision_pending_read_count=source.decision_read_at_count,
        management_source_nonempty_batch_sizes=management_source_batches,
        decision_source_nonempty_batch_sizes=decision_source_batches,
        request_receipt_nonempty_batch_sizes=request_receipt_batches,
        decision_receipt_nonempty_batch_sizes=decision_receipt_batches,
        mixed_receipt_cycle_count=mixed_receipt_cycle_count,
        request_input_to_receipt_p50_ms=_percentile_values(request_latencies, 50),
        request_input_to_receipt_p95_ms=_percentile_values(request_latencies, 95),
        request_input_to_receipt_p99_ms=_percentile_values(request_latencies, 99),
        request_input_to_receipt_max_ms=max(request_latencies, default=0.0),
        decision_input_to_receipt_p50_ms=_percentile_values(decision_latencies, 50),
        decision_input_to_receipt_p95_ms=_percentile_values(decision_latencies, 95),
        decision_input_to_receipt_p99_ms=_percentile_values(decision_latencies, 99),
        decision_input_to_receipt_max_ms=max(decision_latencies, default=0.0),
        remaining_window_min_seconds=min(remaining_windows, default=None),
        remaining_window_max_seconds=max(remaining_windows, default=None),
        durable_record_count=len(records),
        functional_integrity_ok=functional_integrity_ok,
    )


def _build_sustained_deactivation_decision_pressure_metrics(
    *,
    scenario: BaselineScenario,
    phase_a_runtime,
    phase_b_runtime,
    phase_a_state: dict[str, object],
    records,
    snapshots=(),
) -> SustainedDeactivationDecisionPressureMetrics:
    request_source = phase_a_runtime.input_source
    decision_source = phase_b_runtime.input_source
    request_consumer = phase_a_runtime.input_consumer
    decision_consumer = phase_b_runtime.input_consumer
    if not isinstance(request_source, SustainedDeactivationRequestInputSource):
        raise RuntimeError('multi deactivation phase A requires its canonical request source')
    if not isinstance(decision_source, SustainedDeactivationDecisionInputSource):
        raise RuntimeError('multi deactivation phase B requires its canonical decision source')

    expected_count = scenario.effective_deactivation_decision_count
    expected_request_ids = set(request_source.request_ids)
    expected_management_input_ids = set(request_source.input_ids)
    expected_decision_ids = set(decision_source.decision_ids)
    expected_effect_ids = {f'PERF-DE-{request_id}' for request_id in expected_request_ids}
    expected_management_effect_ids = {
        f'PERF-ME-{input_id}' for input_id in expected_management_input_ids
    }

    request_receipts = []
    decision_receipts = []
    request_documents = []
    management_effects = []
    deactivation_effects = []
    decision_receipt_entries: dict[str, list[tuple[object, list[dict[str, object]]]]] = {}
    for entry in records:
        payload = entry.record.records
        entry_effects = [
            item
            for item in payload.get('deactivation_effects', [])
            if item.get('effect_id') in expected_effect_ids
        ]
        for item in payload.get('input_receipts', []):
            input_kind = item.get('input_kind')
            input_id = item.get('input_id')
            if input_kind == 'DEACTIVATION_REQUEST' and input_id in expected_management_input_ids:
                request_receipts.append(item)
            elif input_kind == 'DEACTIVATION_DECISION' and input_id in expected_decision_ids:
                decision_receipts.append(item)
                decision_receipt_entries.setdefault(str(input_id), []).append(
                    (entry, entry_effects)
                )
        request_documents.extend(
            item
            for item in payload.get('deactivation_requests', [])
            if item.get('request_id') in expected_request_ids
        )
        management_effects.extend(
            item
            for item in payload.get('management_effects', [])
            if item.get('effect_id') in expected_management_effect_ids
        )
        deactivation_effects.extend(entry_effects)

    request_receipt_ids = [str(item.get('input_id')) for item in request_receipts]
    decision_receipt_ids = [str(item.get('input_id')) for item in decision_receipts]
    request_receipt_id_set = set(request_receipt_ids)
    decision_receipt_id_set = set(decision_receipt_ids)
    lost_request_input_count = len(expected_management_input_ids - request_receipt_id_set)
    duplicate_request_receipt_count = len(request_receipt_ids) - len(request_receipt_id_set)
    lost_decision_input_count = len(expected_decision_ids - decision_receipt_id_set)
    duplicate_decision_receipt_count = len(decision_receipt_ids) - len(decision_receipt_id_set)

    pending_approval_receipt_count = sum(
        item.get('outcome') == 'PENDING_APPROVAL' for item in request_receipts
    )
    applied_decision_receipt_count = sum(
        item.get('outcome') == 'APPLIED' for item in decision_receipts
    )
    approval_required_request_count = sum(
        item.get('approval_required') is True for item in request_documents
    )
    management_effect_started_count = sum(
        item.get('kind') == 'STARTED' for item in management_effects
    )
    deactivation_effect_started_count = sum(
        item.get('kind') == 'STARTED' for item in deactivation_effects
    )
    deactivation_effect_cleared_count = sum(
        item.get('kind') == 'CLEARED' for item in deactivation_effects
    )

    requests_by_id: dict[str, list[dict[str, object]]] = {}
    for request in request_documents:
        requests_by_id.setdefault(str(request.get('request_id')), []).append(request)
    effects_by_id: dict[str, list[dict[str, object]]] = {}
    for effect in deactivation_effects:
        effects_by_id.setdefault(str(effect.get('effect_id')), []).append(effect)

    request_occurrence_identity_mismatch_count = 0
    final_occurrence_identity_mismatch_count = 0
    effect_window_mismatch_count = 0
    wrong_decision_request_correlation_count = 0
    remaining_windows: list[float] = []
    expected_target_keys: set[str] = set()

    final_occurrences: dict[str, object] = {}
    for snapshot in snapshots:
        for alarm_key, alarm in snapshot.as_document()['alarms'].items():
            if not isinstance(alarm, dict):
                continue
            occurrence = alarm.get('occurrence')
            if isinstance(occurrence, dict):
                final_occurrences[alarm_key] = occurrence.get('occurrence_id')

    for index in range(expected_count):
        input_id = request_source.input_ids[index]
        request_id = request_source.request_ids[index]
        decision_id = decision_source.decision_ids[index]
        expected_effect_id = f'PERF-DE-{request_id}'
        identity, _priority_group = request_source.target_for_request(index)
        alarm_key = identity.canonical_key
        expected_target_keys.add(alarm_key)
        expected_occurrence_id = request_source.target_occurrence_ids.get(alarm_key)
        expected_requested_at = (
            request_source.requested_at_for(index).isoformat().replace('+00:00', 'Z')
        )
        expected_effective_until = (
            request_source.effective_until_for(index).isoformat().replace('+00:00', 'Z')
        )
        expected_decided_at = (
            decision_source.decided_at_for(index).isoformat().replace('+00:00', 'Z')
        )

        request_candidates = requests_by_id.get(request_id, [])
        request = request_candidates[0] if len(request_candidates) == 1 else None
        if (
            request is None
            or request.get('source_management_input_id') != input_id
            or request.get('alarm_key') != alarm_key
            or request.get('source_occurrence_id') != expected_occurrence_id
            or request.get('requested_at') != expected_requested_at
            or request.get('effective_until') != expected_effective_until
        ):
            request_occurrence_identity_mismatch_count += 1
        if final_occurrences.get(alarm_key) != expected_occurrence_id:
            final_occurrence_identity_mismatch_count += 1

        effect_candidates = effects_by_id.get(expected_effect_id, [])
        effect = effect_candidates[0] if len(effect_candidates) == 1 else None
        if (
            effect is None
            or request is None
            or effect.get('alarm_key') != alarm_key
            or effect.get('kind') != 'STARTED'
            or effect.get('effective_from') != expected_decided_at
            or effect.get('effective_until') != request.get('effective_until')
        ):
            effect_window_mismatch_count += 1
        elif effect is not None:
            effective_from = _parse_report_timestamp(effect.get('effective_from'))
            effective_until = _parse_report_timestamp(effect.get('effective_until'))
            if effective_from is not None and effective_until is not None:
                remaining_windows.append((effective_until - effective_from).total_seconds())

        receipt_entries = decision_receipt_entries.get(decision_id, [])
        if len(receipt_entries) != 1:
            wrong_decision_request_correlation_count += 1
        else:
            _entry, entry_effects = receipt_entries[0]
            matching = [
                item
                for item in entry_effects
                if item.get('effect_id') == expected_effect_id
                and item.get('alarm_key') == alarm_key
            ]
            if len(matching) != 1:
                wrong_decision_request_correlation_count += 1

    snapshot_management_effect_count = 0
    snapshot_deactivation_effect_count = 0
    for snapshot in snapshots:
        alarms = snapshot.as_document()['alarms']
        for alarm_key in expected_target_keys:
            alarm = alarms.get(alarm_key)
            if not isinstance(alarm, dict):
                continue
            if alarm.get('management_effect') is not None:
                snapshot_management_effect_count += 1
            if isinstance(alarm.get('deactivation_effect'), dict):
                snapshot_deactivation_effect_count += 1

    unique_target_count = len(
        {
            str(request.get('alarm_key'))
            for request in request_documents
            if request.get('alarm_key') in expected_target_keys
        }
    )
    unique_decision_request_count = len(
        {
            str(effect.get('effect_id'))[len('PERF-DE-') :]
            for effect in deactivation_effects
            if effect.get('kind') == 'STARTED'
            and isinstance(effect.get('effect_id'), str)
            and str(effect.get('effect_id')).startswith('PERF-DE-')
        }
        & expected_request_ids
    )

    phase_a_management = phase_a_state.get('management')
    phase_a_management_cursor = (
        phase_a_management.get('cursor') if isinstance(phase_a_management, dict) else None
    )
    phase_a_management_pending = (
        phase_a_management.get('pending') if isinstance(phase_a_management, dict) else None
    )
    phase_a_pending_request_ids = phase_a_state.get('pending_deactivation_request_ids')
    phase_a_management_cursor_byte_offset = (
        phase_a_management_cursor.get('byte_offset')
        if isinstance(phase_a_management_cursor, dict)
        else None
    )
    phase_a_management_pending_count = (
        len(phase_a_management_pending) if isinstance(phase_a_management_pending, list) else -1
    )
    phase_a_pending_request_count = (
        len(phase_a_pending_request_ids) if isinstance(phase_a_pending_request_ids, list) else -1
    )

    store = AtomicJsonStore(
        root_path=phase_b_runtime.composition.durability.persistence.paths.alarms_root
    )
    final_state = store.read('runtime/state/consumers/management.json') or {}
    final_management = final_state.get('management') if isinstance(final_state, dict) else None
    final_decisions = final_state.get('decisions') if isinstance(final_state, dict) else None
    final_management_cursor = (
        final_management.get('cursor') if isinstance(final_management, dict) else None
    )
    final_decision_cursor = (
        final_decisions.get('cursor') if isinstance(final_decisions, dict) else None
    )
    final_management_pending = (
        final_management.get('pending') if isinstance(final_management, dict) else None
    )
    final_decision_pending = (
        final_decisions.get('pending') if isinstance(final_decisions, dict) else None
    )
    final_pending_request_ids = (
        final_state.get('pending_deactivation_request_ids')
        if isinstance(final_state, dict)
        else None
    )
    final_management_cursor_byte_offset = (
        final_management_cursor.get('byte_offset')
        if isinstance(final_management_cursor, dict)
        else None
    )
    final_decision_cursor_byte_offset = (
        final_decision_cursor.get('byte_offset')
        if isinstance(final_decision_cursor, dict)
        else None
    )
    final_management_pending_count = (
        len(final_management_pending) if isinstance(final_management_pending, list) else -1
    )
    final_decision_pending_count = (
        len(final_decision_pending) if isinstance(final_decision_pending, list) else -1
    )
    final_pending_request_count = (
        len(final_pending_request_ids) if isinstance(final_pending_request_ids, list) else -1
    )

    decision_latencies = [
        (confirmed_at - decision_source.visible_monotonic_by_input_id[decision_id]) * 1000
        for decision_id, confirmed_at in (
            decision_consumer.deactivation_decision_receipt_confirmed_monotonic_by_input_id.items()
        )
        if decision_id in decision_source.visible_monotonic_by_input_id
    ]
    decision_nonempty_batch_sizes = decision_source.nonempty_batch_sizes
    decision_receipt_nonempty_batch_sizes = tuple(
        size for size in decision_consumer.deactivation_decision_receipt_batch_sizes if size > 0
    )
    decision_first_nonempty_receipt_batch_size = (
        decision_receipt_nonempty_batch_sizes[0] if decision_receipt_nonempty_batch_sizes else 0
    )
    expected_decision_first_nonempty_receipt_batch_size = (
        expected_count if scenario.has_burst_deactivation_decision_pressure else None
    )
    decision_fully_absorbed_in_first_eligible_iteration = (
        decision_first_nonempty_receipt_batch_size == expected_count
        if scenario.has_burst_deactivation_decision_pressure
        else None
    )
    pending_request_high_water_count = max(
        request_consumer.pending_request_high_water_count,
        decision_consumer.pending_request_high_water_count,
    )
    decision_pending_high_water_count = max(
        request_consumer.decision_pending_high_water_count,
        decision_consumer.decision_pending_high_water_count,
    )

    expected_cursor_byte_offset = expected_count * request_source.byte_length
    functional_integrity_ok = (
        len(request_receipts) == expected_count
        and pending_approval_receipt_count == expected_count
        and len(request_documents) == expected_count
        and approval_required_request_count == expected_count
        and len(decision_receipts) == expected_count
        and applied_decision_receipt_count == expected_count
        and management_effect_started_count == expected_count
        and deactivation_effect_started_count == expected_count
        and deactivation_effect_cleared_count == 0
        and lost_request_input_count == 0
        and duplicate_request_receipt_count == 0
        and lost_decision_input_count == 0
        and duplicate_decision_receipt_count == 0
        and unique_target_count == expected_count
        and unique_decision_request_count == expected_count
        and wrong_decision_request_correlation_count == 0
        and request_occurrence_identity_mismatch_count == 0
        and final_occurrence_identity_mismatch_count == 0
        and effect_window_mismatch_count == 0
        and snapshot_management_effect_count == expected_count
        and snapshot_deactivation_effect_count == expected_count
        and phase_a_management_cursor_byte_offset == expected_cursor_byte_offset
        and final_management_cursor_byte_offset == expected_cursor_byte_offset
        and final_decision_cursor_byte_offset == expected_cursor_byte_offset
        and phase_a_management_pending_count == 0
        and phase_a_pending_request_count == expected_count
        and final_management_pending_count == 0
        and final_decision_pending_count == 0
        and final_pending_request_count == 0
        and pending_request_high_water_count == expected_count
        and decision_pending_high_water_count == 0
        and request_consumer.deactivation_request_receipt_before_cursor_checked_count
        == expected_count
        and request_consumer.deactivation_request_receipt_before_cursor_advance_ok
        and decision_consumer.deactivation_decision_receipt_before_cursor_checked_count
        == expected_count
        and decision_consumer.deactivation_decision_receipt_before_cursor_advance_ok
        and len(decision_latencies) == expected_count
        and all(value >= 0 for value in decision_latencies)
        and (
            not scenario.has_burst_deactivation_decision_pressure
            or decision_fully_absorbed_in_first_eligible_iteration is True
        )
    )
    return SustainedDeactivationDecisionPressureMetrics(
        phase_a_duration_seconds=scenario.deactivation_phase_duration_seconds,
        phase_b_duration_seconds=scenario.deactivation_phase_duration_seconds,
        request_at_seconds=scenario.management_action_at_seconds,
        request_count=expected_count,
        request_interval_seconds=scenario.management_action_interval_seconds,
        request_last_at_seconds=scenario.management_last_action_at_seconds,
        decision_at_seconds=scenario.deactivation_decision_at_seconds,
        decision_count=expected_count,
        decision_interval_seconds=scenario.deactivation_decision_interval_seconds,
        decision_last_at_seconds=scenario.deactivation_decision_last_at_seconds,
        deactivation_window_seconds=scenario.deactivation_window_seconds,
        request_receipt_count=len(request_receipts),
        expected_request_receipt_count=expected_count,
        pending_approval_receipt_count=pending_approval_receipt_count,
        expected_pending_approval_receipt_count=expected_count,
        deactivation_request_count=len(request_documents),
        expected_deactivation_request_count=expected_count,
        approval_required_request_count=approval_required_request_count,
        expected_approval_required_request_count=expected_count,
        decision_receipt_count=len(decision_receipts),
        expected_decision_receipt_count=expected_count,
        applied_decision_receipt_count=applied_decision_receipt_count,
        expected_applied_decision_receipt_count=expected_count,
        management_effect_started_count=management_effect_started_count,
        expected_management_effect_started_count=expected_count,
        deactivation_effect_started_count=deactivation_effect_started_count,
        expected_deactivation_effect_started_count=expected_count,
        deactivation_effect_cleared_count=deactivation_effect_cleared_count,
        expected_deactivation_effect_cleared_count=0,
        lost_request_input_count=lost_request_input_count,
        duplicate_request_receipt_count=duplicate_request_receipt_count,
        lost_decision_input_count=lost_decision_input_count,
        duplicate_decision_receipt_count=duplicate_decision_receipt_count,
        unique_target_count=unique_target_count,
        expected_unique_target_count=expected_count,
        unique_decision_request_count=unique_decision_request_count,
        expected_unique_decision_request_count=expected_count,
        wrong_decision_request_correlation_count=wrong_decision_request_correlation_count,
        request_occurrence_identity_mismatch_count=(request_occurrence_identity_mismatch_count),
        final_occurrence_identity_mismatch_count=final_occurrence_identity_mismatch_count,
        effect_window_mismatch_count=effect_window_mismatch_count,
        snapshot_management_effect_count=snapshot_management_effect_count,
        expected_snapshot_management_effect_count=expected_count,
        snapshot_deactivation_effect_count=snapshot_deactivation_effect_count,
        expected_snapshot_deactivation_effect_count=expected_count,
        phase_a_management_cursor_byte_offset=phase_a_management_cursor_byte_offset,
        expected_phase_a_management_cursor_byte_offset=expected_cursor_byte_offset,
        final_management_cursor_byte_offset=final_management_cursor_byte_offset,
        expected_final_management_cursor_byte_offset=expected_cursor_byte_offset,
        final_decision_cursor_byte_offset=final_decision_cursor_byte_offset,
        expected_final_decision_cursor_byte_offset=(expected_count * decision_source.byte_length),
        phase_a_management_pending_count=phase_a_management_pending_count,
        phase_a_pending_request_count=phase_a_pending_request_count,
        final_management_pending_count=final_management_pending_count,
        final_decision_pending_count=final_decision_pending_count,
        final_pending_request_count=final_pending_request_count,
        pending_request_high_water_count=pending_request_high_water_count,
        decision_pending_high_water_count=decision_pending_high_water_count,
        request_receipt_before_cursor_checked_count=(
            request_consumer.deactivation_request_receipt_before_cursor_checked_count
        ),
        request_receipt_before_cursor_advance_ok=(
            request_consumer.deactivation_request_receipt_before_cursor_advance_ok
        ),
        decision_receipt_before_cursor_checked_count=(
            decision_consumer.deactivation_decision_receipt_before_cursor_checked_count
        ),
        decision_receipt_before_cursor_advance_ok=(
            decision_consumer.deactivation_decision_receipt_before_cursor_advance_ok
        ),
        request_source_max_batch_size=max(request_source.nonempty_batch_sizes, default=0),
        request_receipt_max_batch_size=max(
            request_consumer.deactivation_request_receipt_batch_sizes,
            default=0,
        ),
        decision_source_max_batch_size=max(decision_nonempty_batch_sizes, default=0),
        decision_receipt_max_batch_size=max(
            decision_consumer.deactivation_decision_receipt_batch_sizes,
            default=0,
        ),
        decision_nonempty_batch_count=len(decision_nonempty_batch_sizes),
        decision_nonempty_batch_sizes=decision_nonempty_batch_sizes,
        decision_arrival_mode=scenario.deactivation_decision_arrival_mode,
        decision_receipt_nonempty_batch_count=len(decision_receipt_nonempty_batch_sizes),
        decision_receipt_nonempty_batch_sizes=decision_receipt_nonempty_batch_sizes,
        decision_first_nonempty_receipt_batch_size=(decision_first_nonempty_receipt_batch_size),
        expected_decision_first_nonempty_receipt_batch_size=(
            expected_decision_first_nonempty_receipt_batch_size
        ),
        decision_fully_absorbed_in_first_eligible_iteration=(
            decision_fully_absorbed_in_first_eligible_iteration
        ),
        decision_input_to_receipt_p50_ms=_percentile_values(decision_latencies, 50),
        decision_input_to_receipt_p95_ms=_percentile_values(decision_latencies, 95),
        decision_input_to_receipt_p99_ms=_percentile_values(decision_latencies, 99),
        decision_input_to_receipt_max_ms=max(decision_latencies, default=0.0),
        remaining_window_min_seconds=min(remaining_windows, default=None),
        remaining_window_max_seconds=max(remaining_windows, default=None),
        durable_record_count=len(records),
        functional_integrity_ok=functional_integrity_ok,
    )


def _parse_report_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _build_management_pressure_metrics(
    *,
    scenario: BaselineScenario,
    runtime,
    records,
    snapshots=(),
) -> ManagementPressureMetrics | SustainedManagementPressureMetrics | None:
    if not scenario.has_management_pressure or scenario.has_deactivation_decision_pressure:
        return None
    if scenario.has_multi_management_pressure:
        return _build_sustained_management_pressure_metrics(
            scenario=scenario,
            runtime=runtime,
            records=records,
            snapshots=snapshots,
        )
    source = runtime.input_source
    consumer = runtime.input_consumer
    input_id = getattr(source, 'input_id', '')
    target_occurrence_id = getattr(source, 'target_occurrence_id', None)
    target_identity = getattr(source, 'target_identity', None)
    if not input_id or target_occurrence_id is None or target_identity is None:
        return ManagementPressureMetrics(
            action_at_seconds=scenario.management_action_at_seconds,
            input_id=str(input_id),
            target_alarm_key='',
            target_occurrence_id='',
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
            target_group_durable_record_count=0,
            expected_target_group_durable_record_count=2,
            total_durable_record_count=len(records),
            expected_total_durable_record_count=scenario.priority_group_count + 1,
            consumer_cursor_byte_offset=None,
            expected_consumer_cursor_byte_offset=256,
            consumer_pending_count=0,
            expected_consumer_pending_count=0,
            snapshot_management_effect_count=0,
            expected_snapshot_management_effect_count=1,
            occurrence_identity_mismatch_count=1,
            receipt_commit_id=getattr(consumer, 'management_receipt_commit_id', None),
            receipt_before_cursor_advance_ok=False,
            input_to_receipt_ms=getattr(consumer, 'management_input_to_receipt_ms', None),
            functional_integrity_ok=False,
        )
    target_alarm_key = target_identity.canonical_key
    receipts = []
    effects = []
    deactivation_requests = []
    management_commit_count = 0
    target_group_durable_record_count = 0
    for entry in records:
        payload = entry.record.records
        matching_receipts = [
            item
            for item in payload.get('input_receipts', [])
            if item.get('input_kind') == 'MANAGEMENT' and item.get('input_id') == input_id
        ]
        if matching_receipts:
            management_commit_count += 1
            receipts.extend(matching_receipts)
        effects.extend(
            item
            for item in payload.get('management_effects', [])
            if item.get('alarm_key') == target_alarm_key
        )
        deactivation_requests.extend(payload.get('deactivation_requests', []))
        if entry.record.commit.priority_group == getattr(source, 'target_priority_group', ''):
            target_group_durable_record_count += 1
    effective_receipt_count = sum(item.get('outcome') == 'EFFECTIVE' for item in receipts)
    management_effect_started_count = sum(item.get('kind') == 'STARTED' for item in effects)
    management_effect_cleared_count = sum(item.get('kind') == 'CLEARED' for item in effects)

    store = AtomicJsonStore(root_path=runtime.composition.durability.persistence.paths.alarms_root)
    state = store.read('runtime/state/consumers/management.json') or {}
    management_state = state.get('management') if isinstance(state, dict) else None
    cursor_value = management_state.get('cursor') if isinstance(management_state, dict) else None
    pending_value = management_state.get('pending') if isinstance(management_state, dict) else None
    cursor_byte_offset = cursor_value.get('byte_offset') if isinstance(cursor_value, dict) else None
    consumer_pending_count = len(pending_value) if isinstance(pending_value, list) else -1

    snapshot_management_effect_count = 0
    final_occurrence_id = None
    for snapshot in snapshots:
        alarm = snapshot.as_document()['alarms'].get(target_alarm_key)
        if not isinstance(alarm, dict):
            continue
        occurrence = alarm.get('occurrence')
        if isinstance(occurrence, dict):
            final_occurrence_id = occurrence.get('occurrence_id')
        if alarm.get('management_effect') is not None:
            snapshot_management_effect_count += 1
    occurrence_identity_mismatch_count = int(final_occurrence_id != target_occurrence_id)
    receipt_commit_id = getattr(consumer, 'management_receipt_commit_id', None)
    input_to_receipt_ms = getattr(consumer, 'management_input_to_receipt_ms', None)
    receipt_before_cursor_advance_ok = bool(
        getattr(consumer, 'receipt_before_cursor_advance_ok', False)
    )
    expected_total_durable_record_count = scenario.priority_group_count + 1
    functional_integrity_ok = (
        len(receipts) == 1
        and effective_receipt_count == 1
        and management_effect_started_count == 1
        and management_effect_cleared_count == 0
        and management_commit_count == 1
        and len(deactivation_requests) == 0
        and target_group_durable_record_count == 2
        and len(records) == expected_total_durable_record_count
        and cursor_byte_offset == 256
        and consumer_pending_count == 0
        and snapshot_management_effect_count == 1
        and occurrence_identity_mismatch_count == 0
        and receipt_commit_id is not None
        and receipt_before_cursor_advance_ok
        and input_to_receipt_ms is not None
        and input_to_receipt_ms >= 0
    )
    return ManagementPressureMetrics(
        action_at_seconds=scenario.management_action_at_seconds,
        input_id=input_id,
        target_alarm_key=target_alarm_key,
        target_occurrence_id=target_occurrence_id,
        input_receipt_count=len(receipts),
        expected_input_receipt_count=1,
        effective_receipt_count=effective_receipt_count,
        expected_effective_receipt_count=1,
        management_effect_started_count=management_effect_started_count,
        expected_management_effect_started_count=1,
        management_effect_cleared_count=management_effect_cleared_count,
        expected_management_effect_cleared_count=0,
        management_commit_count=management_commit_count,
        expected_management_commit_count=1,
        deactivation_request_count=len(deactivation_requests),
        expected_deactivation_request_count=0,
        target_group_durable_record_count=target_group_durable_record_count,
        expected_target_group_durable_record_count=2,
        total_durable_record_count=len(records),
        expected_total_durable_record_count=expected_total_durable_record_count,
        consumer_cursor_byte_offset=cursor_byte_offset,
        expected_consumer_cursor_byte_offset=256,
        consumer_pending_count=consumer_pending_count,
        expected_consumer_pending_count=0,
        snapshot_management_effect_count=snapshot_management_effect_count,
        expected_snapshot_management_effect_count=1,
        occurrence_identity_mismatch_count=occurrence_identity_mismatch_count,
        receipt_commit_id=receipt_commit_id,
        receipt_before_cursor_advance_ok=receipt_before_cursor_advance_ok,
        input_to_receipt_ms=input_to_receipt_ms,
        functional_integrity_ok=functional_integrity_ok,
    )


def _build_functional_pressure_metrics(
    *,
    scenario: BaselineScenario,
    source_loader,
    records,
    snapshots=(),
) -> FunctionalPressureMetrics | None:
    if not scenario.has_functional_pressure and not scenario.has_routing_pressure:
        return None
    occurrence_started_count = 0
    occurrence_closed_count = 0
    episode_started_count = 0
    episode_closed_count = 0
    assignment_change_count = 0
    assignment_assigned_count = 0
    assignment_scheduled_count = 0
    assignment_rescheduled_count = 0
    assignment_removed_count = 0
    routing_scheduled_delay_counts_by_seconds: dict[int, int] = {}
    routing_rescheduled_delay_counts_by_seconds: dict[int, int] = {}
    routing_delayed_assignment_delay_counts_by_seconds: dict[int, int] = {}
    routing_wave_assignment_counts_by_time: dict[str, int] = {}
    routing_wave_pending_counts_by_time: dict[str, int] = {}
    journey_event_count = 0
    evidence_record_count = 0
    lifecycle_commit_count = 0
    evidence_only_commit_count = 0
    technical_hold_started_count = 0
    technical_hold_cleared_count = 0
    technical_hold_expired_count = 0
    occurrence_identity_mismatch_count = 0
    post_expiry_occurrence_started_count = 0
    post_expiry_occurrence_identity_reuse_count = 0
    invalid_occurrence_closure_reason_count = 0
    invalid_episode_closure_reason_count = 0
    pre_activation_lifecycle_commit_count = 0
    expired_alarm_keys: set[str] = set()
    lifecycle_commit_keys: set[tuple[str, int]] = set()
    duplicate_lifecycle_commit_count = 0
    initial_occurrence_ids: dict[str, str] = {}
    initial_occurrence_started_at: dict[str, datetime] = {}
    reschedule_commit_count = 0
    removal_commit_count = 0
    routing_removed_tool_counts_by_key: dict[str, int] = {}
    removed_assigned_count = 0
    removed_pending_count = 0
    scheduled_revision_keys: set[str] = set()
    rescheduled_revision_keys: set[str] = set()
    removed_revision_keys: set[str] = set()
    first_generation = source_loader.first_generation
    if first_generation is None:
        raise RuntimeError('functional pressure source loader did not initialize generation')
    expired_group_transition_count = getattr(
        source_loader, 'technical_hold_expired_group_transition_count', 0
    )
    expired_transition_count = getattr(source_loader, 'technical_hold_expired_transition_count', 0)
    reappearance_group_transition_count = getattr(
        source_loader, 'technical_hold_reappearance_group_transition_count', 0
    )
    reappearance_transition_count = getattr(
        source_loader, 'technical_hold_reappearance_transition_count', 0
    )
    initial_error_activation_group_transition_count = getattr(
        source_loader, 'initial_error_activation_group_transition_count', 0
    )
    initial_error_activation_transition_count = getattr(
        source_loader, 'initial_error_activation_transition_count', 0
    )
    # Adoption y restart de los cinco reset groups comparten el mismo bucket de generación (+60/+65); ese par es legítimo y no es un duplicate lifecycle.
    lease_loss_reset_groups = (
        {
            f'perf-group-{index + 1:03d}'
            for index in range(scenario.lease_loss_structural_reset_priority_group_count)
        }
        if scenario.has_lease_loss_adoption_pressure
        else set()
    )
    for entry in records:
        record = entry.record
        payload = record.records
        occurrence_changes = payload.get('occurrence_changes', [])
        episode_changes = payload.get('episode_changes', [])
        assignment_changes = payload.get('assignment_changes', [])
        journey_events = payload.get('journey_events', [])
        evidence_records = payload.get('evidence_records', [])
        for item in occurrence_changes:
            if item['kind'] == 'STARTED':
                occurrence_started_count += 1
                initial_occurrence_id = initial_occurrence_ids.get(item['alarm_key'])
                if initial_occurrence_id is None:
                    initial_occurrence_ids[item['alarm_key']] = item['occurrence_id']
                    started_at_value = item.get('started_at')
                    if isinstance(started_at_value, str):
                        initial_occurrence_started_at[item['alarm_key']] = datetime.fromisoformat(
                            started_at_value.replace('Z', '+00:00')
                        )
                elif (
                    scenario.technical_hold_expiry_percent > 0
                    and item['alarm_key'] in expired_alarm_keys
                ):
                    post_expiry_occurrence_started_count += 1
                    if item['occurrence_id'] == initial_occurrence_id:
                        post_expiry_occurrence_identity_reuse_count += 1
            elif item['kind'] == 'CLOSED':
                occurrence_closed_count += 1
                if (
                    scenario.technical_hold_expiry_percent > 0
                    and item.get('closure_reason') != 'technical_hold_expired'
                ):
                    invalid_occurrence_closure_reason_count += 1
        episode_started_count += sum(item['kind'] == 'STARTED' for item in episode_changes)
        for item in episode_changes:
            if item['kind'] == 'CLOSED':
                episode_closed_count += 1
                if (
                    scenario.technical_hold_expiry_percent > 0
                    and item.get('closure_reason') != 'technical_uncertainty'
                ):
                    invalid_episode_closure_reason_count += 1
        assignment_change_count += len(assignment_changes)
        assignment_assigned_count += sum(
            item.get('kind') == 'ASSIGNED' for item in assignment_changes
        )
        assignment_scheduled_count += sum(
            item.get('kind') == 'SCHEDULED' for item in assignment_changes
        )
        assignment_rescheduled_count += sum(
            item.get('kind') == 'RESCHEDULED' for item in assignment_changes
        )
        assignment_removed_count += sum(
            item.get('kind') == 'REMOVED' for item in assignment_changes
        )
        if scenario.has_routing_pressure:
            for item in assignment_changes:
                expected_occurrence_id = initial_occurrence_ids.get(item.get('alarm_key', ''))
                if (
                    expected_occurrence_id is not None
                    and item.get('occurrence_id') != expected_occurrence_id
                ):
                    occurrence_identity_mismatch_count += 1
        if scenario.has_c2_routing_pressure:
            if any(item.get('kind') == 'RESCHEDULED' for item in assignment_changes):
                reschedule_commit_count += 1
            if any(item.get('kind') == 'REMOVED' for item in assignment_changes):
                removal_commit_count += 1
                removed_revision_keys.add(record.commit.alarm_configuration_revision)
            for item in assignment_changes:
                kind = item.get('kind')
                started_at = initial_occurrence_started_at.get(item.get('alarm_key', ''))
                if kind == 'REMOVED':
                    tool_key = str(item.get('tool_key', ''))
                    routing_removed_tool_counts_by_key[tool_key] = (
                        routing_removed_tool_counts_by_key.get(tool_key, 0) + 1
                    )
                    if started_at is not None and tool_key.startswith('perf-route-'):
                        try:
                            destination_index = int(tool_key.rsplit('-', 1)[1]) - 1
                        except ValueError:
                            destination_index = -1
                        if 0 <= destination_index < len(scenario.c2_routing_delay_seconds):
                            original_delay = scenario.c2_routing_delay_seconds[destination_index]
                            if original_delay <= scenario.c2_remove_destinations_phase_a_seconds:
                                removed_assigned_count += 1
                            else:
                                removed_pending_count += 1
                if (
                    kind == 'ASSIGNED'
                    and item.get('tool_key') != 'perf-tool'
                    and started_at is not None
                    and isinstance(item.get('effective_at'), str)
                ):
                    assigned_at = datetime.fromisoformat(
                        item['effective_at'].replace('Z', '+00:00')
                    )
                    delay_seconds = int((assigned_at - started_at).total_seconds())
                    routing_delayed_assignment_delay_counts_by_seconds[delay_seconds] = (
                        routing_delayed_assignment_delay_counts_by_seconds.get(delay_seconds, 0) + 1
                    )
                if kind not in {'SCHEDULED', 'RESCHEDULED'}:
                    continue
                due_at_value = item.get('due_at')
                if not isinstance(due_at_value, str) or started_at is None:
                    continue
                due_at = datetime.fromisoformat(due_at_value.replace('Z', '+00:00'))
                delay_seconds = int((due_at - started_at).total_seconds())
                if kind == 'SCHEDULED':
                    routing_scheduled_delay_counts_by_seconds[delay_seconds] = (
                        routing_scheduled_delay_counts_by_seconds.get(delay_seconds, 0) + 1
                    )
                    scheduled_revision_keys.add(record.commit.alarm_configuration_revision)
                else:
                    routing_rescheduled_delay_counts_by_seconds[delay_seconds] = (
                        routing_rescheduled_delay_counts_by_seconds.get(delay_seconds, 0) + 1
                    )
                    rescheduled_revision_keys.add(record.commit.alarm_configuration_revision)
            if assignment_changes:
                snapshot_document = record.snapshot_after.as_document()
                assigned_in_snapshot = 0
                pending_in_snapshot = 0
                for alarm_document in snapshot_document['alarms'].values():
                    occurrence = alarm_document.get('occurrence')
                    if occurrence is None:
                        continue
                    assigned_in_snapshot += len(occurrence['assignments'])
                    pending_in_snapshot += len(occurrence['pending_assignments'])
                routing_wave_assignment_counts_by_time[record.commit.evaluated_at] = (
                    routing_wave_assignment_counts_by_time.get(record.commit.evaluated_at, 0)
                    + assigned_in_snapshot
                )
                routing_wave_pending_counts_by_time[record.commit.evaluated_at] = (
                    routing_wave_pending_counts_by_time.get(record.commit.evaluated_at, 0)
                    + pending_in_snapshot
                )
        journey_event_count += len(journey_events)
        evidence_record_count += len(evidence_records)
        technical_events = []
        for item in journey_events:
            event_key = item.get('event_key')
            if event_key == 'technical_hold_started':
                technical_hold_started_count += 1
                technical_events.append(item)
            elif event_key == 'technical_hold_recovered':
                technical_hold_cleared_count += 1
                technical_events.append(item)
            elif event_key == 'technical_hold_expired':
                technical_hold_expired_count += 1
                expired_alarm_keys.add(item['alarm_key'])
                technical_events.append(item)
        for item in technical_events:
            expected_occurrence_id = initial_occurrence_ids.get(item['alarm_key'])
            if (
                expected_occurrence_id is None
                or item.get('occurrence_id') != expected_occurrence_id
            ):
                occurrence_identity_mismatch_count += 1
        has_lifecycle_changes = bool(
            occurrence_changes or episode_changes or assignment_changes or technical_events
        )
        if has_lifecycle_changes:
            lifecycle_commit_count += 1
            evaluated_at = datetime.fromisoformat(record.commit.evaluated_at.replace('Z', '+00:00'))
            generation = int(evaluated_at.timestamp()) // scenario.data_refresh_seconds
            generation_index = generation - first_generation
            if scenario.initial_error_activation_percent > 0:
                first_activation_generation = (
                    scenario.initial_error_hold_seconds // scenario.data_refresh_seconds
                )
                if generation_index < first_activation_generation:
                    pre_activation_lifecycle_commit_count += 1
            key = (record.commit.priority_group, generation_index)
            is_lease_loss_restart = (
                scenario.has_lease_loss_adoption_pressure
                and record.commit.priority_group in lease_loss_reset_groups
                and any(item.get('kind') == 'STARTED' for item in occurrence_changes)
                and record.commit.alarm_configuration_revision == 'PERF-AC-2'
            )
            if key in lifecycle_commit_keys and not is_lease_loss_restart:
                duplicate_lifecycle_commit_count += 1
            lifecycle_commit_keys.add(key)
        elif evidence_records:
            evidence_only_commit_count += 1
    snapshot_assignment_count = 0
    snapshot_pending_assignment_count = 0
    if scenario.has_routing_pressure:
        for snapshot in snapshots:
            for alarm_document in snapshot.as_document()['alarms'].values():
                occurrence = alarm_document.get('occurrence')
                if occurrence is None:
                    continue
                snapshot_assignment_count += len(occurrence['assignments'])
                snapshot_pending_assignment_count += len(occurrence['pending_assignments'])

    if scenario.initial_error_activation_percent > 0:
        expected_lifecycle_commit_count = initial_error_activation_group_transition_count
    elif scenario.fixed_initial_error_percent > 0:
        expected_lifecycle_commit_count = (
            scenario.initial_active_alarm_count // scenario.effective_priority_group_size
        )
    else:
        expected_lifecycle_commit_count = (
            scenario.priority_group_count
            + source_loader.churn_group_transition_count
            + expired_group_transition_count
        )
        if scenario.has_lease_loss_adoption_pressure:
            expected_lifecycle_commit_count += (
                scenario.lease_loss_structural_reset_priority_group_count * 2
            )
    if scenario.technical_hold_expiry_percent > 0:
        expected_occurrence_started_count = (
            scenario.initial_active_alarm_count + reappearance_transition_count
        )
        expected_occurrence_closed_count = expired_transition_count
        expected_episode_started_count = (
            scenario.priority_group_count + reappearance_group_transition_count
        )
        expected_episode_closed_count = expired_group_transition_count
        expected_assignment_change_count = expected_occurrence_started_count
        expected_technical_hold_started_count = (
            source_loader.technical_hold_started_transition_count
        )
        expected_technical_hold_cleared_count = 0
        expected_technical_hold_expired_count = expired_transition_count
        expected_post_expiry_occurrence_started_count = reappearance_transition_count
    elif scenario.initial_error_activation_percent > 0:
        expected_occurrence_started_count = initial_error_activation_transition_count
        expected_occurrence_closed_count = 0
        expected_episode_started_count = initial_error_activation_group_transition_count
        expected_episode_closed_count = 0
        expected_assignment_change_count = initial_error_activation_transition_count
        expected_technical_hold_started_count = 0
        expected_technical_hold_cleared_count = 0
        expected_technical_hold_expired_count = 0
        expected_post_expiry_occurrence_started_count = 0
    elif scenario.fixed_initial_error_percent > 0:
        expected_occurrence_started_count = scenario.initial_active_alarm_count
        expected_occurrence_closed_count = 0
        expected_episode_started_count = (
            scenario.initial_active_alarm_count // scenario.effective_priority_group_size
        )
        expected_episode_closed_count = 0
        expected_assignment_change_count = scenario.initial_active_alarm_count
        expected_technical_hold_started_count = 0
        expected_technical_hold_cleared_count = 0
        expected_technical_hold_expired_count = 0
        expected_post_expiry_occurrence_started_count = 0
    elif scenario.technical_hold_churn_percent > 0:
        expected_episode_started_count = scenario.priority_group_count
        expected_episode_closed_count = 0
        expected_occurrence_started_count = scenario.initial_active_alarm_count
        expected_occurrence_closed_count = 0
        expected_assignment_change_count = scenario.initial_active_alarm_count
        expected_technical_hold_started_count = (
            source_loader.technical_hold_started_transition_count
        )
        expected_technical_hold_cleared_count = (
            source_loader.technical_hold_cleared_transition_count
        )
        expected_technical_hold_expired_count = 0
        expected_post_expiry_occurrence_started_count = 0
    else:
        expected_episode_started_count = scenario.priority_group_count
        expected_episode_closed_count = 0
        expected_occurrence_started_count = (
            scenario.initial_active_alarm_count + source_loader.churn_transition_count // 2
        )
        expected_occurrence_closed_count = source_loader.churn_transition_count // 2
        expected_assignment_change_count = expected_occurrence_started_count
        if scenario.has_lease_loss_adoption_pressure:
            active_reset_alarm_count = (
                scenario.lease_loss_structural_reset_alarm_count
                * scenario.initial_active_percent
                // 100
            )
            expected_episode_started_count += (
                scenario.lease_loss_structural_reset_priority_group_count
            )
            expected_episode_closed_count += (
                scenario.lease_loss_structural_reset_priority_group_count
            )
            expected_occurrence_started_count += active_reset_alarm_count
            expected_occurrence_closed_count += active_reset_alarm_count
            expected_assignment_change_count += active_reset_alarm_count
        expected_technical_hold_started_count = 0
        expected_technical_hold_cleared_count = 0
        expected_technical_hold_expired_count = 0
        expected_post_expiry_occurrence_started_count = 0
    expected_assignment_scheduled_count = 0
    expected_assignment_rescheduled_count = 0
    expected_assignment_removed_count = 0
    expected_routing_removed_tool_counts: tuple[int, ...] = ()
    expected_removed_assigned_count = 0
    expected_removed_pending_count = 0
    expected_removal_commit_count = 0
    expected_routing_scheduled_delay_counts: tuple[int, ...] = ()
    expected_routing_rescheduled_delay_counts: tuple[int, ...] = ()
    expected_routing_delayed_assignment_delay_counts: tuple[int, ...] = ()
    expected_reschedule_commit_count = 0
    routing_revision_transition_ok = True
    expected_routing_wave_assignment_counts: tuple[int, ...] = ()
    expected_routing_wave_pending_counts: tuple[int, ...] = ()
    if scenario.has_c1_routing_pressure:
        expected_assignment_assigned_count = expected_occurrence_started_count * (
            scenario.c1_routing_destination_count + 1
        )
        expected_assignment_change_count = expected_assignment_assigned_count
        expected_snapshot_assignment_count = expected_assignment_assigned_count
    elif scenario.has_c2_routing_pressure:
        destination_count = len(scenario.c2_routing_delay_seconds)
        if scenario.has_c2_remove_destinations_pressure:
            reached_destination_count = sum(
                delay <= scenario.c2_remove_destinations_phase_a_seconds
                for delay in scenario.c2_routing_delay_seconds
            )
            pending_destination_count = destination_count - reached_destination_count
            expected_assignment_assigned_count = expected_occurrence_started_count * (
                reached_destination_count + 1
            )
            expected_assignment_scheduled_count = (
                expected_occurrence_started_count * destination_count
            )
            expected_assignment_removed_count = (
                expected_occurrence_started_count * destination_count
            )
            expected_assignment_change_count = (
                expected_assignment_assigned_count
                + expected_assignment_scheduled_count
                + expected_assignment_removed_count
            )
            expected_snapshot_assignment_count = expected_occurrence_started_count
            expected_lifecycle_commit_count = scenario.priority_group_count * (
                reached_destination_count + 2
            )
            expected_routing_scheduled_delay_counts = tuple(
                scenario.alarm_count for _ in scenario.c2_routing_delay_seconds
            )
            expected_routing_delayed_assignment_delay_counts = tuple(
                scenario.alarm_count
                if delay <= scenario.c2_remove_destinations_phase_a_seconds
                else 0
                for delay in scenario.c2_routing_delay_seconds
            )
            expected_routing_removed_tool_counts = tuple(
                scenario.alarm_count for _ in scenario.c2_routing_delay_seconds
            )
            expected_removed_assigned_count = scenario.alarm_count * reached_destination_count
            expected_removed_pending_count = scenario.alarm_count * pending_destination_count
            expected_removal_commit_count = scenario.priority_group_count
            routing_revision_transition_ok = (
                len(scheduled_revision_keys) == 1
                and len(removed_revision_keys) == 1
                and scheduled_revision_keys.isdisjoint(removed_revision_keys)
            )
            expected_routing_wave_assignment_counts = (
                scenario.alarm_count,
                *(
                    scenario.alarm_count * (wave_index + 2)
                    for wave_index in range(reached_destination_count)
                ),
                scenario.alarm_count,
            )
            expected_routing_wave_pending_counts = (
                scenario.alarm_count * destination_count,
                *(
                    scenario.alarm_count * (destination_count - wave_index - 1)
                    for wave_index in range(reached_destination_count)
                ),
                0,
            )
        else:
            expected_assignment_assigned_count = expected_occurrence_started_count * (
                destination_count + 1
            )
            expected_assignment_scheduled_count = (
                expected_occurrence_started_count * destination_count
            )
            if scenario.has_c2_routing_adoption_pressure:
                expected_rescheduled_destinations = sum(
                    source_delay > scenario.c2_routing_adoption_at_seconds
                    and target_delay > scenario.c2_routing_adoption_at_seconds
                    and source_delay != target_delay
                    for source_delay, target_delay in zip(
                        scenario.c2_routing_delay_seconds,
                        scenario.c2_routing_adoption_target_delay_seconds,
                        strict=True,
                    )
                )
                expected_assignment_rescheduled_count = (
                    expected_occurrence_started_count * expected_rescheduled_destinations
                )
            else:
                expected_assignment_rescheduled_count = (
                    expected_occurrence_started_count * destination_count
                    if scenario.has_c2_reschedule_pressure
                    else 0
                )
            expected_assignment_change_count = (
                expected_assignment_assigned_count
                + expected_assignment_scheduled_count
                + expected_assignment_rescheduled_count
            )
            expected_snapshot_assignment_count = expected_assignment_assigned_count
            if scenario.has_c2_routing_adoption_pressure:
                source_wave_count = sum(
                    delay < scenario.c2_routing_adoption_at_seconds
                    for delay in scenario.c2_routing_delay_seconds
                )
                target_wave_count = sum(
                    delay > scenario.c2_routing_adoption_at_seconds
                    for delay in scenario.c2_routing_adoption_target_delay_seconds
                )
                expected_lifecycle_commit_count = scenario.priority_group_count * (
                    2 + source_wave_count + target_wave_count
                )
            else:
                expected_lifecycle_commit_count = scenario.priority_group_count * (
                    destination_count + (2 if scenario.has_c2_reschedule_pressure else 1)
                )
            expected_routing_scheduled_delay_counts = tuple(
                scenario.alarm_count for _ in scenario.c2_routing_delay_seconds
            )
            effective_delays = (
                scenario.c2_routing_adoption_target_delay_seconds
                if scenario.has_c2_routing_adoption_pressure
                else scenario.c2_reschedule_delay_seconds
                if scenario.has_c2_reschedule_pressure
                else scenario.c2_routing_delay_seconds
            )
            expected_routing_delayed_assignment_delay_counts = tuple(
                scenario.alarm_count for _ in effective_delays
            )
            if scenario.has_c2_routing_adoption_pressure:
                expected_reschedule_commit_count = scenario.priority_group_count
                routing_revision_transition_ok = (
                    len(scheduled_revision_keys) == 1
                    and len(rescheduled_revision_keys) == 1
                    and scheduled_revision_keys.isdisjoint(rescheduled_revision_keys)
                )
                source_reached = sum(
                    delay < scenario.c2_routing_adoption_at_seconds
                    for delay in scenario.c2_routing_delay_seconds
                )
                target_reached = sum(
                    delay <= scenario.c2_routing_adoption_at_seconds
                    for delay in scenario.c2_routing_adoption_target_delay_seconds
                )
                target_future = sum(
                    delay > scenario.c2_routing_adoption_at_seconds
                    for delay in scenario.c2_routing_adoption_target_delay_seconds
                )
                expected_routing_wave_assignment_counts = (
                    scenario.alarm_count,
                    *(
                        scenario.alarm_count * (wave_index + 2)
                        for wave_index in range(source_reached)
                    ),
                    scenario.alarm_count * (target_reached + 1),
                    *(
                        scenario.alarm_count * (target_reached + wave_index + 2)
                        for wave_index in range(target_future)
                    ),
                )
                expected_routing_wave_pending_counts = (
                    scenario.alarm_count * destination_count,
                    *(
                        scenario.alarm_count * (destination_count - wave_index - 1)
                        for wave_index in range(source_reached)
                    ),
                    scenario.alarm_count * target_future,
                    *(
                        scenario.alarm_count * (target_future - wave_index - 1)
                        for wave_index in range(target_future)
                    ),
                )
            elif scenario.has_c2_reschedule_pressure:
                expected_routing_rescheduled_delay_counts = tuple(
                    scenario.alarm_count for _ in scenario.c2_reschedule_delay_seconds
                )
                expected_reschedule_commit_count = scenario.priority_group_count
                routing_revision_transition_ok = (
                    len(scheduled_revision_keys) == 1
                    and len(rescheduled_revision_keys) == 1
                    and scheduled_revision_keys.isdisjoint(rescheduled_revision_keys)
                )
                expected_routing_wave_assignment_counts = (
                    scenario.alarm_count,
                    scenario.alarm_count,
                    *(
                        scenario.alarm_count * (wave_index + 2)
                        for wave_index in range(destination_count)
                    ),
                )
                expected_routing_wave_pending_counts = (
                    scenario.alarm_count * destination_count,
                    scenario.alarm_count * destination_count,
                    *(
                        scenario.alarm_count * (destination_count - wave_index - 1)
                        for wave_index in range(destination_count)
                    ),
                )
            else:
                expected_routing_wave_assignment_counts = tuple(
                    scenario.alarm_count * (wave_index + 1)
                    for wave_index in range(destination_count + 1)
                )
                expected_routing_wave_pending_counts = tuple(
                    scenario.alarm_count * (destination_count - wave_index)
                    for wave_index in range(destination_count + 1)
                )
    else:
        expected_assignment_assigned_count = expected_assignment_change_count
        expected_snapshot_assignment_count = 0
    expected_snapshot_pending_assignment_count = 0
    routing_scheduled_delay_counts = tuple(
        routing_scheduled_delay_counts_by_seconds.get(delay_seconds, 0)
        for delay_seconds in scenario.c2_routing_delay_seconds
    )
    routing_rescheduled_delay_counts = tuple(
        routing_rescheduled_delay_counts_by_seconds.get(delay_seconds, 0)
        for delay_seconds in scenario.c2_reschedule_delay_seconds
    )
    routing_removed_tool_counts = tuple(
        routing_removed_tool_counts_by_key.get(f'perf-route-{index + 1:02d}', 0)
        for index in range(len(scenario.c2_routing_delay_seconds))
    )
    effective_assignment_delays = (
        scenario.c2_routing_adoption_target_delay_seconds
        if scenario.has_c2_routing_adoption_pressure
        else scenario.c2_reschedule_delay_seconds
        if scenario.has_c2_reschedule_pressure
        else scenario.c2_routing_delay_seconds
    )
    routing_delayed_assignment_delay_counts = tuple(
        routing_delayed_assignment_delay_counts_by_seconds.get(delay_seconds, 0)
        for delay_seconds in effective_assignment_delays
    )
    routing_wave_times = tuple(sorted(routing_wave_assignment_counts_by_time))
    routing_wave_assignment_counts = tuple(
        routing_wave_assignment_counts_by_time[wave_time] for wave_time in routing_wave_times
    )
    routing_wave_pending_counts = tuple(
        routing_wave_pending_counts_by_time[wave_time] for wave_time in routing_wave_times
    )

    functional_integrity_ok = (
        lifecycle_commit_count == expected_lifecycle_commit_count
        and duplicate_lifecycle_commit_count == 0
        and occurrence_started_count == expected_occurrence_started_count
        and occurrence_closed_count == expected_occurrence_closed_count
        and episode_started_count == expected_episode_started_count
        and episode_closed_count == expected_episode_closed_count
        and assignment_change_count == expected_assignment_change_count
        and assignment_assigned_count == expected_assignment_assigned_count
        and assignment_scheduled_count == expected_assignment_scheduled_count
        and assignment_rescheduled_count == expected_assignment_rescheduled_count
        and assignment_removed_count == expected_assignment_removed_count
        and (
            not scenario.has_c2_remove_destinations_pressure
            or routing_removed_tool_counts == expected_routing_removed_tool_counts
        )
        and removed_assigned_count == expected_removed_assigned_count
        and removed_pending_count == expected_removed_pending_count
        and removal_commit_count == expected_removal_commit_count
        and (
            not scenario.has_c2_routing_pressure
            or routing_scheduled_delay_counts == expected_routing_scheduled_delay_counts
        )
        and (
            not scenario.has_c2_reschedule_pressure
            or routing_rescheduled_delay_counts == expected_routing_rescheduled_delay_counts
        )
        and (
            not scenario.has_c2_routing_pressure
            or routing_delayed_assignment_delay_counts
            == expected_routing_delayed_assignment_delay_counts
        )
        and (
            not (scenario.has_c2_reschedule_pressure or scenario.has_c2_routing_adoption_pressure)
            or reschedule_commit_count == expected_reschedule_commit_count
        )
        and routing_revision_transition_ok
        and (
            not scenario.has_c2_routing_pressure
            or routing_wave_assignment_counts == expected_routing_wave_assignment_counts
        )
        and (
            not scenario.has_c2_routing_pressure
            or routing_wave_pending_counts == expected_routing_wave_pending_counts
        )
        and (
            not scenario.has_routing_pressure
            or snapshot_assignment_count == expected_snapshot_assignment_count
        )
        and (
            not scenario.has_routing_pressure
            or snapshot_pending_assignment_count == expected_snapshot_pending_assignment_count
        )
        and technical_hold_started_count == expected_technical_hold_started_count
        and technical_hold_cleared_count == expected_technical_hold_cleared_count
        and technical_hold_expired_count == expected_technical_hold_expired_count
        and occurrence_identity_mismatch_count == 0
        and post_expiry_occurrence_started_count == expected_post_expiry_occurrence_started_count
        and post_expiry_occurrence_identity_reuse_count == 0
        and invalid_occurrence_closure_reason_count == 0
        and invalid_episode_closure_reason_count == 0
        and pre_activation_lifecycle_commit_count == 0
    )
    return FunctionalPressureMetrics(
        priority_group_count=scenario.priority_group_count,
        priority_group_size=scenario.effective_priority_group_size,
        operational_churn_percent=scenario.operational_churn_percent,
        technical_hold_churn_percent=scenario.technical_hold_churn_percent,
        technical_hold_expiry_percent=scenario.technical_hold_expiry_percent,
        technical_hold_expiry_stagger_seconds=scenario.technical_hold_expiry_stagger_seconds,
        technical_hold_error_duration_seconds=scenario.technical_hold_error_duration_seconds,
        initial_error_activation_percent=scenario.initial_error_activation_percent,
        initial_error_hold_seconds=scenario.initial_error_hold_seconds,
        initial_error_activation_stagger_seconds=scenario.initial_error_activation_stagger_seconds,
        fixed_initial_error_percent=scenario.fixed_initial_error_percent,
        initial_active_percent=scenario.initial_active_percent,
        churn_generation_count=source_loader.churn_generation_count,
        planned_state_transition_count=source_loader.churn_transition_count,
        lifecycle_commit_count=lifecycle_commit_count,
        expected_lifecycle_commit_count=expected_lifecycle_commit_count,
        duplicate_lifecycle_commit_count=duplicate_lifecycle_commit_count,
        occurrence_started_count=occurrence_started_count,
        expected_occurrence_started_count=expected_occurrence_started_count,
        occurrence_closed_count=occurrence_closed_count,
        expected_occurrence_closed_count=expected_occurrence_closed_count,
        episode_started_count=episode_started_count,
        expected_episode_started_count=expected_episode_started_count,
        episode_closed_count=episode_closed_count,
        expected_episode_closed_count=expected_episode_closed_count,
        assignment_change_count=assignment_change_count,
        expected_assignment_change_count=expected_assignment_change_count,
        c1_routing_destination_count=scenario.c1_routing_destination_count,
        c2_routing_delay_seconds=scenario.c2_routing_delay_seconds,
        c2_reschedule_delay_seconds=scenario.c2_reschedule_delay_seconds,
        c2_reschedule_phase_a_seconds=scenario.c2_reschedule_phase_a_seconds,
        c2_remove_destinations_phase_a_seconds=(scenario.c2_remove_destinations_phase_a_seconds),
        routing_criticality=scenario.routing_criticality,
        assignment_assigned_count=assignment_assigned_count,
        expected_assignment_assigned_count=expected_assignment_assigned_count,
        assignment_scheduled_count=assignment_scheduled_count,
        expected_assignment_scheduled_count=expected_assignment_scheduled_count,
        assignment_rescheduled_count=assignment_rescheduled_count,
        expected_assignment_rescheduled_count=expected_assignment_rescheduled_count,
        assignment_removed_count=assignment_removed_count,
        expected_assignment_removed_count=expected_assignment_removed_count,
        routing_removed_tool_counts=routing_removed_tool_counts,
        expected_routing_removed_tool_counts=expected_routing_removed_tool_counts,
        removed_assigned_count=removed_assigned_count,
        expected_removed_assigned_count=expected_removed_assigned_count,
        removed_pending_count=removed_pending_count,
        expected_removed_pending_count=expected_removed_pending_count,
        removal_commit_count=removal_commit_count,
        expected_removal_commit_count=expected_removal_commit_count,
        routing_scheduled_delay_counts=routing_scheduled_delay_counts,
        expected_routing_scheduled_delay_counts=expected_routing_scheduled_delay_counts,
        routing_rescheduled_delay_counts=routing_rescheduled_delay_counts,
        expected_routing_rescheduled_delay_counts=expected_routing_rescheduled_delay_counts,
        routing_delayed_assignment_delay_counts=routing_delayed_assignment_delay_counts,
        expected_routing_delayed_assignment_delay_counts=(
            expected_routing_delayed_assignment_delay_counts
        ),
        reschedule_commit_count=reschedule_commit_count,
        expected_reschedule_commit_count=expected_reschedule_commit_count,
        routing_revision_transition_ok=routing_revision_transition_ok,
        routing_wave_assignment_counts=routing_wave_assignment_counts,
        expected_routing_wave_assignment_counts=expected_routing_wave_assignment_counts,
        routing_wave_pending_counts=routing_wave_pending_counts,
        expected_routing_wave_pending_counts=expected_routing_wave_pending_counts,
        snapshot_assignment_count=snapshot_assignment_count,
        expected_snapshot_assignment_count=expected_snapshot_assignment_count,
        snapshot_pending_assignment_count=snapshot_pending_assignment_count,
        expected_snapshot_pending_assignment_count=expected_snapshot_pending_assignment_count,
        technical_hold_started_count=technical_hold_started_count,
        expected_technical_hold_started_count=expected_technical_hold_started_count,
        technical_hold_cleared_count=technical_hold_cleared_count,
        expected_technical_hold_cleared_count=expected_technical_hold_cleared_count,
        technical_hold_expired_count=technical_hold_expired_count,
        expected_technical_hold_expired_count=expected_technical_hold_expired_count,
        occurrence_identity_mismatch_count=occurrence_identity_mismatch_count,
        post_expiry_occurrence_started_count=post_expiry_occurrence_started_count,
        expected_post_expiry_occurrence_started_count=(
            expected_post_expiry_occurrence_started_count
        ),
        post_expiry_occurrence_identity_reuse_count=(post_expiry_occurrence_identity_reuse_count),
        invalid_occurrence_closure_reason_count=invalid_occurrence_closure_reason_count,
        invalid_episode_closure_reason_count=invalid_episode_closure_reason_count,
        initial_error_activation_count=initial_error_activation_transition_count,
        expected_initial_error_activation_count=initial_error_activation_transition_count,
        pre_activation_lifecycle_commit_count=pre_activation_lifecycle_commit_count,
        journey_event_count=journey_event_count,
        evidence_record_count=evidence_record_count,
        evidence_only_commit_count=evidence_only_commit_count,
        functional_integrity_ok=functional_integrity_ok,
    )


if __name__ == '__main__':
    raise SystemExit(main())
