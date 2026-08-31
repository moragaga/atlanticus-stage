from __future__ import annotations

import math
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from ada.alarms.core import (
    TECHNICAL_HOLD_GRACE_SECONDS,
    AlarmEvaluation,
    AlarmIdentity,
    AlarmKind,
    AlarmRouting,
    AlarmStatus,
    Criticality,
    DeactivationDecision,
    DeactivationDecisionKind,
    DeactivationIntent,
    DeactivationPolicy,
    EvaluationError,
    EvaluationErrorOrigin,
    EvidenceContractRef,
    EvidenceSnapshot,
    ManagementAction,
    PlannedAlarm,
    RoutingDestination,
)
from ada.data.core import (
    DataColumn,
    DataColumnType,
    DataPartition,
    DataRequirement,
    DataSource,
    TimeWindow,
    TimeWindowUnit,
)
from ada.data.sources import (
    LoadedDataSources,
    LoadedDataSourceView,
    PiSourceProvider,
    build_current_source_registry,
)
from ada.processes.alarms_runtime import (
    RUNTIME_MANIFEST_SCHEMA_VERSION,
    AlarmConfigurationAdoptionExecutor,
    AlarmConfigurationRevision,
    AlarmDurableInputConsumer,
    AlarmEvaluatorContract,
    AlarmEvaluatorRegistry,
    AlarmInputCursor,
    AlarmInputLocator,
    AlarmInputRecord,
    AlarmInputStream,
    AlarmOperationalCycle,
    AlarmRuntimeComposition,
    AlarmRuntimeJobComposition,
    FileRuntimeRevisionCache,
    FileRuntimeRevisionSource,
    RuntimeManifest,
    RuntimeRevisionBundle,
    RuntimeRevisionContractError,
    RuntimeRevisionResolver,
    RuntimeRevisionSourceError,
    build_alarm_execution_session,
    build_alarm_runtime_composition,
)
from atlanticus.runtime import RuntimeConfiguration
from atlanticus.state import AtomicJsonStore
from performance.f007_physical import (
    F007PhysicalDataSourceLoader,
    f007_daily_signal_column_for_alarm,
    f007_latest_signal_columns,
    f007_signal_column_for_alarm,
)
from performance.metrics import DurableHistoryLookupMetrics

_ALARM_REVISION = 'PERF-AC-1'
_RESCHEDULE_ALARM_REVISION = 'PERF-AC-2'
_STALE_TARGET_ALARM_REVISION = 'PERF-AC-2'
_PARAMETER_ADOPTION_ALARM_REVISION = 'PERF-AC-2'
_C2_ROUTING_ADOPTION_ALARM_REVISION = 'PERF-AC-2'
_DISABLED_ADOPTION_ALARM_REVISION = 'PERF-AC-2'
_REMOVED_ADOPTION_ALARM_REVISION = 'PERF-AC-2'
_STRUCTURAL_RESET_ADOPTION_ALARM_REVISION = 'PERF-AC-2'
_MIXED_REVISION_ADOPTION_ALARM_REVISION = 'PERF-AC-2'
_REJECTED_TARGET_ALARM_REVISION = 'PERF-AC-2'
_REJECTED_TARGET_PRIORITY_GROUP = 'perf-rejected-group'
_INVALID_CANDIDATE_ALARM_REVISION = 'PERF-AC-2'
_INVALID_CANDIDATE_DOCUMENT_REVISION = 'PERF-AC-CORRUPT'
_TOOL_REVISION = 'PERF-TR-1'
_FAMILY_KEY = 'perf'
_SHARED_EVALUATOR_KEY = 'steady-threshold'
_F007_PHYSICAL_EVALUATOR_PREFIX = 'f007-physical-threshold'
_F010_PHYSICAL_EVALUATOR_PREFIX = 'f010-physical-threshold'
_PRIORITY_GROUP = 'perf-baseline'
_PRIORITY_GROUP_PREFIX = 'perf-group'
_SHARED_SIGNAL_COLUMN = 'signal'
_SHARED_LATEST = 'shared-latest'
_LATEST_NARROW = 'latest-narrow'
_LATEST_WIDE = 'latest-wide'
_LATEST_HISTORICAL = 'latest-historical'
_F007_PHYSICAL_WARM = 'f007-physical-warm'
_F010_PHYSICAL_INTEGRATED = 'f010-physical-integrated'
_DATA_PROFILES = (
    _SHARED_LATEST,
    _LATEST_NARROW,
    _LATEST_WIDE,
    _LATEST_HISTORICAL,
    _F007_PHYSICAL_WARM,
    _F010_PHYSICAL_INTEGRATED,
)
_PARTITION_LAYOUTS = ('balanced', 'skewed', 'mixed')
_DURABLE_HISTORY_LOOKUP_MODES = ('baseline', 'indexed')
_TECHNICAL_ERROR_SIGNAL_VALUE = -9999.0


