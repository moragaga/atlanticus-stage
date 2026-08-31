from __future__ import annotations

import json
import math
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import psutil

from ada.processes.alarms_runtime import AlarmRuntimeJobComposition
from atlanticus.runtime import JobRuntimeContext


@dataclass(frozen=True, slots=True)
class IterationSample:
    iteration: int
    started_monotonic: float
    start_interval_ms: float | None
    duration_ms: float
    cpu_percent: float
    rss_before_mb: float
    rss_after_mb: float
    revision_origin: str
    adoption_outcome: str
    cycle_executed: bool
    degraded: bool


@dataclass(frozen=True, slots=True)
class DurableHistoryLookupMetrics:
    mode: str
    cycle_count: int
    lookup_call_count: int
    lookup_total_ms: float
    durable_record_scan_count: int
    durable_record_scan_total_ms: float
    durable_record_entries_seen: int
    index_build_count: int
    index_build_total_ms: float


@dataclass(frozen=True, slots=True)
class FunctionalPressureMetrics:
    priority_group_count: int
    priority_group_size: int
    operational_churn_percent: int
    technical_hold_churn_percent: int
    technical_hold_expiry_percent: int
    technical_hold_expiry_stagger_seconds: int
    technical_hold_error_duration_seconds: int
    initial_error_activation_percent: int
    initial_error_hold_seconds: int
    initial_error_activation_stagger_seconds: int
    fixed_initial_error_percent: int
    initial_active_percent: int
    churn_generation_count: int
    planned_state_transition_count: int
    lifecycle_commit_count: int
    expected_lifecycle_commit_count: int
    duplicate_lifecycle_commit_count: int
    occurrence_started_count: int
    expected_occurrence_started_count: int
    occurrence_closed_count: int
    expected_occurrence_closed_count: int
    episode_started_count: int
    expected_episode_started_count: int
    episode_closed_count: int
    expected_episode_closed_count: int
    assignment_change_count: int
    expected_assignment_change_count: int
    c1_routing_destination_count: int
    c2_routing_delay_seconds: tuple[int, ...]
    c2_reschedule_delay_seconds: tuple[int, ...]
    c2_reschedule_phase_a_seconds: int
    c2_remove_destinations_phase_a_seconds: int
    routing_criticality: str
    assignment_assigned_count: int
    expected_assignment_assigned_count: int
    assignment_scheduled_count: int
    expected_assignment_scheduled_count: int
    assignment_rescheduled_count: int
    expected_assignment_rescheduled_count: int
    assignment_removed_count: int
    expected_assignment_removed_count: int
    routing_removed_tool_counts: tuple[int, ...]
    expected_routing_removed_tool_counts: tuple[int, ...]
    removed_assigned_count: int
    expected_removed_assigned_count: int
    removed_pending_count: int
    expected_removed_pending_count: int
    removal_commit_count: int
    expected_removal_commit_count: int
    routing_scheduled_delay_counts: tuple[int, ...]
    expected_routing_scheduled_delay_counts: tuple[int, ...]
    routing_rescheduled_delay_counts: tuple[int, ...]
    expected_routing_rescheduled_delay_counts: tuple[int, ...]
    routing_delayed_assignment_delay_counts: tuple[int, ...]
    expected_routing_delayed_assignment_delay_counts: tuple[int, ...]
    reschedule_commit_count: int
    expected_reschedule_commit_count: int
    routing_revision_transition_ok: bool
    routing_wave_assignment_counts: tuple[int, ...]
    expected_routing_wave_assignment_counts: tuple[int, ...]
    routing_wave_pending_counts: tuple[int, ...]
    expected_routing_wave_pending_counts: tuple[int, ...]
    snapshot_assignment_count: int
    expected_snapshot_assignment_count: int
    snapshot_pending_assignment_count: int
    expected_snapshot_pending_assignment_count: int
    technical_hold_started_count: int
    expected_technical_hold_started_count: int
    technical_hold_cleared_count: int
    expected_technical_hold_cleared_count: int
    technical_hold_expired_count: int
    expected_technical_hold_expired_count: int
    occurrence_identity_mismatch_count: int
    post_expiry_occurrence_started_count: int
    expected_post_expiry_occurrence_started_count: int
    post_expiry_occurrence_identity_reuse_count: int
    invalid_occurrence_closure_reason_count: int
    invalid_episode_closure_reason_count: int
    initial_error_activation_count: int
    expected_initial_error_activation_count: int
    pre_activation_lifecycle_commit_count: int
    journey_event_count: int
    evidence_record_count: int
    evidence_only_commit_count: int
    functional_integrity_ok: bool


@dataclass(frozen=True, slots=True)
class ManagementPressureMetrics:
    action_at_seconds: int
    input_id: str
    target_alarm_key: str
    target_occurrence_id: str
    input_receipt_count: int
    expected_input_receipt_count: int
    effective_receipt_count: int
    expected_effective_receipt_count: int
    management_effect_started_count: int
    expected_management_effect_started_count: int
    management_effect_cleared_count: int
    expected_management_effect_cleared_count: int
    management_commit_count: int
    expected_management_commit_count: int
    deactivation_request_count: int
    expected_deactivation_request_count: int
    target_group_durable_record_count: int
    expected_target_group_durable_record_count: int
    total_durable_record_count: int
    expected_total_durable_record_count: int
    consumer_cursor_byte_offset: int | None
    expected_consumer_cursor_byte_offset: int
    consumer_pending_count: int
    expected_consumer_pending_count: int
    snapshot_management_effect_count: int
    expected_snapshot_management_effect_count: int
    occurrence_identity_mismatch_count: int
    receipt_commit_id: str | None
    receipt_before_cursor_advance_ok: bool
    input_to_receipt_ms: float | None
    functional_integrity_ok: bool


@dataclass(frozen=True, slots=True)
class SustainedManagementPressureMetrics:
    action_at_seconds: int
    action_count: int
    action_interval_seconds: int
    arrival_mode: str
    first_input_id: str
    last_input_id: str
    input_receipt_count: int
    expected_input_receipt_count: int
    effective_receipt_count: int
    expected_effective_receipt_count: int
    management_effect_started_count: int
    expected_management_effect_started_count: int
    management_effect_cleared_count: int
    expected_management_effect_cleared_count: int
    management_commit_count: int
    deactivation_request_count: int
    expected_deactivation_request_count: int
    lost_input_count: int
    duplicate_receipt_count: int
    unique_target_count: int
    expected_unique_target_count: int
    consumer_cursor_byte_offset: int | None
    expected_consumer_cursor_byte_offset: int
    consumer_pending_count: int
    expected_consumer_pending_count: int
    consumer_pending_high_water_count: int
    max_batch_size: int
    nonempty_batch_count: int
    nonempty_batch_sizes: tuple[int, ...]
    first_nonempty_batch_size: int
    expected_first_nonempty_batch_size: int | None
    fully_absorbed_in_first_eligible_iteration: bool | None
    snapshot_management_effect_count: int
    expected_snapshot_management_effect_count: int
    occurrence_identity_mismatch_count: int
    receipt_before_cursor_checked_count: int
    receipt_before_cursor_advance_ok: bool
    input_to_receipt_p50_ms: float
    input_to_receipt_p95_ms: float
    input_to_receipt_p99_ms: float
    input_to_receipt_max_ms: float
    durable_record_count: int
    functional_integrity_ok: bool


@dataclass(frozen=True, slots=True)
class DeactivationDecisionPressureMetrics:
    request_at_seconds: int
    decision_at_seconds: int
    deactivation_window_seconds: int
    management_input_id: str
    request_id: str
    decision_id: str
    target_alarm_key: str
    target_occurrence_id: str
    request_receipt_count: int
    expected_request_receipt_count: int
    pending_approval_receipt_count: int
    expected_pending_approval_receipt_count: int
    deactivation_request_count: int
    expected_deactivation_request_count: int
    approval_required_request_count: int
    expected_approval_required_request_count: int
    decision_receipt_count: int
    expected_decision_receipt_count: int
    applied_decision_receipt_count: int
    expected_applied_decision_receipt_count: int
    management_effect_started_count: int
    expected_management_effect_started_count: int
    deactivation_effect_started_count: int
    expected_deactivation_effect_started_count: int
    deactivation_effect_cleared_count: int
    expected_deactivation_effect_cleared_count: int
    management_cursor_byte_offset: int | None
    expected_management_cursor_byte_offset: int
    decision_cursor_byte_offset: int | None
    expected_decision_cursor_byte_offset: int
    management_pending_count: int
    decision_pending_count: int
    pending_request_count: int
    pending_request_high_water_count: int
    decision_pending_high_water_count: int
    snapshot_management_effect_count: int
    snapshot_deactivation_effect_count: int
    request_occurrence_identity_mismatch_count: int
    final_occurrence_identity_mismatch_count: int
    request_before_decision_ok: bool
    target_visible_while_pending_ok: bool
    request_receipt_before_management_cursor_ok: bool
    decision_receipt_before_decision_cursor_ok: bool
    effect_window_preserved_ok: bool
    remaining_window_seconds: float | None
    expected_remaining_window_seconds: float
    request_receipt_commit_id: str | None
    decision_receipt_commit_id: str | None
    decision_input_to_receipt_ms: float | None
    target_group_durable_record_count: int
    expected_target_group_durable_record_count: int
    total_durable_record_count: int
    expected_total_durable_record_count: int
    functional_integrity_ok: bool