@dataclass(frozen=True, slots=True)
class BaselineScenario:
    test_id: str = 'A-001'
    alarm_count: int = 100
    duration_seconds: float = 120.0
    iteration_period_seconds: float = 5.0
    data_refresh_seconds: int = 10
    data_profile: str = _SHARED_LATEST
    columns_per_alarm: int = 1
    physical_partition_count: int = 1
    physical_partition_layout: str = 'balanced'
    historical_series_per_alarm: int = 0
    historical_window_minutes: int = 0
    historical_step_seconds: int = 0
    priority_group_size: int = 0
    operational_churn_percent: int = 0
    technical_hold_churn_percent: int = 0
    technical_hold_expiry_percent: int = 0
    technical_hold_expiry_stagger_seconds: int = 0
    technical_hold_error_duration_seconds: int = 0
    initial_error_activation_percent: int = 0
    initial_error_hold_seconds: int = 0
    initial_error_activation_stagger_seconds: int = 0
    fixed_initial_error_percent: int = 0
    c1_routing_destination_count: int = 0
    c2_routing_delay_seconds: tuple[int, ...] = ()
    c2_reschedule_delay_seconds: tuple[int, ...] = ()
    c2_reschedule_phase_a_seconds: int = 0
    c2_remove_destinations_phase_a_seconds: int = 0
    c2_remove_destinations_target: bool = False
    c2_routing_adoption_at_seconds: int = 0
    c2_routing_adoption_target_delay_seconds: tuple[int, ...] = ()
    management_action_at_seconds: int = 0
    management_action_count: int = 0
    management_action_interval_seconds: int = 0
    deactivation_decision_at_seconds: int = 0
    deactivation_decision_count: int = 0
    deactivation_decision_interval_seconds: int = 0
    deactivation_request_delivery_at_seconds: int = 0
    deactivation_target_removal_at_seconds: int = 0
    deactivation_window_seconds: int = 0
    parameter_adoption_at_seconds: int = 0
    parameter_target_threshold: float | None = None
    disabled_adoption_at_seconds: int = 0
    disabled_alarm_percent: int = 0
    removed_adoption_at_seconds: int = 0
    removed_alarm_percent: int = 0
    structural_reset_adoption_at_seconds: int = 0
    structural_reset_alarm_percent: int = 0
    mixed_revision_adoption_at_seconds: int = 0
    mixed_revision_target_threshold: float | None = None
    mixed_revision_disabled_alarm_percent: int = 0
    mixed_revision_removed_alarm_percent: int = 0
    mixed_revision_structural_reset_alarm_percent: int = 0
    rejected_candidate_at_seconds: int = 0
    source_unavailable_at_seconds: int = 0
    invalid_candidate_at_seconds: int = 0
    lease_loss_adoption_at_seconds: int = 0
    cache_promotion_failure_at_seconds: int = 0
    drain_under_workload_at_seconds: int = 0
    soak_warmup_seconds: int = 0
    soak_window_seconds: int = 0
    durable_history_lookup_mode: str = 'baseline'
    initial_active_percent: int = 100
    signal_value: float = 1.0
    threshold: float = 0.5

    def __post_init__(self) -> None:
        if not isinstance(self.test_id, str) or not self.test_id.strip():
            raise ValueError('test_id must be a non-empty string')
        if isinstance(self.alarm_count, bool) or not isinstance(self.alarm_count, int):
            raise TypeError('alarm_count must be an int')
        if self.alarm_count <= 0:
            raise ValueError('alarm_count must be greater than zero')
        for name in ('duration_seconds', 'iteration_period_seconds'):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise TypeError(f'{name} must be an int or float')
            if value <= 0:
                raise ValueError(f'{name} must be greater than zero')
        if isinstance(self.data_refresh_seconds, bool) or not isinstance(
            self.data_refresh_seconds, int
        ):
            raise TypeError('data_refresh_seconds must be an int')
        if self.data_refresh_seconds <= 0:
            raise ValueError('data_refresh_seconds must be greater than zero')
        if not isinstance(self.data_profile, str):
            raise TypeError('data_profile must be a string')
        normalized_profile = self.data_profile.strip().lower()
        if normalized_profile not in _DATA_PROFILES:
            raise ValueError(f'data_profile must be one of: {", ".join(_DATA_PROFILES)}')
        object.__setattr__(self, 'data_profile', normalized_profile)
        if isinstance(self.columns_per_alarm, bool) or not isinstance(self.columns_per_alarm, int):
            raise TypeError('columns_per_alarm must be an int')
        if self.columns_per_alarm <= 0:
            raise ValueError('columns_per_alarm must be greater than zero')
        if (
            normalized_profile
            in (
                _SHARED_LATEST,
                _LATEST_NARROW,
                _LATEST_HISTORICAL,
                _F007_PHYSICAL_WARM,
                _F010_PHYSICAL_INTEGRATED,
            )
            and self.columns_per_alarm != 1
        ):
            raise ValueError(f'{normalized_profile} requires columns_per_alarm=1')
        if normalized_profile == _LATEST_WIDE and self.columns_per_alarm < 2:
            raise ValueError('latest-wide requires columns_per_alarm greater than one')
        if isinstance(self.physical_partition_count, bool) or not isinstance(
            self.physical_partition_count, int
        ):
            raise TypeError('physical_partition_count must be an int')
        if self.physical_partition_count <= 0:
            raise ValueError('physical_partition_count must be greater than zero')
        source_column_count = (
            1
            if normalized_profile == _SHARED_LATEST
            else 1000
            if normalized_profile in (_F007_PHYSICAL_WARM, _F010_PHYSICAL_INTEGRATED)
            else self.alarm_count * self.columns_per_alarm
        )
        if self.physical_partition_count > source_column_count:
            raise ValueError('physical_partition_count must not exceed source column count')
        if not isinstance(self.physical_partition_layout, str):
            raise TypeError('physical_partition_layout must be a string')
        normalized_layout = self.physical_partition_layout.strip().lower()
        if normalized_layout not in _PARTITION_LAYOUTS:
            raise ValueError(
                f'physical_partition_layout must be one of: {", ".join(_PARTITION_LAYOUTS)}'
            )
        object.__setattr__(self, 'physical_partition_layout', normalized_layout)
        if not isinstance(self.durable_history_lookup_mode, str):
            raise TypeError('durable_history_lookup_mode must be a string')
        normalized_lookup_mode = self.durable_history_lookup_mode.strip().lower()
        if normalized_lookup_mode not in _DURABLE_HISTORY_LOOKUP_MODES:
            raise ValueError(
                'durable_history_lookup_mode must be one of: '
                + ', '.join(_DURABLE_HISTORY_LOOKUP_MODES)
            )
        object.__setattr__(self, 'durable_history_lookup_mode', normalized_lookup_mode)
        historical_fields = (
            ('historical_series_per_alarm', self.historical_series_per_alarm),
            ('historical_window_minutes', self.historical_window_minutes),
            ('historical_step_seconds', self.historical_step_seconds),
        )
        for name, value in historical_fields:
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f'{name} must be an int')
            if value < 0:
                raise ValueError(f'{name} must not be negative')
        if normalized_profile in (_LATEST_HISTORICAL, _F010_PHYSICAL_INTEGRATED):
            if self.historical_series_per_alarm <= 0:
                raise ValueError(f'{normalized_profile} requires historical_series_per_alarm > 0')
            if self.historical_window_minutes <= 0:
                raise ValueError(f'{normalized_profile} requires historical_window_minutes > 0')
            if self.historical_step_seconds <= 0:
                raise ValueError(f'{normalized_profile} requires historical_step_seconds > 0')
            window_seconds = self.historical_window_minutes * 60
            if window_seconds % self.historical_step_seconds != 0:
                raise ValueError(
                    'historical window seconds must be divisible by historical_step_seconds'
                )
        elif any(value != 0 for _, value in historical_fields):
            raise ValueError('historical parameters require a historical data profile')
        for name in ('signal_value', 'threshold'):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise TypeError(f'{name} must be an int or float')
        for name in (
            'priority_group_size',
            'operational_churn_percent',
            'technical_hold_churn_percent',
            'technical_hold_expiry_percent',
            'technical_hold_expiry_stagger_seconds',
            'technical_hold_error_duration_seconds',
            'initial_error_activation_percent',
            'initial_error_hold_seconds',
            'initial_error_activation_stagger_seconds',
            'fixed_initial_error_percent',
            'c1_routing_destination_count',
            'c2_reschedule_phase_a_seconds',
            'c2_remove_destinations_phase_a_seconds',
            'c2_routing_adoption_at_seconds',
            'management_action_at_seconds',
            'management_action_count',
            'management_action_interval_seconds',
            'deactivation_decision_at_seconds',
            'deactivation_decision_count',
            'deactivation_decision_interval_seconds',
            'deactivation_request_delivery_at_seconds',
            'deactivation_target_removal_at_seconds',
            'deactivation_window_seconds',
            'parameter_adoption_at_seconds',
            'disabled_adoption_at_seconds',
            'removed_adoption_at_seconds',
            'structural_reset_adoption_at_seconds',
            'mixed_revision_adoption_at_seconds',
            'disabled_alarm_percent',
            'removed_alarm_percent',
            'structural_reset_alarm_percent',
            'mixed_revision_disabled_alarm_percent',
            'mixed_revision_removed_alarm_percent',
            'mixed_revision_structural_reset_alarm_percent',
            'rejected_candidate_at_seconds',
            'source_unavailable_at_seconds',
            'invalid_candidate_at_seconds',
            'lease_loss_adoption_at_seconds',
            'cache_promotion_failure_at_seconds',
            'drain_under_workload_at_seconds',
            'soak_warmup_seconds',
            'soak_window_seconds',
            'initial_active_percent',
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f'{name} must be an int')
        if not isinstance(self.c2_remove_destinations_target, bool):
            raise TypeError('c2_remove_destinations_target must be a bool')
        if self.management_action_at_seconds < 0:
            raise ValueError('management_action_at_seconds must not be negative')
        if self.management_action_count < 0:
            raise ValueError('management_action_count must not be negative')
        if self.management_action_interval_seconds < 0:
            raise ValueError('management_action_interval_seconds must not be negative')
        if self.deactivation_decision_at_seconds < 0:
            raise ValueError('deactivation_decision_at_seconds must not be negative')
        if self.deactivation_decision_count < 0:
            raise ValueError('deactivation_decision_count must not be negative')
        if self.deactivation_decision_interval_seconds < 0:
            raise ValueError('deactivation_decision_interval_seconds must not be negative')
        if self.deactivation_request_delivery_at_seconds < 0:
            raise ValueError('deactivation_request_delivery_at_seconds must not be negative')
        if self.deactivation_target_removal_at_seconds < 0:
            raise ValueError('deactivation_target_removal_at_seconds must not be negative')
        if self.deactivation_window_seconds < 0:
            raise ValueError('deactivation_window_seconds must not be negative')
        if self.parameter_adoption_at_seconds < 0:
            raise ValueError('parameter_adoption_at_seconds must not be negative')
        if self.disabled_adoption_at_seconds < 0:
            raise ValueError('disabled_adoption_at_seconds must not be negative')
        if self.removed_adoption_at_seconds < 0:
            raise ValueError('removed_adoption_at_seconds must not be negative')
        if self.structural_reset_adoption_at_seconds < 0:
            raise ValueError('structural_reset_adoption_at_seconds must not be negative')
        if self.mixed_revision_adoption_at_seconds < 0:
            raise ValueError('mixed_revision_adoption_at_seconds must not be negative')
        if self.rejected_candidate_at_seconds < 0:
            raise ValueError('rejected_candidate_at_seconds must not be negative')
        if self.source_unavailable_at_seconds < 0:
            raise ValueError('source_unavailable_at_seconds must not be negative')
        if self.invalid_candidate_at_seconds < 0:
            raise ValueError('invalid_candidate_at_seconds must not be negative')
        if self.lease_loss_adoption_at_seconds < 0:
            raise ValueError('lease_loss_adoption_at_seconds must not be negative')
        if self.cache_promotion_failure_at_seconds < 0:
            raise ValueError('cache_promotion_failure_at_seconds must not be negative')
        if self.drain_under_workload_at_seconds < 0:
            raise ValueError('drain_under_workload_at_seconds must not be negative')
        if self.soak_warmup_seconds < 0:
            raise ValueError('soak_warmup_seconds must not be negative')
        if self.soak_window_seconds < 0:
            raise ValueError('soak_window_seconds must not be negative')
        if (self.soak_warmup_seconds == 0) != (self.soak_window_seconds == 0):
            raise ValueError('soak warmup and window must be configured together')
        if not 0 <= self.disabled_alarm_percent <= 100:
            raise ValueError('disabled_alarm_percent must be between zero and 100')
        if not 0 <= self.removed_alarm_percent <= 100:
            raise ValueError('removed_alarm_percent must be between zero and 100')
        if not 0 <= self.structural_reset_alarm_percent <= 100:
            raise ValueError('structural_reset_alarm_percent must be between zero and 100')
        if not 0 <= self.mixed_revision_disabled_alarm_percent <= 100:
            raise ValueError('mixed_revision_disabled_alarm_percent must be between zero and 100')
        if not 0 <= self.mixed_revision_removed_alarm_percent <= 100:
            raise ValueError('mixed_revision_removed_alarm_percent must be between zero and 100')
        if not 0 <= self.mixed_revision_structural_reset_alarm_percent <= 100:
            raise ValueError(
                'mixed_revision_structural_reset_alarm_percent must be between zero and 100'
            )
        if (
            self.structural_reset_adoption_at_seconds == 0
            and self.structural_reset_alarm_percent != 0
        ):
            raise ValueError(
                'structural_reset_alarm_percent requires structural_reset_adoption_at_seconds > 0'
            )
        if self.removed_adoption_at_seconds == 0 and self.removed_alarm_percent != 0:
            raise ValueError('removed_alarm_percent requires removed_adoption_at_seconds > 0')
        if self.mixed_revision_adoption_at_seconds == 0 and (
            self.mixed_revision_target_threshold is not None
            or self.mixed_revision_disabled_alarm_percent != 0
            or self.mixed_revision_removed_alarm_percent != 0
            or self.mixed_revision_structural_reset_alarm_percent != 0
        ):
            raise ValueError(
                'mixed revision parameters require mixed_revision_adoption_at_seconds > 0'
            )
        if self.parameter_target_threshold is not None:
            if isinstance(self.parameter_target_threshold, bool) or not isinstance(
                self.parameter_target_threshold, int | float
            ):
                raise TypeError('parameter_target_threshold must be an int, float, or None')
            if not math.isfinite(float(self.parameter_target_threshold)):
                raise ValueError('parameter_target_threshold must be finite')
        if self.mixed_revision_target_threshold is not None:
            if isinstance(self.mixed_revision_target_threshold, bool) or not isinstance(
                self.mixed_revision_target_threshold, int | float
            ):
                raise TypeError('mixed_revision_target_threshold must be an int, float, or None')
            if not math.isfinite(float(self.mixed_revision_target_threshold)):
                raise ValueError('mixed_revision_target_threshold must be finite')
        if self.priority_group_size < 0:
            raise ValueError('priority_group_size must not be negative')
        if self.c1_routing_destination_count < 0:
            raise ValueError('c1_routing_destination_count must not be negative')
        if not isinstance(self.c2_routing_delay_seconds, tuple) or not all(
            isinstance(delay, int) and not isinstance(delay, bool)
            for delay in self.c2_routing_delay_seconds
        ):
            raise TypeError('c2_routing_delay_seconds must be a tuple of ints')
        if any(delay <= 0 for delay in self.c2_routing_delay_seconds):
            raise ValueError('c2_routing_delay_seconds must contain positive values')
        if tuple(sorted(set(self.c2_routing_delay_seconds))) != self.c2_routing_delay_seconds:
            raise ValueError('c2_routing_delay_seconds must be unique and strictly increasing')
        if self.c1_routing_destination_count > 0 and self.c2_routing_delay_seconds:
            raise ValueError('C1 and C2 routing pressure are mutually exclusive')
        if not isinstance(self.c2_reschedule_delay_seconds, tuple) or not all(
            isinstance(delay, int) and not isinstance(delay, bool)
            for delay in self.c2_reschedule_delay_seconds
        ):
            raise TypeError('c2_reschedule_delay_seconds must be a tuple of ints')
        if any(delay <= 0 for delay in self.c2_reschedule_delay_seconds):
            raise ValueError('c2_reschedule_delay_seconds must contain positive values')
        if tuple(sorted(set(self.c2_reschedule_delay_seconds))) != self.c2_reschedule_delay_seconds:
            raise ValueError('c2_reschedule_delay_seconds must be unique and strictly increasing')
        if not isinstance(self.c2_routing_adoption_target_delay_seconds, tuple) or not all(
            isinstance(delay, int) and not isinstance(delay, bool)
            for delay in self.c2_routing_adoption_target_delay_seconds
        ):
            raise TypeError('c2_routing_adoption_target_delay_seconds must be a tuple of ints')
        if any(delay <= 0 for delay in self.c2_routing_adoption_target_delay_seconds):
            raise ValueError(
                'c2_routing_adoption_target_delay_seconds must contain positive values'
            )
        if (
            tuple(sorted(set(self.c2_routing_adoption_target_delay_seconds)))
            != self.c2_routing_adoption_target_delay_seconds
        ):
            raise ValueError(
                'c2_routing_adoption_target_delay_seconds must be unique and strictly increasing'
            )
        if self.c2_reschedule_delay_seconds and not self.c2_routing_delay_seconds:
            raise ValueError('C2 reschedule requires initial C2 routing delays')
        if self.c2_reschedule_phase_a_seconds > 0 and not self.c2_reschedule_delay_seconds:
            raise ValueError('C2 reschedule phase A timing requires reschedule delays')
        if self.c2_remove_destinations_phase_a_seconds > 0 and not self.c2_routing_delay_seconds:
            raise ValueError('C2 destination removal requires initial C2 routing delays')
        if self.c2_reschedule_delay_seconds and self.c2_remove_destinations_phase_a_seconds > 0:
            raise ValueError('C2 reschedule and destination removal are mutually exclusive')
        if self.c2_routing_adoption_at_seconds == 0:
            if self.c2_routing_adoption_target_delay_seconds:
                raise ValueError(
                    'C2 routing adoption target delays require c2_routing_adoption_at_seconds > 0'
                )
        else:
            if not self.c2_routing_delay_seconds:
                raise ValueError('C2 routing adoption requires initial C2 routing delays')
            if not self.c2_routing_adoption_target_delay_seconds:
                raise ValueError('C2 routing adoption requires target delays')
            if self.c2_reschedule_delay_seconds or self.c2_remove_destinations_phase_a_seconds > 0:
                raise ValueError(
                    'C2 routing adoption must not combine with legacy C2 phase adoption pressure'
                )
            if self.parameter_adoption_at_seconds > 0:
                raise ValueError('C2 routing adoption must not combine with parameter adoption')
            if self.disabled_adoption_at_seconds > 0:
                raise ValueError('C2 routing adoption must not combine with disabled adoption')
            if self.removed_adoption_at_seconds > 0:
                raise ValueError('C2 routing adoption must not combine with removed adoption')
        if self.priority_group_size > 0 and self.alarm_count % self.priority_group_size != 0:
            raise ValueError('alarm_count must be divisible by priority_group_size')
        if not 0 <= self.operational_churn_percent <= 100:
            raise ValueError('operational_churn_percent must be between zero and 100')
        if not 0 <= self.technical_hold_churn_percent <= 100:
            raise ValueError('technical_hold_churn_percent must be between zero and 100')
        if not 0 <= self.technical_hold_expiry_percent <= 100:
            raise ValueError('technical_hold_expiry_percent must be between zero and 100')
        if not 0 <= self.initial_error_activation_percent <= 100:
            raise ValueError('initial_error_activation_percent must be between zero and 100')
        if not 0 <= self.fixed_initial_error_percent <= 100:
            raise ValueError('fixed_initial_error_percent must be between zero and 100')
        if (
            sum(
                value > 0
                for value in (
                    self.operational_churn_percent,
                    self.technical_hold_churn_percent,
                    self.technical_hold_expiry_percent,
                    self.initial_error_activation_percent,
                    self.fixed_initial_error_percent,
                )
            )
            > 1
        ):
            raise ValueError('functional churn modes are mutually exclusive')
        if not 0 <= self.initial_active_percent <= 100:
            raise ValueError('initial_active_percent must be between zero and 100')
        if self.operational_churn_percent > 0:
            if normalized_profile != _LATEST_NARROW:
                raise ValueError('operational churn requires data_profile=latest-narrow')
            if normalized_layout != 'balanced':
                raise ValueError('operational churn requires physical_partition_layout=balanced')
            if self.priority_group_size <= 0:
                raise ValueError('operational churn requires priority_group_size > 0')
            if self.initial_active_percent != 50:
                raise ValueError('operational churn requires initial_active_percent=50')
            if self.signal_value < self.threshold:
                raise ValueError('operational churn requires signal_value >= threshold')
        if self.technical_hold_churn_percent > 0:
            if normalized_profile != _LATEST_NARROW:
                raise ValueError('technical hold churn requires data_profile=latest-narrow')
            if normalized_layout != 'balanced':
                raise ValueError('technical hold churn requires physical_partition_layout=balanced')
            if self.priority_group_size <= 0:
                raise ValueError('technical hold churn requires priority_group_size > 0')
            if self.initial_active_percent != 100:
                raise ValueError('technical hold churn requires initial_active_percent=100')
            if self.signal_value < self.threshold:
                raise ValueError('technical hold churn requires signal_value >= threshold')
        if self.technical_hold_expiry_percent > 0:
            if normalized_profile != _LATEST_NARROW:
                raise ValueError('technical hold expiry requires data_profile=latest-narrow')
            if normalized_layout != 'balanced':
                raise ValueError(
                    'technical hold expiry requires physical_partition_layout=balanced'
                )
            if self.priority_group_size <= 0:
                raise ValueError('technical hold expiry requires priority_group_size > 0')
            if self.initial_active_percent != 100:
                raise ValueError('technical hold expiry requires initial_active_percent=100')
            if self.signal_value < self.threshold:
                raise ValueError('technical hold expiry requires signal_value >= threshold')
            if self.technical_hold_expiry_stagger_seconds <= 0:
                raise ValueError('technical_hold_expiry_stagger_seconds must be greater than zero')
            if self.technical_hold_expiry_stagger_seconds % self.data_refresh_seconds != 0:
                raise ValueError('technical hold expiry stagger must align with data refresh')
            if TECHNICAL_HOLD_GRACE_SECONDS % self.data_refresh_seconds != 0:
                raise ValueError('technical hold grace must align with data refresh')
            if self.technical_hold_error_duration_seconds <= TECHNICAL_HOLD_GRACE_SECONDS:
                raise ValueError('technical hold error duration must exceed technical hold grace')
            if self.technical_hold_error_duration_seconds % self.data_refresh_seconds != 0:
                raise ValueError('technical hold error duration must align with data refresh')
        elif (
            self.technical_hold_expiry_stagger_seconds != 0
            or self.technical_hold_error_duration_seconds != 0
        ):
            raise ValueError(
                'technical hold expiry timing requires technical_hold_expiry_percent > 0'
            )
        if self.initial_error_activation_percent > 0:
            if normalized_profile != _LATEST_NARROW:
                raise ValueError('initial error activation requires data_profile=latest-narrow')
            if normalized_layout != 'balanced':
                raise ValueError(
                    'initial error activation requires physical_partition_layout=balanced'
                )
            if self.priority_group_size <= 0:
                raise ValueError('initial error activation requires priority_group_size > 0')
            if self.initial_active_percent != 0:
                raise ValueError('initial error activation requires initial_active_percent=0')
            if self.signal_value < self.threshold:
                raise ValueError('initial error activation requires signal_value >= threshold')
            if self.initial_error_hold_seconds < self.data_refresh_seconds * 2:
                raise ValueError('initial error hold must cover at least two data generations')
            if self.initial_error_hold_seconds % self.data_refresh_seconds != 0:
                raise ValueError('initial error hold must align with data refresh')
            if self.initial_error_activation_stagger_seconds <= 0:
                raise ValueError(
                    'initial_error_activation_stagger_seconds must be greater than zero'
                )
            if self.initial_error_activation_stagger_seconds % self.data_refresh_seconds != 0:
                raise ValueError('initial error activation stagger must align with data refresh')
        elif (
            self.initial_error_hold_seconds != 0
            or self.initial_error_activation_stagger_seconds != 0
        ):
            raise ValueError(
                'initial error activation timing requires initial_error_activation_percent > 0'
            )
        if self.fixed_initial_error_percent > 0:
            if normalized_profile != _LATEST_NARROW:
                raise ValueError('fixed initial error requires data_profile=latest-narrow')
            if normalized_layout != 'balanced':
                raise ValueError('fixed initial error requires physical_partition_layout=balanced')
            if self.priority_group_size <= 0:
                raise ValueError('fixed initial error requires priority_group_size > 0')
            if self.initial_active_percent != 100 - self.fixed_initial_error_percent:
                raise ValueError(
                    'fixed initial error requires initial_active_percent to complement error percent'
                )
            if self.signal_value < self.threshold:
                raise ValueError('fixed initial error requires signal_value >= threshold')
            if self.priority_group_count * self.fixed_initial_error_percent % 100 != 0:
                raise ValueError('priority groups must support exact fixed initial error percent')
        if self.c1_routing_destination_count > 0:
            if normalized_profile != _LATEST_NARROW:
                raise ValueError('C1 routing pressure requires data_profile=latest-narrow')
            if normalized_layout != 'balanced':
                raise ValueError('C1 routing pressure requires physical_partition_layout=balanced')
            if self.priority_group_size <= 0:
                raise ValueError('C1 routing pressure requires priority_group_size > 0')
            if self.initial_active_percent != 100:
                raise ValueError('C1 routing pressure requires initial_active_percent=100')
            if self.signal_value < self.threshold:
                raise ValueError('C1 routing pressure requires signal_value >= threshold')
            if self.functional_churn_percent > 0:
                raise ValueError('C1 routing pressure must not combine with functional churn')
        if self.c2_routing_delay_seconds:
            if normalized_profile != _LATEST_NARROW:
                raise ValueError('C2 routing pressure requires data_profile=latest-narrow')
            if normalized_layout != 'balanced':
                raise ValueError('C2 routing pressure requires physical_partition_layout=balanced')
            if self.priority_group_size <= 0:
                raise ValueError('C2 routing pressure requires priority_group_size > 0')
            if self.initial_active_percent != 100:
                raise ValueError('C2 routing pressure requires initial_active_percent=100')
            if self.signal_value < self.threshold:
                raise ValueError('C2 routing pressure requires signal_value >= threshold')
            if self.functional_churn_percent > 0:
                raise ValueError('C2 routing pressure must not combine with functional churn')
            if any(
                delay % self.data_refresh_seconds != 0 for delay in self.c2_routing_delay_seconds
            ):
                raise ValueError('C2 routing delays must align with data refresh')
            maximum_delay = self.c2_routing_delay_seconds[-1]
            if self.c2_reschedule_delay_seconds:
                if len(self.c2_reschedule_delay_seconds) != len(self.c2_routing_delay_seconds):
                    raise ValueError('C2 reschedule must preserve the destination count')
                if any(
                    initial == revised
                    for initial, revised in zip(
                        self.c2_routing_delay_seconds,
                        self.c2_reschedule_delay_seconds,
                        strict=True,
                    )
                ):
                    raise ValueError('C2 reschedule must change every destination delay')
                if any(
                    delay % self.data_refresh_seconds != 0
                    for delay in self.c2_reschedule_delay_seconds
                ):
                    raise ValueError('C2 reschedule delays must align with data refresh')
                if self.c2_reschedule_phase_a_seconds <= 0:
                    raise ValueError('C2 reschedule requires phase A duration > 0')
                if self.c2_reschedule_phase_a_seconds % self.data_refresh_seconds != 0:
                    raise ValueError('C2 reschedule phase A duration must align with data refresh')
                if self.c2_reschedule_phase_a_seconds >= self.c2_routing_delay_seconds[0]:
                    raise ValueError(
                        'C2 reschedule phase A must stop before the first initial due_at'
                    )
                if self.c2_reschedule_phase_a_seconds >= self.c2_reschedule_delay_seconds[0]:
                    raise ValueError(
                        'C2 reschedule adoption must occur before every revised due_at'
                    )
                maximum_delay = self.c2_reschedule_delay_seconds[-1]
            elif self.c2_remove_destinations_phase_a_seconds > 0:
                if len(self.c2_routing_delay_seconds) < 2:
                    raise ValueError('C2 destination removal requires at least two destinations')
                if self.c2_remove_destinations_phase_a_seconds % self.data_refresh_seconds != 0:
                    raise ValueError('C2 destination removal phase A must align with data refresh')
                if self.c2_remove_destinations_phase_a_seconds <= self.c2_routing_delay_seconds[0]:
                    raise ValueError(
                        'C2 destination removal phase A must pass the first initial due_at'
                    )
                if self.c2_remove_destinations_phase_a_seconds >= self.c2_routing_delay_seconds[1]:
                    raise ValueError(
                        'C2 destination removal phase A must stop before the second initial due_at'
                    )
            elif self.c2_routing_adoption_at_seconds > 0:
                target_delays = self.c2_routing_adoption_target_delay_seconds
                if len(target_delays) != len(self.c2_routing_delay_seconds):
                    raise ValueError('C2 routing adoption must preserve the destination count')
                if target_delays == self.c2_routing_delay_seconds:
                    raise ValueError(
                        'C2 routing adoption must change at least one destination delay'
                    )
                if any(delay % self.data_refresh_seconds != 0 for delay in target_delays):
                    raise ValueError(
                        'C2 routing adoption target delays must align with data refresh'
                    )
                if self.c2_routing_adoption_at_seconds % self.data_refresh_seconds != 0:
                    raise ValueError('C2 routing adoption timing must align with data refresh')
                assigned_before = tuple(
                    source_delay <= self.c2_routing_adoption_at_seconds
                    for source_delay in self.c2_routing_delay_seconds
                )
                if not any(assigned_before) or all(assigned_before):
                    raise ValueError(
                        'C2 routing adoption must occur with both assigned and pending destinations'
                    )
                retained_assigned = sum(
                    source_delay <= self.c2_routing_adoption_at_seconds
                    and target_delay == source_delay
                    for source_delay, target_delay in zip(
                        self.c2_routing_delay_seconds, target_delays, strict=True
                    )
                )
                promoted_pending = sum(
                    source_delay > self.c2_routing_adoption_at_seconds
                    and target_delay <= self.c2_routing_adoption_at_seconds
                    for source_delay, target_delay in zip(
                        self.c2_routing_delay_seconds, target_delays, strict=True
                    )
                )
                rescheduled_pending = sum(
                    source_delay > self.c2_routing_adoption_at_seconds
                    and target_delay > self.c2_routing_adoption_at_seconds
                    and target_delay != source_delay
                    for source_delay, target_delay in zip(
                        self.c2_routing_delay_seconds, target_delays, strict=True
                    )
                )
                if retained_assigned == 0:
                    raise ValueError(
                        'C2 routing adoption requires at least one retained assigned destination'
                    )
                if promoted_pending == 0:
                    raise ValueError(
                        'C2 routing adoption requires at least one pending destination to become assigned'
                    )
                if rescheduled_pending == 0:
                    raise ValueError(
                        'C2 routing adoption requires at least one pending destination to be rescheduled'
                    )
                maximum_delay = max(maximum_delay, target_delays[-1])
            if self.duration_seconds <= maximum_delay:
                raise ValueError('C2 routing pressure duration must exceed the maximum delay')
        if self.management_action_at_seconds == 0 and (
            self.management_action_count > 0 or self.management_action_interval_seconds > 0
        ):
            raise ValueError(
                'management action count/interval requires management_action_at_seconds > 0'
            )
        if self.deactivation_decision_at_seconds == 0 and (
            self.deactivation_window_seconds > 0
            or self.deactivation_decision_count > 0
            or self.deactivation_decision_interval_seconds > 0
            or self.deactivation_request_delivery_at_seconds > 0
            or self.deactivation_target_removal_at_seconds > 0
            or self.removed_adoption_at_seconds > 0
        ):
            raise ValueError(
                'deactivation decision count/interval/window requires '
                'deactivation_decision_at_seconds > 0'
            )
        if self.deactivation_decision_at_seconds > 0:
            if self.management_action_at_seconds <= 0:
                raise ValueError('deactivation decision pressure requires management setup')
            if self.deactivation_window_seconds <= 0:
                raise ValueError(
                    'deactivation decision pressure requires deactivation_window_seconds > 0'
                )
            if self.has_removed_adoption_pressure:
                if self.removed_alarm_percent <= 0:
                    raise ValueError('removed adoption requires removed_alarm_percent > 0')
                if self.effective_management_action_count != self.removed_alarm_count:
                    raise ValueError(
                        'removed adoption requires one management request per removed alarm'
                    )
                if self.effective_deactivation_decision_count != self.removed_alarm_count:
                    raise ValueError('removed adoption requires one decision per removed alarm')
                if self.management_action_interval_seconds != 0:
                    raise ValueError(
                        'removed adoption request setup requires request interval zero'
                    )
                if self.deactivation_decision_interval_seconds != 0:
                    raise ValueError('removed adoption decisions require decision interval zero')
                if not (
                    self.management_action_at_seconds
                    < self.deactivation_decision_at_seconds
                    < self.removed_adoption_at_seconds
                ):
                    raise ValueError(
                        'removed adoption requires request < decision < adoption timing'
                    )
                if self.management_action_at_seconds % self.data_refresh_seconds != 0:
                    raise ValueError('removed adoption request timing must align with data refresh')
                if self.deactivation_decision_at_seconds % self.data_refresh_seconds != 0:
                    raise ValueError(
                        'removed adoption decision timing must align with data refresh'
                    )
                if self.removed_adoption_at_seconds % self.data_refresh_seconds != 0:
                    raise ValueError('removed adoption timing must align with data refresh')
                if self.deactivation_window_seconds <= (
                    self.duration_seconds - self.management_action_at_seconds
                ):
                    raise ValueError(
                        'removed adoption deactivation window must remain active through the run'
                    )
                if (
                    self.duration_seconds
                    <= self.removed_adoption_at_seconds + self.data_refresh_seconds
                ):
                    raise ValueError('removed adoption duration must extend beyond adoption')
            elif self.has_stale_target_deactivation_pressure:
                if not self.has_multi_deactivation_decision_pressure:
                    raise ValueError('stale-target deactivation pressure requires multiple inputs')
                if (
                    self.effective_management_action_count
                    != self.effective_deactivation_decision_count
                ):
                    raise ValueError(
                        'stale-target deactivation pressure requires matching request and decision counts'
                    )
                if self.management_action_interval_seconds != 0:
                    raise ValueError('stale-target request setup requires request interval zero')
                if self.deactivation_decision_interval_seconds != 0:
                    raise ValueError('stale-target decisions require decision interval zero')
                if not (
                    self.management_action_at_seconds
                    < self.deactivation_target_removal_at_seconds
                    < self.deactivation_decision_at_seconds
                ):
                    raise ValueError(
                        'stale-target removal must occur after request setup and before decisions'
                    )
                if self.deactivation_target_removal_at_seconds % self.data_refresh_seconds != 0:
                    raise ValueError('stale-target removal timing must align with data refresh')
                if self.deactivation_decision_at_seconds % self.data_refresh_seconds != 0:
                    raise ValueError('stale-target decision timing must align with data refresh')
                if self.effective_deactivation_decision_count > self.priority_group_count:
                    raise ValueError(
                        'stale-target input count must not exceed priority group count'
                    )
                if self.deactivation_window_seconds <= (
                    self.deactivation_decision_at_seconds - self.management_action_at_seconds
                ):
                    raise ValueError(
                        'deactivation window must remain open at stale-target decision time'
                    )
                if (
                    self.duration_seconds
                    <= self.deactivation_decision_at_seconds + self.data_refresh_seconds
                ):
                    raise ValueError('stale-target duration must extend beyond the decision cycle')
            elif self.has_inverted_deactivation_delivery_pressure:
                if not self.has_multi_deactivation_decision_pressure:
                    raise ValueError(
                        'inverted deactivation delivery requires multiple request and decision inputs'
                    )
                if (
                    self.effective_management_action_count
                    != self.effective_deactivation_decision_count
                ):
                    raise ValueError(
                        'inverted deactivation delivery requires matching request and decision counts'
                    )
                if self.management_action_interval_seconds != 0:
                    raise ValueError(
                        'inverted deactivation request delivery requires request interval zero'
                    )
                if self.deactivation_decision_interval_seconds != 0:
                    raise ValueError(
                        'inverted deactivation decision delivery requires decision interval zero'
                    )
                if self.deactivation_decision_at_seconds <= self.management_action_at_seconds:
                    raise ValueError(
                        'inverted deactivation delivery requires request logical time before decision time'
                    )
                if (
                    self.deactivation_request_delivery_at_seconds
                    <= self.deactivation_decision_at_seconds
                ):
                    raise ValueError(
                        'inverted deactivation delivery requires requests to be delivered after decisions'
                    )
                if self.deactivation_window_seconds <= (
                    self.deactivation_decision_at_seconds - self.management_action_at_seconds
                ):
                    raise ValueError(
                        'deactivation window must remain open at inverted decision time'
                    )
                if (
                    self.duration_seconds
                    <= self.deactivation_request_delivery_at_seconds + self.data_refresh_seconds
                ):
                    raise ValueError(
                        'inverted deactivation duration must extend beyond request delivery and replay'
                    )
            elif self.has_drain_under_workload_pressure:
                if self.alarm_count != 1000:
                    raise ValueError('drain-under-workload pressure requires alarm_count=1000')
                if self.effective_priority_group_size != 10:
                    raise ValueError(
                        'drain-under-workload pressure requires priority_group_size=10'
                    )
                if self.duration_seconds != 600:
                    raise ValueError('drain-under-workload pressure requires duration_seconds=600')
                if self.iteration_period_seconds != 5:
                    raise ValueError(
                        'drain-under-workload pressure requires iteration_period_seconds=5'
                    )
                if self.data_refresh_seconds != 10:
                    raise ValueError(
                        'drain-under-workload pressure requires data_refresh_seconds=10'
                    )
                if self.data_profile != _LATEST_NARROW:
                    raise ValueError(
                        'drain-under-workload pressure requires data_profile=latest-narrow'
                    )
                if self.operational_churn_percent != 0:
                    raise ValueError(
                        'drain-under-workload pressure requires operational_churn_percent=0'
                    )
                if self.initial_active_percent != 100:
                    raise ValueError(
                        'drain-under-workload pressure requires initial_active_percent=100'
                    )
                if (
                    self.management_action_at_seconds != 30
                    or self.effective_management_action_count != 480
                    or self.management_action_interval_seconds != 1
                ):
                    raise ValueError(
                        'drain-under-workload pressure requires 480 management inputs at 1/s from +30s'
                    )
                if (
                    self.deactivation_decision_at_seconds != 60
                    or self.effective_deactivation_decision_count != 480
                    or self.deactivation_decision_interval_seconds != 1
                ):
                    raise ValueError(
                        'drain-under-workload pressure requires 480 decisions at 1/s from +60s'
                    )
                if self.deactivation_window_seconds != 900:
                    raise ValueError(
                        'drain-under-workload pressure requires deactivation_window_seconds=900'
                    )
                if self.drain_under_workload_at_seconds != 300:
                    raise ValueError('drain-under-workload pressure requires stop at +300 seconds')
            elif self.has_mixed_deactivation_pressure:
                if (
                    self.effective_management_action_count
                    != self.effective_deactivation_decision_count
                ):
                    raise ValueError(
                        'mixed deactivation pressure requires matching request and decision counts'
                    )
                if self.management_action_interval_seconds <= 0:
                    raise ValueError(
                        'mixed deactivation pressure requires positive request interval'
                    )
                if (
                    self.management_action_interval_seconds
                    != self.deactivation_decision_interval_seconds
                ):
                    raise ValueError('mixed deactivation request and decision intervals must match')
                if self.deactivation_decision_at_seconds <= self.management_action_at_seconds:
                    raise ValueError(
                        'mixed deactivation decisions must start after management requests'
                    )
                if self.deactivation_window_seconds <= (
                    self.deactivation_decision_at_seconds - self.management_action_at_seconds
                ):
                    raise ValueError('deactivation window must remain open at mixed decision time')
                if (
                    self.duration_seconds
                    <= max(
                        self.management_last_action_at_seconds,
                        self.deactivation_decision_last_at_seconds,
                    )
                    + self.data_refresh_seconds
                ):
                    raise ValueError(
                        'mixed deactivation duration must extend beyond both final input cycles'
                    )
            elif self.has_multi_deactivation_decision_pressure:
                if (
                    self.effective_management_action_count
                    != self.effective_deactivation_decision_count
                ):
                    raise ValueError(
                        'multi deactivation decisions require matching request and decision counts'
                    )
                if (
                    self.management_action_interval_seconds
                    != self.deactivation_decision_interval_seconds
                ):
                    raise ValueError('multi deactivation request and decision intervals must match')
                phase_duration = self.deactivation_phase_duration_seconds
                if (
                    phase_duration
                    <= self.management_last_action_at_seconds + self.data_refresh_seconds
                ):
                    raise ValueError(
                        'deactivation request phase must extend beyond the final setup cycle'
                    )
                if (
                    phase_duration
                    <= self.deactivation_decision_last_at_seconds + self.data_refresh_seconds
                ):
                    raise ValueError(
                        'deactivation decision phase must extend beyond the final decision cycle'
                    )
                if self.deactivation_window_seconds <= phase_duration:
                    raise ValueError(
                        'deactivation window must remain open across the two-phase boundary'
                    )
            else:
                if self.effective_management_action_count != 1:
                    raise ValueError(
                        'deactivation decision baseline requires exactly one management setup'
                    )
                if self.management_action_interval_seconds != 0:
                    raise ValueError(
                        'deactivation decision baseline requires single management setup'
                    )
                if self.deactivation_decision_interval_seconds != 0:
                    raise ValueError('single deactivation decision requires decision interval zero')
                if self.deactivation_decision_at_seconds <= self.management_action_at_seconds:
                    raise ValueError(
                        'deactivation decision must occur after management request setup'
                    )
                if self.deactivation_decision_at_seconds % self.data_refresh_seconds != 0:
                    raise ValueError('deactivation decision timing must align with data refresh')
                if self.deactivation_window_seconds <= (
                    self.deactivation_decision_at_seconds - self.management_action_at_seconds
                ):
                    raise ValueError(
                        'deactivation window must remain open when the decision is applied'
                    )
                if (
                    self.duration_seconds
                    <= self.deactivation_decision_at_seconds + self.data_refresh_seconds
                ):
                    raise ValueError(
                        'deactivation decision duration must extend beyond the decision cycle'
                    )
        if self.parameter_adoption_at_seconds == 0:
            if self.parameter_target_threshold is not None:
                raise ValueError(
                    'parameter_target_threshold requires parameter_adoption_at_seconds > 0'
                )
        else:
            if self.parameter_target_threshold is None:
                raise ValueError('parameter adoption requires parameter_target_threshold')
            if normalized_profile not in (_LATEST_NARROW, _F010_PHYSICAL_INTEGRATED):
                raise ValueError('parameter adoption requires a supported compatible data profile')
            if normalized_layout != 'balanced':
                raise ValueError('parameter adoption requires physical_partition_layout=balanced')
            if self.priority_group_size <= 0:
                raise ValueError('parameter adoption requires priority_group_size > 0')
            if self.initial_active_percent != 100:
                raise ValueError('parameter adoption requires initial_active_percent=100')
            if float(self.parameter_target_threshold) == float(self.threshold):
                raise ValueError('parameter adoption must change the threshold')
            if self.signal_value < max(
                float(self.threshold), float(self.parameter_target_threshold)
            ):
                raise ValueError(
                    'parameter adoption requires signal_value >= source and target thresholds'
                )
            if self.parameter_adoption_at_seconds % self.data_refresh_seconds != 0:
                raise ValueError('parameter adoption timing must align with data refresh')
            if (
                self.duration_seconds
                <= self.parameter_adoption_at_seconds + self.data_refresh_seconds
            ):
                raise ValueError('parameter adoption duration must extend beyond adoption')
            if (
                self.has_stale_target_deactivation_pressure
                or self.has_c2_reschedule_pressure
                or self.has_c2_remove_destinations_pressure
                or self.has_c2_routing_adoption_pressure
                or self.has_removed_adoption_pressure
            ):
                raise ValueError(
                    'parameter adoption must not combine with another configuration adoption pressure'
                )

        if self.disabled_adoption_at_seconds == 0:
            if self.disabled_alarm_percent != 0:
                raise ValueError('disabled_alarm_percent requires disabled_adoption_at_seconds > 0')
        else:
            if self.disabled_alarm_percent <= 0:
                raise ValueError('disabled adoption requires disabled_alarm_percent > 0')
            if normalized_profile != _LATEST_NARROW:
                raise ValueError('disabled adoption requires data_profile=latest-narrow')
            if normalized_layout != 'balanced':
                raise ValueError('disabled adoption requires physical_partition_layout=balanced')
            if self.priority_group_size <= 0:
                raise ValueError('disabled adoption requires priority_group_size > 0')
            if self.initial_active_percent != 100:
                raise ValueError('disabled adoption requires initial_active_percent=100')
            if self.signal_value < self.threshold:
                raise ValueError('disabled adoption requires signal_value >= threshold')
            if self.alarm_count * self.disabled_alarm_percent % 100 != 0:
                raise ValueError('alarm_count must support exact disabled_alarm_percent')
            if self.disabled_alarm_count != self.priority_group_count:
                raise ValueError(
                    'disabled adoption requires exactly one disabled alarm per priority group'
                )
            if self.disabled_adoption_at_seconds % self.data_refresh_seconds != 0:
                raise ValueError('disabled adoption timing must align with data refresh')
            if (
                self.duration_seconds
                <= self.disabled_adoption_at_seconds + self.data_refresh_seconds
            ):
                raise ValueError('disabled adoption duration must extend beyond adoption')
            if (
                self.has_stale_target_deactivation_pressure
                or self.has_c2_reschedule_pressure
                or self.has_c2_remove_destinations_pressure
                or self.has_c2_routing_adoption_pressure
                or self.has_parameter_adoption_pressure
                or self.has_removed_adoption_pressure
            ):
                raise ValueError(
                    'disabled adoption must not combine with another configuration adoption pressure'
                )

        if self.removed_adoption_at_seconds == 0:
            if self.removed_alarm_percent != 0:
                raise ValueError('removed_alarm_percent requires removed_adoption_at_seconds > 0')
        else:
            if self.removed_alarm_percent <= 0:
                raise ValueError('removed adoption requires removed_alarm_percent > 0')
            if normalized_profile != _LATEST_NARROW:
                raise ValueError('removed adoption requires data_profile=latest-narrow')
            if normalized_layout != 'balanced':
                raise ValueError('removed adoption requires physical_partition_layout=balanced')
            if self.priority_group_size <= 0:
                raise ValueError('removed adoption requires priority_group_size > 0')
            if self.initial_active_percent != 100:
                raise ValueError('removed adoption requires initial_active_percent=100')
            if self.signal_value < self.threshold:
                raise ValueError('removed adoption requires signal_value >= threshold')
            if self.alarm_count * self.removed_alarm_percent % 100 != 0:
                raise ValueError('alarm_count must support exact removed_alarm_percent')
            if self.removed_alarm_count != self.priority_group_count:
                raise ValueError(
                    'removed adoption requires exactly one removed alarm per priority group'
                )
            if (
                self.has_stale_target_deactivation_pressure
                or self.has_inverted_deactivation_delivery_pressure
                or self.has_c2_reschedule_pressure
                or self.has_c2_remove_destinations_pressure
                or self.has_c2_routing_adoption_pressure
                or self.has_parameter_adoption_pressure
                or self.has_disabled_adoption_pressure
            ):
                raise ValueError(
                    'removed adoption must not combine with another configuration adoption pressure'
                )

        if self.structural_reset_adoption_at_seconds == 0:
            if self.structural_reset_alarm_percent != 0:
                raise ValueError(
                    'structural_reset_alarm_percent requires '
                    'structural_reset_adoption_at_seconds > 0'
                )
        else:
            if self.structural_reset_alarm_percent <= 0:
                raise ValueError(
                    'structural reset adoption requires structural_reset_alarm_percent > 0'
                )
            if normalized_profile != _LATEST_NARROW:
                raise ValueError('structural reset adoption requires data_profile=latest-narrow')
            if normalized_layout != 'balanced':
                raise ValueError(
                    'structural reset adoption requires physical_partition_layout=balanced'
                )
            if self.priority_group_size <= 0:
                raise ValueError('structural reset adoption requires priority_group_size > 0')
            if self.initial_active_percent != 100:
                raise ValueError('structural reset adoption requires initial_active_percent=100')
            if self.signal_value < self.threshold:
                raise ValueError('structural reset adoption requires signal_value >= threshold')
            if self.has_routing_pressure:
                raise ValueError(
                    'structural reset adoption requires source C3 without routing pressure'
                )
            if self.alarm_count * self.structural_reset_alarm_percent % 100 != 0:
                raise ValueError('alarm_count must support exact structural_reset_alarm_percent')
            if self.structural_reset_alarm_count % self.effective_priority_group_size != 0:
                raise ValueError('structural reset adoption must cover complete priority groups')
            if self.structural_reset_priority_group_count <= 0:
                raise ValueError(
                    'structural reset adoption must affect at least one priority group'
                )
            if self.structural_reset_adoption_at_seconds % self.data_refresh_seconds != 0:
                raise ValueError('structural reset adoption timing must align with data refresh')
            if (
                self.duration_seconds
                <= self.structural_reset_adoption_at_seconds + self.iteration_period_seconds
            ):
                raise ValueError(
                    'structural reset adoption duration must include the next iteration'
                )
            if (
                self.has_stale_target_deactivation_pressure
                or self.has_c2_reschedule_pressure
                or self.has_c2_remove_destinations_pressure
                or self.has_c2_routing_adoption_pressure
                or self.has_parameter_adoption_pressure
                or self.has_disabled_adoption_pressure
                or self.has_removed_adoption_pressure
                or self.has_management_pressure
                or self.has_deactivation_decision_pressure
            ):
                raise ValueError('structural reset adoption must not combine with another pressure')

        if self.mixed_revision_adoption_at_seconds > 0:
            if self.mixed_revision_target_threshold is None:
                raise ValueError('mixed revision adoption requires target threshold')
            if float(self.mixed_revision_target_threshold) == float(self.threshold):
                raise ValueError('mixed revision adoption must change target threshold')
            if normalized_profile != _LATEST_NARROW:
                raise ValueError('mixed revision adoption requires data_profile=latest-narrow')
            if normalized_layout != 'balanced':
                raise ValueError(
                    'mixed revision adoption requires physical_partition_layout=balanced'
                )
            if self.priority_group_size <= 1:
                raise ValueError('mixed revision adoption requires priority_group_size > 1')
            if self.initial_active_percent != 100:
                raise ValueError('mixed revision adoption requires initial_active_percent=100')
            if self.signal_value < self.threshold:
                raise ValueError('mixed revision adoption requires signal_value >= threshold')
            if self.signal_value < float(self.mixed_revision_target_threshold):
                raise ValueError(
                    'mixed revision adoption requires signal_value >= target threshold'
                )
            if self.has_routing_pressure or self.functional_churn_percent > 0:
                raise ValueError(
                    'mixed revision adoption requires steady C3 source without routing/churn'
                )
            for name, percent in (
                ('disabled', self.mixed_revision_disabled_alarm_percent),
                ('removed', self.mixed_revision_removed_alarm_percent),
                ('structural reset', self.mixed_revision_structural_reset_alarm_percent),
            ):
                if percent <= 0:
                    raise ValueError(f'mixed revision adoption requires {name} alarm percent > 0')
                if self.alarm_count * percent % 100 != 0:
                    raise ValueError(
                        f'alarm_count must support exact mixed revision {name} alarm percent'
                    )
            if (
                self.mixed_revision_structural_reset_alarm_count
                % self.effective_priority_group_size
                != 0
            ):
                raise ValueError(
                    'mixed revision structural reset must cover complete priority groups'
                )
            if self.mixed_revision_structural_reset_priority_group_count <= 0:
                raise ValueError('mixed revision adoption must reset at least one priority group')
            remaining_groups = self.mixed_revision_non_reset_priority_group_count
            if self.mixed_revision_disabled_alarm_count > remaining_groups:
                raise ValueError(
                    'mixed revision disabled alarm count must not exceed non-reset groups'
                )
            if self.mixed_revision_removed_alarm_count > remaining_groups:
                raise ValueError(
                    'mixed revision removed alarm count must not exceed non-reset groups'
                )
            if (
                self.mixed_revision_disabled_alarm_count + self.mixed_revision_removed_alarm_count
                < remaining_groups
            ):
                raise ValueError(
                    'mixed revision disabled/removed groups must cover every non-reset group'
                )
            if (
                self.mixed_revision_disabled_removed_overlap_group_count > 0
                and self.effective_priority_group_size <= 2
            ):
                raise ValueError(
                    'mixed revision overlapping disabled/removed groups must retain an executable alarm'
                )
            if self.mixed_revision_compatible_alarm_count <= 0:
                raise ValueError('mixed revision adoption requires at least one compatible alarm')
            if self.mixed_revision_adoption_at_seconds % self.data_refresh_seconds != 0:
                raise ValueError('mixed revision adoption timing must align with data refresh')
            if (
                self.duration_seconds
                <= self.mixed_revision_adoption_at_seconds + self.iteration_period_seconds
            ):
                raise ValueError('mixed revision adoption duration must include the next iteration')
            if (
                self.has_stale_target_deactivation_pressure
                or self.has_c2_reschedule_pressure
                or self.has_c2_remove_destinations_pressure
                or self.has_c2_routing_adoption_pressure
                or self.has_parameter_adoption_pressure
                or self.has_disabled_adoption_pressure
                or self.has_removed_adoption_pressure
                or self.has_structural_reset_adoption_pressure
                or self.has_management_pressure
                or self.has_deactivation_decision_pressure
            ):
                raise ValueError('mixed revision adoption must not combine with another pressure')

        if self.rejected_candidate_at_seconds > 0:
            if normalized_profile != _LATEST_NARROW:
                raise ValueError('rejected candidate requires data_profile=latest-narrow')
            if normalized_layout != 'balanced':
                raise ValueError('rejected candidate requires physical_partition_layout=balanced')
            if self.priority_group_size <= 0:
                raise ValueError('rejected candidate requires priority_group_size > 0')
            if self.initial_active_percent != 100:
                raise ValueError('rejected candidate requires initial_active_percent=100')
            if self.signal_value < self.threshold:
                raise ValueError('rejected candidate requires signal_value >= threshold')
            if self.functional_churn_percent > 0 or self.has_routing_pressure:
                raise ValueError(
                    'rejected candidate requires steady C3 source without routing/churn'
                )
            if self.rejected_candidate_at_seconds % self.data_refresh_seconds != 0:
                raise ValueError('rejected candidate timing must align with data refresh')
            if (
                self.duration_seconds
                <= self.rejected_candidate_at_seconds + self.iteration_period_seconds
            ):
                raise ValueError(
                    'rejected candidate duration must include continued LKG iterations'
                )
            if (
                self.has_stale_target_deactivation_pressure
                or self.has_c2_reschedule_pressure
                or self.has_c2_remove_destinations_pressure
                or self.has_c2_routing_adoption_pressure
                or self.has_parameter_adoption_pressure
                or self.has_disabled_adoption_pressure
                or self.has_removed_adoption_pressure
                or self.has_structural_reset_adoption_pressure
                or self.has_mixed_revision_adoption_pressure
                or self.has_invalid_source_candidate_pressure
                or self.has_management_pressure
                or self.has_deactivation_decision_pressure
            ):
                raise ValueError('rejected candidate must not combine with another pressure')

        if self.source_unavailable_at_seconds > 0:
            if normalized_profile != _LATEST_NARROW:
                raise ValueError('source unavailable pressure requires data_profile=latest-narrow')
            if normalized_layout != 'balanced':
                raise ValueError(
                    'source unavailable pressure requires physical_partition_layout=balanced'
                )
            if self.priority_group_size <= 0:
                raise ValueError('source unavailable pressure requires priority_group_size > 0')
            if self.operational_churn_percent <= 0:
                raise ValueError('source unavailable pressure requires operational churn')
            if self.source_unavailable_at_seconds % self.data_refresh_seconds != 0:
                raise ValueError('source unavailable timing must align with data refresh')
            if (
                self.duration_seconds
                <= self.source_unavailable_at_seconds + self.iteration_period_seconds
            ):
                raise ValueError('source unavailable duration must include fallback iterations')
            if (
                self.has_stale_target_deactivation_pressure
                or self.has_c2_reschedule_pressure
                or self.has_c2_remove_destinations_pressure
                or self.has_c2_routing_adoption_pressure
                or self.has_parameter_adoption_pressure
                or self.has_disabled_adoption_pressure
                or self.has_removed_adoption_pressure
                or self.has_structural_reset_adoption_pressure
                or self.has_mixed_revision_adoption_pressure
                or self.has_rejected_candidate_pressure
                or self.has_invalid_source_candidate_pressure
                or self.has_management_pressure
                or self.has_deactivation_decision_pressure
            ):
                raise ValueError(
                    'source unavailable pressure must not combine with another pressure'
                )

        if self.invalid_candidate_at_seconds > 0:
            if normalized_profile != _LATEST_NARROW:
                raise ValueError('invalid candidate pressure requires data_profile=latest-narrow')
            if normalized_layout != 'balanced':
                raise ValueError(
                    'invalid candidate pressure requires physical_partition_layout=balanced'
                )
            if self.priority_group_size <= 0:
                raise ValueError('invalid candidate pressure requires priority_group_size > 0')
            if self.operational_churn_percent <= 0:
                raise ValueError('invalid candidate pressure requires operational churn')
            if self.invalid_candidate_at_seconds % self.data_refresh_seconds != 0:
                raise ValueError('invalid candidate timing must align with data refresh')
            if (
                self.duration_seconds
                <= self.invalid_candidate_at_seconds + self.iteration_period_seconds
            ):
                raise ValueError('invalid candidate duration must include fallback iterations')
            if (
                self.has_stale_target_deactivation_pressure
                or self.has_c2_reschedule_pressure
                or self.has_c2_remove_destinations_pressure
                or self.has_c2_routing_adoption_pressure
                or self.has_parameter_adoption_pressure
                or self.has_disabled_adoption_pressure
                or self.has_removed_adoption_pressure
                or self.has_structural_reset_adoption_pressure
                or self.has_mixed_revision_adoption_pressure
                or self.has_rejected_candidate_pressure
                or self.has_source_unavailable_pressure
                or self.has_management_pressure
                or self.has_deactivation_decision_pressure
            ):
                raise ValueError(
                    'invalid candidate pressure must not combine with another pressure'
                )

        if self.lease_loss_adoption_at_seconds > 0:
            if normalized_profile != _LATEST_NARROW:
                raise ValueError('lease loss adoption pressure requires data_profile=latest-narrow')
            if normalized_layout != 'balanced':
                raise ValueError(
                    'lease loss adoption pressure requires physical_partition_layout=balanced'
                )
            if self.priority_group_size <= 0:
                raise ValueError('lease loss adoption pressure requires priority_group_size > 0')
            if self.operational_churn_percent != 10:
                raise ValueError('lease loss adoption pressure requires operational churn 10%')
            if self.initial_active_percent != 50:
                raise ValueError('lease loss adoption pressure requires initial_active_percent=50')
            if self.signal_value < self.threshold:
                raise ValueError('lease loss adoption pressure requires signal_value >= threshold')
            if self.has_routing_pressure:
                raise ValueError('lease loss adoption pressure requires source C3 without routing')
            if self.alarm_count * 5 % 100 != 0:
                raise ValueError('alarm_count must support exact lease loss structural reset 5%')
            if (
                self.lease_loss_structural_reset_alarm_count % self.effective_priority_group_size
                != 0
            ):
                raise ValueError('lease loss adoption pressure must reset complete priority groups')
            if self.lease_loss_structural_reset_priority_group_count <= 0:
                raise ValueError(
                    'lease loss adoption pressure must reset at least one priority group'
                )
            if self.lease_loss_adoption_at_seconds % self.data_refresh_seconds != 0:
                raise ValueError('lease loss adoption timing must align with data refresh')
            if self.lease_loss_adoption_at_seconds % self.iteration_period_seconds != 0:
                raise ValueError('lease loss adoption timing must align with iteration period')
            if (
                self.duration_seconds
                <= self.lease_loss_adoption_at_seconds + self.iteration_period_seconds
            ):
                raise ValueError('lease loss adoption duration must include takeover replay')
            if (
                self.has_stale_target_deactivation_pressure
                or self.has_c2_reschedule_pressure
                or self.has_c2_remove_destinations_pressure
                or self.has_c2_routing_adoption_pressure
                or self.has_parameter_adoption_pressure
                or self.has_disabled_adoption_pressure
                or self.has_removed_adoption_pressure
                or self.has_structural_reset_adoption_pressure
                or self.has_mixed_revision_adoption_pressure
                or self.has_rejected_candidate_pressure
                or self.has_source_unavailable_pressure
                or self.has_invalid_source_candidate_pressure
                or self.has_management_pressure
                or self.has_deactivation_decision_pressure
            ):
                raise ValueError(
                    'lease loss adoption pressure must not combine with another pressure'
                )

        if self.cache_promotion_failure_at_seconds > 0:
            if normalized_profile != _LATEST_NARROW:
                raise ValueError(
                    'cache promotion failure pressure requires data_profile=latest-narrow'
                )
            if normalized_layout != 'balanced':
                raise ValueError(
                    'cache promotion failure pressure requires physical_partition_layout=balanced'
                )
            if self.priority_group_size <= 0:
                raise ValueError(
                    'cache promotion failure pressure requires priority_group_size > 0'
                )
            if self.operational_churn_percent != 10:
                raise ValueError('cache promotion failure pressure requires operational churn 10%')
            if self.initial_active_percent != 50:
                raise ValueError(
                    'cache promotion failure pressure requires initial_active_percent=50'
                )
            if self.signal_value < self.threshold:
                raise ValueError(
                    'cache promotion failure pressure requires signal_value >= threshold'
                )
            if self.has_routing_pressure:
                raise ValueError(
                    'cache promotion failure pressure requires source C3 without routing'
                )
            if self.alarm_count * 5 % 100 != 0:
                raise ValueError(
                    'alarm_count must support exact cache promotion failure structural reset 5%'
                )
            if (
                self.cache_promotion_failure_structural_reset_alarm_count
                % self.effective_priority_group_size
                != 0
            ):
                raise ValueError(
                    'cache promotion failure pressure must reset complete priority groups'
                )
            if self.cache_promotion_failure_structural_reset_priority_group_count <= 0:
                raise ValueError(
                    'cache promotion failure pressure must reset at least one priority group'
                )
            if self.cache_promotion_failure_at_seconds % self.data_refresh_seconds != 0:
                raise ValueError('cache promotion failure timing must align with data refresh')
            if self.cache_promotion_failure_at_seconds % self.iteration_period_seconds != 0:
                raise ValueError('cache promotion failure timing must align with iteration period')
            if self.duration_seconds <= self.cache_promotion_failure_at_seconds:
                raise ValueError(
                    'cache promotion failure duration must extend beyond the failure point'
                )
            if (
                self.has_stale_target_deactivation_pressure
                or self.has_c2_reschedule_pressure
                or self.has_c2_remove_destinations_pressure
                or self.has_c2_routing_adoption_pressure
                or self.has_parameter_adoption_pressure
                or self.has_disabled_adoption_pressure
                or self.has_removed_adoption_pressure
                or self.has_structural_reset_adoption_pressure
                or self.has_mixed_revision_adoption_pressure
                or self.has_rejected_candidate_pressure
                or self.has_source_unavailable_pressure
                or self.has_invalid_source_candidate_pressure
                or self.has_lease_loss_adoption_pressure
                or self.has_management_pressure
                or self.has_deactivation_decision_pressure
            ):
                raise ValueError(
                    'cache promotion failure pressure must not combine with another pressure'
                )

        temporal_soak_test_id = self.test_id.strip().upper()
        if (
            temporal_soak_test_id in {'F-001', 'F-002'}
            and self.has_temporal_soak
            and self.duration_seconds != 1800
        ):
            raise ValueError(f'{temporal_soak_test_id} requires duration_seconds=1800')

        if self.has_temporal_soak:
            if self.soak_warmup_seconds % self.iteration_period_seconds != 0:
                raise ValueError('soak warmup must align with iteration period')
            if self.soak_window_seconds % self.iteration_period_seconds != 0:
                raise ValueError('soak window must align with iteration period')
            if self.duration_seconds <= self.soak_warmup_seconds:
                raise ValueError('soak duration must extend beyond warmup')
            steady_seconds = self.duration_seconds - self.soak_warmup_seconds
            if steady_seconds % self.soak_window_seconds != 0:
                raise ValueError('soak steady duration must divide evenly into windows')
            if self.soak_window_count < 2:
                raise ValueError('soak requires at least two steady windows')
            if self.duration_seconds % 300 != 0:
                raise ValueError('soak duration must align with 300-second evidence cadence')
            if (
                self.has_functional_pressure
                or self.has_routing_pressure
                or self.has_c2_reschedule_pressure
                or self.has_c2_remove_destinations_pressure
                or self.has_c2_routing_adoption_pressure
                or self.has_parameter_adoption_pressure
                or self.has_disabled_adoption_pressure
                or self.has_removed_adoption_pressure
                or self.has_structural_reset_adoption_pressure
                or self.has_mixed_revision_adoption_pressure
                or self.has_rejected_candidate_pressure
                or self.has_source_unavailable_pressure
                or self.has_invalid_source_candidate_pressure
                or self.has_lease_loss_adoption_pressure
                or self.has_cache_promotion_failure_pressure
                or self.has_drain_under_workload_pressure
                or self.has_management_pressure
                or self.has_deactivation_decision_pressure
            ):
                raise ValueError('temporal soak must not combine with another pressure')

        if self.test_id.strip().upper() == 'F-001':
            if not self.has_temporal_soak:
                raise ValueError('F-001 requires temporal soak configuration')
            if self.alarm_count != 500:
                raise ValueError('F-001 requires alarm_count=500')
            if self.iteration_period_seconds != 5:
                raise ValueError('F-001 requires iteration_period_seconds=5')
            if self.data_refresh_seconds != 10:
                raise ValueError('F-001 requires data_refresh_seconds=10')
            if self.data_profile != _SHARED_LATEST:
                raise ValueError('F-001 requires data_profile=shared-latest')
            if self.physical_partition_count != 1:
                raise ValueError('F-001 requires physical_partition_count=1')
            if self.priority_group_size != 0:
                raise ValueError('F-001 requires one default priority group')
            if self.soak_warmup_seconds != 300 or self.soak_window_seconds != 300:
                raise ValueError('F-001 requires 300-second warmup and windows')
            if self.initial_active_percent != 100:
                raise ValueError('F-001 requires initial_active_percent=100')

        if self.test_id.strip().upper() == 'F-002':
            if not self.has_temporal_soak:
                raise ValueError('F-002 requires temporal soak configuration')
            if self.alarm_count != 1000:
                raise ValueError('F-002 requires alarm_count=1000')
            if self.iteration_period_seconds != 5:
                raise ValueError('F-002 requires iteration_period_seconds=5')
            if self.data_refresh_seconds != 10:
                raise ValueError('F-002 requires data_refresh_seconds=10')
            if self.data_profile != _SHARED_LATEST:
                raise ValueError('F-002 requires data_profile=shared-latest')
            if self.physical_partition_count != 1:
                raise ValueError('F-002 requires physical_partition_count=1')
            if self.priority_group_size != 0:
                raise ValueError('F-002 requires one default priority group')
            if self.soak_warmup_seconds != 300 or self.soak_window_seconds != 300:
                raise ValueError('F-002 requires 300-second warmup and windows')
            if self.initial_active_percent != 100:
                raise ValueError('F-002 requires initial_active_percent=100')

        if self.management_action_at_seconds > 0:
            if normalized_profile not in (_LATEST_NARROW, _F010_PHYSICAL_INTEGRATED):
                raise ValueError('management pressure requires a supported management data profile')
            if normalized_profile == _F010_PHYSICAL_INTEGRATED and self.test_id not in {
                'F-010',
                'F-010-TARGET',
            }:
                raise ValueError(
                    'f010-physical-integrated management pressure is reserved for F-010'
                )
            if normalized_layout != 'balanced':
                raise ValueError('management pressure requires physical_partition_layout=balanced')
            if self.priority_group_size <= 0:
                raise ValueError('management pressure requires priority_group_size > 0')
            if self.initial_active_percent != 100:
                raise ValueError('management pressure requires initial_active_percent=100')
            if self.signal_value < self.threshold:
                raise ValueError('management pressure requires signal_value >= threshold')
            if self.functional_churn_percent > 0 or self.has_routing_pressure:
                raise ValueError(
                    'management pressure must not combine with functional/routing pressure'
                )
            if self.management_action_at_seconds % self.data_refresh_seconds != 0:
                raise ValueError('management action timing must align with data refresh')
            if self.effective_management_action_count > self.alarm_count:
                raise ValueError('management action count must not exceed alarm_count')
            if (
                self.effective_management_action_count == 1
                and self.management_action_interval_seconds != 0
            ):
                raise ValueError('management action interval requires more than one action')
            if (
                self.duration_seconds
                <= self.management_last_action_at_seconds + self.data_refresh_seconds
            ):
                raise ValueError(
                    'management pressure duration must extend beyond the action cycle/final action'
                )
        if self.durable_history_lookup_mode == 'indexed' and self.fixed_initial_error_percent <= 0:
            raise ValueError(
                'indexed durable history lookup requires fixed_initial_error_percent > 0'
            )
        if self.has_functional_pressure:
            if self.priority_group_size * self.initial_active_percent % 100 != 0:
                raise ValueError('priority_group_size must support exact initial_active_percent')
            if self.alarm_count * self.functional_churn_percent % 100 != 0:
                raise ValueError('alarm_count must support exact functional churn percent')
            if self.changed_alarm_count % self.priority_group_size != 0:
                raise ValueError('churn alarm count must be divisible by priority_group_size')
            if self.priority_group_count % self.changed_priority_group_count != 0:
                raise ValueError('priority groups must divide evenly into churn cohorts')
            if self.technical_hold_expiry_percent > 0:
                cohort_count = self.priority_group_count // self.changed_priority_group_count
                last_recovery_seconds = (
                    self.data_refresh_seconds
                    + (cohort_count - 1) * self.technical_hold_expiry_stagger_seconds
                    + self.technical_hold_error_duration_seconds
                )
                if self.duration_seconds < last_recovery_seconds:
                    raise ValueError(
                        'duration_seconds must cover all technical hold expiry cohorts'
                    )
            if self.initial_error_activation_percent > 0:
                cohort_count = self.priority_group_count // self.changed_priority_group_count
                last_activation_seconds = (
                    self.initial_error_hold_seconds
                    + (cohort_count - 1) * self.initial_error_activation_stagger_seconds
                )
                if self.duration_seconds < last_activation_seconds:
                    raise ValueError(
                        'duration_seconds must cover all initial error activation cohorts'
                    )
        if self.test_id == 'F-010':
            expected = {
                'alarm_count': 1000,
                'duration_seconds': 1800,
                'iteration_period_seconds': 5,
                'data_refresh_seconds': 10,
                'data_profile': _F010_PHYSICAL_INTEGRATED,
                'columns_per_alarm': 1,
                'physical_partition_count': 1,
                'physical_partition_layout': 'balanced',
                'historical_series_per_alarm': 1,
                'historical_window_minutes': 60,
                'historical_step_seconds': 10,
                'priority_group_size': 10,
                'initial_active_percent': 100,
                'management_action_at_seconds': 300,
                'management_action_count': 480,
                'management_action_interval_seconds': 1,
                'deactivation_decision_at_seconds': 330,
                'deactivation_decision_count': 480,
                'deactivation_decision_interval_seconds': 1,
                'deactivation_window_seconds': 900,
                'parameter_adoption_at_seconds': 900,
                'parameter_target_threshold': 0.75,
                'threshold': 0.5,
            }
            for name, expected_value in expected.items():
                if getattr(self, name) != expected_value:
                    raise ValueError(f'F-010 requires {name}={expected_value}')
            if any(
                (
                    self.operational_churn_percent,
                    self.technical_hold_churn_percent,
                    self.technical_hold_expiry_percent,
                    self.initial_error_activation_percent,
                    self.fixed_initial_error_percent,
                    self.c1_routing_destination_count,
                    len(self.c2_routing_delay_seconds),
                    self.disabled_adoption_at_seconds,
                    self.removed_adoption_at_seconds,
                    self.structural_reset_adoption_at_seconds,
                    self.mixed_revision_adoption_at_seconds,
                    self.rejected_candidate_at_seconds,
                    self.source_unavailable_at_seconds,
                    self.invalid_candidate_at_seconds,
                    self.lease_loss_adoption_at_seconds,
                    self.cache_promotion_failure_at_seconds,
                    self.drain_under_workload_at_seconds,
                )
            ):
                raise ValueError('F-010 must not combine excluded pressure modes')

    @property
    def historical_points_per_series(self) -> int:
        if self.data_profile not in (_LATEST_HISTORICAL, _F010_PHYSICAL_INTEGRATED):
            return 0
        return self.historical_window_minutes * 60 // self.historical_step_seconds

    @property
    def historical_value_count(self) -> int:
        return (
            self.alarm_count * self.historical_series_per_alarm * self.historical_points_per_series
        )

    @property
    def effective_priority_group_size(self) -> int:
        return self.alarm_count if self.priority_group_size == 0 else self.priority_group_size

    @property
    def priority_group_count(self) -> int:
        return self.alarm_count // self.effective_priority_group_size

    @property
    def has_functional_pressure(self) -> bool:
        return self.functional_churn_percent > 0

    @property
    def functional_churn_percent(self) -> int:
        if self.operational_churn_percent > 0:
            return self.operational_churn_percent
        if self.technical_hold_churn_percent > 0:
            return self.technical_hold_churn_percent
        if self.technical_hold_expiry_percent > 0:
            return self.technical_hold_expiry_percent
        if self.initial_error_activation_percent > 0:
            return self.initial_error_activation_percent
        return self.fixed_initial_error_percent

    @property
    def has_c1_routing_pressure(self) -> bool:
        return self.c1_routing_destination_count > 0

    @property
    def has_c2_routing_pressure(self) -> bool:
        return bool(self.c2_routing_delay_seconds)

    @property
    def has_c2_reschedule_pressure(self) -> bool:
        return bool(self.c2_reschedule_delay_seconds)

    @property
    def has_c2_remove_destinations_pressure(self) -> bool:
        return self.c2_remove_destinations_phase_a_seconds > 0

    @property
    def has_c2_routing_adoption_pressure(self) -> bool:
        return self.c2_routing_adoption_at_seconds > 0

    @property
    def c2_phase_b_duration_seconds(self) -> float:
        if self.has_c2_reschedule_pressure:
            return float(self.duration_seconds) - self.c2_reschedule_phase_a_seconds
        if self.has_c2_remove_destinations_pressure:
            return float(self.duration_seconds) - self.c2_remove_destinations_phase_a_seconds
        return 0.0

    @property
    def has_routing_pressure(self) -> bool:
        return self.has_c1_routing_pressure or self.has_c2_routing_pressure

    @property
    def has_management_pressure(self) -> bool:
        return self.management_action_at_seconds > 0

    @property
    def has_deactivation_decision_pressure(self) -> bool:
        return self.deactivation_decision_at_seconds > 0

    @property
    def effective_deactivation_decision_count(self) -> int:
        if not self.has_deactivation_decision_pressure:
            return 0
        return self.deactivation_decision_count or 1

    @property
    def has_multi_deactivation_decision_pressure(self) -> bool:
        return self.effective_deactivation_decision_count > 1

    @property
    def has_stale_target_deactivation_pressure(self) -> bool:
        return self.deactivation_target_removal_at_seconds > 0

    @property
    def has_parameter_adoption_pressure(self) -> bool:
        return self.parameter_adoption_at_seconds > 0

    @property
    def has_disabled_adoption_pressure(self) -> bool:
        return self.disabled_adoption_at_seconds > 0

    @property
    def disabled_alarm_count(self) -> int:
        return self.alarm_count * self.disabled_alarm_percent // 100

    @property
    def has_removed_adoption_pressure(self) -> bool:
        return self.removed_adoption_at_seconds > 0

    @property
    def removed_alarm_count(self) -> int:
        return self.alarm_count * self.removed_alarm_percent // 100

    @property
    def has_structural_reset_adoption_pressure(self) -> bool:
        return self.structural_reset_adoption_at_seconds > 0

    @property
    def structural_reset_alarm_count(self) -> int:
        return self.alarm_count * self.structural_reset_alarm_percent // 100

    @property
    def structural_reset_priority_group_count(self) -> int:
        if self.effective_priority_group_size <= 0:
            return 0
        return self.structural_reset_alarm_count // self.effective_priority_group_size

    @property
    def has_mixed_revision_adoption_pressure(self) -> bool:
        return self.mixed_revision_adoption_at_seconds > 0

    @property
    def has_rejected_candidate_pressure(self) -> bool:
        return self.rejected_candidate_at_seconds > 0

    @property
    def has_source_unavailable_pressure(self) -> bool:
        return self.source_unavailable_at_seconds > 0

    @property
    def has_invalid_source_candidate_pressure(self) -> bool:
        return self.invalid_candidate_at_seconds > 0

    @property
    def has_lease_loss_adoption_pressure(self) -> bool:
        return self.lease_loss_adoption_at_seconds > 0

    @property
    def has_cache_promotion_failure_pressure(self) -> bool:
        return self.cache_promotion_failure_at_seconds > 0

    @property
    def has_drain_under_workload_pressure(self) -> bool:
        return self.drain_under_workload_at_seconds > 0

    @property
    def has_temporal_soak(self) -> bool:
        return self.soak_window_seconds > 0

    @property
    def soak_window_count(self) -> int:
        if not self.has_temporal_soak:
            return 0
        return int((self.duration_seconds - self.soak_warmup_seconds) / self.soak_window_seconds)

    @property
    def soak_samples_per_window(self) -> int:
        if not self.has_temporal_soak:
            return 0
        return int(self.soak_window_seconds / self.iteration_period_seconds)

    @property
    def soak_expected_iteration_count(self) -> int:
        if not self.has_temporal_soak:
            return 0
        return int(self.duration_seconds / self.iteration_period_seconds) + 1

    @property
    def soak_expected_durable_record_count(self) -> int:
        if not self.has_temporal_soak:
            return 0
        evidence_cycles = int(self.duration_seconds / 300)
        return self.priority_group_count * (1 + evidence_cycles)

    @property
    def drain_under_workload_stop_iteration(self) -> int:
        if not self.has_drain_under_workload_pressure:
            return 0
        return int(self.drain_under_workload_at_seconds / self.iteration_period_seconds) + 1

    @property
    def drain_under_workload_management_consumed_count(self) -> int:
        if not self.has_drain_under_workload_pressure:
            return 0
        return min(
            self.effective_management_action_count,
            (self.drain_under_workload_at_seconds - self.management_action_at_seconds)
            // self.management_action_interval_seconds
            + 1,
        )

    @property
    def drain_under_workload_decision_consumed_count(self) -> int:
        if not self.has_drain_under_workload_pressure:
            return 0
        return min(
            self.effective_deactivation_decision_count,
            (self.drain_under_workload_at_seconds - self.deactivation_decision_at_seconds)
            // self.deactivation_decision_interval_seconds
            + 1,
        )

    @property
    def drain_under_workload_pending_request_count(self) -> int:
        return (
            self.drain_under_workload_management_consumed_count
            - self.drain_under_workload_decision_consumed_count
        )

    @property
    def drain_under_workload_expected_durable_record_count(self) -> int:
        if not self.has_drain_under_workload_pressure:
            return 0
        return 702

    @property
    def cache_promotion_failure_structural_reset_alarm_count(self) -> int:
        return self.alarm_count * 5 // 100

    @property
    def cache_promotion_failure_structural_reset_priority_group_count(self) -> int:
        if self.effective_priority_group_size <= 0:
            return 0
        return (
            self.cache_promotion_failure_structural_reset_alarm_count
            // self.effective_priority_group_size
        )

    @property
    def lease_loss_structural_reset_alarm_count(self) -> int:
        return self.alarm_count * 5 // 100

    @property
    def lease_loss_structural_reset_priority_group_count(self) -> int:
        if self.effective_priority_group_size <= 0:
            return 0
        return self.lease_loss_structural_reset_alarm_count // self.effective_priority_group_size

    @property
    def mixed_revision_disabled_alarm_count(self) -> int:
        return self.alarm_count * self.mixed_revision_disabled_alarm_percent // 100

    @property
    def mixed_revision_removed_alarm_count(self) -> int:
        return self.alarm_count * self.mixed_revision_removed_alarm_percent // 100

    @property
    def mixed_revision_structural_reset_alarm_count(self) -> int:
        return self.alarm_count * self.mixed_revision_structural_reset_alarm_percent // 100

    @property
    def mixed_revision_structural_reset_priority_group_count(self) -> int:
        if self.effective_priority_group_size <= 0:
            return 0
        return (
            self.mixed_revision_structural_reset_alarm_count // self.effective_priority_group_size
        )

    @property
    def mixed_revision_non_reset_priority_group_count(self) -> int:
        return self.priority_group_count - self.mixed_revision_structural_reset_priority_group_count

    @property
    def mixed_revision_compatible_alarm_count(self) -> int:
        return (
            self.alarm_count
            - self.mixed_revision_disabled_alarm_count
            - self.mixed_revision_removed_alarm_count
            - self.mixed_revision_structural_reset_alarm_count
        )

    @property
    def mixed_revision_disabled_removed_overlap_group_count(self) -> int:
        return max(
            0,
            self.mixed_revision_disabled_alarm_count
            + self.mixed_revision_removed_alarm_count
            - self.mixed_revision_non_reset_priority_group_count,
        )

    @property
    def has_inverted_deactivation_delivery_pressure(self) -> bool:
        return self.deactivation_request_delivery_at_seconds > 0

    @property
    def has_mixed_deactivation_pressure(self) -> bool:
        return (
            self.has_multi_deactivation_decision_pressure
            and not self.has_stale_target_deactivation_pressure
            and not self.has_inverted_deactivation_delivery_pressure
            and not self.has_removed_adoption_pressure
            and self.deactivation_decision_at_seconds > self.management_action_at_seconds
        )

    @property
    def has_sustained_deactivation_decision_pressure(self) -> bool:
        return (
            self.has_multi_deactivation_decision_pressure
            and self.deactivation_decision_interval_seconds > 0
            and not self.has_mixed_deactivation_pressure
        )

    @property
    def has_burst_deactivation_decision_pressure(self) -> bool:
        return (
            self.has_multi_deactivation_decision_pressure
            and self.deactivation_decision_interval_seconds == 0
        )

    @property
    def deactivation_decision_arrival_mode(self) -> str:
        if not self.has_deactivation_decision_pressure:
            return 'none'
        if self.has_removed_adoption_pressure:
            return 'pre-removal'
        if self.has_stale_target_deactivation_pressure:
            return 'stale-target'
        if self.has_mixed_deactivation_pressure:
            return 'mixed'
        if self.has_burst_deactivation_decision_pressure:
            return 'burst'
        if self.has_sustained_deactivation_decision_pressure:
            return 'sustained'
        return 'single'

    @property
    def deactivation_decision_last_at_seconds(self) -> int:
        if not self.has_deactivation_decision_pressure:
            return 0
        return (
            self.deactivation_decision_at_seconds
            + (self.effective_deactivation_decision_count - 1)
            * self.deactivation_decision_interval_seconds
        )

    @property
    def deactivation_phase_duration_seconds(self) -> float:
        if (
            not self.has_multi_deactivation_decision_pressure
            or self.has_stale_target_deactivation_pressure
            or self.has_inverted_deactivation_delivery_pressure
            or self.has_mixed_deactivation_pressure
        ):
            return self.duration_seconds
        return self.duration_seconds / 2

    @property
    def effective_management_action_count(self) -> int:
        if not self.has_management_pressure:
            return 0
        return self.management_action_count or 1

    @property
    def has_multi_management_pressure(self) -> bool:
        return self.effective_management_action_count > 1

    @property
    def has_sustained_management_pressure(self) -> bool:
        return self.has_multi_management_pressure and self.management_action_interval_seconds > 0

    @property
    def has_burst_management_pressure(self) -> bool:
        return self.has_multi_management_pressure and self.management_action_interval_seconds == 0

    @property
    def management_arrival_mode(self) -> str:
        if not self.has_management_pressure:
            return 'none'
        if self.has_burst_management_pressure:
            return 'burst'
        if self.has_sustained_management_pressure:
            return 'sustained'
        return 'single'

    @property
    def management_last_action_at_seconds(self) -> int:
        if not self.has_management_pressure:
            return 0
        return (
            self.management_action_at_seconds
            + (self.effective_management_action_count - 1) * self.management_action_interval_seconds
        )

    @property
    def routing_criticality(self) -> str:
        if self.has_c1_routing_pressure:
            return 'C1'
        if self.has_c2_routing_pressure:
            return 'C2'
        return 'C3'

    @property
    def changed_alarm_count(self) -> int:
        return self.alarm_count * self.functional_churn_percent // 100

    @property
    def changed_priority_group_count(self) -> int:
        if not self.has_functional_pressure:
            return 0
        return self.changed_alarm_count // self.effective_priority_group_size

    @property
    def initial_active_alarm_count(self) -> int:
        return self.alarm_count * self.initial_active_percent // 100

    def expected_snapshot_alarm_count(
        self,
        *,
        missing_source_columns: tuple[str, ...],
    ) -> int:
        if self.has_stale_target_deactivation_pressure:
            return self.alarm_count - self.effective_deactivation_decision_count
        if self.has_disabled_adoption_pressure:
            return self.alarm_count - self.disabled_alarm_count
        if self.has_mixed_revision_adoption_pressure:
            return (
                self.alarm_count
                - self.mixed_revision_disabled_alarm_count
                - self.mixed_revision_removed_alarm_count
            )
        if self.operational_churn_percent > 0:
            return self.initial_active_alarm_count
        if (
            self.technical_hold_churn_percent > 0
            or self.technical_hold_expiry_percent > 0
            or self.initial_error_activation_percent > 0
        ):
            return self.alarm_count
        if self.fixed_initial_error_percent > 0:
            return self.initial_active_alarm_count
        if self.signal_value < self.threshold:
            return 0
        missing = set(missing_source_columns)
        return sum(
            _primary_signal_column(index, scenario=self) not in missing
            for index in range(self.alarm_count)
        )