@dataclass(frozen=True, slots=True)
class SustainedDeactivationDecisionPressureMetrics:
    phase_a_duration_seconds: float
    phase_b_duration_seconds: float
    request_at_seconds: int
    request_count: int
    request_interval_seconds: int
    request_last_at_seconds: int
    decision_at_seconds: int
    decision_count: int
    decision_interval_seconds: int
    decision_last_at_seconds: int
    deactivation_window_seconds: int
    request_receipt_count: int
    expected_request_receipt_count: int
    pending_approval_receipt_count: int
    expected_pending_approval_receipt_count: int
    deactivation_request_count: int
    expected_deactivation_request_count: int
    approval_required_request_count: int
    expected_approval_required_request_count: int
    decision_receipt_count: int
    expected_decision_receipt_count: int
    applied_decision_receipt_count: int
    expected_applied_decision_receipt_count: int
    management_effect_started_count: int
    expected_management_effect_started_count: int
    deactivation_effect_started_count: int
    expected_deactivation_effect_started_count: int
    deactivation_effect_cleared_count: int
    expected_deactivation_effect_cleared_count: int
    lost_request_input_count: int
    duplicate_request_receipt_count: int
    lost_decision_input_count: int
    duplicate_decision_receipt_count: int
    unique_target_count: int
    expected_unique_target_count: int
    unique_decision_request_count: int
    expected_unique_decision_request_count: int
    wrong_decision_request_correlation_count: int
    request_occurrence_identity_mismatch_count: int
    final_occurrence_identity_mismatch_count: int
    effect_window_mismatch_count: int
    snapshot_management_effect_count: int
    expected_snapshot_management_effect_count: int
    snapshot_deactivation_effect_count: int
    expected_snapshot_deactivation_effect_count: int
    phase_a_management_cursor_byte_offset: int | None
    expected_phase_a_management_cursor_byte_offset: int
    final_management_cursor_byte_offset: int | None
    expected_final_management_cursor_byte_offset: int
    final_decision_cursor_byte_offset: int | None
    expected_final_decision_cursor_byte_offset: int
    phase_a_management_pending_count: int
    phase_a_pending_request_count: int
    final_management_pending_count: int
    final_decision_pending_count: int
    final_pending_request_count: int
    pending_request_high_water_count: int
    decision_pending_high_water_count: int
    request_receipt_before_cursor_checked_count: int
    request_receipt_before_cursor_advance_ok: bool
    decision_receipt_before_cursor_checked_count: int
    decision_receipt_before_cursor_advance_ok: bool
    request_source_max_batch_size: int
    request_receipt_max_batch_size: int
    decision_source_max_batch_size: int
    decision_receipt_max_batch_size: int
    decision_nonempty_batch_count: int
    decision_nonempty_batch_sizes: tuple[int, ...]
    decision_arrival_mode: str
    decision_receipt_nonempty_batch_count: int
    decision_receipt_nonempty_batch_sizes: tuple[int, ...]
    decision_first_nonempty_receipt_batch_size: int
    expected_decision_first_nonempty_receipt_batch_size: int | None
    decision_fully_absorbed_in_first_eligible_iteration: bool | None
    decision_input_to_receipt_p50_ms: float
    decision_input_to_receipt_p95_ms: float
    decision_input_to_receipt_p99_ms: float
    decision_input_to_receipt_max_ms: float
    remaining_window_min_seconds: float | None
    remaining_window_max_seconds: float | None
    durable_record_count: int
    functional_integrity_ok: bool


@dataclass(frozen=True, slots=True)
class InvertedDeactivationDecisionPressureMetrics:
    request_logical_at_seconds: int
    decision_delivery_at_seconds: int
    request_delivery_at_seconds: int
    input_count: int
    deactivation_window_seconds: int
    request_receipt_count: int
    expected_request_receipt_count: int
    pending_approval_receipt_count: int
    decision_receipt_count: int
    expected_decision_receipt_count: int
    applied_decision_receipt_count: int
    management_effect_started_count: int
    deactivation_effect_started_count: int
    deactivation_effect_cleared_count: int
    lost_request_input_count: int
    duplicate_request_receipt_count: int
    lost_decision_input_count: int
    duplicate_decision_receipt_count: int
    wrong_decision_request_correlation_count: int
    request_occurrence_identity_mismatch_count: int
    final_occurrence_identity_mismatch_count: int
    effect_window_mismatch_count: int
    early_decision_cursor_byte_offset: int | None
    expected_cursor_byte_offset: int
    early_decision_pending_count: int | None
    early_decision_receipt_count: int | None
    post_request_management_cursor_byte_offset: int | None
    post_request_decision_pending_count: int | None
    post_request_pending_request_count: int | None
    post_request_decision_receipt_count: int | None
    final_management_cursor_byte_offset: int | None
    final_decision_cursor_byte_offset: int | None
    final_management_pending_count: int
    final_decision_pending_count: int
    final_pending_request_count: int
    decision_pending_high_water_count: int
    pending_request_high_water_count: int
    decision_fresh_record_count: int
    decision_pending_read_count: int
    decision_receipt_nonempty_batch_sizes: tuple[int, ...]
    decision_delivery_to_receipt_p50_ms: float
    decision_delivery_to_receipt_p95_ms: float
    decision_delivery_to_receipt_p99_ms: float
    decision_delivery_to_receipt_max_ms: float
    remaining_window_min_seconds: float | None
    remaining_window_max_seconds: float | None
    snapshot_management_effect_count: int
    snapshot_deactivation_effect_count: int
    durable_record_count: int
    expected_durable_record_count: int
    early_pending_state_ok: bool
    post_request_pending_state_ok: bool
    final_replay_state_ok: bool
    functional_integrity_ok: bool


@dataclass(frozen=True, slots=True)
class MixedDeactivationDecisionPressureMetrics:
    request_at_seconds: int
    request_count: int
    request_interval_seconds: int
    request_last_at_seconds: int
    decision_at_seconds: int
    decision_count: int
    decision_interval_seconds: int
    decision_last_at_seconds: int
    decision_lag_seconds: int
    deactivation_window_seconds: int
    request_receipt_count: int
    expected_request_receipt_count: int
    pending_approval_receipt_count: int
    deactivation_request_count: int
    approval_required_request_count: int
    decision_receipt_count: int
    expected_decision_receipt_count: int
    applied_decision_receipt_count: int
    management_effect_started_count: int
    deactivation_effect_started_count: int
    deactivation_effect_cleared_count: int
    lost_request_input_count: int
    duplicate_request_receipt_count: int
    lost_decision_input_count: int
    duplicate_decision_receipt_count: int
    unique_target_count: int
    wrong_decision_request_correlation_count: int
    request_occurrence_identity_mismatch_count: int
    final_occurrence_identity_mismatch_count: int
    effect_window_mismatch_count: int
    snapshot_management_effect_count: int
    snapshot_deactivation_effect_count: int
    final_management_cursor_byte_offset: int | None
    expected_management_cursor_byte_offset: int
    final_decision_cursor_byte_offset: int | None
    expected_decision_cursor_byte_offset: int
    final_management_pending_count: int
    final_decision_pending_count: int
    final_pending_request_count: int
    management_pending_high_water_count: int
    decision_pending_high_water_count: int
    pending_request_high_water_count: int
    management_fresh_record_count: int
    decision_fresh_record_count: int
    management_pending_read_count: int
    decision_pending_read_count: int
    management_source_nonempty_batch_sizes: tuple[int, ...]
    decision_source_nonempty_batch_sizes: tuple[int, ...]
    request_receipt_nonempty_batch_sizes: tuple[int, ...]
    decision_receipt_nonempty_batch_sizes: tuple[int, ...]
    mixed_receipt_cycle_count: int
    request_input_to_receipt_p50_ms: float
    request_input_to_receipt_p95_ms: float
    request_input_to_receipt_p99_ms: float
    request_input_to_receipt_max_ms: float
    decision_input_to_receipt_p50_ms: float
    decision_input_to_receipt_p95_ms: float
    decision_input_to_receipt_p99_ms: float
    decision_input_to_receipt_max_ms: float
    remaining_window_min_seconds: float | None
    remaining_window_max_seconds: float | None
    durable_record_count: int
    functional_integrity_ok: bool


@dataclass(frozen=True, slots=True)
class StaleTargetDeactivationDecisionPressureMetrics:
    request_at_seconds: int
    removal_at_seconds: int
    decision_at_seconds: int
    input_count: int
    deactivation_window_seconds: int
    source_revision: str
    target_revision: str
    request_receipt_count: int
    pending_approval_receipt_count: int
    deactivation_request_count: int
    approval_required_request_count: int
    decision_receipt_count: int
    stale_target_receipt_count: int
    applied_decision_receipt_count: int
    management_effect_started_count: int
    management_effect_cleared_count: int
    configuration_removed_occurrence_count: int
    deactivation_effect_started_count: int
    deactivation_effect_cleared_count: int
    lost_request_input_count: int
    duplicate_request_receipt_count: int
    lost_decision_input_count: int
    duplicate_decision_receipt_count: int
    wrong_decision_request_correlation_count: int
    request_occurrence_identity_mismatch_count: int
    stale_target_occurrence_mismatch_count: int
    final_removed_target_present_count: int
    final_management_cursor_byte_offset: int | None
    expected_management_cursor_byte_offset: int
    final_decision_cursor_byte_offset: int | None
    expected_decision_cursor_byte_offset: int
    final_management_pending_count: int
    final_decision_pending_count: int
    final_pending_request_count: int
    management_pending_high_water_count: int
    decision_pending_high_water_count: int
    pending_request_high_water_count: int
    management_fresh_record_count: int
    decision_fresh_record_count: int
    management_pending_read_count: int
    decision_pending_read_count: int
    request_receipt_nonempty_batch_sizes: tuple[int, ...]
    decision_receipt_nonempty_batch_sizes: tuple[int, ...]
    request_input_to_receipt_p50_ms: float
    request_input_to_receipt_p95_ms: float
    request_input_to_receipt_p99_ms: float
    request_input_to_receipt_max_ms: float
    decision_input_to_receipt_p50_ms: float
    decision_input_to_receipt_p95_ms: float
    decision_input_to_receipt_p99_ms: float
    decision_input_to_receipt_max_ms: float
    remaining_window_seconds: float
    target_runtime_alarm_count: int
    expected_target_runtime_alarm_count: int
    durable_record_count: int
    expected_durable_record_count: int
    functional_integrity_ok: bool


@dataclass(frozen=True, slots=True)
class ParameterAdoptionPressureMetrics:
    adoption_at_seconds: int
    source_revision: str
    target_revision: str
    source_threshold: float
    target_threshold: float
    plan_change_count: int
    compatible_change_count: int
    unchanged_change_count: int
    structural_reset_change_count: int
    disabled_change_count: int
    removed_change_count: int
    rejected_change_count: int
    target_runtime_alarm_count: int
    expected_target_runtime_alarm_count: int
    target_threshold_alarm_count: int
    effective_cache_revision: str | None
    adoption_iteration_count: int
    adoption_iteration: int | None
    adoption_iteration_ms: float | None
    adoption_iteration_cpu_percent: float | None
    adoption_cycle_executed: bool
    post_adoption_cache_current_iteration_count: int
    source_revision_durable_record_count: int
    target_revision_durable_record_count: int
    durable_record_count: int
    expected_durable_record_count: int
    source_state_basis_snapshot_count: int
    target_state_basis_snapshot_count: int
    open_occurrence_count: int
    expected_open_occurrence_count: int
    functional_integrity_ok: bool


@dataclass(frozen=True, slots=True)
class C2RoutingAdoptionPressureMetrics:
    adoption_at_seconds: int
    source_revision: str
    target_revision: str
    source_delay_seconds: tuple[int, ...]
    target_delay_seconds: tuple[int, ...]
    plan_change_count: int
    compatible_change_count: int
    unchanged_change_count: int
    structural_reset_change_count: int
    disabled_change_count: int
    removed_change_count: int
    rejected_change_count: int
    target_runtime_alarm_count: int
    expected_target_runtime_alarm_count: int
    effective_cache_revision: str | None
    adoption_iteration_count: int
    adoption_iteration: int | None
    adoption_iteration_ms: float | None
    adoption_iteration_cpu_percent: float | None
    adoption_cycle_executed: bool
    post_adoption_cache_current_iteration_count: int
    adoption_commit_count: int
    expected_adoption_commit_count: int
    adoption_assigned_count: int
    expected_adoption_assigned_count: int
    adoption_rescheduled_count: int
    expected_adoption_rescheduled_count: int
    source_revision_durable_record_count: int
    expected_source_revision_durable_record_count: int
    target_revision_durable_record_count: int
    expected_target_revision_durable_record_count: int
    durable_record_count: int
    expected_durable_record_count: int
    source_state_basis_snapshot_count: int
    target_state_basis_snapshot_count: int
    final_assignment_count: int
    expected_final_assignment_count: int
    final_pending_assignment_count: int
    open_occurrence_count: int
    expected_open_occurrence_count: int
    occurrence_identity_mismatch_count: int
    functional_integrity_ok: bool


@dataclass(frozen=True, slots=True)
class DisabledAdoptionPressureMetrics:
    adoption_at_seconds: int
    disabled_alarm_percent: int
    source_revision: str
    target_revision: str
    plan_change_count: int
    compatible_change_count: int
    unchanged_change_count: int
    structural_reset_change_count: int
    disabled_change_count: int
    expected_disabled_change_count: int
    removed_change_count: int
    rejected_change_count: int
    target_defined_alarm_count: int
    expected_target_defined_alarm_count: int
    target_runtime_alarm_count: int
    expected_target_runtime_alarm_count: int
    effective_cache_revision: str | None
    adoption_iteration_count: int
    adoption_iteration: int | None
    adoption_iteration_ms: float | None
    adoption_iteration_cpu_percent: float | None
    adoption_cycle_executed: bool
    post_adoption_cache_current_iteration_count: int
    adoption_commit_count: int
    expected_adoption_commit_count: int
    configuration_disabled_occurrence_count: int
    expected_configuration_disabled_occurrence_count: int
    configuration_removed_occurrence_count: int
    occurrence_identity_mismatch_count: int
    source_revision_durable_record_count: int
    expected_source_revision_durable_record_count: int
    target_revision_durable_record_count: int
    expected_target_revision_durable_record_count: int
    durable_record_count: int
    expected_durable_record_count: int
    source_state_basis_snapshot_count: int
    target_state_basis_snapshot_count: int
    final_alarm_count: int
    expected_final_alarm_count: int
    final_assignment_count: int
    expected_final_assignment_count: int
    open_occurrence_count: int
    expected_open_occurrence_count: int
    open_episode_count: int
    expected_open_episode_count: int
    disabled_target_present_count: int
    functional_integrity_ok: bool