@dataclass(slots=True)
class SyntheticDataSourceLoader:
    refresh_seconds: int
    signal_value: float
    threshold: float
    alarm_count: int
    priority_group_size: int = 0
    operational_churn_percent: int = 0
    technical_hold_churn_percent: int = 0
    technical_hold_expiry_percent: int = 0
    technical_hold_expiry_stagger_seconds: int = 0
    technical_hold_error_duration_seconds: int = 0
    initial_error_activation_percent: int = 0
    initial_error_hold_seconds: int = 0
    initial_error_activation_stagger_seconds: int = 0
    fixed_initial_error_percent: int = 0
    initial_active_percent: int = 100
    physical_partition_count: int = 1
    physical_partition_layout: str = 'balanced'
    historical_step_seconds: int = 0
    historical_points_per_series: int = 0
    load_count: int = 0
    first_generation: int | None = None
    last_generation: int | None = None
    churn_generation_count: int = 0
    churn_group_transition_count: int = 0
    churn_transition_count: int = 0
    technical_hold_started_transition_count: int = 0
    technical_hold_cleared_transition_count: int = 0
    technical_hold_expired_transition_count: int = 0
    technical_hold_expired_group_transition_count: int = 0
    technical_hold_reappearance_transition_count: int = 0
    technical_hold_reappearance_group_transition_count: int = 0
    initial_error_activation_transition_count: int = 0
    initial_error_activation_group_transition_count: int = 0
    view_count: int = 0
    column_count: int = 0
    row_count: int = 0
    frame_bytes: int = 0
    numeric_value_count: int = 0
    latest_column_count: int = 0
    historical_column_count: int = 0
    historical_row_count: int = 0
    historical_value_count: int = 0
    physical_partition_column_counts: tuple[int, ...] = ()
    empty_physical_partition_count: int = 0
    missing_source_column_count: int = 0
    missing_source_columns: tuple[str, ...] = ()
    synthesized_null_column_count: int = 0
    load_durations_ms: list[float] = field(default_factory=list)
    merge_durations_ms: list[float] = field(default_factory=list)

    def load(self, *, plan, as_of: datetime) -> LoadedDataSources:
        started = time.perf_counter()
        self.load_count += 1
        generation = int(as_of.timestamp()) // self.refresh_seconds
        if self.first_generation is None:
            self.first_generation = generation
        if (
            self._has_functional_pressure
            and self.last_generation is not None
            and generation > self.last_generation
        ):
            previous_index = self.last_generation - self.first_generation
            generation_index = generation - self.first_generation
            (
                changed_groups,
                started_groups,
                cleared_groups,
                reappeared_groups,
                initially_activated_groups,
            ) = self._transition_group_counts(
                previous_generation_index=previous_index,
                generation_index=generation_index,
            )
            expired_groups = self._technical_hold_expired_group_count_between(
                previous_generation_index=previous_index,
                generation_index=generation_index,
            )
            self.churn_generation_count += 1
            self.churn_group_transition_count += changed_groups
            self.churn_transition_count += changed_groups * self.priority_group_size
            self.technical_hold_started_transition_count += (
                started_groups * self.priority_group_size
            )
            self.technical_hold_cleared_transition_count += (
                cleared_groups * self.priority_group_size
            )
            self.technical_hold_expired_group_transition_count += expired_groups
            self.technical_hold_expired_transition_count += (
                expired_groups * self.priority_group_size
            )
            self.technical_hold_reappearance_group_transition_count += reappeared_groups
            self.technical_hold_reappearance_transition_count += (
                reappeared_groups * self.priority_group_size
            )
            self.initial_error_activation_group_transition_count += initially_activated_groups
            self.initial_error_activation_transition_count += (
                initially_activated_groups * self.priority_group_size
            )
        self.last_generation = generation
        generation_index = generation - self.first_generation
        loaded: dict = {}
        partition_column_counts: list[int] = []
        all_missing_columns: list[str] = []
        merge_duration_ms = 0.0
        empty_partition_count_total = 0
        for view_plan in plan.views:
            physical_partitions = _partition_columns(
                view_plan.column_names,
                self.physical_partition_count,
                layout=self.physical_partition_layout,
            )
            row_count = self._row_count_for(view_plan.partition)
            partition_frames: list[pd.DataFrame] = []
            missing_columns: list[str] = []
            for partition_index, partition_columns in enumerate(physical_partitions):
                selected_columns = partition_columns
                if self.physical_partition_layout == 'mixed':
                    if _mixed_empty_partition(partition_index):
                        missing_columns.extend(partition_columns)
                        selected_columns = ()
                        empty_partition_count_total += 1
                    elif _mixed_missing_column_partition(partition_index) and partition_columns:
                        missing_columns.append(partition_columns[0])
                        selected_columns = partition_columns[1:]
                partition_frames.append(
                    self._build_partition_frame(
                        partition=view_plan.partition,
                        column_names=selected_columns,
                        row_count=row_count,
                        generation_index=generation_index,
                    )
                )
            partition_column_counts.extend(len(item.columns) for item in partition_frames)
            if len(partition_frames) == 1:
                frame = partition_frames[0]
            else:
                merge_started = time.perf_counter()
                frame = pd.concat(partition_frames, axis=1)
                merge_duration_ms += (time.perf_counter() - merge_started) * 1000
            if missing_columns:
                frame = frame.reindex(columns=view_plan.column_names)
                all_missing_columns.extend(missing_columns)
            if view_plan.partition is DataPartition.DAILY:
                frame.insert(
                    0,
                    'timestamp_utc',
                    _historical_timestamps(
                        as_of=as_of,
                        point_count=row_count,
                        step_seconds=self.historical_step_seconds,
                    ),
                )
            loaded[view_plan.view] = LoadedDataSourceView(
                view=view_plan.view,
                frame=frame,
            )
        duration_ms = (time.perf_counter() - started) * 1000
        self.load_durations_ms.append(duration_ms)
        self.merge_durations_ms.append(merge_duration_ms)
        if self.load_count == 1:
            self.view_count = len(loaded)
            self.column_count = sum(len(item.column_names) for item in plan.views)
            self.row_count = sum(len(item.frame.index) for item in loaded.values())
            self.frame_bytes = sum(
                int(item.frame.memory_usage(index=True, deep=True).sum())
                for item in loaded.values()
            )
            self.numeric_value_count = sum(
                len(view_plan.column_names) * len(loaded[view_plan.view].frame.index)
                for view_plan in plan.views
            )
            self.latest_column_count = sum(
                len(view_plan.column_names)
                for view_plan in plan.views
                if view_plan.partition is DataPartition.LATEST
            )
            self.historical_column_count = sum(
                len(view_plan.column_names)
                for view_plan in plan.views
                if view_plan.partition is DataPartition.DAILY
            )
            self.historical_row_count = sum(
                len(loaded[view_plan.view].frame.index)
                for view_plan in plan.views
                if view_plan.partition is DataPartition.DAILY
            )
            self.historical_value_count = sum(
                len(view_plan.column_names) * len(loaded[view_plan.view].frame.index)
                for view_plan in plan.views
                if view_plan.partition is DataPartition.DAILY
            )
            self.physical_partition_column_counts = tuple(partition_column_counts)
            self.empty_physical_partition_count = empty_partition_count_total
            self.missing_source_columns = tuple(dict.fromkeys(all_missing_columns))
            self.missing_source_column_count = len(self.missing_source_columns)
            self.synthesized_null_column_count = len(self.missing_source_columns)
        return LoadedDataSources(
            as_of=as_of,
            plan=plan,
            registry=build_current_source_registry(pi_source=PiSourceProvider.NOTPII),
            loaded=loaded,
            failures={},
        )

    def _build_partition_frame(
        self,
        *,
        partition: DataPartition,
        column_names: tuple[str, ...],
        row_count: int,
        generation_index: int,
    ) -> pd.DataFrame:
        if partition is not DataPartition.LATEST or not self._has_functional_pressure:
            return pd.DataFrame(
                float(self.signal_value),
                index=range(row_count),
                columns=column_names,
            )
        values = {
            column_name: self._functional_signal_value(
                alarm_index=_alarm_index_from_signal_column(column_name),
                generation_index=generation_index,
            )
            for column_name in column_names
        }
        return pd.DataFrame([values], columns=column_names)

    @property
    def _has_functional_pressure(self) -> bool:
        return (
            self.operational_churn_percent > 0
            or self.technical_hold_churn_percent > 0
            or self.technical_hold_expiry_percent > 0
            or self.initial_error_activation_percent > 0
            or self.fixed_initial_error_percent > 0
        )

    def _functional_signal_value(self, *, alarm_index: int, generation_index: int) -> float:
        if self.fixed_initial_error_percent > 0:
            group_index = alarm_index // self.priority_group_size
            active_group_count = (
                self.alarm_count // self.priority_group_size * self.initial_active_percent // 100
            )
            if group_index >= active_group_count:
                return _TECHNICAL_ERROR_SIGNAL_VALUE
            return float(self.signal_value)
        if self.initial_error_activation_percent > 0:
            group_index = alarm_index // self.priority_group_size
            if not self._initial_error_group_is_active(
                group_index=group_index,
                generation_index=generation_index,
            ):
                return _TECHNICAL_ERROR_SIGNAL_VALUE
            return float(self.signal_value)
        if self.technical_hold_churn_percent > 0 or self.technical_hold_expiry_percent > 0:
            group_index = alarm_index // self.priority_group_size
            if self._technical_hold_group_is_error(
                group_index=group_index,
                generation_index=generation_index,
            ):
                return _TECHNICAL_ERROR_SIGNAL_VALUE
            return float(self.signal_value)
        return self._churn_signal_value(
            alarm_index=alarm_index,
            generation_index=generation_index,
        )

    def _churn_signal_value(self, *, alarm_index: int, generation_index: int) -> float:
        group_size = self.priority_group_size
        group_index = alarm_index // group_size
        local_index = alarm_index % group_size
        active_per_group = group_size * self.initial_active_percent // 100
        initially_active = local_index < active_per_group
        toggle_count = self._group_toggle_count(
            group_index=group_index,
            generation_index=generation_index,
        )
        active = initially_active if toggle_count % 2 == 0 else not initially_active
        return float(self.signal_value if active else self.threshold - 1.0)

    def _transition_group_counts(
        self,
        *,
        previous_generation_index: int,
        generation_index: int,
    ) -> tuple[int, int, int, int, int]:
        if not self._has_functional_pressure:
            return 0, 0, 0, 0, 0
        group_count = self.alarm_count // self.priority_group_size
        changed = 0
        started = 0
        cleared = 0
        reappeared = 0
        initially_activated = 0
        if self.fixed_initial_error_percent > 0:
            return 0, 0, 0, 0, 0
        for group_index in range(group_count):
            if self.initial_error_activation_percent > 0:
                previous_active = self._initial_error_group_is_active(
                    group_index=group_index,
                    generation_index=previous_generation_index,
                )
                current_active = self._initial_error_group_is_active(
                    group_index=group_index,
                    generation_index=generation_index,
                )
                if previous_active != current_active:
                    changed += 1
                    if current_active:
                        initially_activated += 1
                continue
            if self.technical_hold_churn_percent > 0 or self.technical_hold_expiry_percent > 0:
                previous_error = self._technical_hold_group_is_error(
                    group_index=group_index,
                    generation_index=previous_generation_index,
                )
                current_error = self._technical_hold_group_is_error(
                    group_index=group_index,
                    generation_index=generation_index,
                )
                if previous_error != current_error:
                    changed += 1
                    if current_error:
                        started += 1
                    elif self.technical_hold_expiry_percent > 0:
                        reappeared += 1
                    else:
                        cleared += 1
                continue
            previous_toggled = (
                self._group_toggle_count(
                    group_index=group_index,
                    generation_index=previous_generation_index,
                )
                % 2
            )
            current_toggled = (
                self._group_toggle_count(
                    group_index=group_index,
                    generation_index=generation_index,
                )
                % 2
            )
            if previous_toggled != current_toggled:
                changed += 1
        return changed, started, cleared, reappeared, initially_activated

    def _initial_error_group_is_active(
        self,
        *,
        group_index: int,
        generation_index: int,
    ) -> bool:
        if self.initial_error_activation_percent == 0:
            return True
        changed_group_count = (
            self.alarm_count
            * self.initial_error_activation_percent
            // 100
            // self.priority_group_size
        )
        cohort_index = group_index // changed_group_count
        hold_generations = self.initial_error_hold_seconds // self.refresh_seconds
        stagger_generations = self.initial_error_activation_stagger_seconds // self.refresh_seconds
        activation_generation = hold_generations + cohort_index * stagger_generations
        return generation_index >= activation_generation

    def _technical_hold_group_is_error(
        self,
        *,
        group_index: int,
        generation_index: int,
    ) -> bool:
        if generation_index <= 0:
            return False
        if self.technical_hold_expiry_percent > 0:
            changed_group_count = (
                self.alarm_count
                * self.technical_hold_expiry_percent
                // 100
                // self.priority_group_size
            )
            cohort_index = group_index // changed_group_count
            stagger_generations = self.technical_hold_expiry_stagger_seconds // self.refresh_seconds
            duration_generations = (
                self.technical_hold_error_duration_seconds // self.refresh_seconds
            )
            start_generation = 1 + cohort_index * stagger_generations
            return start_generation <= generation_index < start_generation + duration_generations
        if self.technical_hold_churn_percent == 0:
            return False
        changed_group_count = (
            self.alarm_count * self.technical_hold_churn_percent // 100 // self.priority_group_size
        )
        cohort_count = self.alarm_count // self.priority_group_size // changed_group_count
        target_cohort = ((generation_index - 1) // 2) % cohort_count
        cohort_index = group_index // changed_group_count
        return generation_index % 2 == 1 and cohort_index == target_cohort

    def _technical_hold_expired_group_count_between(
        self,
        *,
        previous_generation_index: int,
        generation_index: int,
    ) -> int:
        if self.technical_hold_expiry_percent == 0:
            return 0
        changed_group_count = (
            self.alarm_count * self.technical_hold_expiry_percent // 100 // self.priority_group_size
        )
        cohort_count = self.alarm_count // self.priority_group_size // changed_group_count
        stagger_generations = self.technical_hold_expiry_stagger_seconds // self.refresh_seconds
        grace_generations = TECHNICAL_HOLD_GRACE_SECONDS // self.refresh_seconds
        expired_cohorts = sum(
            previous_generation_index
            < 1 + cohort_index * stagger_generations + grace_generations
            <= generation_index
            for cohort_index in range(cohort_count)
        )
        return expired_cohorts * changed_group_count

    def _group_toggle_count(self, *, group_index: int, generation_index: int) -> int:
        if self.operational_churn_percent == 0:
            return 0
        group_count = self.alarm_count // self.priority_group_size
        changed_group_count = (
            self.alarm_count * self.operational_churn_percent // 100 // self.priority_group_size
        )
        cohort_count = group_count // changed_group_count
        cohort_index = group_index // changed_group_count
        if generation_index <= cohort_index:
            return 0
        return 1 + (generation_index - 1 - cohort_index) // cohort_count

    def _row_count_for(self, partition: DataPartition) -> int:
        if partition is DataPartition.DAILY:
            if self.historical_points_per_series <= 0:
                raise ValueError('daily source view requires historical_points_per_series > 0')
            return self.historical_points_per_series
        return 1


class EmptyInputSource:
    def read_after(
        self,
        *,
        stream: AlarmInputStream,
        cursor: AlarmInputCursor | None,
    ) -> tuple[AlarmInputRecord, ...]:
        return ()

    def read_at(
        self,
        *,
        stream: AlarmInputStream,
        locator: AlarmInputLocator,
    ) -> AlarmInputRecord:
        raise RuntimeError('no pending input exists in the baseline scenario')


@dataclass(slots=True)
class SingleManagementInputSource:
    composition: AlarmRuntimeComposition
    visible_after_seconds: int
    target_identity: AlarmIdentity
    target_priority_group: str
    input_id: str = 'PERF-M-000001'
    actor_key: str = 'perf-operator'
    tool_key: str = 'perf-tool'
    started_monotonic: float = field(default_factory=time.perf_counter)
    source_created_at: datetime = field(init=False)
    exposed_monotonic: float | None = field(default=None, init=False)
    target_occurrence_id: str | None = field(default=None, init=False)
    _record: AlarmInputRecord | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.composition, AlarmRuntimeComposition):
            raise TypeError('composition must be AlarmRuntimeComposition')
        if isinstance(self.visible_after_seconds, bool) or not isinstance(
            self.visible_after_seconds, int
        ):
            raise TypeError('visible_after_seconds must be an int')
        if self.visible_after_seconds < 0:
            raise ValueError('visible_after_seconds must not be negative')
        if not isinstance(self.target_identity, AlarmIdentity):
            raise TypeError('target_identity must be AlarmIdentity')
        if (
            not isinstance(self.target_priority_group, str)
            or not self.target_priority_group.strip()
        ):
            raise ValueError('target_priority_group must be a non-empty string')
        self.target_priority_group = self.target_priority_group.strip()
        self.source_created_at = datetime.now(UTC).replace(microsecond=0) + timedelta(
            seconds=self.visible_after_seconds
        )

    def read_after(
        self,
        *,
        stream: AlarmInputStream,
        cursor: AlarmInputCursor | None,
    ) -> tuple[AlarmInputRecord, ...]:
        if stream is not AlarmInputStream.MANAGEMENT or cursor is not None:
            return ()
        if time.perf_counter() - self.started_monotonic < self.visible_after_seconds:
            return ()
        if self._record is None:
            snapshot = self.composition.durability.persistence.read_snapshot(
                self.target_priority_group
            )
            if snapshot is None:
                return ()
            alarm = snapshot.as_document()['alarms'].get(self.target_identity.canonical_key)
            if not isinstance(alarm, dict) or not isinstance(alarm.get('occurrence'), dict):
                return ()
            occurrence_id = alarm['occurrence'].get('occurrence_id')
            if not isinstance(occurrence_id, str) or not occurrence_id:
                return ()
            self.target_occurrence_id = occurrence_id
            hour_bucket = self.source_created_at.strftime('%Y-%m-%dT%HZ')
            self._record = AlarmInputRecord(
                locator=AlarmInputLocator(
                    input_id=self.input_id,
                    hour_bucket=hour_bucket,
                    byte_offset=0,
                    byte_length=256,
                ),
                next_cursor=AlarmInputCursor(hour_bucket=hour_bucket, byte_offset=256),
                value=ManagementAction(
                    input_id=self.input_id,
                    alarm_identity=self.target_identity,
                    source_occurrence_id=occurrence_id,
                    tool_key=self.tool_key,
                    actor_key=self.actor_key,
                    source_created_at=self.source_created_at,
                    context={'scenario': 'performance-single-management'},
                ),
            )
        if self.exposed_monotonic is None:
            self.exposed_monotonic = time.perf_counter()
        return (self._record,)

    def read_at(
        self,
        *,
        stream: AlarmInputStream,
        locator: AlarmInputLocator,
    ) -> AlarmInputRecord:
        if (
            stream is AlarmInputStream.MANAGEMENT
            and self._record is not None
            and self._record.locator == locator
        ):
            return self._record
        raise LookupError(locator.input_id)