@dataclass(frozen=True, slots=True)
class StructuralResetAdoptionPressureMetrics:
    adoption_at_seconds: int
    structural_reset_alarm_percent: int
    source_revision: str
    target_revision: str
    plan_change_count: int
    compatible_change_count: int
    unchanged_change_count: int
    structural_reset_change_count: int
    expected_structural_reset_change_count: int
    structural_reset_group_count: int
    expected_structural_reset_group_count: int
    disabled_change_count: int
    removed_change_count: int
    rejected_change_count: int
    effective_cache_revision: str | None
    adoption_iteration_count: int
    adoption_iteration: int | None
    adoption_iteration_ms: float | None
    adoption_iteration_cpu_percent: float | None
    adoption_cycle_executed: bool
    immediate_next_iteration: int | None
    immediate_next_iteration_cycle_executed: bool
    immediate_next_iteration_cache_current: bool
    adoption_commit_count: int
    expected_adoption_commit_count: int
    next_cycle_commit_count: int
    expected_next_cycle_commit_count: int
    configuration_reconfigured_occurrence_count: int
    expected_configuration_reconfigured_occurrence_count: int
    configuration_terminated_episode_count: int
    expected_configuration_terminated_episode_count: int
    restarted_occurrence_count: int
    expected_restarted_occurrence_count: int
    restarted_episode_count: int
    expected_restarted_episode_count: int
    occurrence_identity_reuse_count: int
    source_revision_durable_record_count: int
    expected_source_revision_durable_record_count: int
    target_revision_durable_record_count: int
    expected_target_revision_durable_record_count: int
    durable_record_count: int
    expected_durable_record_count: int
    source_state_basis_snapshot_count: int
    expected_source_state_basis_snapshot_count: int
    target_state_basis_snapshot_count: int
    expected_target_state_basis_snapshot_count: int
    final_alarm_count: int
    expected_final_alarm_count: int
    final_assignment_count: int
    expected_final_assignment_count: int
    open_occurrence_count: int
    expected_open_occurrence_count: int
    open_episode_count: int
    expected_open_episode_count: int
    functional_integrity_ok: bool


@dataclass(frozen=True, slots=True)
class MixedRevisionAdoptionPressureMetrics:
    adoption_at_seconds: int
    target_threshold: float
    disabled_alarm_percent: int
    removed_alarm_percent: int
    structural_reset_alarm_percent: int
    source_revision: str
    target_revision: str
    plan_change_count: int
    compatible_change_count: int
    expected_compatible_change_count: int
    unchanged_change_count: int
    structural_reset_change_count: int
    expected_structural_reset_change_count: int
    structural_reset_group_count: int
    expected_structural_reset_group_count: int
    disabled_change_count: int
    expected_disabled_change_count: int
    removed_change_count: int
    expected_removed_change_count: int
    rejected_change_count: int
    target_defined_alarm_count: int
    expected_target_defined_alarm_count: int
    target_runtime_alarm_count: int
    expected_target_runtime_alarm_count: int
    touched_priority_group_count: int
    expected_touched_priority_group_count: int
    disabled_removed_overlap_group_count: int
    expected_disabled_removed_overlap_group_count: int
    effective_cache_revision: str | None
    adoption_iteration_count: int
    adoption_iteration: int | None
    adoption_iteration_ms: float | None
    adoption_iteration_cpu_percent: float | None
    adoption_cycle_executed: bool
    immediate_next_iteration: int | None
    immediate_next_iteration_cycle_executed: bool
    immediate_next_iteration_cache_current: bool
    immediate_next_start_interval_ms: float | None
    immediate_next_duration_ms: float | None
    adoption_commit_count: int
    expected_adoption_commit_count: int
    next_cycle_commit_count: int
    expected_next_cycle_commit_count: int
    configuration_reconfigured_occurrence_count: int
    expected_configuration_reconfigured_occurrence_count: int
    configuration_disabled_occurrence_count: int
    expected_configuration_disabled_occurrence_count: int
    configuration_removed_occurrence_count: int
    expected_configuration_removed_occurrence_count: int
    configuration_terminated_episode_count: int
    expected_configuration_terminated_episode_count: int
    restarted_occurrence_count: int
    expected_restarted_occurrence_count: int
    restarted_episode_count: int
    expected_restarted_episode_count: int
    occurrence_identity_reuse_count: int
    episode_identity_reuse_count: int
    source_revision_durable_record_count: int
    expected_source_revision_durable_record_count: int
    target_revision_durable_record_count: int
    expected_target_revision_durable_record_count: int
    durable_record_count: int
    expected_durable_record_count: int
    groups_with_two_records: int
    expected_groups_with_two_records: int
    groups_with_three_records: int
    expected_groups_with_three_records: int
    source_state_basis_snapshot_count: int
    expected_source_state_basis_snapshot_count: int
    target_state_basis_snapshot_count: int
    expected_target_state_basis_snapshot_count: int
    final_alarm_count: int
    expected_final_alarm_count: int
    final_assignment_count: int
    expected_final_assignment_count: int
    open_occurrence_count: int
    expected_open_occurrence_count: int
    open_episode_count: int
    expected_open_episode_count: int
    functional_integrity_ok: bool


@dataclass(frozen=True, slots=True)
class RejectedTargetPressureMetrics:
    candidate_at_seconds: int
    source_revision: str
    target_revision: str
    rejected_alarm_key: str
    rejected_target_priority_group: str
    plan_change_count: int
    unchanged_change_count: int
    compatible_change_count: int
    structural_reset_change_count: int
    disabled_change_count: int
    removed_change_count: int
    rejected_change_count: int
    expected_rejected_change_count: int
    priority_group_changed_rejection_count: int
    plan_adoptable: bool
    effective_cache_revision: str | None
    first_rejected_iteration: int | None
    expected_first_rejected_iteration: int
    rejected_iteration_count: int
    degraded_rejected_iteration_count: int
    rejected_cycle_executed_count: int
    post_candidate_non_rejected_iteration_count: int
    target_revision_durable_record_count: int
    expected_target_revision_durable_record_count: int
    source_revision_durable_record_count: int
    expected_source_revision_durable_record_count: int
    durable_record_count: int
    expected_durable_record_count: int
    source_state_basis_snapshot_count: int
    target_state_basis_snapshot_count: int
    rejected_priority_group_materialized_count: int
    final_alarm_count: int
    expected_final_alarm_count: int
    final_assignment_count: int
    expected_final_assignment_count: int
    open_occurrence_count: int
    expected_open_occurrence_count: int
    open_episode_count: int
    expected_open_episode_count: int
    functional_integrity_ok: bool


# Agrupa las invariantes específicas de E-008: fallback continuo, cache LKG y base durable.
@dataclass(frozen=True, slots=True)
class SourceUnavailablePressureMetrics:
    unavailable_at_seconds: int
    source_alarm_revision: str
    source_tool_revision: str
    first_fallback_iteration: int | None
    expected_first_fallback_iteration: int
    fallback_iteration_count: int
    expected_fallback_iteration_count: int
    degraded_fallback_iteration_count: int
    fallback_cycle_executed_count: int
    not_required_fallback_iteration_count: int
    post_failure_non_fallback_iteration_count: int
    manifest_success_count: int
    manifest_failure_count: int
    cache_replace_count: int
    post_failure_cache_replace_count: int
    effective_cache_alarm_revision: str | None
    effective_cache_tool_revision: str | None
    source_revision_durable_record_count: int
    unexpected_revision_durable_record_count: int
    source_state_basis_snapshot_count: int
    unexpected_state_basis_snapshot_count: int
    functional_integrity_ok: bool


# Resume la evidencia que distingue E-009 de E-008: el Source responde, el bundle
# candidato se lee, la validación contractual falla y el LKG permanece efectivo.
@dataclass(frozen=True, slots=True)
class InvalidSourceCandidatePressureMetrics:
    invalid_at_seconds: int
    source_alarm_revision: str
    source_tool_revision: str
    candidate_alarm_revision: str
    candidate_tool_revision: str
    invalid_alarm_document_revision: str
    first_fallback_iteration: int | None
    expected_first_fallback_iteration: int
    fallback_iteration_count: int
    expected_fallback_iteration_count: int
    degraded_fallback_iteration_count: int
    fallback_cycle_executed_count: int
    not_required_fallback_iteration_count: int
    post_candidate_non_fallback_iteration_count: int
    post_candidate_source_candidate_iteration_count: int
    post_candidate_rejected_iteration_count: int
    manifest_success_count: int
    manifest_failure_count: int
    candidate_manifest_count: int
    candidate_alarm_document_read_count: int
    candidate_tool_document_read_count: int
    candidate_contract_failure_count: int
    cache_replace_count: int
    post_candidate_cache_replace_count: int
    effective_cache_alarm_revision: str | None
    effective_cache_tool_revision: str | None
    source_revision_durable_record_count: int
    candidate_revision_durable_record_count: int
    unexpected_revision_durable_record_count: int
    source_state_basis_snapshot_count: int
    candidate_state_basis_snapshot_count: int
    unexpected_state_basis_snapshot_count: int
    functional_integrity_ok: bool


# Evidencia específica de E-010: fencing, takeover generacional, replay idempotente, cache y geometría durable.
@dataclass(frozen=True, slots=True)
class LeaseLossAdoptionPressureMetrics:
    adoption_at_seconds: int
    structural_reset_alarm_percent: int
    source_revision: str
    target_revision: str
    plan_change_count: int
    unchanged_change_count: int
    structural_reset_change_count: int
    expected_structural_reset_change_count: int
    structural_reset_group_count: int
    expected_structural_reset_group_count: int
    preflight_status: str
    owner_a_generation: int | None
    owner_b_generation: int | None
    owner_a_failed_iteration: int | None
    expected_owner_a_failed_iteration: int
    owner_b_first_iteration: int | None
    expected_owner_b_first_iteration: int
    owner_a_failed_iteration_ms: float | None
    owner_a_lease_loss_observed: bool
    owner_a_journal_aligned_before_loss: bool
    owner_a_cache_alarm_revision_before_loss: str | None
    owner_a_cache_replace_count: int
    owner_a_post_adoption_cache_replace_count: int
    owner_a_adoption_commit_count: int
    expected_owner_a_adoption_commit_count: int
    owner_b_recovery_applied_count: int
    owner_b_recovery_skipped_count: int
    owner_b_recovery_discarded_tail_bytes: int
    owner_b_first_revision_origin: str | None
    owner_b_first_adoption_outcome: str | None
    owner_b_first_cycle_executed: bool
    owner_b_first_degraded: bool
    owner_b_post_replay_cache_current_count: int
    expected_owner_b_post_replay_cache_current_count: int
    owner_b_replay_adoption_commit_count: int
    owner_b_cache_replace_count: int
    stale_owner_cache_write_count: int
    final_cache_alarm_revision: str | None
    final_cache_tool_revision: str | None
    configuration_reconfigured_occurrence_count: int
    expected_configuration_reconfigured_occurrence_count: int
    configuration_terminated_episode_count: int
    expected_configuration_terminated_episode_count: int
    restarted_occurrence_count: int
    expected_restarted_occurrence_count: int
    restarted_episode_count: int
    expected_restarted_episode_count: int
    occurrence_identity_reuse_count: int
    episode_identity_reuse_count: int
    source_revision_durable_record_count: int
    expected_source_revision_durable_record_count: int
    target_revision_durable_record_count: int
    expected_target_revision_durable_record_count: int
    durable_record_count: int
    expected_durable_record_count: int
    groups_with_7_records: int
    expected_groups_with_7_records: int
    groups_with_9_records: int
    expected_groups_with_9_records: int
    target_state_basis_snapshot_count: int
    expected_target_state_basis_snapshot_count: int
    final_alarm_count: int
    final_assignment_count: int
    final_pending_assignment_count: int
    open_occurrence_count: int
    open_episode_count: int
    functional_integrity_ok: bool


# Métricas del probe E-011. No premian continuidad: PASS exige que la
# excepción de promoción detenga el ciclo cuando el durable ya está en AC2.
@dataclass(frozen=True, slots=True)
class CachePromotionFailurePressureMetrics:
    failure_at_seconds: int
    structural_reset_alarm_percent: int
    source_revision: str
    target_revision: str
    plan_change_count: int
    unchanged_change_count: int
    structural_reset_change_count: int
    expected_structural_reset_change_count: int
    structural_reset_group_count: int
    expected_structural_reset_group_count: int
    failed_iteration: int | None
    expected_failed_iteration: int
    failed_iteration_ms: float | None
    successful_iteration_count: int
    expected_successful_iteration_count: int
    failed_iteration_sample_count: int
    exception_type: str | None
    exception_message: str | None
    cache_replace_count: int
    target_promotion_attempt_count: int
    target_promotion_failure_count: int
    successful_target_replace_count: int
    final_cache_alarm_revision: str | None
    final_cache_tool_revision: str | None
    source_revision_durable_record_count: int
    expected_source_revision_durable_record_count: int
    target_revision_durable_record_count: int
    expected_target_revision_durable_record_count: int
    durable_record_count: int
    expected_durable_record_count: int
    duplicate_commit_id_count: int
    commit_chain_mismatch_count: int
    unexpected_target_operational_commit_count: int
    groups_with_1_record: int
    expected_groups_with_1_record: int
    groups_with_2_records: int
    expected_groups_with_2_records: int
    groups_with_3_records: int
    expected_groups_with_3_records: int
    source_state_basis_snapshot_count: int
    expected_source_state_basis_snapshot_count: int
    target_state_basis_snapshot_count: int
    expected_target_state_basis_snapshot_count: int
    reset_snapshot_count: int
    expected_reset_snapshot_count: int
    reset_empty_snapshot_count: int
    expected_reset_empty_snapshot_count: int
    reset_snapshot_without_state_basis_count: int
    expected_reset_snapshot_without_state_basis_count: int
    reset_snapshot_target_last_commit_count: int
    expected_reset_snapshot_target_last_commit_count: int
    configuration_reconfigured_occurrence_count: int
    expected_configuration_reconfigured_occurrence_count: int
    configuration_terminated_episode_count: int
    expected_configuration_terminated_episode_count: int
    occurrence_started_count: int
    expected_occurrence_started_count: int
    occurrence_closed_count: int
    expected_occurrence_closed_count: int
    episode_started_count: int
    expected_episode_started_count: int
    episode_closed_count: int
    expected_episode_closed_count: int
    assignment_change_count: int
    expected_assignment_change_count: int
    final_alarm_count: int
    expected_final_alarm_count: int
    final_assignment_count: int
    expected_final_assignment_count: int
    final_pending_assignment_count: int
    open_occurrence_count: int
    expected_open_occurrence_count: int
    open_episode_count: int
    expected_open_episode_count: int
    durable_materialized_aligned: bool
    functional_integrity_ok: bool


# E-012 separa la evidencia de la frontera RUNNING→DRAINING de las métricas normales
# de iteración. Así drain_ms y los invariantes de no-nuevo-trabajo quedan auditables
# sin contaminar p50/p95/p99 del workload.
@dataclass(frozen=True, slots=True)
class DrainUnderWorkloadPressureMetrics:
    stop_at_seconds: int
    stop_iteration: int
    expected_stop_iteration: int
    execution_iteration_count: int
    execution_stop_reason: str
    management_consumed_count: int
    expected_management_consumed_count: int
    decision_consumed_count: int
    expected_decision_consumed_count: int
    management_cursor_byte_offset: int | None
    expected_management_cursor_byte_offset: int
    decision_cursor_byte_offset: int | None
    expected_decision_cursor_byte_offset: int
    management_pending_count: int
    decision_pending_count: int
    pending_deactivation_request_count: int
    expected_pending_deactivation_request_count: int
    future_management_count: int
    expected_future_management_count: int
    future_decision_count: int
    expected_future_decision_count: int
    durable_record_count_before_drain: int
    durable_record_count_after_drain: int
    expected_durable_record_count: int
    journal_bytes_before_drain: int
    journal_bytes_after_drain: int
    journal_bytes_unchanged: bool
    durable_head_before_drain: str | None
    durable_head_after_drain: str | None
    materialized_head_before_drain: str | None
    materialized_head_after_drain: str | None
    journal_head_unchanged: bool
    durable_materialized_aligned_before_drain: bool
    durable_materialized_aligned_after_drain: bool
    snapshot_count_before_drain: int
    snapshot_count_after_drain: int
    snapshot_documents_unchanged: bool
    final_snapshot_match_count: int
    expected_final_snapshot_match_count: int
    source_load_count_before_drain: int
    source_load_count_after_drain: int
    management_read_batch_count_before_drain: int
    management_read_batch_count_after_drain: int
    decision_read_batch_count_before_drain: int
    decision_read_batch_count_after_drain: int
    management_read_at_count_before_drain: int
    management_read_at_count_after_drain: int
    decision_read_at_count_before_drain: int
    decision_read_at_count_after_drain: int
    cache_replace_count_before_drain: int
    cache_replace_count_after_drain: int
    recovery_applied_count: int
    recovery_discarded_tail_bytes: int
    duplicate_commit_id_count: int
    commit_chain_mismatch_count: int
    new_frozen_durable_unit_count: int
    post_drain_source_load_count: int
    post_drain_management_read_count: int
    post_drain_decision_read_count: int
    post_drain_cache_replace_count: int
    functional_integrity_ok: bool


# Resumen compacto por ventana estable. No agregamos sampling: resumimos las muestras
# que PerformanceRecorder ya captura en cada iteración.
@dataclass(frozen=True, slots=True)
class SoakWindowMetrics:
    window_index: int
    start_seconds: int
    end_seconds: int
    sample_count: int
    expected_sample_count: int
    median_rss_mb: float
    p95_iteration_ms: float
    median_cpu_percent: float
    p95_start_interval_ms: float