@dataclass(slots=True)
class SingleDeactivationDecisionInputSource:
    composition: AlarmRuntimeComposition
    request_visible_after_seconds: int
    decision_visible_after_seconds: int
    deactivation_window_seconds: int
    target_identity: AlarmIdentity
    target_priority_group: str
    management_input_id: str = 'PERF-M-000001'
    request_id: str = 'PERF-DR-PERF-M-000001'
    decision_id: str = 'PERF-D-000001'
    management_actor_key: str = 'perf-operator'
    decision_actor_key: str = 'perf-approver'
    tool_key: str = 'perf-tool'
    byte_length: int = 256
    started_monotonic: float = field(default_factory=time.perf_counter)
    request_created_at: datetime = field(init=False)
    decision_decided_at: datetime = field(init=False)
    effective_until: datetime = field(init=False)
    management_exposed_monotonic: float | None = field(default=None, init=False)
    decision_exposed_monotonic: float | None = field(default=None, init=False)
    target_occurrence_id: str | None = field(default=None, init=False)
    target_visible_while_pending: bool = field(default=False, init=False)
    _management_record: AlarmInputRecord | None = field(default=None, init=False, repr=False)
    _decision_record: AlarmInputRecord | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.composition, AlarmRuntimeComposition):
            raise TypeError('composition must be AlarmRuntimeComposition')
        for name in (
            'request_visible_after_seconds',
            'decision_visible_after_seconds',
            'deactivation_window_seconds',
            'byte_length',
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f'{name} must be an int')
        if self.request_visible_after_seconds < 0:
            raise ValueError('request_visible_after_seconds must not be negative')
        if self.decision_visible_after_seconds <= self.request_visible_after_seconds:
            raise ValueError('decision must become visible after the request setup')
        if self.deactivation_window_seconds <= (
            self.decision_visible_after_seconds - self.request_visible_after_seconds
        ):
            raise ValueError('deactivation window must remain open at decision time')
        if self.byte_length <= 0:
            raise ValueError('byte_length must be greater than zero')
        if not isinstance(self.target_identity, AlarmIdentity):
            raise TypeError('target_identity must be AlarmIdentity')
        if (
            not isinstance(self.target_priority_group, str)
            or not self.target_priority_group.strip()
        ):
            raise ValueError('target_priority_group must be a non-empty string')
        self.target_priority_group = self.target_priority_group.strip()
        base = datetime.now(UTC).replace(microsecond=0)
        self.request_created_at = base + timedelta(seconds=self.request_visible_after_seconds)
        self.decision_decided_at = base + timedelta(seconds=self.decision_visible_after_seconds)
        self.effective_until = self.request_created_at + timedelta(
            seconds=self.deactivation_window_seconds
        )

    @property
    def management_cursor(self) -> AlarmInputCursor:
        return AlarmInputCursor(
            hour_bucket=self.request_created_at.strftime('%Y-%m-%dT%HZ'),
            byte_offset=self.byte_length,
        )

    @property
    def decision_cursor(self) -> AlarmInputCursor:
        return AlarmInputCursor(
            hour_bucket=self.decision_decided_at.strftime('%Y-%m-%dT%HZ'),
            byte_offset=self.byte_length,
        )

    def read_after(
        self,
        *,
        stream: AlarmInputStream,
        cursor: AlarmInputCursor | None,
    ) -> tuple[AlarmInputRecord, ...]:
        elapsed = time.perf_counter() - self.started_monotonic
        if stream is AlarmInputStream.MANAGEMENT:
            if cursor is not None or elapsed < self.request_visible_after_seconds:
                return ()
            record = self._management_input_record()
            if record is None:
                return ()
            if self.management_exposed_monotonic is None:
                self.management_exposed_monotonic = time.perf_counter()
            return (record,)
        if stream is AlarmInputStream.DEACTIVATION_DECISION:
            if cursor is not None or elapsed < self.decision_visible_after_seconds:
                return ()
            if not self._request_is_durable():
                return ()
            self.target_visible_while_pending = self._target_is_visible_while_pending()
            record = self._deactivation_decision_record()
            if self.decision_exposed_monotonic is None:
                self.decision_exposed_monotonic = time.perf_counter()
            return (record,)
        return ()

    def read_at(
        self,
        *,
        stream: AlarmInputStream,
        locator: AlarmInputLocator,
    ) -> AlarmInputRecord:
        if (
            stream is AlarmInputStream.MANAGEMENT
            and self._management_record is not None
            and self._management_record.locator == locator
        ):
            return self._management_record
        if (
            stream is AlarmInputStream.DEACTIVATION_DECISION
            and self._decision_record is not None
            and self._decision_record.locator == locator
        ):
            return self._decision_record
        raise LookupError(locator.input_id)

    def _management_input_record(self) -> AlarmInputRecord | None:
        if self._management_record is not None:
            return self._management_record
        snapshot = self.composition.durability.persistence.read_snapshot(self.target_priority_group)
        if snapshot is None:
            return None
        alarm = snapshot.as_document()['alarms'].get(self.target_identity.canonical_key)
        if not isinstance(alarm, dict) or not isinstance(alarm.get('occurrence'), dict):
            return None
        occurrence_id = alarm['occurrence'].get('occurrence_id')
        if not isinstance(occurrence_id, str) or not occurrence_id:
            return None
        self.target_occurrence_id = occurrence_id
        hour_bucket = self.request_created_at.strftime('%Y-%m-%dT%HZ')
        self._management_record = AlarmInputRecord(
            locator=AlarmInputLocator(
                input_id=self.management_input_id,
                hour_bucket=hour_bucket,
                byte_offset=0,
                byte_length=self.byte_length,
            ),
            next_cursor=self.management_cursor,
            value=ManagementAction(
                input_id=self.management_input_id,
                alarm_identity=self.target_identity,
                source_occurrence_id=occurrence_id,
                tool_key=self.tool_key,
                actor_key=self.management_actor_key,
                source_created_at=self.request_created_at,
                context={'scenario': 'performance-single-deactivation-decision'},
                deactivation_intent=DeactivationIntent(effective_until=self.effective_until),
            ),
        )
        return self._management_record

    def _deactivation_decision_record(self) -> AlarmInputRecord:
        if self._decision_record is not None:
            return self._decision_record
        hour_bucket = self.decision_decided_at.strftime('%Y-%m-%dT%HZ')
        self._decision_record = AlarmInputRecord(
            locator=AlarmInputLocator(
                input_id=self.decision_id,
                hour_bucket=hour_bucket,
                byte_offset=0,
                byte_length=self.byte_length,
            ),
            next_cursor=self.decision_cursor,
            value=DeactivationDecision(
                decision_id=self.decision_id,
                request_id=self.request_id,
                kind=DeactivationDecisionKind.APPROVED,
                decided_at=self.decision_decided_at,
                actor_key=self.decision_actor_key,
            ),
        )
        return self._decision_record

    def _target_is_visible_while_pending(self) -> bool:
        snapshot = self.composition.durability.persistence.read_snapshot(self.target_priority_group)
        if snapshot is None:
            return False
        alarm = snapshot.as_document()['alarms'].get(self.target_identity.canonical_key)
        if not isinstance(alarm, dict):
            return False
        occurrence = alarm.get('occurrence')
        return bool(
            isinstance(occurrence, dict)
            and occurrence.get('occurrence_id') == self.target_occurrence_id
            and alarm.get('deactivation_effect') is None
        )

    def _request_is_durable(self) -> bool:
        return any(
            request.get('request_id') == self.request_id
            for entry in self.composition.durability.persistence.read_durable_records()
            for request in entry.record.records.get('deactivation_requests', [])
        )