@dataclass(frozen=True, slots=True)
class TemporalSoakMetrics:
    warmup_seconds: int
    window_seconds: int
    expected_window_count: int
    expected_samples_per_window: int
    expected_iteration_count: int
    actual_iteration_count: int
    expected_durable_record_count: int
    durable_record_count: int
    duplicate_commit_id_count: int
    commit_chain_mismatch_count: int
    journal_aligned: bool
    expected_snapshot_count: int
    snapshot_count: int
    expected_snapshot_alarm_count: int
    snapshot_alarm_count: int
    windows: tuple[SoakWindowMetrics, ...]
    rss_w5_w1_ratio: float
    rss_rising_transition_count: int
    latency_p95_w5_w1_ratio: float
    steady_window_at_or_above_period_count: int
    overrun_count: int
    overrun_ratio: float
    memory_classification: str
    latency_classification: str
    overall_classification: str
    hard_gate_ok: bool


# El adjudicador separa warmup de steady-state y clasifica tendencia de memoria
# y latencia. REVIEW no rompe integridad; FAIL sí bloquea el resultado autoritativo.
def build_temporal_soak_metrics(
    *,
    samples: list[IterationSample] | tuple[IterationSample, ...],
    warmup_seconds: int,
    window_seconds: int,
    iteration_period_seconds: float,
    expected_window_count: int,
    expected_samples_per_window: int,
    expected_iteration_count: int,
    durable_record_count: int,
    expected_durable_record_count: int,
    duplicate_commit_id_count: int,
    commit_chain_mismatch_count: int,
    journal_aligned: bool,
    snapshot_count: int,
    expected_snapshot_count: int,
    snapshot_alarm_count: int,
    expected_snapshot_alarm_count: int,
) -> TemporalSoakMetrics:
    warmup_sample_count = int(warmup_seconds / iteration_period_seconds) + 1
    windows: list[SoakWindowMetrics] = []
    for window_index in range(expected_window_count):
        start = warmup_sample_count + window_index * expected_samples_per_window
        end = start + expected_samples_per_window
        window_samples = samples[start:end]
        rss_values = [sample.rss_after_mb for sample in window_samples]
        duration_values = [sample.duration_ms for sample in window_samples]
        cpu_values = [sample.cpu_percent for sample in window_samples]
        interval_values = [
            sample.start_interval_ms
            for sample in window_samples
            if sample.start_interval_ms is not None
        ]
        windows.append(
            SoakWindowMetrics(
                window_index=window_index + 1,
                start_seconds=warmup_seconds + window_index * window_seconds,
                end_seconds=warmup_seconds + (window_index + 1) * window_seconds,
                sample_count=len(window_samples),
                expected_sample_count=expected_samples_per_window,
                median_rss_mb=statistics.median(rss_values) if rss_values else 0.0,
                p95_iteration_ms=_percentile(duration_values, 95),
                median_cpu_percent=statistics.median(cpu_values) if cpu_values else 0.0,
                p95_start_interval_ms=_percentile(interval_values, 95),
            )
        )

    first = windows[0] if windows else None
    last = windows[-1] if windows else None
    rss_ratio = (
        0.0
        if first is None or last is None or first.median_rss_mb <= 0
        else last.median_rss_mb / first.median_rss_mb
    )
    latency_ratio = (
        0.0
        if first is None or last is None or first.p95_iteration_ms <= 0
        else last.p95_iteration_ms / first.p95_iteration_ms
    )
    rss_rising_transition_count = sum(
        current.median_rss_mb > previous.median_rss_mb
        for previous, current in zip(windows, windows[1:], strict=False)
    )
    period_ms = iteration_period_seconds * 1000
    window_period_flags = [window.p95_iteration_ms >= period_ms for window in windows]
    consecutive_period_windows = any(
        left and right
        for left, right in zip(window_period_flags, window_period_flags[1:], strict=False)
    )
    overrun_count = sum(sample.duration_ms > period_ms for sample in samples)
    overrun_ratio = 0.0 if not samples else overrun_count / len(samples)

    if rss_ratio > 1.10:
        memory_classification = 'FAIL'
    elif rss_ratio > 1.05 or rss_rising_transition_count >= 4:
        memory_classification = 'REVIEW'
    else:
        memory_classification = 'GREEN'

    if latency_ratio > 1.50 or overrun_ratio >= 0.01 or consecutive_period_windows:
        latency_classification = 'FAIL'
    elif latency_ratio > 1.25 or overrun_count > 0 or any(window_period_flags):
        latency_classification = 'REVIEW'
    else:
        latency_classification = 'GREEN'

    structural_gate_ok = (
        len(samples) == expected_iteration_count
        and len(windows) == expected_window_count
        and all(window.sample_count == expected_samples_per_window for window in windows)
        and durable_record_count == expected_durable_record_count
        and duplicate_commit_id_count == 0
        and commit_chain_mismatch_count == 0
        and journal_aligned
        and snapshot_count == expected_snapshot_count
        and snapshot_alarm_count == expected_snapshot_alarm_count
    )
    if (
        not structural_gate_ok
        or memory_classification == 'FAIL'
        or latency_classification == 'FAIL'
    ):
        overall_classification = 'FAIL'
    elif memory_classification == 'REVIEW' or latency_classification == 'REVIEW':
        overall_classification = 'REVIEW'
    else:
        overall_classification = 'GREEN'

    return TemporalSoakMetrics(
        warmup_seconds=warmup_seconds,
        window_seconds=window_seconds,
        expected_window_count=expected_window_count,
        expected_samples_per_window=expected_samples_per_window,
        expected_iteration_count=expected_iteration_count,
        actual_iteration_count=len(samples),
        expected_durable_record_count=expected_durable_record_count,
        durable_record_count=durable_record_count,
        duplicate_commit_id_count=duplicate_commit_id_count,
        commit_chain_mismatch_count=commit_chain_mismatch_count,
        journal_aligned=journal_aligned,
        expected_snapshot_count=expected_snapshot_count,
        snapshot_count=snapshot_count,
        expected_snapshot_alarm_count=expected_snapshot_alarm_count,
        snapshot_alarm_count=snapshot_alarm_count,
        windows=tuple(windows),
        rss_w5_w1_ratio=rss_ratio,
        rss_rising_transition_count=rss_rising_transition_count,
        latency_p95_w5_w1_ratio=latency_ratio,
        steady_window_at_or_above_period_count=sum(window_period_flags),
        overrun_count=overrun_count,
        overrun_ratio=overrun_ratio,
        memory_classification=memory_classification,
        latency_classification=latency_classification,
        overall_classification=overall_classification,
        hard_gate_ok=overall_classification != 'FAIL',
    )


@dataclass(frozen=True, slots=True)
class RemovedAdoptionPressureMetrics:
    adoption_at_seconds: int
    removed_alarm_percent: int
    request_at_seconds: int
    decision_at_seconds: int
    deactivation_window_seconds: int
    source_revision: str
    target_revision: str
    plan_change_count: int
    compatible_change_count: int
    unchanged_change_count: int
    structural_reset_change_count: int
    disabled_change_count: int
    removed_change_count: int
    expected_removed_change_count: int
    rejected_change_count: int
    target_defined_alarm_count: int
    expected_target_defined_alarm_count: int
    target_runtime_alarm_count: int
    expected_target_runtime_alarm_count: int
    effective_cache_revision: str | None
    adoption_iteration_count: int
    adoption_iteration: int | None
    adoption_iteration_ms: float | None
    adoption_iteration_cpu_percent: float | None
    adoption_cycle_executed: bool
    post_adoption_cache_current_iteration_count: int
    request_receipt_count: int
    pending_approval_receipt_count: int
    deactivation_request_count: int
    management_effect_started_count: int
    decision_receipt_count: int
    applied_decision_receipt_count: int
    deactivation_effect_started_count: int
    deactivation_effect_cleared_count: int
    adoption_commit_count: int
    expected_adoption_commit_count: int
    configuration_removed_occurrence_count: int
    expected_configuration_removed_occurrence_count: int
    configuration_disabled_occurrence_count: int
    management_effect_cleared_count: int
    occurrence_identity_mismatch_count: int
    control_plane_correlation_mismatch_count: int
    source_revision_durable_record_count: int
    expected_source_revision_durable_record_count: int
    target_revision_durable_record_count: int
    expected_target_revision_durable_record_count: int
    durable_record_count: int
    expected_durable_record_count: int
    source_state_basis_snapshot_count: int
    target_state_basis_snapshot_count: int
    final_alarm_state_count: int
    expected_final_alarm_state_count: int
    final_assignment_count: int
    expected_final_assignment_count: int
    open_occurrence_count: int
    expected_open_occurrence_count: int
    open_episode_count: int
    expected_open_episode_count: int
    orphan_deactivation_state_count: int
    expected_orphan_deactivation_state_count: int
    orphan_occurrence_count: int
    orphan_management_effect_count: int
    management_cursor_byte_offset: int | None
    decision_cursor_byte_offset: int | None
    management_pending_count: int
    decision_pending_count: int
    pending_deactivation_request_count: int
    functional_integrity_ok: bool