@dataclass(slots=True)
class SustainedDeactivationRequestInputSource:
    composition: AlarmRuntimeComposition
    visible_after_seconds: int
    request_count: int
    interval_seconds: int
    deactivation_window_seconds: int
    alarm_count: int
    priority_group_size: int
    actor_key: str = 'perf-operator'
    tool_key: str = 'perf-tool'
    byte_length: int = 256
    started_monotonic: float = field(default_factory=time.perf_counter)
    first_source_created_at: datetime = field(init=False)
    hour_bucket: str = field(init=False)
    _records: dict[int, AlarmInputRecord] = field(default_factory=dict, init=False, repr=False)
    target_occurrence_ids: dict[str, str] = field(default_factory=dict, init=False)
    visible_monotonic_by_input_id: dict[str, float] = field(default_factory=dict, init=False)
    read_batch_sizes: list[int] = field(default_factory=list, init=False)
    iteration_as_of: datetime | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.composition, AlarmRuntimeComposition):
            raise TypeError('composition must be AlarmRuntimeComposition')
        for name in (
            'visible_after_seconds',
            'request_count',
            'interval_seconds',
            'deactivation_window_seconds',
            'alarm_count',
            'priority_group_size',
            'byte_length',
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f'{name} must be an int')
        if self.visible_after_seconds < 0:
            raise ValueError('visible_after_seconds must not be negative')
        if self.request_count <= 1:
            raise ValueError('request_count must be greater than one')
        if self.interval_seconds < 0:
            raise ValueError('interval_seconds must not be negative')
        if self.deactivation_window_seconds <= 0:
            raise ValueError('deactivation_window_seconds must be greater than zero')
        if self.priority_group_size <= 0 or self.alarm_count % self.priority_group_size != 0:
            raise ValueError('priority groups must be complete')
        if self.request_count > self.alarm_count:
            raise ValueError('request_count must not exceed alarm_count')
        if self.byte_length <= 0:
            raise ValueError('byte_length must be greater than zero')
        self.first_source_created_at = datetime.now(UTC).replace(microsecond=0) + timedelta(
            seconds=self.visible_after_seconds
        )
        self.hour_bucket = self.first_source_created_at.strftime('%Y-%m-%dT%HZ')

    @property
    def priority_group_count(self) -> int:
        return self.alarm_count // self.priority_group_size

    @property
    def input_ids(self) -> tuple[str, ...]:
        return tuple(f'PERF-M-{index + 1:06d}' for index in range(self.request_count))

    @property
    def request_ids(self) -> tuple[str, ...]:
        return tuple(f'PERF-DR-{input_id}' for input_id in self.input_ids)

    @property
    def expected_final_cursor(self) -> AlarmInputCursor:
        return AlarmInputCursor(
            hour_bucket=self.hour_bucket,
            byte_offset=self.request_count * self.byte_length,
        )

    @property
    def nonempty_batch_sizes(self) -> tuple[int, ...]:
        return tuple(size for size in self.read_batch_sizes if size > 0)

    def requested_at_for(self, request_index: int) -> datetime:
        if not 0 <= request_index < self.request_count:
            raise IndexError(request_index)
        return self.first_source_created_at + timedelta(
            seconds=request_index * self.interval_seconds
        )

    def effective_until_for(self, request_index: int) -> datetime:
        return self.requested_at_for(request_index) + timedelta(
            seconds=self.deactivation_window_seconds
        )

    def target_for_request(self, request_index: int) -> tuple[AlarmIdentity, str]:
        if not 0 <= request_index < self.request_count:
            raise IndexError(request_index)
        group_index = request_index % self.priority_group_count
        slot_index = request_index // self.priority_group_count
        alarm_index = group_index * self.priority_group_size + slot_index
        identity = AlarmIdentity(
            family_key=_FAMILY_KEY,
            alarm_key=f'alarm_{alarm_index + 1:05d}',
        )
        return identity, f'{_PRIORITY_GROUP_PREFIX}-{group_index + 1:03d}'

    def prepare_iteration(self, *, as_of: datetime) -> None:
        if not isinstance(as_of, datetime):
            raise TypeError('as_of must be a datetime')
        if as_of.tzinfo is None or as_of.utcoffset() != timedelta(0):
            raise ValueError('as_of must be UTC-aware')
        self.iteration_as_of = as_of

    def read_after(
        self,
        *,
        stream: AlarmInputStream,
        cursor: AlarmInputCursor | None,
    ) -> tuple[AlarmInputRecord, ...]:
        if stream is not AlarmInputStream.MANAGEMENT:
            return ()
        iteration_as_of = self.iteration_as_of
        if iteration_as_of is None:
            raise RuntimeError('deactivation request source requires iteration as_of')
        elapsed = time.perf_counter() - self.started_monotonic
        if elapsed < self.visible_after_seconds:
            self.read_batch_sizes.append(0)
            return ()
        elapsed_visible_count = (
            self.request_count
            if self.interval_seconds == 0
            else min(
                self.request_count,
                int((elapsed - self.visible_after_seconds) // self.interval_seconds) + 1,
            )
        )
        logical_elapsed = (iteration_as_of - self.first_source_created_at).total_seconds()
        logical_visible_count = (
            0
            if logical_elapsed < 0
            else (
                self.request_count
                if self.interval_seconds == 0
                else min(
                    self.request_count,
                    int(logical_elapsed // self.interval_seconds) + 1,
                )
            )
        )
        visible_count = min(elapsed_visible_count, logical_visible_count)
        cursor_offset = 0 if cursor is None else cursor.byte_offset
        first_index = cursor_offset // self.byte_length
        records = tuple(self._record_for(index) for index in range(first_index, visible_count))
        self.read_batch_sizes.append(len(records))
        return records

    def read_at(
        self,
        *,
        stream: AlarmInputStream,
        locator: AlarmInputLocator,
    ) -> AlarmInputRecord:
        if stream is AlarmInputStream.MANAGEMENT:
            index = locator.byte_offset // self.byte_length
            record = self._records.get(index)
            if record is not None and record.locator == locator:
                return record
        raise LookupError(locator.input_id)

    def _record_for(self, request_index: int) -> AlarmInputRecord:
        existing = self._records.get(request_index)
        if existing is not None:
            return existing
        identity, priority_group = self.target_for_request(request_index)
        snapshot = self.composition.durability.persistence.read_snapshot(priority_group)
        if snapshot is None:
            raise RuntimeError(
                f'deactivation request target snapshot is not durable: {priority_group}'
            )
        alarm = snapshot.as_document()['alarms'].get(identity.canonical_key)
        if not isinstance(alarm, dict) or not isinstance(alarm.get('occurrence'), dict):
            raise RuntimeError(
                f'deactivation request target occurrence is not durable: {identity.canonical_key}'
            )
        occurrence_id = alarm['occurrence'].get('occurrence_id')
        if not isinstance(occurrence_id, str) or not occurrence_id:
            raise RuntimeError(
                f'deactivation request target occurrence id is invalid: {identity.canonical_key}'
            )
        input_id = self.input_ids[request_index]
        source_created_at = self.requested_at_for(request_index)
        byte_offset = request_index * self.byte_length
        record = AlarmInputRecord(
            locator=AlarmInputLocator(
                input_id=input_id,
                hour_bucket=self.hour_bucket,
                byte_offset=byte_offset,
                byte_length=self.byte_length,
            ),
            next_cursor=AlarmInputCursor(
                hour_bucket=self.hour_bucket,
                byte_offset=byte_offset + self.byte_length,
            ),
            value=ManagementAction(
                input_id=input_id,
                alarm_identity=identity,
                source_occurrence_id=occurrence_id,
                tool_key=self.tool_key,
                actor_key=self.actor_key,
                source_created_at=source_created_at,
                context={
                    'scenario': 'performance-sustained-deactivation-request',
                    'sequence': str(request_index + 1),
                },
                deactivation_intent=DeactivationIntent(
                    effective_until=self.effective_until_for(request_index)
                ),
            ),
        )
        self._records[request_index] = record
        self.target_occurrence_ids[identity.canonical_key] = occurrence_id
        self.visible_monotonic_by_input_id[input_id] = (
            self.started_monotonic
            + self.visible_after_seconds
            + request_index * self.interval_seconds
        )
        return record


@dataclass(slots=True)
class SustainedDeactivationDecisionInputSource:
    composition: AlarmRuntimeComposition
    visible_after_seconds: int
    decision_count: int
    interval_seconds: int
    byte_length: int = 256
    actor_key: str = 'perf-approver'
    started_monotonic: float = field(default_factory=time.perf_counter)
    first_decided_at: datetime = field(init=False)
    hour_bucket: str = field(init=False)
    _records: dict[int, AlarmInputRecord] = field(default_factory=dict, init=False, repr=False)
    visible_monotonic_by_input_id: dict[str, float] = field(default_factory=dict, init=False)
    read_batch_sizes: list[int] = field(default_factory=list, init=False)
    iteration_as_of: datetime | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.composition, AlarmRuntimeComposition):
            raise TypeError('composition must be AlarmRuntimeComposition')
        for name in ('visible_after_seconds', 'decision_count', 'interval_seconds', 'byte_length'):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f'{name} must be an int')
        if self.visible_after_seconds < 0:
            raise ValueError('visible_after_seconds must not be negative')
        if self.decision_count <= 1:
            raise ValueError('decision_count must be greater than one')
        if self.interval_seconds < 0:
            raise ValueError('interval_seconds must not be negative')
        if self.byte_length <= 0:
            raise ValueError('byte_length must be greater than zero')
        durable_requests = {
            request.get('request_id'): request
            for entry in self.composition.durability.persistence.read_durable_records()
            for request in entry.record.records.get('deactivation_requests', [])
        }
        expected_request_ids = set(self.request_ids)
        durable_pending_request_ids = {
            request_id
            for request_id, request in durable_requests.items()
            if request_id in expected_request_ids and request.get('approval_required') is True
        }
        store = AtomicJsonStore(root_path=self.composition.durability.persistence.paths.alarms_root)
        state = store.read('runtime/state/consumers/management.json') or {}
        state_pending_request_ids = state.get('pending_deactivation_request_ids', [])
        if not isinstance(state_pending_request_ids, list):
            state_pending_request_ids = []
        if (
            durable_pending_request_ids != expected_request_ids
            or set(state_pending_request_ids) != expected_request_ids
        ):
            raise RuntimeError(
                'deactivation decision phase requires all durable requests to be pending'
            )
        self.first_decided_at = datetime.now(UTC).replace(microsecond=0) + timedelta(
            seconds=self.visible_after_seconds
        )
        self.hour_bucket = self.first_decided_at.strftime('%Y-%m-%dT%HZ')

    @property
    def decision_ids(self) -> tuple[str, ...]:
        return tuple(f'PERF-D-{index + 1:06d}' for index in range(self.decision_count))

    @property
    def request_ids(self) -> tuple[str, ...]:
        return tuple(f'PERF-DR-PERF-M-{index + 1:06d}' for index in range(self.decision_count))

    @property
    def expected_final_cursor(self) -> AlarmInputCursor:
        return AlarmInputCursor(
            hour_bucket=self.hour_bucket,
            byte_offset=self.decision_count * self.byte_length,
        )

    @property
    def nonempty_batch_sizes(self) -> tuple[int, ...]:
        return tuple(size for size in self.read_batch_sizes if size > 0)

    def decided_at_for(self, decision_index: int) -> datetime:
        if not 0 <= decision_index < self.decision_count:
            raise IndexError(decision_index)
        return self.first_decided_at + timedelta(seconds=decision_index * self.interval_seconds)

    def prepare_iteration(self, *, as_of: datetime) -> None:
        if not isinstance(as_of, datetime):
            raise TypeError('as_of must be a datetime')
        if as_of.tzinfo is None or as_of.utcoffset() != timedelta(0):
            raise ValueError('as_of must be UTC-aware')
        self.iteration_as_of = as_of

    def read_after(
        self,
        *,
        stream: AlarmInputStream,
        cursor: AlarmInputCursor | None,
    ) -> tuple[AlarmInputRecord, ...]:
        if stream is not AlarmInputStream.DEACTIVATION_DECISION:
            return ()
        iteration_as_of = self.iteration_as_of
        if iteration_as_of is None:
            raise RuntimeError('deactivation decision source requires iteration as_of')
        elapsed = time.perf_counter() - self.started_monotonic
        if elapsed < self.visible_after_seconds:
            self.read_batch_sizes.append(0)
            return ()
        elapsed_visible_count = (
            self.decision_count
            if self.interval_seconds == 0
            else min(
                self.decision_count,
                int((elapsed - self.visible_after_seconds) // self.interval_seconds) + 1,
            )
        )
        logical_elapsed = (iteration_as_of - self.first_decided_at).total_seconds()
        logical_visible_count = (
            0
            if logical_elapsed < 0
            else (
                self.decision_count
                if self.interval_seconds == 0
                else min(
                    self.decision_count,
                    int(logical_elapsed // self.interval_seconds) + 1,
                )
            )
        )
        visible_count = min(elapsed_visible_count, logical_visible_count)
        cursor_offset = 0 if cursor is None else cursor.byte_offset
        first_index = cursor_offset // self.byte_length
        records = tuple(self._record_for(index) for index in range(first_index, visible_count))
        self.read_batch_sizes.append(len(records))
        return records

    def read_at(
        self,
        *,
        stream: AlarmInputStream,
        locator: AlarmInputLocator,
    ) -> AlarmInputRecord:
        if stream is AlarmInputStream.DEACTIVATION_DECISION:
            index = locator.byte_offset // self.byte_length
            record = self._records.get(index)
            if record is not None and record.locator == locator:
                return record
        raise LookupError(locator.input_id)

    def _record_for(self, decision_index: int) -> AlarmInputRecord:
        existing = self._records.get(decision_index)
        if existing is not None:
            return existing
        decision_id = self.decision_ids[decision_index]
        decided_at = self.decided_at_for(decision_index)
        byte_offset = decision_index * self.byte_length
        record = AlarmInputRecord(
            locator=AlarmInputLocator(
                input_id=decision_id,
                hour_bucket=self.hour_bucket,
                byte_offset=byte_offset,
                byte_length=self.byte_length,
            ),
            next_cursor=AlarmInputCursor(
                hour_bucket=self.hour_bucket,
                byte_offset=byte_offset + self.byte_length,
            ),
            value=DeactivationDecision(
                decision_id=decision_id,
                request_id=self.request_ids[decision_index],
                kind=DeactivationDecisionKind.APPROVED,
                decided_at=decided_at,
                actor_key=self.actor_key,
            ),
        )
        self._records[decision_index] = record
        self.visible_monotonic_by_input_id[decision_id] = (
            self.started_monotonic
            + self.visible_after_seconds
            + decision_index * self.interval_seconds
        )
        return record


@dataclass(slots=True)
class InvertedDeliveryDeactivationInputSource:
    composition: AlarmRuntimeComposition
    request_logical_at_seconds: int
    request_delivery_after_seconds: int
    decision_visible_after_seconds: int
    input_count: int
    deactivation_window_seconds: int
    alarm_count: int
    priority_group_size: int
    management_actor_key: str = 'perf-operator'
    decision_actor_key: str = 'perf-approver'
    tool_key: str = 'perf-tool'
    byte_length: int = 256
    started_monotonic: float = field(default_factory=time.perf_counter)
    base_at: datetime = field(init=False)
    request_created_at: datetime = field(init=False)
    decision_decided_at: datetime = field(init=False)
    management_hour_bucket: str = field(init=False)
    decision_hour_bucket: str = field(init=False)
    _management_records: dict[int, AlarmInputRecord] = field(
        default_factory=dict, init=False, repr=False
    )
    _decision_records: dict[int, AlarmInputRecord] = field(
        default_factory=dict, init=False, repr=False
    )
    target_occurrence_ids: dict[str, str] = field(default_factory=dict, init=False)
    management_visible_monotonic_by_input_id: dict[str, float] = field(
        default_factory=dict, init=False
    )
    decision_visible_monotonic_by_input_id: dict[str, float] = field(
        default_factory=dict, init=False
    )
    management_read_batch_sizes: list[int] = field(default_factory=list, init=False)
    decision_read_batch_sizes: list[int] = field(default_factory=list, init=False)
    decision_read_at_count: int = field(default=0, init=False)
    iteration_as_of: datetime | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.composition, AlarmRuntimeComposition):
            raise TypeError('composition must be AlarmRuntimeComposition')
        for name in (
            'request_logical_at_seconds',
            'request_delivery_after_seconds',
            'decision_visible_after_seconds',
            'input_count',
            'deactivation_window_seconds',
            'alarm_count',
            'priority_group_size',
            'byte_length',
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f'{name} must be an int')
        if self.request_logical_at_seconds < 0:
            raise ValueError('request_logical_at_seconds must not be negative')
        if self.decision_visible_after_seconds <= self.request_logical_at_seconds:
            raise ValueError('decision logical time must be after request logical time')
        if self.request_delivery_after_seconds <= self.decision_visible_after_seconds:
            raise ValueError('request delivery must occur after decision delivery')
        if self.input_count <= 1:
            raise ValueError('input_count must be greater than one')
        if self.deactivation_window_seconds <= (
            self.decision_visible_after_seconds - self.request_logical_at_seconds
        ):
            raise ValueError('deactivation window must remain open at decision time')
        if self.priority_group_size <= 0 or self.alarm_count % self.priority_group_size != 0:
            raise ValueError('priority groups must be complete')
        if self.input_count > self.alarm_count:
            raise ValueError('input_count must not exceed alarm_count')
        if self.byte_length <= 0:
            raise ValueError('byte_length must be greater than zero')
        self.base_at = datetime.now(UTC).replace(microsecond=0)
        self.request_created_at = self.base_at + timedelta(seconds=self.request_logical_at_seconds)
        self.decision_decided_at = self.base_at + timedelta(
            seconds=self.decision_visible_after_seconds
        )
        self.management_hour_bucket = self.request_created_at.strftime('%Y-%m-%dT%HZ')
        self.decision_hour_bucket = self.decision_decided_at.strftime('%Y-%m-%dT%HZ')

    @property
    def priority_group_count(self) -> int:
        return self.alarm_count // self.priority_group_size

    @property
    def input_ids(self) -> tuple[str, ...]:
        return tuple(f'PERF-M-{index + 1:06d}' for index in range(self.input_count))

    @property
    def request_ids(self) -> tuple[str, ...]:
        return tuple(f'PERF-DR-{input_id}' for input_id in self.input_ids)

    @property
    def decision_ids(self) -> tuple[str, ...]:
        return tuple(f'PERF-D-{index + 1:06d}' for index in range(self.input_count))

    @property
    def expected_management_cursor(self) -> AlarmInputCursor:
        return AlarmInputCursor(
            hour_bucket=self.management_hour_bucket,
            byte_offset=self.input_count * self.byte_length,
        )

    @property
    def expected_decision_cursor(self) -> AlarmInputCursor:
        return AlarmInputCursor(
            hour_bucket=self.decision_hour_bucket,
            byte_offset=self.input_count * self.byte_length,
        )

    @property
    def effective_until(self) -> datetime:
        return self.request_created_at + timedelta(seconds=self.deactivation_window_seconds)

    def target_for_input(self, input_index: int) -> tuple[AlarmIdentity, str]:
        if not 0 <= input_index < self.input_count:
            raise IndexError(input_index)
        group_index = input_index % self.priority_group_count
        slot_index = input_index // self.priority_group_count
        alarm_index = group_index * self.priority_group_size + slot_index
        identity = AlarmIdentity(
            family_key=_FAMILY_KEY,
            alarm_key=f'alarm_{alarm_index + 1:05d}',
        )
        return identity, f'{_PRIORITY_GROUP_PREFIX}-{group_index + 1:03d}'

    def prepare_iteration(self, *, as_of: datetime) -> None:
        if not isinstance(as_of, datetime):
            raise TypeError('as_of must be a datetime')
        if as_of.tzinfo is None or as_of.utcoffset() != timedelta(0):
            raise ValueError('as_of must be UTC-aware')
        self.iteration_as_of = as_of

    def read_after(
        self,
        *,
        stream: AlarmInputStream,
        cursor: AlarmInputCursor | None,
    ) -> tuple[AlarmInputRecord, ...]:
        iteration_as_of = self.iteration_as_of
        if iteration_as_of is None:
            raise RuntimeError('inverted deactivation source requires iteration as_of')
        elapsed = time.perf_counter() - self.started_monotonic
        if stream is AlarmInputStream.MANAGEMENT:
            if (
                elapsed < self.request_delivery_after_seconds
                or iteration_as_of < self.request_created_at
            ):
                self.management_read_batch_sizes.append(0)
                return ()
            cursor_offset = 0 if cursor is None else cursor.byte_offset
            first_index = cursor_offset // self.byte_length
            records = tuple(
                self._management_record_for(index) for index in range(first_index, self.input_count)
            )
            self.management_read_batch_sizes.append(len(records))
            return records
        if stream is AlarmInputStream.DEACTIVATION_DECISION:
            if (
                elapsed < self.decision_visible_after_seconds
                or iteration_as_of < self.decision_decided_at
            ):
                self.decision_read_batch_sizes.append(0)
                return ()
            cursor_offset = 0 if cursor is None else cursor.byte_offset
            first_index = cursor_offset // self.byte_length
            records = tuple(
                self._decision_record_for(index) for index in range(first_index, self.input_count)
            )
            self.decision_read_batch_sizes.append(len(records))
            return records
        return ()

    def read_at(
        self,
        *,
        stream: AlarmInputStream,
        locator: AlarmInputLocator,
    ) -> AlarmInputRecord:
        index = locator.byte_offset // self.byte_length
        if stream is AlarmInputStream.MANAGEMENT:
            record = self._management_records.get(index)
            if record is not None and record.locator == locator:
                return record
        if stream is AlarmInputStream.DEACTIVATION_DECISION:
            record = self._decision_records.get(index)
            if record is not None and record.locator == locator:
                self.decision_read_at_count += 1
                return record
        raise LookupError(locator.input_id)

    def _management_record_for(self, input_index: int) -> AlarmInputRecord:
        existing = self._management_records.get(input_index)
        if existing is not None:
            return existing
        identity, priority_group = self.target_for_input(input_index)
        snapshot = self.composition.durability.persistence.read_snapshot(priority_group)
        if snapshot is None:
            raise RuntimeError(
                f'inverted deactivation target snapshot is not durable: {priority_group}'
            )
        alarm = snapshot.as_document()['alarms'].get(identity.canonical_key)
        if not isinstance(alarm, dict) or not isinstance(alarm.get('occurrence'), dict):
            raise RuntimeError(
                f'inverted deactivation target occurrence is not durable: {identity.canonical_key}'
            )
        occurrence_id = alarm['occurrence'].get('occurrence_id')
        if not isinstance(occurrence_id, str) or not occurrence_id:
            raise RuntimeError(
                f'inverted deactivation target occurrence id is invalid: {identity.canonical_key}'
            )
        input_id = self.input_ids[input_index]
        byte_offset = input_index * self.byte_length
        record = AlarmInputRecord(
            locator=AlarmInputLocator(
                input_id=input_id,
                hour_bucket=self.management_hour_bucket,
                byte_offset=byte_offset,
                byte_length=self.byte_length,
            ),
            next_cursor=AlarmInputCursor(
                hour_bucket=self.management_hour_bucket,
                byte_offset=byte_offset + self.byte_length,
            ),
            value=ManagementAction(
                input_id=input_id,
                alarm_identity=identity,
                source_occurrence_id=occurrence_id,
                tool_key=self.tool_key,
                actor_key=self.management_actor_key,
                source_created_at=self.request_created_at,
                context={
                    'scenario': 'performance-inverted-deactivation-delivery',
                    'sequence': str(input_index + 1),
                },
                deactivation_intent=DeactivationIntent(effective_until=self.effective_until),
            ),
        )
        self._management_records[input_index] = record
        self.target_occurrence_ids[identity.canonical_key] = occurrence_id
        self.management_visible_monotonic_by_input_id[input_id] = (
            self.started_monotonic + self.request_delivery_after_seconds
        )
        return record

    def _decision_record_for(self, input_index: int) -> AlarmInputRecord:
        existing = self._decision_records.get(input_index)
        if existing is not None:
            return existing
        decision_id = self.decision_ids[input_index]
        byte_offset = input_index * self.byte_length
        record = AlarmInputRecord(
            locator=AlarmInputLocator(
                input_id=decision_id,
                hour_bucket=self.decision_hour_bucket,
                byte_offset=byte_offset,
                byte_length=self.byte_length,
            ),
            next_cursor=AlarmInputCursor(
                hour_bucket=self.decision_hour_bucket,
                byte_offset=byte_offset + self.byte_length,
            ),
            value=DeactivationDecision(
                decision_id=decision_id,
                request_id=self.request_ids[input_index],
                kind=DeactivationDecisionKind.APPROVED,
                decided_at=self.decision_decided_at,
                actor_key=self.decision_actor_key,
            ),
        )
        self._decision_records[input_index] = record
        self.decision_visible_monotonic_by_input_id[decision_id] = (
            self.started_monotonic + self.decision_visible_after_seconds
        )
        return record


@dataclass(slots=True)
class StaleTargetDeactivationInputSource:
    composition: AlarmRuntimeComposition
    request_visible_after_seconds: int
    decision_visible_after_seconds: int
    input_count: int
    deactivation_window_seconds: int
    alarm_count: int
    priority_group_size: int
    management_actor_key: str = 'perf-operator'
    decision_actor_key: str = 'perf-approver'
    tool_key: str = 'perf-tool'
    byte_length: int = 256
    started_monotonic: float = field(default_factory=time.perf_counter)
    base_at: datetime = field(init=False)
    request_created_at: datetime = field(init=False)
    decision_decided_at: datetime = field(init=False)
    effective_until: datetime = field(init=False)
    management_hour_bucket: str = field(init=False)
    decision_hour_bucket: str = field(init=False)
    _management_records: dict[int, AlarmInputRecord] = field(
        default_factory=dict, init=False, repr=False
    )
    _decision_records: dict[int, AlarmInputRecord] = field(
        default_factory=dict, init=False, repr=False
    )
    target_occurrence_ids: dict[str, str] = field(default_factory=dict, init=False)
    management_visible_monotonic_by_input_id: dict[str, float] = field(
        default_factory=dict, init=False
    )
    decision_visible_monotonic_by_input_id: dict[str, float] = field(
        default_factory=dict, init=False
    )
    management_read_batch_sizes: list[int] = field(default_factory=list, init=False)
    decision_read_batch_sizes: list[int] = field(default_factory=list, init=False)
    management_read_at_count: int = field(default=0, init=False)
    decision_read_at_count: int = field(default=0, init=False)
    iteration_as_of: datetime | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.composition, AlarmRuntimeComposition):
            raise TypeError('composition must be AlarmRuntimeComposition')
        for name in (
            'request_visible_after_seconds',
            'decision_visible_after_seconds',
            'input_count',
            'deactivation_window_seconds',
            'alarm_count',
            'priority_group_size',
            'byte_length',
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f'{name} must be an int')
        if self.request_visible_after_seconds < 0:
            raise ValueError('request_visible_after_seconds must not be negative')
        if self.decision_visible_after_seconds <= self.request_visible_after_seconds:
            raise ValueError('decision visibility must start after request visibility')
        if self.input_count <= 0:
            raise ValueError('input_count must be greater than zero')
        if self.deactivation_window_seconds <= (
            self.decision_visible_after_seconds - self.request_visible_after_seconds
        ):
            raise ValueError('deactivation window must remain open at decision time')
        if self.priority_group_size <= 0 or self.alarm_count % self.priority_group_size != 0:
            raise ValueError('priority groups must be complete')
        if self.input_count > self.priority_group_count:
            raise ValueError('input_count must not exceed priority group count')
        if self.byte_length <= 0:
            raise ValueError('byte_length must be greater than zero')
        self.base_at = datetime.now(UTC).replace(microsecond=0)
        self.request_created_at = self.base_at + timedelta(
            seconds=self.request_visible_after_seconds
        )
        self.decision_decided_at = self.base_at + timedelta(
            seconds=self.decision_visible_after_seconds
        )
        self.effective_until = self.request_created_at + timedelta(
            seconds=self.deactivation_window_seconds
        )
        self.management_hour_bucket = self.request_created_at.strftime('%Y-%m-%dT%HZ')
        self.decision_hour_bucket = self.decision_decided_at.strftime('%Y-%m-%dT%HZ')

    @property
    def priority_group_count(self) -> int:
        return self.alarm_count // self.priority_group_size

    @property
    def input_ids(self) -> tuple[str, ...]:
        return tuple(f'PERF-M-{index + 1:06d}' for index in range(self.input_count))

    @property
    def request_ids(self) -> tuple[str, ...]:
        return tuple(f'PERF-DR-{input_id}' for input_id in self.input_ids)

    @property
    def decision_ids(self) -> tuple[str, ...]:
        return tuple(f'PERF-D-{index + 1:06d}' for index in range(self.input_count))

    @property
    def expected_management_cursor(self) -> AlarmInputCursor:
        return AlarmInputCursor(
            hour_bucket=self.management_hour_bucket,
            byte_offset=self.input_count * self.byte_length,
        )

    @property
    def expected_decision_cursor(self) -> AlarmInputCursor:
        return AlarmInputCursor(
            hour_bucket=self.decision_hour_bucket,
            byte_offset=self.input_count * self.byte_length,
        )

    def target_for_input(self, input_index: int) -> tuple[AlarmIdentity, str]:
        if not 0 <= input_index < self.input_count:
            raise IndexError(input_index)
        alarm_index = _group_first_target_alarm_index(
            input_index,
            alarm_count=self.alarm_count,
            priority_group_size=self.priority_group_size,
        )
        group_index = alarm_index // self.priority_group_size
        return (
            AlarmIdentity(
                family_key=_FAMILY_KEY,
                alarm_key=f'alarm_{alarm_index + 1:05d}',
            ),
            f'{_PRIORITY_GROUP_PREFIX}-{group_index + 1:03d}',
        )

    def prepare_iteration(self, *, as_of: datetime) -> None:
        if not isinstance(as_of, datetime):
            raise TypeError('as_of must be a datetime')
        if as_of.tzinfo is None or as_of.utcoffset() != timedelta(0):
            raise ValueError('as_of must be UTC-aware')
        self.iteration_as_of = as_of

    def read_after(
        self,
        *,
        stream: AlarmInputStream,
        cursor: AlarmInputCursor | None,
    ) -> tuple[AlarmInputRecord, ...]:
        iteration_as_of = self.iteration_as_of
        if iteration_as_of is None:
            raise RuntimeError('stale-target deactivation source requires iteration as_of')
        if stream is AlarmInputStream.MANAGEMENT:
            visible = self._is_visible(
                logical_at=self.request_created_at,
                visible_after_seconds=self.request_visible_after_seconds,
                iteration_as_of=iteration_as_of,
            )
            cursor_offset = 0 if cursor is None else cursor.byte_offset
            first_index = cursor_offset // self.byte_length
            records = (
                tuple(
                    self._management_record_for(index)
                    for index in range(first_index, self.input_count)
                )
                if visible
                else ()
            )
            self.management_read_batch_sizes.append(len(records))
            return records
        if stream is AlarmInputStream.DEACTIVATION_DECISION:
            visible = self._is_visible(
                logical_at=self.decision_decided_at,
                visible_after_seconds=self.decision_visible_after_seconds,
                iteration_as_of=iteration_as_of,
            )
            cursor_offset = 0 if cursor is None else cursor.byte_offset
            first_index = cursor_offset // self.byte_length
            records = (
                tuple(
                    self._decision_record_for(index)
                    for index in range(first_index, self.input_count)
                )
                if visible
                else ()
            )
            self.decision_read_batch_sizes.append(len(records))
            return records
        return ()

    def read_at(
        self,
        *,
        stream: AlarmInputStream,
        locator: AlarmInputLocator,
    ) -> AlarmInputRecord:
        index = locator.byte_offset // self.byte_length
        if stream is AlarmInputStream.MANAGEMENT:
            record = self._management_records.get(index)
            if record is not None and record.locator == locator:
                self.management_read_at_count += 1
                return record
        if stream is AlarmInputStream.DEACTIVATION_DECISION:
            record = self._decision_records.get(index)
            if record is not None and record.locator == locator:
                self.decision_read_at_count += 1
                return record
        raise LookupError(locator.input_id)

    def _is_visible(
        self,
        *,
        logical_at: datetime,
        visible_after_seconds: int,
        iteration_as_of: datetime,
    ) -> bool:
        return (
            time.perf_counter() - self.started_monotonic >= visible_after_seconds
            and iteration_as_of >= logical_at
        )

    def _management_record_for(self, input_index: int) -> AlarmInputRecord:
        existing = self._management_records.get(input_index)
        if existing is not None:
            return existing
        identity, priority_group = self.target_for_input(input_index)
        snapshot = self.composition.durability.persistence.read_snapshot(priority_group)
        if snapshot is None:
            raise RuntimeError(f'stale-target snapshot is not durable: {priority_group}')
        alarm = snapshot.as_document()['alarms'].get(identity.canonical_key)
        if not isinstance(alarm, dict) or not isinstance(alarm.get('occurrence'), dict):
            raise RuntimeError(f'stale-target occurrence is not durable: {identity.canonical_key}')
        occurrence_id = alarm['occurrence'].get('occurrence_id')
        if not isinstance(occurrence_id, str) or not occurrence_id:
            raise RuntimeError(f'stale-target occurrence id is invalid: {identity.canonical_key}')
        input_id = self.input_ids[input_index]
        byte_offset = input_index * self.byte_length
        record = AlarmInputRecord(
            locator=AlarmInputLocator(
                input_id=input_id,
                hour_bucket=self.management_hour_bucket,
                byte_offset=byte_offset,
                byte_length=self.byte_length,
            ),
            next_cursor=AlarmInputCursor(
                hour_bucket=self.management_hour_bucket,
                byte_offset=byte_offset + self.byte_length,
            ),
            value=ManagementAction(
                input_id=input_id,
                alarm_identity=identity,
                source_occurrence_id=occurrence_id,
                tool_key=self.tool_key,
                actor_key=self.management_actor_key,
                source_created_at=self.request_created_at,
                context={
                    'scenario': 'performance-stale-target-deactivation',
                    'sequence': str(input_index + 1),
                },
                deactivation_intent=DeactivationIntent(effective_until=self.effective_until),
            ),
        )
        self._management_records[input_index] = record
        self.target_occurrence_ids[identity.canonical_key] = occurrence_id
        self.management_visible_monotonic_by_input_id[input_id] = (
            self.started_monotonic + self.request_visible_after_seconds
        )
        return record

    def _decision_record_for(self, input_index: int) -> AlarmInputRecord:
        existing = self._decision_records.get(input_index)
        if existing is not None:
            return existing
        decision_id = self.decision_ids[input_index]
        byte_offset = input_index * self.byte_length
        record = AlarmInputRecord(
            locator=AlarmInputLocator(
                input_id=decision_id,
                hour_bucket=self.decision_hour_bucket,
                byte_offset=byte_offset,
                byte_length=self.byte_length,
            ),
            next_cursor=AlarmInputCursor(
                hour_bucket=self.decision_hour_bucket,
                byte_offset=byte_offset + self.byte_length,
            ),
            value=DeactivationDecision(
                decision_id=decision_id,
                request_id=self.request_ids[input_index],
                kind=DeactivationDecisionKind.APPROVED,
                decided_at=self.decision_decided_at,
                actor_key=self.decision_actor_key,
            ),
        )
        self._decision_records[input_index] = record
        self.decision_visible_monotonic_by_input_id[decision_id] = (
            self.started_monotonic + self.decision_visible_after_seconds
        )
        return record


@dataclass(slots=True)
class MixedDeactivationInputSource:
    composition: AlarmRuntimeComposition
    request_visible_after_seconds: int
    decision_visible_after_seconds: int
    input_count: int
    interval_seconds: int
    deactivation_window_seconds: int
    alarm_count: int
    priority_group_size: int
    management_actor_key: str = 'perf-operator'
    decision_actor_key: str = 'perf-approver'
    tool_key: str = 'perf-tool'
    byte_length: int = 256
    started_monotonic: float = field(default_factory=time.perf_counter)
    base_at: datetime = field(init=False)
    first_request_created_at: datetime = field(init=False)
    first_decision_decided_at: datetime = field(init=False)
    management_hour_bucket: str = field(init=False)
    decision_hour_bucket: str = field(init=False)
    _management_records: dict[int, AlarmInputRecord] = field(
        default_factory=dict, init=False, repr=False
    )
    _decision_records: dict[int, AlarmInputRecord] = field(
        default_factory=dict, init=False, repr=False
    )
    target_occurrence_ids: dict[str, str] = field(default_factory=dict, init=False)
    management_visible_monotonic_by_input_id: dict[str, float] = field(
        default_factory=dict, init=False
    )
    decision_visible_monotonic_by_input_id: dict[str, float] = field(
        default_factory=dict, init=False
    )
    management_read_batch_sizes: list[int] = field(default_factory=list, init=False)
    decision_read_batch_sizes: list[int] = field(default_factory=list, init=False)
    management_read_at_count: int = field(default=0, init=False)
    decision_read_at_count: int = field(default=0, init=False)
    iteration_as_of: datetime | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.composition, AlarmRuntimeComposition):
            raise TypeError('composition must be AlarmRuntimeComposition')
        for name in (
            'request_visible_after_seconds',
            'decision_visible_after_seconds',
            'input_count',
            'interval_seconds',
            'deactivation_window_seconds',
            'alarm_count',
            'priority_group_size',
            'byte_length',
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f'{name} must be an int')
        if self.request_visible_after_seconds < 0:
            raise ValueError('request_visible_after_seconds must not be negative')
        if self.decision_visible_after_seconds <= self.request_visible_after_seconds:
            raise ValueError('decision visibility must start after request visibility')
        if self.input_count <= 1:
            raise ValueError('input_count must be greater than one')
        if self.interval_seconds <= 0:
            raise ValueError('interval_seconds must be greater than zero')
        if self.deactivation_window_seconds <= (
            self.decision_visible_after_seconds - self.request_visible_after_seconds
        ):
            raise ValueError('deactivation window must remain open at decision time')
        if self.priority_group_size <= 0 or self.alarm_count % self.priority_group_size != 0:
            raise ValueError('priority groups must be complete')
        if self.input_count > self.alarm_count:
            raise ValueError('input_count must not exceed alarm_count')
        if self.byte_length <= 0:
            raise ValueError('byte_length must be greater than zero')
        self.base_at = datetime.now(UTC).replace(microsecond=0)
        self.first_request_created_at = self.base_at + timedelta(
            seconds=self.request_visible_after_seconds
        )
        self.first_decision_decided_at = self.base_at + timedelta(
            seconds=self.decision_visible_after_seconds
        )
        self.management_hour_bucket = self.first_request_created_at.strftime('%Y-%m-%dT%HZ')
        self.decision_hour_bucket = self.first_decision_decided_at.strftime('%Y-%m-%dT%HZ')

    @property
    def priority_group_count(self) -> int:
        return self.alarm_count // self.priority_group_size

    @property
    def input_ids(self) -> tuple[str, ...]:
        return tuple(f'PERF-M-{index + 1:06d}' for index in range(self.input_count))

    @property
    def request_ids(self) -> tuple[str, ...]:
        return tuple(f'PERF-DR-{input_id}' for input_id in self.input_ids)

    @property
    def decision_ids(self) -> tuple[str, ...]:
        return tuple(f'PERF-D-{index + 1:06d}' for index in range(self.input_count))

    @property
    def expected_management_cursor(self) -> AlarmInputCursor:
        return AlarmInputCursor(
            hour_bucket=self.management_hour_bucket,
            byte_offset=self.input_count * self.byte_length,
        )

    @property
    def expected_decision_cursor(self) -> AlarmInputCursor:
        return AlarmInputCursor(
            hour_bucket=self.decision_hour_bucket,
            byte_offset=self.input_count * self.byte_length,
        )

    @property
    def request_last_at_seconds(self) -> int:
        return self.request_visible_after_seconds + (self.input_count - 1) * self.interval_seconds

    @property
    def decision_last_at_seconds(self) -> int:
        return self.decision_visible_after_seconds + (self.input_count - 1) * self.interval_seconds

    def request_created_at_for(self, input_index: int) -> datetime:
        return self.first_request_created_at + timedelta(
            seconds=input_index * self.interval_seconds
        )

    def decision_decided_at_for(self, input_index: int) -> datetime:
        return self.first_decision_decided_at + timedelta(
            seconds=input_index * self.interval_seconds
        )

    def effective_until_for(self, input_index: int) -> datetime:
        return self.request_created_at_for(input_index) + timedelta(
            seconds=self.deactivation_window_seconds
        )

    def target_for_input(self, input_index: int) -> tuple[AlarmIdentity, str]:
        if not 0 <= input_index < self.input_count:
            raise IndexError(input_index)
        group_index = input_index % self.priority_group_count
        slot_index = input_index // self.priority_group_count
        alarm_index = group_index * self.priority_group_size + slot_index
        identity = AlarmIdentity(
            family_key=_FAMILY_KEY,
            alarm_key=f'alarm_{alarm_index + 1:05d}',
        )
        return identity, f'{_PRIORITY_GROUP_PREFIX}-{group_index + 1:03d}'

    def prepare_iteration(self, *, as_of: datetime) -> None:
        if not isinstance(as_of, datetime):
            raise TypeError('as_of must be a datetime')
        if as_of.tzinfo is None or as_of.utcoffset() != timedelta(0):
            raise ValueError('as_of must be UTC-aware')
        self.iteration_as_of = as_of

    def read_after(
        self,
        *,
        stream: AlarmInputStream,
        cursor: AlarmInputCursor | None,
    ) -> tuple[AlarmInputRecord, ...]:
        iteration_as_of = self.iteration_as_of
        if iteration_as_of is None:
            raise RuntimeError('mixed deactivation source requires iteration as_of')
        if stream is AlarmInputStream.MANAGEMENT:
            visible_count = self._visible_count(
                first_logical_at=self.first_request_created_at,
                visible_after_seconds=self.request_visible_after_seconds,
                iteration_as_of=iteration_as_of,
            )
            cursor_offset = 0 if cursor is None else cursor.byte_offset
            first_index = cursor_offset // self.byte_length
            records = tuple(
                self._management_record_for(index) for index in range(first_index, visible_count)
            )
            self.management_read_batch_sizes.append(len(records))
            return records
        if stream is AlarmInputStream.DEACTIVATION_DECISION:
            visible_count = self._visible_count(
                first_logical_at=self.first_decision_decided_at,
                visible_after_seconds=self.decision_visible_after_seconds,
                iteration_as_of=iteration_as_of,
            )
            cursor_offset = 0 if cursor is None else cursor.byte_offset
            first_index = cursor_offset // self.byte_length
            records = tuple(
                self._decision_record_for(index) for index in range(first_index, visible_count)
            )
            self.decision_read_batch_sizes.append(len(records))
            return records
        return ()

    def read_at(
        self,
        *,
        stream: AlarmInputStream,
        locator: AlarmInputLocator,
    ) -> AlarmInputRecord:
        index = locator.byte_offset // self.byte_length
        if stream is AlarmInputStream.MANAGEMENT:
            record = self._management_records.get(index)
            if record is not None and record.locator == locator:
                self.management_read_at_count += 1
                return record
        if stream is AlarmInputStream.DEACTIVATION_DECISION:
            record = self._decision_records.get(index)
            if record is not None and record.locator == locator:
                self.decision_read_at_count += 1
                return record
        raise LookupError(locator.input_id)

    def _visible_count(
        self,
        *,
        first_logical_at: datetime,
        visible_after_seconds: int,
        iteration_as_of: datetime,
    ) -> int:
        elapsed = time.perf_counter() - self.started_monotonic
        if elapsed < visible_after_seconds or iteration_as_of < first_logical_at:
            return 0
        elapsed_visible_count = min(
            self.input_count,
            int((elapsed - visible_after_seconds) // self.interval_seconds) + 1,
        )
        logical_visible_count = min(
            self.input_count,
            int((iteration_as_of - first_logical_at).total_seconds() // self.interval_seconds) + 1,
        )
        return min(elapsed_visible_count, logical_visible_count)

    def _management_record_for(self, input_index: int) -> AlarmInputRecord:
        existing = self._management_records.get(input_index)
        if existing is not None:
            return existing
        identity, priority_group = self.target_for_input(input_index)
        snapshot = self.composition.durability.persistence.read_snapshot(priority_group)
        if snapshot is None:
            raise RuntimeError(
                f'mixed deactivation target snapshot is not durable: {priority_group}'
            )
        alarm = snapshot.as_document()['alarms'].get(identity.canonical_key)
        if not isinstance(alarm, dict) or not isinstance(alarm.get('occurrence'), dict):
            raise RuntimeError(
                f'mixed deactivation target occurrence is not durable: {identity.canonical_key}'
            )
        occurrence_id = alarm['occurrence'].get('occurrence_id')
        if not isinstance(occurrence_id, str) or not occurrence_id:
            raise RuntimeError(
                f'mixed deactivation target occurrence id is invalid: {identity.canonical_key}'
            )
        input_id = self.input_ids[input_index]
        byte_offset = input_index * self.byte_length
        record = AlarmInputRecord(
            locator=AlarmInputLocator(
                input_id=input_id,
                hour_bucket=self.management_hour_bucket,
                byte_offset=byte_offset,
                byte_length=self.byte_length,
            ),
            next_cursor=AlarmInputCursor(
                hour_bucket=self.management_hour_bucket,
                byte_offset=byte_offset + self.byte_length,
            ),
            value=ManagementAction(
                input_id=input_id,
                alarm_identity=identity,
                source_occurrence_id=occurrence_id,
                tool_key=self.tool_key,
                actor_key=self.management_actor_key,
                source_created_at=self.request_created_at_for(input_index),
                context={
                    'scenario': 'performance-mixed-deactivation-pressure',
                    'sequence': str(input_index + 1),
                },
                deactivation_intent=DeactivationIntent(
                    effective_until=self.effective_until_for(input_index)
                ),
            ),
        )
        self._management_records[input_index] = record
        self.target_occurrence_ids[identity.canonical_key] = occurrence_id
        self.management_visible_monotonic_by_input_id[input_id] = (
            self.started_monotonic
            + self.request_visible_after_seconds
            + input_index * self.interval_seconds
        )
        return record

    def _decision_record_for(self, input_index: int) -> AlarmInputRecord:
        existing = self._decision_records.get(input_index)
        if existing is not None:
            return existing
        decision_id = self.decision_ids[input_index]
        byte_offset = input_index * self.byte_length
        record = AlarmInputRecord(
            locator=AlarmInputLocator(
                input_id=decision_id,
                hour_bucket=self.decision_hour_bucket,
                byte_offset=byte_offset,
                byte_length=self.byte_length,
            ),
            next_cursor=AlarmInputCursor(
                hour_bucket=self.decision_hour_bucket,
                byte_offset=byte_offset + self.byte_length,
            ),
            value=DeactivationDecision(
                decision_id=decision_id,
                request_id=self.request_ids[input_index],
                kind=DeactivationDecisionKind.APPROVED,
                decided_at=self.decision_decided_at_for(input_index),
                actor_key=self.decision_actor_key,
            ),
        )
        self._decision_records[input_index] = record
        self.decision_visible_monotonic_by_input_id[decision_id] = (
            self.started_monotonic
            + self.decision_visible_after_seconds
            + input_index * self.interval_seconds
        )
        return record


@dataclass(slots=True)
class SustainedManagementInputSource:
    composition: AlarmRuntimeComposition
    visible_after_seconds: int
    action_count: int
    interval_seconds: int
    alarm_count: int
    priority_group_size: int
    actor_key: str = 'perf-operator'
    tool_key: str = 'perf-tool'
    byte_length: int = 256
    started_monotonic: float = field(default_factory=time.perf_counter)
    first_source_created_at: datetime = field(init=False)
    hour_bucket: str = field(init=False)
    _records: dict[int, AlarmInputRecord] = field(default_factory=dict, init=False, repr=False)
    target_occurrence_ids: dict[str, str] = field(default_factory=dict, init=False)
    visible_monotonic_by_input_id: dict[str, float] = field(default_factory=dict, init=False)
    read_batch_sizes: list[int] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.composition, AlarmRuntimeComposition):
            raise TypeError('composition must be AlarmRuntimeComposition')
        for name in (
            'visible_after_seconds',
            'action_count',
            'interval_seconds',
            'alarm_count',
            'priority_group_size',
            'byte_length',
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f'{name} must be an int')
        if self.visible_after_seconds < 0:
            raise ValueError('visible_after_seconds must not be negative')
        if self.action_count <= 1:
            raise ValueError('action_count must be greater than one')
        if self.interval_seconds < 0:
            raise ValueError('interval_seconds must not be negative')
        if self.priority_group_size <= 0 or self.alarm_count % self.priority_group_size != 0:
            raise ValueError('priority groups must be complete')
        if self.action_count > self.alarm_count:
            raise ValueError('action_count must not exceed alarm_count')
        if self.byte_length <= 0:
            raise ValueError('byte_length must be greater than zero')
        self.first_source_created_at = datetime.now(UTC).replace(microsecond=0) + timedelta(
            seconds=self.visible_after_seconds
        )
        self.hour_bucket = self.first_source_created_at.strftime('%Y-%m-%dT%HZ')

    @property
    def priority_group_count(self) -> int:
        return self.alarm_count // self.priority_group_size

    @property
    def input_ids(self) -> tuple[str, ...]:
        return tuple(f'PERF-M-{index + 1:06d}' for index in range(self.action_count))

    @property
    def expected_final_cursor(self) -> AlarmInputCursor:
        return AlarmInputCursor(
            hour_bucket=self.hour_bucket,
            byte_offset=self.action_count * self.byte_length,
        )

    @property
    def nonempty_batch_sizes(self) -> tuple[int, ...]:
        return tuple(size for size in self.read_batch_sizes if size > 0)

    def target_for_action(self, action_index: int) -> tuple[AlarmIdentity, str]:
        if not 0 <= action_index < self.action_count:
            raise IndexError(action_index)
        group_index = action_index % self.priority_group_count
        slot_index = action_index // self.priority_group_count
        alarm_index = group_index * self.priority_group_size + slot_index
        identity = AlarmIdentity(
            family_key=_FAMILY_KEY,
            alarm_key=f'alarm_{alarm_index + 1:05d}',
        )
        return identity, f'{_PRIORITY_GROUP_PREFIX}-{group_index + 1:03d}'

    def read_after(
        self,
        *,
        stream: AlarmInputStream,
        cursor: AlarmInputCursor | None,
    ) -> tuple[AlarmInputRecord, ...]:
        if stream is not AlarmInputStream.MANAGEMENT:
            return ()
        elapsed = time.perf_counter() - self.started_monotonic
        if elapsed < self.visible_after_seconds:
            self.read_batch_sizes.append(0)
            return ()
        visible_count = (
            self.action_count
            if self.interval_seconds == 0
            else min(
                self.action_count,
                int((elapsed - self.visible_after_seconds) // self.interval_seconds) + 1,
            )
        )
        cursor_offset = 0 if cursor is None else cursor.byte_offset
        first_index = cursor_offset // self.byte_length
        records = tuple(self._record_for(index) for index in range(first_index, visible_count))
        self.read_batch_sizes.append(len(records))
        return records

    def read_at(
        self,
        *,
        stream: AlarmInputStream,
        locator: AlarmInputLocator,
    ) -> AlarmInputRecord:
        if stream is AlarmInputStream.MANAGEMENT:
            index = locator.byte_offset // self.byte_length
            record = self._records.get(index)
            if record is not None and record.locator == locator:
                return record
        raise LookupError(locator.input_id)

    def _record_for(self, action_index: int) -> AlarmInputRecord:
        existing = self._records.get(action_index)
        if existing is not None:
            return existing
        identity, priority_group = self.target_for_action(action_index)
        snapshot = self.composition.durability.persistence.read_snapshot(priority_group)
        if snapshot is None:
            raise RuntimeError(f'management target snapshot is not durable: {priority_group}')
        alarm = snapshot.as_document()['alarms'].get(identity.canonical_key)
        if not isinstance(alarm, dict) or not isinstance(alarm.get('occurrence'), dict):
            raise RuntimeError(
                f'management target occurrence is not durable: {identity.canonical_key}'
            )
        occurrence_id = alarm['occurrence'].get('occurrence_id')
        if not isinstance(occurrence_id, str) or not occurrence_id:
            raise RuntimeError(
                f'management target occurrence id is invalid: {identity.canonical_key}'
            )
        input_id = f'PERF-M-{action_index + 1:06d}'
        source_created_at = self.first_source_created_at + timedelta(
            seconds=action_index * self.interval_seconds
        )
        byte_offset = action_index * self.byte_length
        record = AlarmInputRecord(
            locator=AlarmInputLocator(
                input_id=input_id,
                hour_bucket=self.hour_bucket,
                byte_offset=byte_offset,
                byte_length=self.byte_length,
            ),
            next_cursor=AlarmInputCursor(
                hour_bucket=self.hour_bucket,
                byte_offset=byte_offset + self.byte_length,
            ),
            value=ManagementAction(
                input_id=input_id,
                alarm_identity=identity,
                source_occurrence_id=occurrence_id,
                tool_key=self.tool_key,
                actor_key=self.actor_key,
                source_created_at=source_created_at,
                context={
                    'scenario': (
                        'performance-management-burst'
                        if self.interval_seconds == 0
                        else 'performance-sustained-management'
                    ),
                    'sequence': str(action_index + 1),
                },
            ),
        )
        self._records[action_index] = record
        self.target_occurrence_ids[identity.canonical_key] = occurrence_id
        self.visible_monotonic_by_input_id[input_id] = (
            self.started_monotonic
            + self.visible_after_seconds
            + action_index * self.interval_seconds
        )
        return record


@dataclass(slots=True)
class PerformanceAlarmDurableInputConsumer(AlarmDurableInputConsumer):
    management_receipt_confirmed_monotonic: float | None = field(default=None, init=False)
    management_receipt_commit_id: str | None = field(default=None, init=False)
    receipt_before_cursor_advance_ok: bool = field(default=False, init=False)
    receipt_confirmed_monotonic_by_input_id: dict[str, float] = field(
        default_factory=dict, init=False
    )
    receipt_commit_id_by_input_id: dict[str, str] = field(default_factory=dict, init=False)
    receipt_before_cursor_checked_count: int = field(default=0, init=False)
    management_pending_high_water_count: int = field(default=0, init=False)
    management_receipt_batch_sizes: list[int] = field(default_factory=list, init=False)
    decision_receipt_confirmed_monotonic: float | None = field(default=None, init=False)
    decision_receipt_commit_id: str | None = field(default=None, init=False)
    request_receipt_confirmed_monotonic: float | None = field(default=None, init=False)
    request_receipt_commit_id: str | None = field(default=None, init=False)
    request_receipt_before_cursor_advance_ok: bool = field(default=False, init=False)
    decision_receipt_before_cursor_advance_ok: bool = field(default=False, init=False)
    decision_pending_high_water_count: int = field(default=0, init=False)
    pending_request_high_water_count: int = field(default=0, init=False)
    deactivation_request_receipt_confirmed_monotonic_by_input_id: dict[str, float] = field(
        default_factory=dict, init=False
    )
    deactivation_request_receipt_commit_id_by_input_id: dict[str, str] = field(
        default_factory=dict, init=False
    )
    deactivation_request_receipt_batch_sizes: list[int] = field(default_factory=list, init=False)
    deactivation_request_receipt_before_cursor_checked_count: int = field(default=0, init=False)
    deactivation_request_receipt_before_cursor_advance_ok: bool = field(default=False, init=False)
    deactivation_decision_receipt_confirmed_monotonic_by_input_id: dict[str, float] = field(
        default_factory=dict, init=False
    )
    deactivation_decision_receipt_commit_id_by_input_id: dict[str, str] = field(
        default_factory=dict, init=False
    )
    deactivation_decision_receipt_batch_sizes: list[int] = field(default_factory=list, init=False)
    deactivation_decision_receipt_before_cursor_checked_count: int = field(default=0, init=False)
    deactivation_decision_receipt_before_cursor_advance_ok: bool = field(default=False, init=False)
    inverted_early_decision_pending_count: int | None = field(default=None, init=False)
    inverted_early_decision_cursor_byte_offset: int | None = field(default=None, init=False)
    inverted_early_decision_receipt_count: int | None = field(default=None, init=False)
    inverted_post_request_decision_pending_count: int | None = field(default=None, init=False)
    inverted_post_request_pending_request_count: int | None = field(default=None, init=False)
    inverted_post_request_management_cursor_byte_offset: int | None = field(
        default=None, init=False
    )
    inverted_post_request_decision_receipt_count: int | None = field(default=None, init=False)
    inverted_final_resolved_observed: bool = field(default=False, init=False)
    inverted_decision_receipt_confirmed_monotonic_by_input_id: dict[str, float] = field(
        default_factory=dict, init=False
    )
    inverted_decision_receipt_batch_sizes: list[int] = field(default_factory=list, init=False)
    mixed_request_receipt_confirmed_monotonic_by_input_id: dict[str, float] = field(
        default_factory=dict, init=False
    )
    mixed_decision_receipt_confirmed_monotonic_by_input_id: dict[str, float] = field(
        default_factory=dict, init=False
    )
    mixed_request_receipt_batch_sizes: list[int] = field(default_factory=list, init=False)
    mixed_decision_receipt_batch_sizes: list[int] = field(default_factory=list, init=False)
    mixed_receipt_cycle_batches: list[tuple[int, int]] = field(default_factory=list, init=False)
    stale_request_receipt_confirmed_monotonic_by_input_id: dict[str, float] = field(
        default_factory=dict, init=False
    )
    stale_decision_receipt_confirmed_monotonic_by_input_id: dict[str, float] = field(
        default_factory=dict, init=False
    )
    stale_request_receipt_batch_sizes: list[int] = field(default_factory=list, init=False)
    stale_decision_receipt_batch_sizes: list[int] = field(default_factory=list, init=False)

    def execute(self, context, *, cycle, iteration):
        source = self.source
        if isinstance(
            source,
            (
                SustainedDeactivationRequestInputSource,
                SustainedDeactivationDecisionInputSource,
                InvertedDeliveryDeactivationInputSource,
                StaleTargetDeactivationInputSource,
                MixedDeactivationInputSource,
            ),
        ):
            source.prepare_iteration(as_of=iteration.as_of)
        return AlarmDurableInputConsumer.execute(
            self,
            context,
            cycle=cycle,
            iteration=iteration,
        )

    def _replace_state(self, context, state) -> None:
        source = self.source
        self.management_pending_high_water_count = max(
            self.management_pending_high_water_count, len(state.management.pending)
        )
        self.decision_pending_high_water_count = max(
            self.decision_pending_high_water_count, len(state.decisions.pending)
        )
        self.pending_request_high_water_count = max(
            self.pending_request_high_water_count, len(state.pending_deactivation_request_ids)
        )
        if isinstance(source, SingleManagementInputSource):
            self._confirm_single_management_receipt(source, state)
        elif isinstance(source, SustainedManagementInputSource):
            self._confirm_sustained_management_receipts(source, state)
        elif isinstance(source, SingleDeactivationDecisionInputSource):
            self._confirm_deactivation_request_receipt(source, state)
            self._confirm_deactivation_decision_receipt(source, state)
        elif isinstance(source, SustainedDeactivationRequestInputSource):
            self._confirm_sustained_deactivation_request_receipts(source, state)
        elif isinstance(source, SustainedDeactivationDecisionInputSource):
            self._confirm_sustained_deactivation_decision_receipts(source, state)
        elif isinstance(source, InvertedDeliveryDeactivationInputSource):
            self._observe_inverted_deactivation_delivery(source, state)
        elif isinstance(source, StaleTargetDeactivationInputSource):
            self._observe_stale_target_deactivation(source)
        elif isinstance(source, MixedDeactivationInputSource):
            self._observe_mixed_deactivation_delivery(source)
        AlarmDurableInputConsumer._replace_state(self, context, state)

    def _observe_stale_target_deactivation(self, source) -> None:
        newly_confirmed_requests = [
            input_id
            for input_id in source.input_ids
            if f'DEACTIVATION_REQUEST:{input_id}' in self._index.receipts
            and input_id not in self.stale_request_receipt_confirmed_monotonic_by_input_id
        ]
        newly_confirmed_decisions = [
            decision_id
            for decision_id in source.decision_ids
            if f'DEACTIVATION_DECISION:{decision_id}' in self._index.receipts
            and decision_id not in self.stale_decision_receipt_confirmed_monotonic_by_input_id
        ]
        if not newly_confirmed_requests and not newly_confirmed_decisions:
            return
        confirmed_at = time.perf_counter()
        for input_id in newly_confirmed_requests:
            self.stale_request_receipt_confirmed_monotonic_by_input_id[input_id] = confirmed_at
        for decision_id in newly_confirmed_decisions:
            self.stale_decision_receipt_confirmed_monotonic_by_input_id[decision_id] = confirmed_at
        if newly_confirmed_requests:
            self.stale_request_receipt_batch_sizes.append(len(newly_confirmed_requests))
        if newly_confirmed_decisions:
            self.stale_decision_receipt_batch_sizes.append(len(newly_confirmed_decisions))

    def _observe_mixed_deactivation_delivery(self, source) -> None:
        newly_confirmed_requests = [
            input_id
            for input_id in source.input_ids
            if f'DEACTIVATION_REQUEST:{input_id}' in self._index.receipts
            and input_id not in self.mixed_request_receipt_confirmed_monotonic_by_input_id
        ]
        newly_confirmed_decisions = [
            decision_id
            for decision_id in source.decision_ids
            if f'DEACTIVATION_DECISION:{decision_id}' in self._index.receipts
            and decision_id not in self.mixed_decision_receipt_confirmed_monotonic_by_input_id
        ]
        if not newly_confirmed_requests and not newly_confirmed_decisions:
            return
        confirmed_at = time.perf_counter()
        for input_id in newly_confirmed_requests:
            self.mixed_request_receipt_confirmed_monotonic_by_input_id[input_id] = confirmed_at
        for decision_id in newly_confirmed_decisions:
            self.mixed_decision_receipt_confirmed_monotonic_by_input_id[decision_id] = confirmed_at
        if newly_confirmed_requests:
            self.mixed_request_receipt_batch_sizes.append(len(newly_confirmed_requests))
        if newly_confirmed_decisions:
            self.mixed_decision_receipt_batch_sizes.append(len(newly_confirmed_decisions))
        self.mixed_receipt_cycle_batches.append(
            (len(newly_confirmed_requests), len(newly_confirmed_decisions))
        )

    def _observe_inverted_deactivation_delivery(self, source, state) -> None:
        expected_count = source.input_count
        expected_cursor = expected_count * source.byte_length
        decision_receipt_ids = {
            decision_id
            for decision_id in source.decision_ids
            if f'DEACTIVATION_DECISION:{decision_id}' in self._index.receipts
        }
        newly_confirmed = [
            decision_id
            for decision_id in decision_receipt_ids
            if decision_id not in self.inverted_decision_receipt_confirmed_monotonic_by_input_id
        ]
        if newly_confirmed:
            confirmed_at = time.perf_counter()
            for decision_id in newly_confirmed:
                self.inverted_decision_receipt_confirmed_monotonic_by_input_id[decision_id] = (
                    confirmed_at
                )
            self.inverted_decision_receipt_batch_sizes.append(len(newly_confirmed))

        request_receipt_count = sum(
            f'DEACTIVATION_REQUEST:{input_id}' in self._index.receipts
            for input_id in source.input_ids
        )
        decision_receipt_count = len(decision_receipt_ids)
        decision_cursor_offset = (
            None if state.decisions.cursor is None else state.decisions.cursor.byte_offset
        )
        management_cursor_offset = (
            None if state.management.cursor is None else state.management.cursor.byte_offset
        )
        expected_pending_ids = set(source.decision_ids)
        actual_pending_ids = {locator.input_id for locator in state.decisions.pending}

        if (
            self.inverted_early_decision_pending_count is None
            and decision_cursor_offset == expected_cursor
            and management_cursor_offset is None
            and actual_pending_ids == expected_pending_ids
            and decision_receipt_count == 0
        ):
            self.inverted_early_decision_pending_count = len(state.decisions.pending)
            self.inverted_early_decision_cursor_byte_offset = decision_cursor_offset
            self.inverted_early_decision_receipt_count = decision_receipt_count

        if (
            self.inverted_post_request_decision_pending_count is None
            and decision_cursor_offset == expected_cursor
            and management_cursor_offset == expected_cursor
            and actual_pending_ids == expected_pending_ids
            and len(state.pending_deactivation_request_ids) == expected_count
            and request_receipt_count == expected_count
            and decision_receipt_count == 0
        ):
            self.inverted_post_request_decision_pending_count = len(state.decisions.pending)
            self.inverted_post_request_pending_request_count = len(
                state.pending_deactivation_request_ids
            )
            self.inverted_post_request_management_cursor_byte_offset = management_cursor_offset
            self.inverted_post_request_decision_receipt_count = decision_receipt_count

        if (
            decision_cursor_offset == expected_cursor
            and management_cursor_offset == expected_cursor
            and not state.decisions.pending
            and not state.pending_deactivation_request_ids
            and request_receipt_count == expected_count
            and decision_receipt_count == expected_count
        ):
            self.inverted_final_resolved_observed = True

    def _confirm_single_management_receipt(self, source, state) -> None:
        if (
            source.exposed_monotonic is None
            or state.management.cursor is None
            or self.management_receipt_confirmed_monotonic is not None
        ):
            return
        receipt = self._index.receipts.get(f'MANAGEMENT:{source.input_id}')
        if receipt is None:
            self.receipt_before_cursor_advance_ok = False
            return
        confirmed_at = time.perf_counter()
        self.receipt_before_cursor_advance_ok = True
        self.management_receipt_commit_id = str(receipt['commit_id'])
        self.management_receipt_confirmed_monotonic = confirmed_at
        self.receipt_confirmed_monotonic_by_input_id[source.input_id] = confirmed_at
        self.receipt_commit_id_by_input_id[source.input_id] = str(receipt['commit_id'])
        self.receipt_before_cursor_checked_count = 1

    def _confirm_sustained_management_receipts(self, source, state) -> None:
        cursor = state.management.cursor
        if cursor is None:
            return
        due_input_ids = tuple(
            input_id
            for input_id in source.input_ids
            if int(input_id.rsplit('-', 1)[1]) * source.byte_length <= cursor.byte_offset
        )
        missing = []
        newly_confirmed_count = 0
        confirmed_at = time.perf_counter()
        for input_id in due_input_ids:
            receipt = self._index.receipts.get(f'MANAGEMENT:{input_id}')
            if receipt is None:
                missing.append(input_id)
                continue
            if input_id not in self.receipt_confirmed_monotonic_by_input_id:
                self.receipt_confirmed_monotonic_by_input_id[input_id] = confirmed_at
                self.receipt_commit_id_by_input_id[input_id] = str(receipt['commit_id'])
                newly_confirmed_count += 1
        if newly_confirmed_count > 0:
            self.management_receipt_batch_sizes.append(newly_confirmed_count)
        self.receipt_before_cursor_checked_count = max(
            self.receipt_before_cursor_checked_count, len(due_input_ids)
        )
        self.receipt_before_cursor_advance_ok = not missing

    def _confirm_sustained_deactivation_request_receipts(self, source, state) -> None:
        cursor = state.management.cursor
        if cursor is None:
            return
        due_input_ids = tuple(
            input_id
            for input_id in source.input_ids
            if int(input_id.rsplit('-', 1)[1]) * source.byte_length <= cursor.byte_offset
        )
        missing = []
        newly_confirmed_count = 0
        confirmed_at = time.perf_counter()
        for input_id in due_input_ids:
            receipt = self._index.receipts.get(f'DEACTIVATION_REQUEST:{input_id}')
            if receipt is None:
                missing.append(input_id)
                continue
            if input_id not in self.deactivation_request_receipt_confirmed_monotonic_by_input_id:
                self.deactivation_request_receipt_confirmed_monotonic_by_input_id[input_id] = (
                    confirmed_at
                )
                self.deactivation_request_receipt_commit_id_by_input_id[input_id] = str(
                    receipt['commit_id']
                )
                newly_confirmed_count += 1
        if newly_confirmed_count > 0:
            self.deactivation_request_receipt_batch_sizes.append(newly_confirmed_count)
        self.deactivation_request_receipt_before_cursor_checked_count = max(
            self.deactivation_request_receipt_before_cursor_checked_count,
            len(due_input_ids),
        )
        self.deactivation_request_receipt_before_cursor_advance_ok = not missing

    def _confirm_sustained_deactivation_decision_receipts(self, source, state) -> None:
        cursor = state.decisions.cursor
        if cursor is None:
            return
        due_decision_ids = tuple(
            decision_id
            for decision_id in source.decision_ids
            if int(decision_id.rsplit('-', 1)[1]) * source.byte_length <= cursor.byte_offset
        )
        missing = []
        newly_confirmed_count = 0
        confirmed_at = time.perf_counter()
        for decision_id in due_decision_ids:
            receipt = self._index.receipts.get(f'DEACTIVATION_DECISION:{decision_id}')
            if receipt is None:
                missing.append(decision_id)
                continue
            if (
                decision_id
                not in self.deactivation_decision_receipt_confirmed_monotonic_by_input_id
            ):
                self.deactivation_decision_receipt_confirmed_monotonic_by_input_id[decision_id] = (
                    confirmed_at
                )
                self.deactivation_decision_receipt_commit_id_by_input_id[decision_id] = str(
                    receipt['commit_id']
                )
                newly_confirmed_count += 1
        if newly_confirmed_count > 0:
            self.deactivation_decision_receipt_batch_sizes.append(newly_confirmed_count)
        self.deactivation_decision_receipt_before_cursor_checked_count = max(
            self.deactivation_decision_receipt_before_cursor_checked_count,
            len(due_decision_ids),
        )
        self.deactivation_decision_receipt_before_cursor_advance_ok = not missing

    def _confirm_deactivation_request_receipt(self, source, state) -> None:
        if (
            source.management_exposed_monotonic is None
            or state.management.cursor is None
            or self.request_receipt_confirmed_monotonic is not None
        ):
            return
        receipt = self._index.receipts.get(f'DEACTIVATION_REQUEST:{source.management_input_id}')
        if receipt is None:
            self.request_receipt_before_cursor_advance_ok = False
            return
        self.request_receipt_confirmed_monotonic = time.perf_counter()
        self.request_receipt_commit_id = str(receipt['commit_id'])
        self.request_receipt_before_cursor_advance_ok = True

    def _confirm_deactivation_decision_receipt(self, source, state) -> None:
        if (
            source.decision_exposed_monotonic is None
            or state.decisions.cursor is None
            or self.decision_receipt_confirmed_monotonic is not None
        ):
            return
        receipt = self._index.receipts.get(f'DEACTIVATION_DECISION:{source.decision_id}')
        if receipt is None:
            self.decision_receipt_before_cursor_advance_ok = False
            return
        self.decision_receipt_confirmed_monotonic = time.perf_counter()
        self.decision_receipt_commit_id = str(receipt['commit_id'])
        self.decision_receipt_before_cursor_advance_ok = True

    @property
    def decision_input_to_receipt_ms(self) -> float | None:
        source = self.source
        if (
            not isinstance(source, SingleDeactivationDecisionInputSource)
            or source.decision_exposed_monotonic is None
            or self.decision_receipt_confirmed_monotonic is None
        ):
            return None
        return (
            self.decision_receipt_confirmed_monotonic - source.decision_exposed_monotonic
        ) * 1000

    @property
    def request_durable_before_decision_exposure(self) -> bool:
        source = self.source
        if not isinstance(source, SingleDeactivationDecisionInputSource):
            return False
        if (
            self.request_receipt_confirmed_monotonic is None
            or source.decision_exposed_monotonic is None
        ):
            return False
        return self.request_receipt_confirmed_monotonic <= source.decision_exposed_monotonic

    @property
    def management_input_to_receipt_ms(self) -> float | None:
        source = self.source
        if (
            not isinstance(source, SingleManagementInputSource)
            or source.exposed_monotonic is None
            or self.management_receipt_confirmed_monotonic is None
        ):
            return None
        return (self.management_receipt_confirmed_monotonic - source.exposed_monotonic) * 1000

    @property
    def sustained_management_input_to_receipt_ms(self) -> dict[str, float]:
        source = self.source
        if not isinstance(source, SustainedManagementInputSource):
            return {}
        return {
            input_id: (confirmed_at - source.visible_monotonic_by_input_id[input_id]) * 1000
            for input_id, confirmed_at in self.receipt_confirmed_monotonic_by_input_id.items()
            if input_id in source.visible_monotonic_by_input_id
        }


@dataclass(slots=True)
class ScheduledRevisionSource:
    source_revision: AlarmConfigurationRevision
    target_revision: AlarmConfigurationRevision
    switch_after_seconds: int
    started_monotonic: float
    base_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.source_revision, AlarmConfigurationRevision):
            raise TypeError('source_revision must be AlarmConfigurationRevision')
        if not isinstance(self.target_revision, AlarmConfigurationRevision):
            raise TypeError('target_revision must be AlarmConfigurationRevision')
        if isinstance(self.switch_after_seconds, bool) or not isinstance(
            self.switch_after_seconds, int
        ):
            raise TypeError('switch_after_seconds must be an int')
        if self.switch_after_seconds <= 0:
            raise ValueError('switch_after_seconds must be greater than zero')
        if not isinstance(self.started_monotonic, int | float):
            raise TypeError('started_monotonic must be numeric')
        if self.base_at.tzinfo is None or self.base_at.utcoffset() != timedelta(0):
            raise ValueError('base_at must be UTC-aware')
        if self.source_revision.revision_key == self.target_revision.revision_key:
            raise ValueError('scheduled revisions must differ')

    @property
    def target_published_at(self) -> datetime:
        return self.base_at + timedelta(seconds=self.switch_after_seconds)

    @property
    def target_is_visible(self) -> bool:
        return (
            time.perf_counter() - self.started_monotonic >= self.switch_after_seconds
            and datetime.now(UTC).replace(microsecond=0) >= self.target_published_at
        )

    def read_manifest(self) -> RuntimeManifest:
        revision = self.target_revision if self.target_is_visible else self.source_revision
        published_at = (
            self.target_published_at if revision is self.target_revision else self.base_at
        )
        return RuntimeManifest(
            schema_version=RUNTIME_MANIFEST_SCHEMA_VERSION,
            alarm_configuration_revision=revision.alarm_configuration_revision,
            tool_registry_revision=revision.tool_registry_revision,
            published_at=published_at,
        )

    def read_alarm_configuration(self, *, revision: str) -> dict[str, object]:
        registered = {
            self.source_revision.alarm_configuration_revision,
            self.target_revision.alarm_configuration_revision,
        }
        if revision not in registered:
            raise RuntimeError('scheduled alarm configuration revision is not registered')
        return {'revision': revision}

    def read_tool_registry(self, *, revision: str) -> dict[str, object]:
        if revision != self.source_revision.tool_registry_revision:
            raise RuntimeError('scheduled tool registry revision is not registered')
        return {'revision': revision}


@dataclass(slots=True)
class ScheduledUnavailableRevisionSource:
    delegate: FileRuntimeRevisionSource
    unavailable_after_seconds: int
    started_monotonic: float
    manifest_success_count: int = 0
    manifest_failure_count: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.delegate, FileRuntimeRevisionSource):
            raise TypeError('delegate must be FileRuntimeRevisionSource')
        if isinstance(self.unavailable_after_seconds, bool) or not isinstance(
            self.unavailable_after_seconds, int
        ):
            raise TypeError('unavailable_after_seconds must be an int')
        if self.unavailable_after_seconds <= 0:
            raise ValueError('unavailable_after_seconds must be greater than zero')
        if not isinstance(self.started_monotonic, int | float):
            raise TypeError('started_monotonic must be numeric')

    @property
    def unavailable(self) -> bool:
        return time.perf_counter() - self.started_monotonic >= self.unavailable_after_seconds

    def read_manifest(self) -> RuntimeManifest:
        if self.unavailable:
            self.manifest_failure_count += 1
            raise RuntimeRevisionSourceError('scheduled runtime manifest is unavailable')
        self.manifest_success_count += 1
        return self.delegate.read_manifest()

    def read_alarm_configuration(self, *, revision: str) -> dict[str, object]:
        return self.delegate.read_alarm_configuration(revision=revision)

    def read_tool_registry(self, *, revision: str) -> dict[str, object]:
        return self.delegate.read_tool_registry(revision=revision)