@dataclass(frozen=True, slots=True)
class PerformanceRunReport:
    test_id: str
    alarm_count: int
    planned_duration_seconds: float
    actual_duration_seconds: float
    iteration_period_seconds: float
    data_refresh_seconds: int
    data_profile: str
    columns_per_alarm: int
    historical_series_per_alarm: int
    historical_window_minutes: int
    historical_step_seconds: int
    historical_points_per_series: int
    historical_value_count: int
    physical_partition_count: int
    physical_partition_column_counts: tuple[int, ...]
    physical_partition_layout: str
    empty_physical_partition_count: int
    missing_source_column_count: int
    synthesized_null_column_count: int
    source_view_count: int
    source_column_count: int
    source_row_count: int
    source_frame_bytes: int
    source_numeric_value_count: int
    latest_source_column_count: int
    historical_source_column_count: int
    historical_source_row_count: int
    source_load_p50_ms: float
    source_load_p95_ms: float
    source_load_p99_ms: float
    source_merge_p50_ms: float
    source_merge_p95_ms: float
    source_merge_p99_ms: float
    iterations: int
    p50_iteration_ms: float
    p95_iteration_ms: float
    p99_iteration_ms: float
    max_iteration_ms: float
    overrun_count: int
    overrun_ratio: float
    average_cpu_percent: float
    peak_cpu_percent: float
    average_rss_mb: float
    peak_rss_mb: float
    recovery_ms: float
    drain_ms: float
    journal_aligned: bool
    durable_record_count: int
    snapshot_count: int
    snapshot_alarm_count: int
    expected_snapshot_alarm_count: int
    neutral_alarm_count: int
    integrity_ok: bool
    source_load_count: int
    result: str
    performance_class: str
    durable_history_lookup: DurableHistoryLookupMetrics | None = None
    functional_pressure: FunctionalPressureMetrics | None = None
    management_pressure: ManagementPressureMetrics | SustainedManagementPressureMetrics | None = (
        None
    )
    parameter_adoption_pressure: ParameterAdoptionPressureMetrics | None = None
    c2_routing_adoption_pressure: C2RoutingAdoptionPressureMetrics | None = None
    disabled_adoption_pressure: DisabledAdoptionPressureMetrics | None = None
    removed_adoption_pressure: RemovedAdoptionPressureMetrics | None = None
    structural_reset_adoption_pressure: StructuralResetAdoptionPressureMetrics | None = None
    mixed_revision_adoption_pressure: MixedRevisionAdoptionPressureMetrics | None = None
    rejected_target_pressure: RejectedTargetPressureMetrics | None = None
    source_unavailable_pressure: SourceUnavailablePressureMetrics | None = None
    invalid_source_candidate_pressure: InvalidSourceCandidatePressureMetrics | None = None
    lease_loss_adoption_pressure: LeaseLossAdoptionPressureMetrics | None = None
    cache_promotion_failure_pressure: CachePromotionFailurePressureMetrics | None = None
    drain_under_workload_pressure: DrainUnderWorkloadPressureMetrics | None = None
    temporal_soak: TemporalSoakMetrics | None = None
    deactivation_decision_pressure: (
        DeactivationDecisionPressureMetrics
        | SustainedDeactivationDecisionPressureMetrics
        | InvertedDeactivationDecisionPressureMetrics
        | MixedDeactivationDecisionPressureMetrics
        | StaleTargetDeactivationDecisionPressureMetrics
        | None
    ) = None

    def as_document(self) -> dict[str, object]:
        return asdict(self)