@dataclass(slots=True)
class ScheduledInvalidCandidateRevisionSource:
    delegate: FileRuntimeRevisionSource
    invalid_after_seconds: int
    started_monotonic: float
    candidate_alarm_revision: str = _INVALID_CANDIDATE_ALARM_REVISION
    invalid_alarm_document_revision: str = _INVALID_CANDIDATE_DOCUMENT_REVISION
    manifest_success_count: int = 0
    manifest_failure_count: int = 0
    candidate_manifest_count: int = 0
    candidate_alarm_document_read_count: int = 0
    candidate_tool_document_read_count: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.delegate, FileRuntimeRevisionSource):
            raise TypeError('delegate must be FileRuntimeRevisionSource')
        if isinstance(self.invalid_after_seconds, bool) or not isinstance(
            self.invalid_after_seconds, int
        ):
            raise TypeError('invalid_after_seconds must be an int')
        if self.invalid_after_seconds <= 0:
            raise ValueError('invalid_after_seconds must be greater than zero')
        if not isinstance(self.started_monotonic, int | float):
            raise TypeError('started_monotonic must be numeric')

    @property
    def invalid_visible(self) -> bool:
        return time.perf_counter() - self.started_monotonic >= self.invalid_after_seconds

    def read_manifest(self) -> RuntimeManifest:
        try:
            manifest = self.delegate.read_manifest()
        except RuntimeRevisionSourceError:
            self.manifest_failure_count += 1
            raise
        self.manifest_success_count += 1
        if not self.invalid_visible:
            return manifest
        self.candidate_manifest_count += 1
        return RuntimeManifest(
            schema_version=RUNTIME_MANIFEST_SCHEMA_VERSION,
            alarm_configuration_revision=self.candidate_alarm_revision,
            tool_registry_revision=manifest.tool_registry_revision,
            published_at=manifest.published_at + timedelta(seconds=self.invalid_after_seconds),
        )

    def read_alarm_configuration(self, *, revision: str) -> dict[str, object]:
        if self.invalid_visible and revision == self.candidate_alarm_revision:
            self.candidate_alarm_document_read_count += 1
        return self.delegate.read_alarm_configuration(revision=revision)

    def read_tool_registry(self, *, revision: str) -> dict[str, object]:
        if self.invalid_visible:
            self.candidate_tool_document_read_count += 1
        return self.delegate.read_tool_registry(revision=revision)


@dataclass(slots=True)
class TrackedInvalidCandidateRevisionDecoder:
    delegate: StaticRevisionDecoder
    candidate_alarm_revision: str = _INVALID_CANDIDATE_ALARM_REVISION
    contract_failure_count: int = 0

    def decode(self, *, bundle: RuntimeRevisionBundle) -> AlarmConfigurationRevision:
        try:
            return self.delegate.decode(bundle=bundle)
        except RuntimeRevisionContractError:
            if bundle.manifest.alarm_configuration_revision == self.candidate_alarm_revision:
                self.contract_failure_count += 1
            raise


@dataclass(slots=True)
class TrackedRuntimeRevisionCache:
    delegate: FileRuntimeRevisionCache
    replace_monotonic: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not isinstance(self.delegate, FileRuntimeRevisionCache):
            raise TypeError('delegate must be FileRuntimeRevisionCache')

    def load_effective(self) -> RuntimeRevisionBundle | None:
        return self.delegate.load_effective()

    def replace_effective(self, *, bundle: RuntimeRevisionBundle) -> None:
        self.delegate.replace_effective(bundle=bundle)
        self.replace_monotonic.append(time.perf_counter())


class InjectedCachePromotionError(RuntimeError):
    pass


@dataclass(slots=True)
class InjectedCachePromotionFailureRuntimeRevisionCache:
    delegate: FileRuntimeRevisionCache
    target_alarm_revision: str = _STRUCTURAL_RESET_ADOPTION_ALARM_REVISION
    target_tool_revision: str = _TOOL_REVISION
    replace_monotonic: list[float] = field(default_factory=list)
    target_attempt_monotonic: list[float] = field(default_factory=list)
    target_failure_monotonic: list[float] = field(default_factory=list)
    successful_target_replace_count: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.delegate, FileRuntimeRevisionCache):
            raise TypeError('delegate must be FileRuntimeRevisionCache')

    def load_effective(self) -> RuntimeRevisionBundle | None:
        return self.delegate.load_effective()

    def replace_effective(self, *, bundle: RuntimeRevisionBundle) -> None:
        target = (
            bundle.manifest.alarm_configuration_revision,
            bundle.manifest.tool_registry_revision,
        )
        expected = (self.target_alarm_revision, self.target_tool_revision)
        if target == expected:
            attempted_at = time.perf_counter()
            self.target_attempt_monotonic.append(attempted_at)
            self.target_failure_monotonic.append(attempted_at)
            raise InjectedCachePromotionError('injected target revision cache promotion failure')
        self.delegate.replace_effective(bundle=bundle)
        self.replace_monotonic.append(time.perf_counter())


@dataclass(slots=True)
class StaticRevisionDecoder:
    revision: AlarmConfigurationRevision
    additional_revisions: tuple[AlarmConfigurationRevision, ...] = ()

    def decode(self, *, bundle: RuntimeRevisionBundle) -> AlarmConfigurationRevision:
        if (
            bundle.alarm_configuration.get('revision')
            != bundle.manifest.alarm_configuration_revision
        ):
            raise RuntimeRevisionContractError('alarm configuration revision mismatch')
        if bundle.tool_registry.get('revision') != bundle.manifest.tool_registry_revision:
            raise RuntimeRevisionContractError('tool registry revision mismatch')
        registered = {
            item.revision_key: item for item in (self.revision, *self.additional_revisions)
        }
        resolved = registered.get(bundle.revision_key)
        if resolved is None:
            raise RuntimeRevisionContractError('runtime revision bundle is not registered')
        return resolved


@dataclass(slots=True)
class BaselineCycleFactory:
    composition: object
    occurrence_count: int = 0
    episode_count: int = 0
    management_enabled: bool = False
    deactivation_enabled: bool = False

    def __call__(self, session):
        def occurrence_id(_identity, _at) -> str:
            self.occurrence_count += 1
            return f'PERF-O-{self.occurrence_count:08d}'

        def episode_id(_priority_group, _at) -> str:
            self.episode_count += 1
            return f'PERF-E-{self.episode_count:08d}'

        return AlarmOperationalCycle(
            session=session,
            composition=self.composition,
            occurrence_id_factory=occurrence_id,
            episode_id_factory=episode_id,
            commit_time_provider=BaselineCommitClock(),
            runtime_artifact_version='ada-alarms-runtime/0.14.2',
            technical_evidence_contract=EvidenceContractRef(
                contract_key='evaluation-error',
                contract_version='v1',
            ),
            management_effect_id_factory=(
                (lambda action: f'PERF-ME-{action.input_id}') if self.management_enabled else None
            ),
            reappearance_due_at_resolver=(
                (lambda action: action.source_created_at + timedelta(hours=1))
                if self.management_enabled
                else None
            ),
            deactivation_request_id_factory=(
                (lambda action: f'PERF-DR-{action.input_id}') if self.deactivation_enabled else None
            ),
            deactivation_effect_id_factory=(
                (lambda request: f'PERF-DE-{request.request_id}')
                if self.deactivation_enabled
                else None
            ),
        )


class BaselineCommitClock:
    def committed_at(self, *, cycle_at: datetime) -> datetime:
        return cycle_at + timedelta(seconds=1)


@dataclass(slots=True)
class PerformanceAlarmRuntimeComposition(AlarmRuntimeComposition):
    durable_history_lookup_mode: str = 'baseline'
    durable_history_lookup_cycle_count: int = 0
    durable_history_lookup_call_count: int = 0
    durable_history_lookup_total_ms: float = 0.0
    durable_record_scan_count: int = 0
    durable_record_scan_total_ms: float = 0.0
    durable_record_entries_seen: int = 0
    durable_history_index_build_count: int = 0
    durable_history_index_build_total_ms: float = 0.0
    _durable_history_groups: frozenset[str] | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.durable_history_lookup_mode not in _DURABLE_HISTORY_LOOKUP_MODES:
            raise ValueError('unsupported durable history lookup mode')

    def begin_durable_history_lookup_cycle(self) -> None:
        self.durable_history_lookup_cycle_count += 1
        self._durable_history_groups = None

    def build_durable_history_lookup_metrics(self) -> DurableHistoryLookupMetrics:
        return DurableHistoryLookupMetrics(
            mode=self.durable_history_lookup_mode,
            cycle_count=self.durable_history_lookup_cycle_count,
            lookup_call_count=self.durable_history_lookup_call_count,
            lookup_total_ms=self.durable_history_lookup_total_ms,
            durable_record_scan_count=self.durable_record_scan_count,
            durable_record_scan_total_ms=self.durable_record_scan_total_ms,
            durable_record_entries_seen=self.durable_record_entries_seen,
            index_build_count=self.durable_history_index_build_count,
            index_build_total_ms=self.durable_history_index_build_total_ms,
        )

    def _has_durable_group_history(self, priority_group: str) -> bool:
        lookup_started = time.perf_counter()
        self.durable_history_lookup_call_count += 1
        try:
            persistence = self.durability.persistence
            if persistence.read_head().durable is None:
                return False
            if self.durable_history_lookup_mode == 'baseline':
                records = self._read_durable_records_for_history_lookup()
                return any(
                    entry.record.commit.priority_group == priority_group for entry in records
                )
            if self._durable_history_groups is None:
                records = self._read_durable_records_for_history_lookup()
                build_started = time.perf_counter()
                self._durable_history_groups = frozenset(
                    entry.record.commit.priority_group for entry in records
                )
                self.durable_history_index_build_count += 1
                self.durable_history_index_build_total_ms += (
                    time.perf_counter() - build_started
                ) * 1000
            return priority_group in self._durable_history_groups
        finally:
            self.durable_history_lookup_total_ms += (time.perf_counter() - lookup_started) * 1000

    def _read_durable_records_for_history_lookup(self):
        started = time.perf_counter()
        records = self.durability.persistence.read_durable_records()
        self.durable_record_scan_count += 1
        self.durable_record_scan_total_ms += (time.perf_counter() - started) * 1000
        self.durable_record_entries_seen += len(records)
        return records


@dataclass(frozen=True, slots=True)
class BaselineRuntime:
    job: AlarmRuntimeJobComposition
    revision: AlarmConfigurationRevision
    source_loader: SyntheticDataSourceLoader | F007PhysicalDataSourceLoader
    composition: PerformanceAlarmRuntimeComposition
    input_source: (
        EmptyInputSource
        | SingleManagementInputSource
        | SingleDeactivationDecisionInputSource
        | SustainedDeactivationRequestInputSource
        | SustainedDeactivationDecisionInputSource
        | InvertedDeliveryDeactivationInputSource
        | StaleTargetDeactivationInputSource
        | MixedDeactivationInputSource
        | SustainedManagementInputSource
    )
    input_consumer: PerformanceAlarmDurableInputConsumer
    target_revision: AlarmConfigurationRevision | None = None
    source_unavailable_revision_source: ScheduledUnavailableRevisionSource | None = None
    invalid_candidate_revision_source: ScheduledInvalidCandidateRevisionSource | None = None
    invalid_candidate_revision_decoder: TrackedInvalidCandidateRevisionDecoder | None = None
    tracked_revision_cache: TrackedRuntimeRevisionCache | None = None
    cache_promotion_failure_cache: InjectedCachePromotionFailureRuntimeRevisionCache | None = None


def build_baseline_runtime(
    *,
    scenario: BaselineScenario,
    volume_path: str | Path,
    source_path: str | Path,
    alarm_configuration_revision: str = _ALARM_REVISION,
    additional_revisions: tuple[AlarmConfigurationRevision, ...] = (),
    occurrence_id_start: int = 0,
    episode_id_start: int = 0,
    deactivation_phase: str | None = None,
    source_loader_override: F007PhysicalDataSourceLoader | None = None,
) -> BaselineRuntime:
    revision = _build_revision(
        scenario,
        alarm_configuration_revision=alarm_configuration_revision,
    )
    _write_source(
        source_path,
        alarm_configuration_revision=alarm_configuration_revision,
    )
    if scenario.has_invalid_source_candidate_pressure:
        _write_invalid_candidate_source_document(source_path)
    runtime_configuration = RuntimeConfiguration.from_sources(
        environ={
            'ENVIRONMENT': 'local',
            'APPLICATION': 'ada-alarms-runtime-performance',
            'VOLUMEN_PATH': str(volume_path),
        }
    )
    base_composition = build_alarm_runtime_composition(runtime_configuration=runtime_configuration)
    composition = PerformanceAlarmRuntimeComposition(
        runtime_configuration=base_composition.runtime_configuration,
        durability=base_composition.durability,
        durable_history_lookup_mode=scenario.durable_history_lookup_mode,
    )
    if source_loader_override is None:
        source_loader: SyntheticDataSourceLoader | F007PhysicalDataSourceLoader = (
            SyntheticDataSourceLoader(
                refresh_seconds=scenario.data_refresh_seconds,
                signal_value=float(scenario.signal_value),
                threshold=float(scenario.threshold),
                alarm_count=scenario.alarm_count,
                priority_group_size=scenario.priority_group_size,
                operational_churn_percent=scenario.operational_churn_percent,
                technical_hold_churn_percent=scenario.technical_hold_churn_percent,
                technical_hold_expiry_percent=scenario.technical_hold_expiry_percent,
                technical_hold_expiry_stagger_seconds=(
                    scenario.technical_hold_expiry_stagger_seconds
                ),
                technical_hold_error_duration_seconds=(
                    scenario.technical_hold_error_duration_seconds
                ),
                initial_error_activation_percent=scenario.initial_error_activation_percent,
                initial_error_hold_seconds=scenario.initial_error_hold_seconds,
                initial_error_activation_stagger_seconds=(
                    scenario.initial_error_activation_stagger_seconds
                ),
                fixed_initial_error_percent=scenario.fixed_initial_error_percent,
                initial_active_percent=scenario.initial_active_percent,
                physical_partition_count=scenario.physical_partition_count,
                physical_partition_layout=scenario.physical_partition_layout,
                historical_step_seconds=scenario.historical_step_seconds,
                historical_points_per_series=scenario.historical_points_per_series,
            )
        )
    else:
        if scenario.data_profile not in (_F007_PHYSICAL_WARM, _F010_PHYSICAL_INTEGRATED):
            raise ValueError('physical source loader override requires a physical data profile')
        source_loader = source_loader_override
    input_source: (
        EmptyInputSource
        | SingleManagementInputSource
        | SingleDeactivationDecisionInputSource
        | SustainedDeactivationRequestInputSource
        | SustainedDeactivationDecisionInputSource
        | InvertedDeliveryDeactivationInputSource
        | StaleTargetDeactivationInputSource
        | MixedDeactivationInputSource
        | SustainedManagementInputSource
    )
    target_revision: AlarmConfigurationRevision | None = None
    schedule_started_monotonic: float | None = None
    schedule_base_at: datetime | None = None
    if scenario.has_rejected_candidate_pressure:
        input_source = EmptyInputSource()
        target_revision = _build_rejected_target_revision(
            scenario,
            alarm_configuration_revision=_REJECTED_TARGET_ALARM_REVISION,
        )
        schedule_started_monotonic = time.perf_counter()
        schedule_base_at = datetime.now(UTC).replace(microsecond=0)
    elif scenario.has_mixed_revision_adoption_pressure:
        input_source = EmptyInputSource()
        target_revision = _build_mixed_revision_target_revision(
            scenario,
            alarm_configuration_revision=_MIXED_REVISION_ADOPTION_ALARM_REVISION,
        )
        schedule_started_monotonic = time.perf_counter()
        schedule_base_at = datetime.now(UTC).replace(microsecond=0)
    elif scenario.test_id == 'F-010':
        input_source = MixedDeactivationInputSource(
            composition=composition,
            request_visible_after_seconds=scenario.management_action_at_seconds,
            decision_visible_after_seconds=scenario.deactivation_decision_at_seconds,
            input_count=scenario.effective_deactivation_decision_count,
            interval_seconds=scenario.deactivation_decision_interval_seconds,
            deactivation_window_seconds=scenario.deactivation_window_seconds,
            alarm_count=scenario.alarm_count,
            priority_group_size=scenario.priority_group_size,
        )
        target_revision = _build_parameter_target_revision(
            scenario,
            alarm_configuration_revision=_PARAMETER_ADOPTION_ALARM_REVISION,
        )
        schedule_started_monotonic = input_source.started_monotonic
        schedule_base_at = input_source.base_at
    elif scenario.has_parameter_adoption_pressure:
        input_source = EmptyInputSource()
        target_revision = _build_parameter_target_revision(
            scenario,
            alarm_configuration_revision=_PARAMETER_ADOPTION_ALARM_REVISION,
        )
        schedule_started_monotonic = time.perf_counter()
        schedule_base_at = datetime.now(UTC).replace(microsecond=0)
    elif scenario.has_c2_routing_adoption_pressure:
        input_source = EmptyInputSource()
        target_revision = _build_c2_routing_adoption_target_revision(
            scenario,
            alarm_configuration_revision=_C2_ROUTING_ADOPTION_ALARM_REVISION,
        )
        schedule_started_monotonic = time.perf_counter()
        schedule_base_at = datetime.now(UTC).replace(microsecond=0)
    elif scenario.has_disabled_adoption_pressure:
        input_source = EmptyInputSource()
        target_revision = _build_disabled_target_revision(
            scenario,
            alarm_configuration_revision=_DISABLED_ADOPTION_ALARM_REVISION,
        )
        schedule_started_monotonic = time.perf_counter()
        schedule_base_at = datetime.now(UTC).replace(microsecond=0)
    elif scenario.has_structural_reset_adoption_pressure:
        input_source = EmptyInputSource()
        target_revision = _build_structural_reset_target_revision(
            scenario,
            alarm_configuration_revision=_STRUCTURAL_RESET_ADOPTION_ALARM_REVISION,
        )
        schedule_started_monotonic = time.perf_counter()
        schedule_base_at = datetime.now(UTC).replace(microsecond=0)
    elif scenario.has_cache_promotion_failure_pressure:
        input_source = EmptyInputSource()
        target_revision = _build_cache_promotion_failure_target_revision(
            scenario,
            alarm_configuration_revision=_STRUCTURAL_RESET_ADOPTION_ALARM_REVISION,
        )
        schedule_started_monotonic = time.perf_counter()
        schedule_base_at = datetime.now(UTC).replace(microsecond=0)
    elif scenario.has_lease_loss_adoption_pressure:
        input_source = EmptyInputSource()
        target_revision = _build_lease_loss_target_revision(
            scenario,
            alarm_configuration_revision=_STRUCTURAL_RESET_ADOPTION_ALARM_REVISION,
        )
        schedule_started_monotonic = time.perf_counter()
        schedule_base_at = datetime.now(UTC).replace(microsecond=0)
    elif scenario.has_removed_adoption_pressure:
        input_source = StaleTargetDeactivationInputSource(
            composition=composition,
            request_visible_after_seconds=scenario.management_action_at_seconds,
            decision_visible_after_seconds=scenario.deactivation_decision_at_seconds,
            input_count=scenario.removed_alarm_count,
            deactivation_window_seconds=scenario.deactivation_window_seconds,
            alarm_count=scenario.alarm_count,
            priority_group_size=scenario.priority_group_size,
        )
        target_revision = _build_removed_target_revision(
            scenario,
            alarm_configuration_revision=_REMOVED_ADOPTION_ALARM_REVISION,
        )
        schedule_started_monotonic = input_source.started_monotonic
        schedule_base_at = input_source.base_at
    elif scenario.has_stale_target_deactivation_pressure:
        input_source = StaleTargetDeactivationInputSource(
            composition=composition,
            request_visible_after_seconds=scenario.management_action_at_seconds,
            decision_visible_after_seconds=scenario.deactivation_decision_at_seconds,
            input_count=scenario.effective_deactivation_decision_count,
            deactivation_window_seconds=scenario.deactivation_window_seconds,
            alarm_count=scenario.alarm_count,
            priority_group_size=scenario.priority_group_size,
        )
        target_revision = _build_removed_target_revision(
            scenario,
            alarm_configuration_revision=_STALE_TARGET_ALARM_REVISION,
        )
    elif scenario.has_mixed_deactivation_pressure:
        input_source = MixedDeactivationInputSource(
            composition=composition,
            request_visible_after_seconds=scenario.management_action_at_seconds,
            decision_visible_after_seconds=scenario.deactivation_decision_at_seconds,
            input_count=scenario.effective_deactivation_decision_count,
            interval_seconds=scenario.deactivation_decision_interval_seconds,
            deactivation_window_seconds=scenario.deactivation_window_seconds,
            alarm_count=scenario.alarm_count,
            priority_group_size=scenario.priority_group_size,
        )
    elif scenario.has_inverted_deactivation_delivery_pressure:
        input_source = InvertedDeliveryDeactivationInputSource(
            composition=composition,
            request_logical_at_seconds=scenario.management_action_at_seconds,
            request_delivery_after_seconds=scenario.deactivation_request_delivery_at_seconds,
            decision_visible_after_seconds=scenario.deactivation_decision_at_seconds,
            input_count=scenario.effective_deactivation_decision_count,
            deactivation_window_seconds=scenario.deactivation_window_seconds,
            alarm_count=scenario.alarm_count,
            priority_group_size=scenario.priority_group_size,
        )
    elif scenario.has_multi_deactivation_decision_pressure:
        if deactivation_phase == 'requests':
            input_source = SustainedDeactivationRequestInputSource(
                composition=composition,
                visible_after_seconds=scenario.management_action_at_seconds,
                request_count=scenario.effective_deactivation_decision_count,
                interval_seconds=scenario.management_action_interval_seconds,
                deactivation_window_seconds=scenario.deactivation_window_seconds,
                alarm_count=scenario.alarm_count,
                priority_group_size=scenario.priority_group_size,
            )
        elif deactivation_phase == 'decisions':
            input_source = SustainedDeactivationDecisionInputSource(
                composition=composition,
                visible_after_seconds=scenario.deactivation_decision_at_seconds,
                decision_count=scenario.effective_deactivation_decision_count,
                interval_seconds=scenario.deactivation_decision_interval_seconds,
            )
        else:
            raise ValueError(
                'multi deactivation decision runtime requires requests or decisions phase'
            )
    elif scenario.has_deactivation_decision_pressure:
        input_source = SingleDeactivationDecisionInputSource(
            composition=composition,
            request_visible_after_seconds=scenario.management_action_at_seconds,
            decision_visible_after_seconds=scenario.deactivation_decision_at_seconds,
            deactivation_window_seconds=scenario.deactivation_window_seconds,
            target_identity=AlarmIdentity(family_key=_FAMILY_KEY, alarm_key='alarm_00001'),
            target_priority_group=_priority_group(0, scenario=scenario),
        )
    elif scenario.has_multi_management_pressure:
        input_source = SustainedManagementInputSource(
            composition=composition,
            visible_after_seconds=scenario.management_action_at_seconds,
            action_count=scenario.effective_management_action_count,
            interval_seconds=scenario.management_action_interval_seconds,
            alarm_count=scenario.alarm_count,
            priority_group_size=scenario.priority_group_size,
        )
    elif scenario.has_management_pressure:
        input_source = SingleManagementInputSource(
            composition=composition,
            visible_after_seconds=scenario.management_action_at_seconds,
            target_identity=AlarmIdentity(family_key=_FAMILY_KEY, alarm_key='alarm_00001'),
            target_priority_group=_priority_group(0, scenario=scenario),
        )
    else:
        input_source = EmptyInputSource()
    registered_additional_revisions = (
        additional_revisions
        if target_revision is None
        else (*additional_revisions, target_revision)
    )
    source_unavailable_revision_source: ScheduledUnavailableRevisionSource | None = None
    invalid_candidate_revision_source: ScheduledInvalidCandidateRevisionSource | None = None
    invalid_candidate_revision_decoder: TrackedInvalidCandidateRevisionDecoder | None = None
    tracked_revision_cache: TrackedRuntimeRevisionCache | None = None
    cache_promotion_failure_cache: InjectedCachePromotionFailureRuntimeRevisionCache | None = None
    if target_revision is None:
        file_revision_source = FileRuntimeRevisionSource(root_path=source_path)
        if scenario.has_source_unavailable_pressure:
            source_unavailable_revision_source = ScheduledUnavailableRevisionSource(
                delegate=file_revision_source,
                unavailable_after_seconds=scenario.source_unavailable_at_seconds,
                started_monotonic=time.perf_counter(),
            )
            revision_source = source_unavailable_revision_source
        elif scenario.has_invalid_source_candidate_pressure:
            invalid_candidate_revision_source = ScheduledInvalidCandidateRevisionSource(
                delegate=file_revision_source,
                invalid_after_seconds=scenario.invalid_candidate_at_seconds,
                started_monotonic=time.perf_counter(),
            )
            revision_source = invalid_candidate_revision_source
        else:
            revision_source = file_revision_source
    elif (
        scenario.has_rejected_candidate_pressure
        or scenario.has_mixed_revision_adoption_pressure
        or scenario.has_parameter_adoption_pressure
        or scenario.has_c2_routing_adoption_pressure
        or scenario.has_disabled_adoption_pressure
        or scenario.has_removed_adoption_pressure
        or scenario.has_structural_reset_adoption_pressure
        or scenario.has_lease_loss_adoption_pressure
        or scenario.has_cache_promotion_failure_pressure
    ):
        if schedule_started_monotonic is None or schedule_base_at is None:
            raise RuntimeError('configuration adoption schedule clock was not initialized')
        if scenario.has_rejected_candidate_pressure:
            switch_after_seconds = scenario.rejected_candidate_at_seconds
        elif scenario.has_mixed_revision_adoption_pressure:
            switch_after_seconds = scenario.mixed_revision_adoption_at_seconds
        elif scenario.has_parameter_adoption_pressure:
            switch_after_seconds = scenario.parameter_adoption_at_seconds
        elif scenario.has_c2_routing_adoption_pressure:
            switch_after_seconds = scenario.c2_routing_adoption_at_seconds
        elif scenario.has_disabled_adoption_pressure:
            switch_after_seconds = scenario.disabled_adoption_at_seconds
        elif scenario.has_removed_adoption_pressure:
            switch_after_seconds = scenario.removed_adoption_at_seconds
        elif scenario.has_structural_reset_adoption_pressure:
            switch_after_seconds = scenario.structural_reset_adoption_at_seconds
        elif scenario.has_cache_promotion_failure_pressure:
            switch_after_seconds = scenario.cache_promotion_failure_at_seconds
        else:
            switch_after_seconds = scenario.lease_loss_adoption_at_seconds
        revision_source = ScheduledRevisionSource(
            source_revision=revision,
            target_revision=target_revision,
            switch_after_seconds=switch_after_seconds,
            started_monotonic=schedule_started_monotonic,
            base_at=schedule_base_at,
        )
    else:
        revision_source = ScheduledRevisionSource(
            source_revision=revision,
            target_revision=target_revision,
            switch_after_seconds=scenario.deactivation_target_removal_at_seconds,
            started_monotonic=input_source.started_monotonic,
            base_at=input_source.base_at,
        )
    file_revision_cache = FileRuntimeRevisionCache(root_path=volume_path)
    if scenario.has_cache_promotion_failure_pressure:
        cache_promotion_failure_cache = InjectedCachePromotionFailureRuntimeRevisionCache(
            delegate=file_revision_cache
        )
        revision_cache = cache_promotion_failure_cache
    elif (
        scenario.has_source_unavailable_pressure
        or scenario.has_invalid_source_candidate_pressure
        or scenario.has_lease_loss_adoption_pressure
        or scenario.has_drain_under_workload_pressure
    ):
        tracked_revision_cache = TrackedRuntimeRevisionCache(delegate=file_revision_cache)
        revision_cache = tracked_revision_cache
    else:
        revision_cache = file_revision_cache
    static_revision_decoder = StaticRevisionDecoder(
        revision=revision,
        additional_revisions=registered_additional_revisions,
    )
    if scenario.has_invalid_source_candidate_pressure:
        invalid_candidate_revision_decoder = TrackedInvalidCandidateRevisionDecoder(
            delegate=static_revision_decoder
        )
        revision_decoder = invalid_candidate_revision_decoder
    else:
        revision_decoder = static_revision_decoder
    resolver = RuntimeRevisionResolver(
        source=revision_source,
        decoder=revision_decoder,
        cache=revision_cache,
    )
    input_consumer = PerformanceAlarmDurableInputConsumer(
        composition=composition,
        source=input_source,
    )
    job = AlarmRuntimeJobComposition(
        composition=composition,
        revision_resolver=resolver,
        adoption_executor=AlarmConfigurationAdoptionExecutor(
            composition=composition,
            commit_time_provider=BaselineCommitClock(),
            runtime_artifact_version='ada-alarms-runtime/0.14.2',
        ),
        input_consumer=input_consumer,
        iteration_source_loader=source_loader,
        cycle_factory=BaselineCycleFactory(
            composition=composition,
            occurrence_count=occurrence_id_start,
            episode_count=episode_id_start,
            management_enabled=scenario.has_management_pressure,
            deactivation_enabled=scenario.has_deactivation_decision_pressure,
        ),
        as_of_provider=lambda _context: datetime.now(UTC).replace(microsecond=0),
    )
    return BaselineRuntime(
        job=job,
        revision=revision,
        source_loader=source_loader,
        composition=composition,
        input_source=input_source,
        input_consumer=input_consumer,
        target_revision=target_revision,
        source_unavailable_revision_source=source_unavailable_revision_source,
        invalid_candidate_revision_source=invalid_candidate_revision_source,
        invalid_candidate_revision_decoder=invalid_candidate_revision_decoder,
        tracked_revision_cache=tracked_revision_cache,
        cache_promotion_failure_cache=cache_promotion_failure_cache,
    )


def _partition_columns(
    column_names: tuple[str, ...],
    partition_count: int,
    *,
    layout: str,
) -> tuple[tuple[str, ...], ...]:
    if layout in ('balanced', 'mixed'):
        sizes = _balanced_partition_sizes(len(column_names), partition_count)
    else:
        sizes = _skewed_partition_sizes(len(column_names), partition_count)
    partitions: list[tuple[str, ...]] = []
    start = 0
    for size in sizes:
        stop = start + size
        partitions.append(column_names[start:stop])
        start = stop
    return tuple(partitions)


def _balanced_partition_sizes(column_count: int, partition_count: int) -> tuple[int, ...]:
    base_size, remainder = divmod(column_count, partition_count)
    return tuple(base_size + (1 if index < remainder else 0) for index in range(partition_count))


def _skewed_partition_sizes(column_count: int, partition_count: int) -> tuple[int, ...]:
    if partition_count == 1:
        return (column_count,)
    hot_partition_count = max(1, min(partition_count - 1, math.ceil(partition_count * 0.2)))
    cold_partition_count = partition_count - hot_partition_count
    hot_column_count = min(
        column_count - cold_partition_count,
        max(hot_partition_count, round(column_count * 0.8)),
    )
    cold_column_count = column_count - hot_column_count
    return (
        *_balanced_partition_sizes(hot_column_count, hot_partition_count),
        *_balanced_partition_sizes(cold_column_count, cold_partition_count),
    )


def _mixed_empty_partition(partition_index: int) -> bool:
    return partition_index % 9 == 0


def _mixed_missing_column_partition(partition_index: int) -> bool:
    return partition_index % 4 == 0


def _historical_timestamps(
    *,
    as_of: datetime,
    point_count: int,
    step_seconds: int,
) -> pd.DatetimeIndex:
    if point_count <= 0:
        return pd.DatetimeIndex([], tz='UTC')
    start = as_of - timedelta(seconds=(point_count - 1) * step_seconds)
    return pd.date_range(
        start=start,
        periods=point_count,
        freq=pd.Timedelta(seconds=step_seconds),
        tz='UTC',
    )


def _group_first_target_alarm_index(
    input_index: int,
    *,
    alarm_count: int,
    priority_group_size: int,
) -> int:
    if priority_group_size <= 0 or alarm_count % priority_group_size != 0:
        raise ValueError('priority groups must be complete')
    priority_group_count = alarm_count // priority_group_size
    group_index = input_index % priority_group_count
    slot_index = input_index // priority_group_count
    alarm_index = group_index * priority_group_size + slot_index
    if alarm_index >= alarm_count:
        raise IndexError(input_index)
    return alarm_index


def _stale_target_removed_alarm_indices(scenario: BaselineScenario) -> frozenset[int]:
    return frozenset(
        _group_first_target_alarm_index(
            input_index,
            alarm_count=scenario.alarm_count,
            priority_group_size=scenario.priority_group_size,
        )
        for input_index in range(scenario.effective_deactivation_decision_count)
    )


def _disabled_target_alarm_indices(scenario: BaselineScenario) -> frozenset[int]:
    return frozenset(
        _group_first_target_alarm_index(
            input_index,
            alarm_count=scenario.alarm_count,
            priority_group_size=scenario.priority_group_size,
        )
        for input_index in range(scenario.disabled_alarm_count)
    )


def _removed_adoption_target_alarm_indices(scenario: BaselineScenario) -> frozenset[int]:
    return frozenset(
        _group_first_target_alarm_index(
            input_index,
            alarm_count=scenario.alarm_count,
            priority_group_size=scenario.priority_group_size,
        )
        for input_index in range(scenario.removed_alarm_count)
    )


def _build_rejected_target_revision(
    scenario: BaselineScenario,
    *,
    alarm_configuration_revision: str,
) -> AlarmConfigurationRevision:
    return _build_revision(
        scenario,
        alarm_configuration_revision=alarm_configuration_revision,
        priority_group_overrides={0: _REJECTED_TARGET_PRIORITY_GROUP},
    )


def _mixed_revision_structural_reset_alarm_indices(
    scenario: BaselineScenario,
) -> frozenset[int]:
    return frozenset(range(scenario.mixed_revision_structural_reset_alarm_count))


def _mixed_revision_disabled_alarm_indices(
    scenario: BaselineScenario,
) -> frozenset[int]:
    group_size = scenario.effective_priority_group_size
    first_group = scenario.mixed_revision_structural_reset_priority_group_count
    return frozenset(
        group_index * group_size
        for group_index in range(
            first_group,
            first_group + scenario.mixed_revision_disabled_alarm_count,
        )
    )


def _mixed_revision_removed_alarm_indices(
    scenario: BaselineScenario,
) -> frozenset[int]:
    group_size = scenario.effective_priority_group_size
    first_group = scenario.priority_group_count - scenario.mixed_revision_removed_alarm_count
    return frozenset(
        group_index * group_size + 1
        for group_index in range(first_group, scenario.priority_group_count)
    )


def _build_mixed_revision_target_revision(
    scenario: BaselineScenario,
    *,
    alarm_configuration_revision: str,
) -> AlarmConfigurationRevision:
    if scenario.mixed_revision_target_threshold is None:
        raise ValueError('mixed revision target requires target threshold')
    target_scenario = replace(
        scenario,
        threshold=float(scenario.mixed_revision_target_threshold),
        mixed_revision_adoption_at_seconds=0,
        mixed_revision_target_threshold=None,
        mixed_revision_disabled_alarm_percent=0,
        mixed_revision_removed_alarm_percent=0,
        mixed_revision_structural_reset_alarm_percent=0,
    )
    return _build_revision(
        target_scenario,
        alarm_configuration_revision=alarm_configuration_revision,
        excluded_alarm_indices=_mixed_revision_removed_alarm_indices(scenario),
        disabled_alarm_indices=_mixed_revision_disabled_alarm_indices(scenario),
        structural_reset_alarm_indices=(_mixed_revision_structural_reset_alarm_indices(scenario)),
    )


def _structural_reset_target_alarm_indices(
    scenario: BaselineScenario,
) -> frozenset[int]:
    return frozenset(range(scenario.structural_reset_alarm_count))


def _build_structural_reset_target_revision(
    scenario: BaselineScenario,
    *,
    alarm_configuration_revision: str,
) -> AlarmConfigurationRevision:
    return _build_revision(
        scenario,
        alarm_configuration_revision=alarm_configuration_revision,
        structural_reset_alarm_indices=_structural_reset_target_alarm_indices(scenario),
    )


def _lease_loss_target_alarm_indices(
    scenario: BaselineScenario,
) -> frozenset[int]:
    return frozenset(range(scenario.lease_loss_structural_reset_alarm_count))


def _build_lease_loss_target_revision(
    scenario: BaselineScenario,
    *,
    alarm_configuration_revision: str,
) -> AlarmConfigurationRevision:
    return _build_revision(
        scenario,
        alarm_configuration_revision=alarm_configuration_revision,
        structural_reset_alarm_indices=_lease_loss_target_alarm_indices(scenario),
    )


def _cache_promotion_failure_target_alarm_indices(
    scenario: BaselineScenario,
) -> frozenset[int]:
    return frozenset(range(scenario.cache_promotion_failure_structural_reset_alarm_count))


def _build_cache_promotion_failure_target_revision(
    scenario: BaselineScenario,
    *,
    alarm_configuration_revision: str,
) -> AlarmConfigurationRevision:
    return _build_revision(
        scenario,
        alarm_configuration_revision=alarm_configuration_revision,
        structural_reset_alarm_indices=_cache_promotion_failure_target_alarm_indices(scenario),
    )


def _build_parameter_target_revision(
    scenario: BaselineScenario,
    *,
    alarm_configuration_revision: str,
) -> AlarmConfigurationRevision:
    if scenario.parameter_target_threshold is None:
        raise ValueError('parameter target revision requires parameter_target_threshold')
    target_scenario = replace(
        scenario,
        test_id='F-010-TARGET' if scenario.test_id == 'F-010' else scenario.test_id,
        threshold=float(scenario.parameter_target_threshold),
        parameter_adoption_at_seconds=0,
        parameter_target_threshold=None,
    )
    return _build_revision(
        target_scenario,
        alarm_configuration_revision=alarm_configuration_revision,
    )


def _build_c2_routing_adoption_target_revision(
    scenario: BaselineScenario,
    *,
    alarm_configuration_revision: str,
) -> AlarmConfigurationRevision:
    if not scenario.c2_routing_adoption_target_delay_seconds:
        raise ValueError('C2 routing adoption target revision requires target delays')
    target_scenario = replace(
        scenario,
        c2_routing_delay_seconds=scenario.c2_routing_adoption_target_delay_seconds,
        c2_routing_adoption_at_seconds=0,
        c2_routing_adoption_target_delay_seconds=(),
    )
    return _build_revision(
        target_scenario,
        alarm_configuration_revision=alarm_configuration_revision,
    )


def _build_disabled_target_revision(
    scenario: BaselineScenario,
    *,
    alarm_configuration_revision: str,
) -> AlarmConfigurationRevision:
    return _build_revision(
        scenario,
        alarm_configuration_revision=alarm_configuration_revision,
        disabled_alarm_indices=_disabled_target_alarm_indices(scenario),
    )


def _build_removed_target_revision(
    scenario: BaselineScenario,
    *,
    alarm_configuration_revision: str,
) -> AlarmConfigurationRevision:
    return _build_revision(
        scenario,
        alarm_configuration_revision=alarm_configuration_revision,
        excluded_alarm_indices=(
            _removed_adoption_target_alarm_indices(scenario)
            if scenario.has_removed_adoption_pressure
            else _stale_target_removed_alarm_indices(scenario)
        ),
    )


def _build_revision(
    scenario: BaselineScenario,
    *,
    alarm_configuration_revision: str = _ALARM_REVISION,
    excluded_alarm_indices: frozenset[int] = frozenset(),
    disabled_alarm_indices: frozenset[int] = frozenset(),
    structural_reset_alarm_indices: frozenset[int] = frozenset(),
    priority_group_overrides: dict[int, str] | None = None,
) -> AlarmConfigurationRevision:
    priority_group_overrides = (
        {} if priority_group_overrides is None else dict(priority_group_overrides)
    )
    if excluded_alarm_indices & disabled_alarm_indices:
        raise ValueError('alarm indices cannot be both removed and disabled')
    planned_by_index = tuple(
        (
            index,
            _planned_alarm(
                index,
                scenario=scenario,
                alarm_configuration_revision=alarm_configuration_revision,
                criticality_override=(
                    Criticality.C2 if index in structural_reset_alarm_indices else None
                ),
                priority_group_override=priority_group_overrides.get(index),
            ),
        )
        for index in range(scenario.alarm_count)
        if index not in excluded_alarm_indices and index not in disabled_alarm_indices
    )
    planned = tuple(item for _index, item in planned_by_index)
    defined_alarm_identities = tuple(
        _planned_alarm(
            index,
            scenario=scenario,
            alarm_configuration_revision=alarm_configuration_revision,
        ).identity
        for index in range(scenario.alarm_count)
        if index not in excluded_alarm_indices
    )
    contracts = _build_contracts(scenario)
    session = build_alarm_execution_session(
        alarm_configuration_revision=alarm_configuration_revision,
        tool_registry_revision=_TOOL_REVISION,
        planned_alarms=planned,
        evaluator_registry=AlarmEvaluatorRegistry(contracts),
        parameters_by_alarm={
            item.identity: {
                'threshold': float(scenario.threshold),
                'signal_column': _primary_signal_column(index, scenario=scenario),
                'historical_columns': ','.join(_historical_columns(index, scenario=scenario)),
                **(
                    {'technical_error_signal': _TECHNICAL_ERROR_SIGNAL_VALUE}
                    if scenario.technical_hold_churn_percent > 0
                    or scenario.technical_hold_expiry_percent > 0
                    or scenario.initial_error_activation_percent > 0
                    or scenario.fixed_initial_error_percent > 0
                    else {}
                ),
            }
            for index, item in planned_by_index
        },
    )
    return AlarmConfigurationRevision(
        alarm_configuration_revision=alarm_configuration_revision,
        tool_registry_revision=_TOOL_REVISION,
        defined_alarm_identities=defined_alarm_identities,
        session=session,
    )


def _build_contracts(scenario: BaselineScenario) -> tuple[AlarmEvaluatorContract, ...]:
    if scenario.data_profile == _F010_PHYSICAL_INTEGRATED:
        return tuple(
            AlarmEvaluatorContract(
                family_key=_FAMILY_KEY,
                evaluator_key=f'{_F010_PHYSICAL_EVALUATOR_PREFIX}-{ordinal:06d}',
                evaluator=_steady_evaluator,
                requirements=(
                    _latest_requirement((signal_column,)),
                    _historical_requirement(
                        (f007_daily_signal_column_for_alarm(ordinal - 1),),
                        window_minutes=scenario.historical_window_minutes,
                    ),
                ),
            )
            for ordinal, signal_column in enumerate(f007_latest_signal_columns(), start=1)
        )
    if scenario.data_profile == _F007_PHYSICAL_WARM:
        return tuple(
            AlarmEvaluatorContract(
                family_key=_FAMILY_KEY,
                evaluator_key=f'{_F007_PHYSICAL_EVALUATOR_PREFIX}-{ordinal:06d}',
                evaluator=_steady_evaluator,
                requirements=(_latest_requirement((signal_column,)),),
            )
            for ordinal, signal_column in enumerate(f007_latest_signal_columns(), start=1)
        )
    if scenario.data_profile == _SHARED_LATEST:
        return (
            AlarmEvaluatorContract(
                family_key=_FAMILY_KEY,
                evaluator_key=_SHARED_EVALUATOR_KEY,
                evaluator=_steady_evaluator,
                requirements=(_latest_requirement((_SHARED_SIGNAL_COLUMN,)),),
            ),
        )
    return tuple(
        AlarmEvaluatorContract(
            family_key=_FAMILY_KEY,
            evaluator_key=_evaluator_key(index, scenario=scenario),
            evaluator=_steady_evaluator,
            requirements=_requirements_for_alarm(index, scenario=scenario),
        )
        for index in range(scenario.alarm_count)
    )


def _requirements_for_alarm(
    index: int,
    *,
    scenario: BaselineScenario,
) -> tuple[DataRequirement, ...]:
    latest = _latest_requirement(_signal_columns(index, scenario=scenario))
    if scenario.data_profile not in (_LATEST_HISTORICAL, _F010_PHYSICAL_INTEGRATED):
        return (latest,)
    return (
        latest,
        _historical_requirement(
            _historical_columns(index, scenario=scenario),
            window_minutes=scenario.historical_window_minutes,
        ),
    )


def _latest_requirement(column_names: tuple[str, ...]) -> DataRequirement:
    return DataRequirement(
        source=DataSource.PI_INTERPOLATED,
        partition=DataPartition.LATEST,
        columns=tuple(DataColumn(name, DataColumnType.FLOAT) for name in column_names),
    )


def _historical_requirement(
    column_names: tuple[str, ...],
    *,
    window_minutes: int,
) -> DataRequirement:
    return DataRequirement(
        source=DataSource.PI_INTERPOLATED,
        partition=DataPartition.DAILY,
        columns=tuple(DataColumn(name, DataColumnType.FLOAT) for name in column_names),
        time_window=TimeWindow(window_minutes, TimeWindowUnit.MINUTES),
    )


def _requires_deactivation_approval(index: int, *, scenario: BaselineScenario) -> bool:
    if not scenario.has_deactivation_decision_pressure:
        return False
    if not scenario.has_multi_deactivation_decision_pressure:
        return index == 0
    group_size = scenario.effective_priority_group_size
    group_count = scenario.priority_group_count
    group_index = index // group_size
    slot_index = index % group_size
    request_index = slot_index * group_count + group_index
    return request_index < scenario.effective_deactivation_decision_count


def _planned_alarm(
    index: int,
    *,
    scenario: BaselineScenario,
    alarm_configuration_revision: str = _ALARM_REVISION,
    criticality_override: Criticality | None = None,
    priority_group_override: str | None = None,
) -> PlannedAlarm:
    group_size = scenario.effective_priority_group_size
    return PlannedAlarm(
        identity=AlarmIdentity(
            family_key=_FAMILY_KEY,
            alarm_key=f'alarm_{index + 1:05d}',
        ),
        kind=AlarmKind.RISK,
        criticality=(
            criticality_override
            if criticality_override is not None
            else Criticality.C1
            if scenario.has_c1_routing_pressure
            else Criticality.C2
            if scenario.has_c2_routing_pressure
            else Criticality.C3
        ),
        priority_group=(
            priority_group_override
            if priority_group_override is not None
            else _priority_group(index, scenario=scenario)
        ),
        priority_order=index % group_size + 1,
        delivery_enabled=True,
        evaluator_key=_evaluator_key(index, scenario=scenario),
        alarm_configuration_revision=alarm_configuration_revision,
        tool_registry_revision=_TOOL_REVISION,
        deactivation_policy=(
            DeactivationPolicy(approval_required=True)
            if _requires_deactivation_approval(index, scenario=scenario)
            else None
        ),
        routing=AlarmRouting(
            origin_tool_key='perf-tool',
            destinations=(
                tuple(
                    RoutingDestination(tool_key=f'perf-route-{destination_index + 1:02d}')
                    for destination_index in range(scenario.c1_routing_destination_count)
                )
                if scenario.has_c1_routing_pressure
                else ()
                if scenario.c2_remove_destinations_target
                else tuple(
                    RoutingDestination(
                        tool_key=f'perf-route-{destination_index + 1:02d}',
                        delay_seconds=delay_seconds,
                    )
                    for destination_index, delay_seconds in enumerate(
                        scenario.c2_routing_delay_seconds
                    )
                )
            ),
        ),
    )


def _evaluator_key(index: int, *, scenario: BaselineScenario) -> str:
    if scenario.data_profile == _F010_PHYSICAL_INTEGRATED:
        signal_column = f007_signal_column_for_alarm(index)
        return f'{_F010_PHYSICAL_EVALUATOR_PREFIX}-{signal_column.removeprefix("signal_")}'
    if scenario.data_profile == _F007_PHYSICAL_WARM:
        signal_column = f007_signal_column_for_alarm(index)
        return f'{_F007_PHYSICAL_EVALUATOR_PREFIX}-{signal_column.removeprefix("signal_")}'
    if scenario.data_profile == _SHARED_LATEST:
        return _SHARED_EVALUATOR_KEY
    return f'{scenario.data_profile}-{index + 1:05d}'


def _signal_columns(index: int, *, scenario: BaselineScenario) -> tuple[str, ...]:
    if scenario.data_profile in (_F007_PHYSICAL_WARM, _F010_PHYSICAL_INTEGRATED):
        return (f007_signal_column_for_alarm(index),)
    if scenario.data_profile == _SHARED_LATEST:
        return (_SHARED_SIGNAL_COLUMN,)
    return tuple(
        f'signal_{index + 1:05d}_{offset + 1:02d}' for offset in range(scenario.columns_per_alarm)
    )


def _primary_signal_column(index: int, *, scenario: BaselineScenario) -> str:
    return _signal_columns(index, scenario=scenario)[0]


def _priority_group(index: int, *, scenario: BaselineScenario) -> str:
    if scenario.priority_group_size == 0:
        return _PRIORITY_GROUP
    group_index = index // scenario.priority_group_size + 1
    return f'{_PRIORITY_GROUP_PREFIX}-{group_index:03d}'


def _alarm_index_from_signal_column(column_name: str) -> int:
    prefix = 'signal_'
    suffix = '_01'
    if not column_name.startswith(prefix) or not column_name.endswith(suffix):
        raise ValueError('functional churn requires latest-narrow signal columns')
    alarm_number = column_name[len(prefix) : -len(suffix)]
    if not alarm_number.isdigit():
        raise ValueError('functional churn signal column is invalid')
    return int(alarm_number) - 1


def _historical_columns(index: int, *, scenario: BaselineScenario) -> tuple[str, ...]:
    if scenario.data_profile == _F010_PHYSICAL_INTEGRATED:
        return (f007_daily_signal_column_for_alarm(index),)
    if scenario.data_profile != _LATEST_HISTORICAL:
        return ()
    return tuple(
        f'history_{index + 1:05d}_{offset + 1:02d}'
        for offset in range(scenario.historical_series_per_alarm)
    )


def _steady_evaluator(context) -> AlarmEvaluation:
    signal_column = str(context.parameters['signal_column'])
    signal = context.data.get(DataSource.PI_INTERPOLATED, DataPartition.LATEST).last_value_number(
        signal_column
    )
    technical_error_signal = context.parameters.get('technical_error_signal')
    if technical_error_signal is not None and signal == float(technical_error_signal):
        return AlarmEvaluation(
            alarm_identity=context.alarm_identity,
            status=AlarmStatus.ERROR,
            evaluated_at=context.now,
            error=EvaluationError(
                origin=EvaluationErrorOrigin.EVALUATOR,
                error_key='synthetic_technical_hold_churn',
                message='Synthetic technical hold churn evaluation error',
            ),
        )
    historical_columns = tuple(
        item for item in str(context.parameters.get('historical_columns', '')).split(',') if item
    )
    historical_mean: float | None = None
    if historical_columns:
        historical = context.data.get(
            DataSource.PI_INTERPOLATED,
            DataPartition.DAILY,
        ).dataframe
        mean_value = historical.loc[:, list(historical_columns)].mean(numeric_only=True).mean()
        if not pd.isna(mean_value):
            historical_mean = float(mean_value)
    threshold = float(context.parameters['threshold'])
    status = (
        AlarmStatus.ACTIVE
        if signal is not None
        and signal >= threshold
        and (historical_mean is None or historical_mean >= threshold)
        else AlarmStatus.INACTIVE
    )
    return AlarmEvaluation(
        alarm_identity=context.alarm_identity,
        status=status,
        evaluated_at=context.now,
        evidence_snapshot=EvidenceSnapshot(
            contract_key='performance-threshold',
            contract_version='v1',
            payload={
                'signal': signal,
                'threshold': threshold,
                'historical_mean': historical_mean,
            },
        ),
    )


def _write_invalid_candidate_source_document(source_path: str | Path) -> None:
    store = AtomicJsonStore(root_path=Path(source_path))
    store.replace(
        f'alarm-configuration/{_INVALID_CANDIDATE_ALARM_REVISION}.json',
        {'revision': _INVALID_CANDIDATE_DOCUMENT_REVISION},
    )


def _write_source(
    source_path: str | Path,
    *,
    alarm_configuration_revision: str = _ALARM_REVISION,
) -> None:
    root = Path(source_path)
    store = AtomicJsonStore(root_path=root)
    published_at = datetime.now(UTC).replace(microsecond=0)
    manifest = RuntimeManifest(
        schema_version=RUNTIME_MANIFEST_SCHEMA_VERSION,
        alarm_configuration_revision=alarm_configuration_revision,
        tool_registry_revision=_TOOL_REVISION,
        published_at=published_at,
    )
    store.replace(
        f'alarm-configuration/{alarm_configuration_revision}.json',
        {'revision': alarm_configuration_revision},
    )
    store.replace(
        f'tool-registry/{_TOOL_REVISION}.json',
        {'revision': _TOOL_REVISION},
    )
    store.replace(
        'runtime-manifest.json',
        {
            'schema_version': manifest.schema_version,
            'alarm_configuration_revision': manifest.alarm_configuration_revision,
            'tool_registry_revision': manifest.tool_registry_revision,
            'published_at': manifest.published_at.isoformat().replace('+00:00', 'Z'),
        },
    )