class PerformanceRecorder:
    def __init__(self, *, iteration_period_seconds: float) -> None:
        self.iteration_period_seconds = float(iteration_period_seconds)
        self.samples: list[IterationSample] = []
        self.recovery_ms = 0.0
        self.drain_ms = 0.0
        self._process = psutil.Process()
        self._previous_start: float | None = None

    def measure_recovery(self, callback, context: JobRuntimeContext):
        started = time.perf_counter()
        try:
            return callback(context)
        finally:
            self.recovery_ms = (time.perf_counter() - started) * 1000

    def measure_drain(self, callback, context: JobRuntimeContext):
        started = time.perf_counter()
        try:
            return callback(context)
        finally:
            self.drain_ms = (time.perf_counter() - started) * 1000

    def measure_iteration(self, callback, context: JobRuntimeContext):
        started = time.perf_counter()
        cpu_before = self._cpu_time()
        rss_before = self._rss_mb()
        interval_ms = (
            None if self._previous_start is None else (started - self._previous_start) * 1000
        )
        self._previous_start = started
        result = callback(context)
        duration = time.perf_counter() - started
        cpu_after = self._cpu_time()
        rss_after = self._rss_mb()
        cpu_percent = 0.0 if duration <= 0 else max(0.0, (cpu_after - cpu_before) / duration * 100)
        self.samples.append(
            IterationSample(
                iteration=context.iteration,
                started_monotonic=started,
                start_interval_ms=interval_ms,
                duration_ms=duration * 1000,
                cpu_percent=cpu_percent,
                rss_before_mb=rss_before,
                rss_after_mb=rss_after,
                revision_origin=result.revision_origin.value,
                adoption_outcome=result.adoption_outcome.value,
                cycle_executed=result.cycle_executed,
                degraded=result.degraded,
            )
        )
        return result

    def build_report(
        self,
        *,
        test_id: str,
        alarm_count: int,
        planned_duration_seconds: float,
        actual_duration_seconds: float,
        data_refresh_seconds: int,
        data_profile: str,
        columns_per_alarm: int,
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
        source_view_count: int,
        source_column_count: int,
        source_row_count: int,
        source_frame_bytes: int,
        source_numeric_value_count: int,
        latest_source_column_count: int,
        historical_source_column_count: int,
        historical_source_row_count: int,
        source_load_durations_ms: list[float],
        source_merge_durations_ms: list[float],
        journal_aligned: bool,
        durable_record_count: int,
        snapshot_count: int,
        snapshot_alarm_count: int,
        expected_snapshot_alarm_count: int,
        source_load_count: int,
        durable_history_lookup: DurableHistoryLookupMetrics | None = None,
        functional_pressure: FunctionalPressureMetrics | None = None,
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
    ) -> PerformanceRunReport:
        durations = [sample.duration_ms for sample in self.samples]
        cpu_values = [sample.cpu_percent for sample in self.samples]
        rss_values = [
            value
            for sample in self.samples
            for value in (sample.rss_before_mb, sample.rss_after_mb)
        ]
        period_ms = self.iteration_period_seconds * 1000
        overrun_count = sum(duration > period_ms for duration in durations)
        p95 = _percentile(durations, 95)
        if not 0 <= expected_snapshot_alarm_count <= alarm_count:
            raise ValueError('expected_snapshot_alarm_count must be between zero and alarm_count')
        neutral_alarm_count = alarm_count - expected_snapshot_alarm_count
        functional_integrity_ok = (
            functional_pressure is None or functional_pressure.functional_integrity_ok
        )
        management_integrity_ok = (
            management_pressure is None or management_pressure.functional_integrity_ok
        )
        parameter_adoption_integrity_ok = (
            parameter_adoption_pressure is None
            or parameter_adoption_pressure.functional_integrity_ok
        )
        c2_routing_adoption_integrity_ok = (
            c2_routing_adoption_pressure is None
            or c2_routing_adoption_pressure.functional_integrity_ok
        )
        disabled_adoption_integrity_ok = (
            disabled_adoption_pressure is None or disabled_adoption_pressure.functional_integrity_ok
        )
        removed_adoption_integrity_ok = (
            removed_adoption_pressure is None or removed_adoption_pressure.functional_integrity_ok
        )
        structural_reset_adoption_integrity_ok = (
            structural_reset_adoption_pressure is None
            or structural_reset_adoption_pressure.functional_integrity_ok
        )
        mixed_revision_adoption_integrity_ok = (
            mixed_revision_adoption_pressure is None
            or mixed_revision_adoption_pressure.functional_integrity_ok
        )
        rejected_target_integrity_ok = (
            rejected_target_pressure is None or rejected_target_pressure.functional_integrity_ok
        )
        source_unavailable_integrity_ok = (
            source_unavailable_pressure is None
            or source_unavailable_pressure.functional_integrity_ok
        )
        invalid_source_candidate_integrity_ok = (
            invalid_source_candidate_pressure is None
            or invalid_source_candidate_pressure.functional_integrity_ok
        )
        lease_loss_adoption_integrity_ok = (
            lease_loss_adoption_pressure is None
            or lease_loss_adoption_pressure.functional_integrity_ok
        )
        cache_promotion_failure_integrity_ok = (
            cache_promotion_failure_pressure is None
            or cache_promotion_failure_pressure.functional_integrity_ok
        )
        drain_under_workload_integrity_ok = (
            drain_under_workload_pressure is None
            or drain_under_workload_pressure.functional_integrity_ok
        )
        temporal_soak_integrity_ok = temporal_soak is None or temporal_soak.hard_gate_ok
        deactivation_decision_integrity_ok = (
            deactivation_decision_pressure is None
            or deactivation_decision_pressure.functional_integrity_ok
        )
        integrity_ok = (
            journal_aligned
            and snapshot_alarm_count == expected_snapshot_alarm_count
            and functional_integrity_ok
            and management_integrity_ok
            and parameter_adoption_integrity_ok
            and c2_routing_adoption_integrity_ok
            and disabled_adoption_integrity_ok
            and removed_adoption_integrity_ok
            and structural_reset_adoption_integrity_ok
            and mixed_revision_adoption_integrity_ok
            and rejected_target_integrity_ok
            and source_unavailable_integrity_ok
            and invalid_source_candidate_integrity_ok
            and lease_loss_adoption_integrity_ok
            and cache_promotion_failure_integrity_ok
            and drain_under_workload_integrity_ok
            and temporal_soak_integrity_ok
            and deactivation_decision_integrity_ok
        )
        result = 'PASS' if integrity_ok else 'FAIL'
        performance_class = (
            'GREEN' if p95 <= 5000 else 'ACCEPTABLE/REVIEW' if p95 <= 10000 else 'INVESTIGATE'
        )
        if temporal_soak is not None:
            if temporal_soak.overall_classification == 'FAIL':
                performance_class = 'INVESTIGATE'
            elif temporal_soak.overall_classification == 'REVIEW' and performance_class == 'GREEN':
                performance_class = 'ACCEPTABLE/REVIEW'
        return PerformanceRunReport(
            test_id=test_id,
            alarm_count=alarm_count,
            planned_duration_seconds=planned_duration_seconds,
            actual_duration_seconds=actual_duration_seconds,
            iteration_period_seconds=self.iteration_period_seconds,
            data_refresh_seconds=data_refresh_seconds,
            data_profile=data_profile,
            columns_per_alarm=columns_per_alarm,
            historical_series_per_alarm=historical_series_per_alarm,
            historical_window_minutes=historical_window_minutes,
            historical_step_seconds=historical_step_seconds,
            historical_points_per_series=historical_points_per_series,
            historical_value_count=historical_value_count,
            physical_partition_count=physical_partition_count,
            physical_partition_column_counts=physical_partition_column_counts,
            physical_partition_layout=physical_partition_layout,
            empty_physical_partition_count=empty_physical_partition_count,
            missing_source_column_count=missing_source_column_count,
            synthesized_null_column_count=synthesized_null_column_count,
            source_view_count=source_view_count,
            source_column_count=source_column_count,
            source_row_count=source_row_count,
            source_frame_bytes=source_frame_bytes,
            source_numeric_value_count=source_numeric_value_count,
            latest_source_column_count=latest_source_column_count,
            historical_source_column_count=historical_source_column_count,
            historical_source_row_count=historical_source_row_count,
            source_load_p50_ms=_percentile(source_load_durations_ms, 50),
            source_load_p95_ms=_percentile(source_load_durations_ms, 95),
            source_load_p99_ms=_percentile(source_load_durations_ms, 99),
            source_merge_p50_ms=_percentile(source_merge_durations_ms, 50),
            source_merge_p95_ms=_percentile(source_merge_durations_ms, 95),
            source_merge_p99_ms=_percentile(source_merge_durations_ms, 99),
            iterations=len(durations),
            p50_iteration_ms=_percentile(durations, 50),
            p95_iteration_ms=p95,
            p99_iteration_ms=_percentile(durations, 99),
            max_iteration_ms=max(durations, default=0.0),
            overrun_count=overrun_count,
            overrun_ratio=0.0 if not durations else overrun_count / len(durations),
            average_cpu_percent=statistics.fmean(cpu_values) if cpu_values else 0.0,
            peak_cpu_percent=max(cpu_values, default=0.0),
            average_rss_mb=statistics.fmean(rss_values) if rss_values else 0.0,
            peak_rss_mb=max(rss_values, default=0.0),
            recovery_ms=self.recovery_ms,
            drain_ms=self.drain_ms,
            journal_aligned=journal_aligned,
            durable_record_count=durable_record_count,
            snapshot_count=snapshot_count,
            snapshot_alarm_count=snapshot_alarm_count,
            expected_snapshot_alarm_count=expected_snapshot_alarm_count,
            neutral_alarm_count=neutral_alarm_count,
            integrity_ok=integrity_ok,
            source_load_count=source_load_count,
            result=result,
            performance_class=performance_class,
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
            lease_loss_adoption_pressure=lease_loss_adoption_pressure,
            cache_promotion_failure_pressure=cache_promotion_failure_pressure,
            drain_under_workload_pressure=drain_under_workload_pressure,
            temporal_soak=temporal_soak,
            deactivation_decision_pressure=deactivation_decision_pressure,
        )

    def write(self, *, output_dir: str | Path, report: PerformanceRunReport) -> None:
        root = Path(output_dir)
        root.mkdir(parents=True, exist_ok=True)
        (root / 'result.json').write_text(
            json.dumps(report.as_document(), indent=2, sort_keys=True) + '\n',
            encoding='utf-8',
        )
        with (root / 'samples.jsonl').open('w', encoding='utf-8') as handle:
            for sample in self.samples:
                handle.write(json.dumps(asdict(sample), sort_keys=True) + '\n')

    def _cpu_time(self) -> float:
        times = self._process.cpu_times()
        return float(times.user + times.system)

    def _rss_mb(self) -> float:
        return self._process.memory_info().rss / (1024 * 1024)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    if not 0 <= percentile <= 100 or not math.isfinite(percentile):
        raise ValueError('percentile must be between zero and 100')
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


class MeasuredAlarmRuntimeJobComposition(AlarmRuntimeJobComposition):
    __slots__ = ('recorder',)

    def __init__(self, *, recorder: PerformanceRecorder, **kwargs) -> None:
        super().__init__(**kwargs)
        self.recorder = recorder

    @classmethod
    def wrap(
        cls,
        composition: AlarmRuntimeJobComposition,
        *,
        recorder: PerformanceRecorder,
    ) -> MeasuredAlarmRuntimeJobComposition:
        return cls(
            recorder=recorder,
            composition=composition.composition,
            revision_resolver=composition.revision_resolver,
            adoption_executor=composition.adoption_executor,
            input_consumer=composition.input_consumer,
            iteration_source_loader=composition.iteration_source_loader,
            cycle_factory=composition.cycle_factory,
            as_of_provider=composition.as_of_provider,
        )

    def recover(self, context: JobRuntimeContext):
        return self.recorder.measure_recovery(super().recover, context)

    def drain(self, context: JobRuntimeContext):
        return self.recorder.measure_drain(super().drain, context)

    def iteration(self, context: JobRuntimeContext):
        begin_lookup_cycle = getattr(
            self.composition,
            'begin_durable_history_lookup_cycle',
            None,
        )
        if callable(begin_lookup_cycle):
            begin_lookup_cycle()
        return self.recorder.measure_iteration(super().iteration, context)
