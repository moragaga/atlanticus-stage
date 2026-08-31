from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa

from ada.data.core import (
    DataColumn,
    DataColumnType,
    DataPartition,
    DataRequirement,
    DataSource,
    ShiftScope,
    ShiftSelection,
    TimeWindow,
    TimeWindowUnit,
)
from ada.data.planner import DataLoadPlan, DataRequirementPlanner
from ada.data.sources import (
    DataSourceLoader,
    DataSourceReadError,
    LoadedDataSources,
    PiSourceProvider,
    build_current_source_registry,
)
from atlanticus.datasets.models import DatasetDefinition, DatasetTarget

_ACCEPTED_DATASET_BANK_ID = 'f007-controlled-physical-bank-v1'
_ACCEPTED_MANIFEST_SHA256 = '1a80b1270af6ca08f6d01b450669392e9484c73a009a660c10cfbcd332553ebc'
_ACCEPTED_CONFORMANCE_SHA256 = '6a2a9b117ed20f2fe35049e08ca324248e4f0cff7e72562d934c44cdcbe65d47'
_ACCEPTED_AGGREGATE_SHA256 = 'fa89dd1386cbc0b4698d8ffaaa831f354d08aacc71687b033d0f837fa2f9cfac'
_ACCEPTED_BANK_SHA256 = 'a495f83bdaa13bc2ca30d125604a1a3bfef32788e0763305dffe6485896e8ea8'
_F007_SIGNAL_POOL_SIZE = 1000
_F007_WINDOW_COUNT = 61
_F007_PI_DAILY_SIGNAL_COUNT = 2
_F007_PI_DAILY_ROWS = 8640
_F007_DISPATCH_VALUE_COLUMNS = 24
_F007_DISPATCH_ROWS_PER_TURN = 256
_F007_FILE_COUNT = 1343
_F007_PARTITIONED_WORKING_SET_BYTES = 147059328


@dataclass(frozen=True, slots=True)
class F007Window:
    window_index: int
    as_of_utc: datetime
    target_paths: tuple[str, ...]
    window_fingerprint_sha256: str


@dataclass(frozen=True, slots=True)
class F007DatasetBank:
    manifest_path: Path
    conformance_path: Path
    input_root: Path
    dataset_bank_id: str
    aggregate_sha256: str
    bank_sha256: str
    latest_path: str
    windows: tuple[F007Window, ...]
    physical_signal_pool_size: int
    partitioned_working_set_bytes: int

    @classmethod
    def load(
        cls,
        *,
        manifest_path: str | Path,
        conformance_path: str | Path,
        input_root: str | Path,
        require_read_only: bool,
    ) -> F007DatasetBank:
        manifest_path = Path(manifest_path)
        conformance_path = Path(conformance_path)
        input_root = Path(input_root)
        manifest_bytes = _read_required_file(manifest_path)
        conformance_bytes = _read_required_file(conformance_path)
        _require_sha256(
            manifest_bytes,
            expected=_ACCEPTED_MANIFEST_SHA256,
            label='F-007 dataset manifest',
        )
        _require_sha256(
            conformance_bytes,
            expected=_ACCEPTED_CONFORMANCE_SHA256,
            label='F-007 dataset conformance',
        )
        manifest = _decode_json(manifest_bytes, label='F-007 dataset manifest')
        conformance = _decode_json(conformance_bytes, label='F-007 dataset conformance')
        _validate_manifest(manifest)
        _validate_conformance(conformance)
        if not input_root.is_dir():
            raise RuntimeError(f'F-007 dataset input root does not exist: {input_root}')
        if require_read_only and not _filesystem_is_read_only(input_root):
            raise RuntimeError('F-007 dataset input root must be mounted read-only')
        windows = tuple(_parse_window(item) for item in manifest['windows'])
        files = tuple(manifest['files'])
        latest = tuple(
            item
            for item in files
            if item.get('source') == DataSource.PI_INTERPOLATED.value
            and item.get('materialization') == DataPartition.LATEST.value
        )
        if len(latest) != 1:
            raise RuntimeError('F-007 dataset manifest must contain exactly one PI Latest file')
        return cls(
            manifest_path=manifest_path,
            conformance_path=conformance_path,
            input_root=input_root,
            dataset_bank_id=str(manifest['dataset_bank_id']),
            aggregate_sha256=str(manifest['fingerprints']['aggregate_sha256']),
            bank_sha256=str(manifest['fingerprints']['bank_sha256']),
            latest_path=str(latest[0]['mount_relative_path']),
            windows=windows,
            physical_signal_pool_size=int(
                manifest['aggregate_physical']['physical_signal_pool_size']
            ),
            partitioned_working_set_bytes=int(
                manifest['aggregate_physical']['partitioned_working_set_bytes']
            ),
        )

    def window(self, window_index: int) -> F007Window:
        if isinstance(window_index, bool) or not isinstance(window_index, int):
            raise TypeError('window_index must be an int')
        if not 0 <= window_index < len(self.windows):
            raise ValueError('window_index is outside the accepted F-007 bank')
        return self.windows[window_index]

    def pi_daily_path(self, window_index: int = 0) -> str:
        window = self.window(window_index)
        value = window.as_of_utc
        partition = f'year={value.year:04d}/month={value.month:02d}/day={value.day:02d}'
        matches = tuple(
            path
            for path in window.target_paths
            if '/pi/not_pii/interpolated/daily/' in f'/{path}' and partition in path
        )
        if len(matches) != 1:
            raise RuntimeError(
                'F-007 dataset window must contain exactly one PI Daily target for its as_of date'
            )
        return matches[0]


class F007DatasetRuntimeSourceReader:
    def __init__(self, *, input_root: str | Path) -> None:
        self._input_root = Path(input_root)
        (
            ParquetDatasetStore,
            DatasetRuntime,
            self._not_found_error,
            self._read_error,
            self._validation_error,
            self._column_filter_type,
            self._filter_operator,
        ) = _load_dataset_runtime_imports()
        self._store = ParquetDatasetStore(root=self._input_root / 'datasets')
        self._runtime = DatasetRuntime(store=self._store)
        self._trace: list[str] | None = None

    def begin_trace(self) -> None:
        if self._trace is not None:
            raise RuntimeError('F-007 source path trace is already active')
        self._trace = []

    def end_trace(self) -> tuple[str, ...]:
        if self._trace is None:
            raise RuntimeError('F-007 source path trace is not active')
        paths = tuple(self._trace)
        self._trace = None
        return paths

    def read_frame(
        self,
        *,
        definition: DatasetDefinition,
        target: DatasetTarget,
        projection_schema: pa.Schema,
        timestamp_column: str | None = None,
        start_utc: datetime | None = None,
        end_utc: datetime | None = None,
    ) -> pd.DataFrame | None:
        if not isinstance(projection_schema, pa.Schema):
            raise TypeError('projection_schema must be pyarrow.Schema')
        if self._trace is not None:
            physical_path = (
                self._store.path_for(definition=definition, target=target) / 'data.parquet'
            )
            self._trace.append(physical_path.relative_to(self._input_root).as_posix())
        filters = _time_filters(
            timestamp_column=timestamp_column,
            start_utc=start_utc,
            end_utc=end_utc,
            column_filter_type=self._column_filter_type,
            filter_operator=self._filter_operator,
        )
        try:
            result = self._runtime.scan_dataframe(
                definition=definition,
                targets=(target,),
                projection_schema=projection_schema,
                filters=filters,
            )
        except self._not_found_error:
            return None
        except (self._read_error, self._validation_error) as error:
            raise DataSourceReadError(f'{target.identifier}: dataset source read failed') from error
        dataframe = getattr(result, 'dataframe', None)
        if not isinstance(dataframe, pd.DataFrame):
            raise DataSourceReadError(f'{target.identifier}: dataset runtime returned invalid data')
        return dataframe


@dataclass(slots=True)
class F007PhysicalDataSourceLoader:
    loader: DataSourceLoader
    reader: F007DatasetRuntimeSourceReader
    refresh_seconds: int = 10
    fixed_as_of_utc: datetime | None = None
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
    prewarm_duration_ms: float | None = None
    prewarm_paths: tuple[str, ...] = ()

    def load(self, *, plan: DataLoadPlan, as_of: datetime) -> LoadedDataSources:
        effective_as_of = self.fixed_as_of_utc or as_of
        started = time.perf_counter()
        loaded = self.loader.load(plan=plan, as_of=effective_as_of)
        if self.fixed_as_of_utc is not None and loaded.as_of != as_of:
            loaded = LoadedDataSources(
                as_of=as_of,
                plan=loaded.plan,
                registry=loaded.registry,
                loaded=loaded.loaded,
                failures=loaded.failures,
                shift_resolver=loaded.shift_resolver,
                operational_resolver=loaded.operational_resolver,
            )
        duration_ms = (time.perf_counter() - started) * 1000
        self.load_count += 1
        generation = int(effective_as_of.timestamp()) // self.refresh_seconds
        if self.first_generation is None:
            self.first_generation = generation
        self.last_generation = generation
        self.load_durations_ms.append(duration_ms)
        self.merge_durations_ms.append(0.0)
        if self.load_count == 1:
            self._capture_first_load_metrics(plan=plan, loaded=loaded)
        return loaded

    def prewarm(
        self,
        *,
        plan: DataLoadPlan,
        as_of: datetime,
        expected_path: str | None = None,
        expected_paths: tuple[str, ...] | None = None,
    ) -> None:
        if (expected_path is None) == (expected_paths is None):
            raise ValueError('physical prewarm requires exactly one expected path contract')
        expected = (expected_path,) if expected_path is not None else tuple(expected_paths or ())
        effective_as_of = self.fixed_as_of_utc or as_of
        self.reader.begin_trace()
        started = time.perf_counter()
        try:
            loaded = self.loader.load(plan=plan, as_of=effective_as_of)
        finally:
            paths = self.reader.end_trace()
        if loaded.failures:
            failures = '; '.join(
                f'{view.source.value}/{view.partition.value}: {failure.message}'
                for view, failure in loaded.failures.items()
            )
            raise RuntimeError(f'F-007 physical prewarm source load failed: {failures}')
        if tuple(sorted(paths)) != tuple(sorted(expected)):
            raise RuntimeError(
                f'F-007 physical prewarm resolved unexpected paths: {paths!r} != {expected!r}'
            )
        self.prewarm_duration_ms = (time.perf_counter() - started) * 1000
        self.prewarm_paths = paths

    def _capture_first_load_metrics(
        self,
        *,
        plan: DataLoadPlan,
        loaded: LoadedDataSources,
    ) -> None:
        self.view_count = len(loaded.loaded)
        self.column_count = sum(len(view_plan.column_names) for view_plan in plan.views)
        self.row_count = sum(len(item.frame.index) for item in loaded.loaded.values())
        self.frame_bytes = sum(
            int(item.frame.memory_usage(index=True, deep=True).sum())
            for item in loaded.loaded.values()
        )
        rows_by_view = {view: len(item.frame.index) for view, item in loaded.loaded.items()}
        self.numeric_value_count = sum(
            len(view_plan.column_names) * rows_by_view.get(view_plan.view, 0)
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
            rows_by_view.get(view_plan.view, 0)
            for view_plan in plan.views
            if view_plan.partition is DataPartition.DAILY
        )
        self.historical_value_count = sum(
            len(view_plan.column_names) * rows_by_view.get(view_plan.view, 0)
            for view_plan in plan.views
            if view_plan.partition is DataPartition.DAILY
        )
        self.physical_partition_column_counts = tuple(
            len(view_plan.column_names) for view_plan in plan.views
        )


@dataclass(frozen=True, slots=True)
class CgroupIoCacheSnapshot:
    memory_current: int
    memory_anon: int
    memory_file: int
    memory_active_file: int
    memory_inactive_file: int
    io_read_bytes: int
    io_read_operations: int
    cpu_usage_usec: int
    cpu_nr_throttled: int
    cpu_throttled_usec: int

    @classmethod
    def read(cls, *, root: str | Path = '/sys/fs/cgroup') -> CgroupIoCacheSnapshot:
        root = Path(root)
        memory_current = _read_required_int(root / 'memory.current')
        memory_stat = _read_key_value_file(root / 'memory.stat')
        cpu_stat = _read_key_value_file(root / 'cpu.stat')
        io_read_bytes, io_read_operations = _read_io_stat(root / 'io.stat')
        return cls(
            memory_current=memory_current,
            memory_anon=_required_key(memory_stat, 'anon', 'memory.stat'),
            memory_file=_required_key(memory_stat, 'file', 'memory.stat'),
            memory_active_file=_required_key(memory_stat, 'active_file', 'memory.stat'),
            memory_inactive_file=_required_key(memory_stat, 'inactive_file', 'memory.stat'),
            io_read_bytes=io_read_bytes,
            io_read_operations=io_read_operations,
            cpu_usage_usec=_required_key(cpu_stat, 'usage_usec', 'cpu.stat'),
            cpu_nr_throttled=_required_key(cpu_stat, 'nr_throttled', 'cpu.stat'),
            cpu_throttled_usec=_required_key(cpu_stat, 'throttled_usec', 'cpu.stat'),
        )


def build_f007_physical_source_loader(
    *,
    input_root: str | Path,
    fixed_as_of_utc: datetime | None = None,
) -> F007PhysicalDataSourceLoader:
    reader = F007DatasetRuntimeSourceReader(input_root=input_root)
    loader = DataSourceLoader(
        reader=reader,
        registry=build_current_source_registry(pi_source=PiSourceProvider.NOTPII),
    )
    return F007PhysicalDataSourceLoader(
        loader=loader,
        reader=reader,
        fixed_as_of_utc=fixed_as_of_utc,
    )


def f007_latest_signal_columns() -> tuple[str, ...]:
    return tuple(f'signal_{ordinal:06d}' for ordinal in range(1, _F007_SIGNAL_POOL_SIZE + 1))


def f007_signal_column_for_alarm(index: int) -> str:
    if isinstance(index, bool) or not isinstance(index, int):
        raise TypeError('alarm index must be an int')
    if index < 0:
        raise ValueError('alarm index must not be negative')
    ordinal = index % _F007_SIGNAL_POOL_SIZE + 1
    return f'signal_{ordinal:06d}'


def f007_daily_signal_columns() -> tuple[str, ...]:
    return tuple(f'signal_{ordinal:06d}' for ordinal in range(1, _F007_PI_DAILY_SIGNAL_COUNT + 1))


def f007_daily_signal_column_for_alarm(index: int) -> str:
    if isinstance(index, bool) or not isinstance(index, int):
        raise TypeError('alarm index must be an int')
    if index < 0:
        raise ValueError('alarm index must not be negative')
    ordinal = index % _F007_PI_DAILY_SIGNAL_COUNT + 1
    return f'signal_{ordinal:06d}'


def build_f007_latest_plan() -> DataLoadPlan:
    requirement = DataRequirement(
        source=DataSource.PI_INTERPOLATED,
        partition=DataPartition.LATEST,
        columns=tuple(
            DataColumn(name, DataColumnType.FLOAT) for name in f007_latest_signal_columns()
        ),
    )
    return DataRequirementPlanner().plan({'f007-warm-fixed': (requirement,)})


def build_f007_partitioned_plan() -> DataLoadPlan:
    daily = DataRequirement(
        source=DataSource.PI_INTERPOLATED,
        partition=DataPartition.DAILY,
        columns=tuple(
            DataColumn(f'signal_{ordinal:06d}', DataColumnType.FLOAT)
            for ordinal in range(1, _F007_PI_DAILY_SIGNAL_COUNT + 1)
        ),
        time_window=TimeWindow(7, TimeWindowUnit.DAYS),
    )
    dispatch = DataRequirement(
        source=DataSource.DISPATCH_STD_SHIFT_STATE,
        partition=DataPartition.SHIFT,
        columns=tuple(
            DataColumn(f'value_{ordinal:03d}', DataColumnType.FLOAT)
            for ordinal in range(1, _F007_DISPATCH_VALUE_COLUMNS + 1)
        ),
        shift=ShiftSelection(ShiftScope.DAYS, days=7),
    )
    return DataRequirementPlanner().plan({'f007-partitioned': (daily, dispatch)})


def run_f007_physical_profile_gate(
    *,
    bank: F007DatasetBank,
    cgroup_root: str | Path = '/sys/fs/cgroup',
) -> dict[str, Any]:
    warm_loader = build_f007_physical_source_loader(input_root=bank.input_root)
    latest_plan = build_f007_latest_plan()
    warm_as_of = bank.windows[0].as_of_utc
    warm_prewarm = _profile_load(
        loader=warm_loader.loader,
        reader=warm_loader.reader,
        plan=latest_plan,
        as_of=warm_as_of,
        expected_paths=(bank.latest_path,),
        cgroup_root=cgroup_root,
    )
    warm_probe = _profile_load(
        loader=warm_loader.loader,
        reader=warm_loader.reader,
        plan=latest_plan,
        as_of=warm_as_of,
        expected_paths=(bank.latest_path,),
        cgroup_root=cgroup_root,
    )

    partitioned_loader = build_f007_physical_source_loader(input_root=bank.input_root)
    partitioned_plan = build_f007_partitioned_plan()
    first_touch = tuple(
        _profile_load(
            loader=partitioned_loader.loader,
            reader=partitioned_loader.reader,
            plan=partitioned_plan,
            as_of=window.as_of_utc,
            expected_paths=window.target_paths,
            cgroup_root=cgroup_root,
            window_index=window.window_index,
        )
        for window in bank.windows
    )
    warm_replay = tuple(
        _profile_load(
            loader=partitioned_loader.loader,
            reader=partitioned_loader.reader,
            plan=partitioned_plan,
            as_of=window.as_of_utc,
            expected_paths=window.target_paths,
            cgroup_root=cgroup_root,
            window_index=window.window_index,
        )
        for window in bank.windows
    )
    return {
        'status': 'PASS',
        'dataset_bank_id': bank.dataset_bank_id,
        'aggregate_sha256': bank.aggregate_sha256,
        'bank_sha256': bank.bank_sha256,
        'physical_signal_pool_size': bank.physical_signal_pool_size,
        'partitioned_working_set_bytes': bank.partitioned_working_set_bytes,
        'cold_cache_guaranteed': False,
        'profiles': {
            'WARM_FIXED': {
                'authoritative_for_alarm_capacity': True,
                'prewarm': warm_prewarm,
                'warm_probe': warm_probe,
            },
            'FIRST_TOUCH_PARTITIONED': _profile_summary(first_touch),
            'WARM_REPLAY_PARTITIONED': _profile_summary(warm_replay),
        },
    }


def _profile_load(
    *,
    loader: DataSourceLoader,
    reader: F007DatasetRuntimeSourceReader,
    plan: DataLoadPlan,
    as_of: datetime,
    expected_paths: tuple[str, ...],
    cgroup_root: str | Path,
    window_index: int | None = None,
) -> dict[str, Any]:
    before = CgroupIoCacheSnapshot.read(root=cgroup_root)
    reader.begin_trace()
    started = time.perf_counter()
    try:
        loaded = loader.load(plan=plan, as_of=as_of)
    finally:
        resolved_paths = reader.end_trace()
    duration_ms = (time.perf_counter() - started) * 1000
    after = CgroupIoCacheSnapshot.read(root=cgroup_root)
    if loaded.failures:
        failures = '; '.join(
            f'{view.source.value}/{view.partition.value}: {failure.message}'
            for view, failure in loaded.failures.items()
        )
        raise RuntimeError(f'F-007 physical profile source load failed: {failures}')
    if resolved_paths != expected_paths:
        raise RuntimeError(
            f'F-007 physical profile resolved unexpected paths for window {window_index}: '
            f'{resolved_paths!r} != {expected_paths!r}'
        )
    return {
        'window_index': window_index,
        'as_of_utc': as_of.isoformat().replace('+00:00', 'Z'),
        'target_count': len(resolved_paths),
        'source_load_ms': duration_ms,
        'io_read_bytes_delta': max(0, after.io_read_bytes - before.io_read_bytes),
        'io_read_operations_delta': max(0, after.io_read_operations - before.io_read_operations),
        'cpu_usage_usec_delta': max(0, after.cpu_usage_usec - before.cpu_usage_usec),
        'cpu_nr_throttled_delta': max(0, after.cpu_nr_throttled - before.cpu_nr_throttled),
        'cpu_throttled_usec_delta': max(0, after.cpu_throttled_usec - before.cpu_throttled_usec),
        'memory_current_before': before.memory_current,
        'memory_current_after': after.memory_current,
        'memory_anon_after': after.memory_anon,
        'memory_file_after': after.memory_file,
        'memory_active_file_after': after.memory_active_file,
        'memory_inactive_file_after': after.memory_inactive_file,
    }


def _profile_summary(samples: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    durations = tuple(float(item['source_load_ms']) for item in samples)
    return {
        'authoritative_for_alarm_capacity': False,
        'window_count': len(samples),
        'window_order': [int(item['window_index']) for item in samples],
        'source_load_p50_ms': _percentile(durations, 50),
        'source_load_p95_ms': _percentile(durations, 95),
        'source_load_p99_ms': _percentile(durations, 99),
        'io_read_bytes_delta_total': sum(int(item['io_read_bytes_delta']) for item in samples),
        'io_read_operations_delta_total': sum(
            int(item['io_read_operations_delta']) for item in samples
        ),
        'cpu_usage_usec_delta_total': sum(int(item['cpu_usage_usec_delta']) for item in samples),
        'cpu_nr_throttled_delta_total': sum(
            int(item['cpu_nr_throttled_delta']) for item in samples
        ),
        'cpu_throttled_usec_delta_total': sum(
            int(item['cpu_throttled_usec_delta']) for item in samples
        ),
        'memory_current_peak': max(int(item['memory_current_after']) for item in samples),
        'memory_anon_peak': max(int(item['memory_anon_after']) for item in samples),
        'memory_file_peak': max(int(item['memory_file_after']) for item in samples),
        'memory_active_file_peak': max(int(item['memory_active_file_after']) for item in samples),
        'memory_inactive_file_peak': max(
            int(item['memory_inactive_file_after']) for item in samples
        ),
        'samples': list(samples),
    }


def _validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get('manifest_version') != '1.1':
        raise RuntimeError('F-007 dataset manifest version is not accepted')
    if manifest.get('dataset_bank_id') != _ACCEPTED_DATASET_BANK_ID:
        raise RuntimeError('F-007 dataset bank id is not accepted')
    if manifest.get('origin_class') != 'controlled_synthetic_physical':
        raise RuntimeError('F-007 dataset origin class is not accepted')
    if manifest.get('operational_representative') is not False:
        raise RuntimeError('F-007 dataset must remain explicitly non-operational-representative')
    mount = manifest.get('mount_contract', {})
    if mount.get('container_path') != '/f007/input' or mount.get('read_only') is not True:
        raise RuntimeError('F-007 dataset mount contract is not accepted')
    aggregate = manifest.get('aggregate_physical', {})
    expected_aggregate = {
        'file_count': _F007_FILE_COUNT,
        'physical_signal_pool_size': _F007_SIGNAL_POOL_SIZE,
        'pi_daily_file_count': 488,
        'dispatch_shift_file_count': 854,
        'pi_latest_file_count': 1,
        'row_group_count_total': _F007_FILE_COUNT,
        'partitioned_working_set_bytes': _F007_PARTITIONED_WORKING_SET_BYTES,
    }
    for key, expected in expected_aggregate.items():
        if aggregate.get(key) != expected:
            raise RuntimeError(f'F-007 dataset aggregate field is not accepted: {key}')
    fingerprints = manifest.get('fingerprints', {})
    if fingerprints.get('aggregate_sha256') != _ACCEPTED_AGGREGATE_SHA256:
        raise RuntimeError('F-007 dataset aggregate fingerprint is not accepted')
    if fingerprints.get('bank_sha256') != _ACCEPTED_BANK_SHA256:
        raise RuntimeError('F-007 dataset bank fingerprint is not accepted')
    mapping = manifest.get('alarm_signal_mapping', {})
    if mapping.get('physical_signal_pool_size') != _F007_SIGNAL_POOL_SIZE:
        raise RuntimeError('F-007 alarm-to-signal pool size is not accepted')
    if mapping.get('strategy') != 'round_robin_modulo':
        raise RuntimeError('F-007 alarm-to-signal strategy is not accepted')
    if mapping.get('alarm_count_ceiling') is not None or mapping.get('ladder_frozen') is not False:
        raise RuntimeError('F-007 alarm-count ladder must remain unfrozen')
    profiles = manifest.get('profiles', {})
    expected_order = list(range(_F007_WINDOW_COUNT))
    first_touch = profiles.get('FIRST_TOUCH_PARTITIONED', {})
    replay = profiles.get('WARM_REPLAY_PARTITIONED', {})
    warm_fixed = profiles.get('WARM_FIXED', {})
    if first_touch.get('window_order') != expected_order:
        raise RuntimeError('F-007 first-touch window order is not accepted')
    if replay.get('window_order') != expected_order or replay.get('same_window_order') is not True:
        raise RuntimeError('F-007 warm replay window order is not accepted')
    if warm_fixed.get('authoritative_for_alarm_capacity') is not True:
        raise RuntimeError('F-007 WARM_FIXED must be authoritative for alarm capacity')
    windows = manifest.get('windows')
    if not isinstance(windows, list) or len(windows) != _F007_WINDOW_COUNT:
        raise RuntimeError('F-007 dataset must contain exactly 61 windows')
    all_paths: list[str] = []
    for expected_index, item in enumerate(windows):
        if item.get('window_index') != expected_index:
            raise RuntimeError('F-007 window indexes must be contiguous from 0 to 60')
        if item.get('target_count') != 22:
            raise RuntimeError(f'F-007 window {expected_index} must contain 22 targets')
        if item.get('pi_daily_target_count') != 8:
            raise RuntimeError(f'F-007 window {expected_index} must contain 8 PI Daily targets')
        if item.get('dispatch_shift_target_count') != 14:
            raise RuntimeError(
                f'F-007 window {expected_index} must contain 14 Dispatch Shift targets'
            )
        paths = item.get('target_paths')
        if not isinstance(paths, list) or len(paths) != 22:
            raise RuntimeError(f'F-007 window {expected_index} target paths are invalid')
        all_paths.extend(str(path) for path in paths)
    if len(all_paths) != len(set(all_paths)):
        raise RuntimeError('F-007 partitioned target paths must be globally unique')


def _validate_conformance(conformance: dict[str, Any]) -> None:
    if conformance.get('dataset_bank_id') != _ACCEPTED_DATASET_BANK_ID:
        raise RuntimeError('F-007 dataset conformance bank id is not accepted')
    if conformance.get('status') != 'PASS':
        raise RuntimeError('F-007 dataset conformance must be PASS')
    claims = conformance.get('claims', {})
    if claims.get('final_61_window_geometry_frozen') is not True:
        raise RuntimeError('F-007 final 61-window geometry is not frozen')
    if claims.get('cold_cache_guaranteed') is not False:
        raise RuntimeError('F-007 conformance must not claim guaranteed cold cache')
    checks = conformance.get('checks', {})
    required_checks = (
        'alarm_count_ladder_remains_unfrozen',
        'alarm_signal_mapping_pool_is_1000',
        'all_files_use_single_row_group',
        'all_files_use_zstd',
        'dispatch_geometry_is_256x25',
        'every_window_has_14_dispatch_shift_targets',
        'every_window_has_22_targets',
        'every_window_has_8_pi_daily_targets',
        'pi_daily_geometry_is_8640x3',
        'pi_latest_geometry_is_1x1001',
        'sample_deterministic_rebuild_verified',
        'total_file_count_is_1343',
        'warm_replay_uses_same_window_order',
        'window_count_is_61',
        'window_target_paths_are_globally_unique',
    )
    missing = tuple(key for key in required_checks if checks.get(key) is not True)
    if missing:
        raise RuntimeError(f'F-007 dataset conformance checks are not accepted: {missing}')


def _parse_window(item: dict[str, Any]) -> F007Window:
    raw_as_of = item.get('as_of_utc')
    if not isinstance(raw_as_of, str):
        raise RuntimeError('F-007 window as_of_utc must be text')
    as_of = datetime.fromisoformat(raw_as_of.replace('Z', '+00:00')).astimezone(UTC)
    if as_of.microsecond != 0:
        raise RuntimeError('F-007 window as_of_utc must use second precision')
    target_paths = tuple(str(path) for path in item.get('target_paths', ()))
    fingerprint = item.get('window_fingerprint_sha256')
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        raise RuntimeError('F-007 window fingerprint is invalid')
    return F007Window(
        window_index=int(item['window_index']),
        as_of_utc=as_of,
        target_paths=target_paths,
        window_fingerprint_sha256=fingerprint,
    )


def _load_dataset_runtime_imports() -> tuple[Any, Any, Any, Any, Any, Any, Any]:
    try:
        from atlanticus.datasets.parquet import ParquetDatasetStore
        from atlanticus.datasets.runtime import (
            ColumnFilter,
            DatasetRuntime,
            DatasetRuntimeNotFoundError,
            DatasetRuntimeReadError,
            DatasetRuntimeValidationError,
            FilterOperator,
        )
    except ImportError as error:
        raise RuntimeError(
            'F-007 physical dataset runtime dependencies are not installed'
        ) from error
    return (
        ParquetDatasetStore,
        DatasetRuntime,
        DatasetRuntimeNotFoundError,
        DatasetRuntimeReadError,
        DatasetRuntimeValidationError,
        ColumnFilter,
        FilterOperator,
    )


def _time_filters(
    *,
    timestamp_column: str | None,
    start_utc: datetime | None,
    end_utc: datetime | None,
    column_filter_type: Any,
    filter_operator: Any,
) -> tuple[Any, ...]:
    if timestamp_column is None:
        if start_utc is not None or end_utc is not None:
            raise ValueError('timestamp_column is required when a time boundary is provided')
        return ()
    filters: list[Any] = []
    if start_utc is not None:
        filters.append(
            column_filter_type(
                column=timestamp_column,
                operator=filter_operator.GREATER_THAN_OR_EQUAL,
                value=start_utc,
            )
        )
    if end_utc is not None:
        filters.append(
            column_filter_type(
                column=timestamp_column,
                operator=filter_operator.LESS_THAN_OR_EQUAL,
                value=end_utc,
            )
        )
    return tuple(filters)


def _read_required_file(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise RuntimeError(f'F-007 required control file cannot be read: {path}') from error


def _decode_json(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f'{label} is not valid JSON') from error
    if not isinstance(document, dict):
        raise RuntimeError(f'{label} must contain a JSON object')
    return document


def _require_sha256(raw: bytes, *, expected: str, label: str) -> None:
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected:
        raise RuntimeError(f'{label} SHA-256 mismatch: {actual} != {expected}')


def _filesystem_is_read_only(path: Path) -> bool:
    try:
        flags = os.statvfs(path).f_flag
    except OSError as error:
        raise RuntimeError(f'F-007 dataset mount cannot be inspected: {path}') from error
    return bool(flags & getattr(os, 'ST_RDONLY', 1))


def _read_required_int(path: Path) -> int:
    try:
        return int(path.read_text(encoding='utf-8').strip())
    except (OSError, ValueError) as error:
        raise RuntimeError(f'F-007 required cgroup metric cannot be read: {path}') from error


def _read_key_value_file(path: Path) -> dict[str, int]:
    try:
        raw = path.read_text(encoding='utf-8')
    except OSError as error:
        raise RuntimeError(f'F-007 required cgroup metric cannot be read: {path}') from error
    values: dict[str, int] = {}
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            values[parts[0]] = int(parts[1])
        except ValueError:
            continue
    return values


def _read_io_stat(path: Path) -> tuple[int, int]:
    try:
        raw = path.read_text(encoding='utf-8')
    except OSError as error:
        raise RuntimeError(f'F-007 required cgroup metric cannot be read: {path}') from error
    if not raw.strip():
        return 0, 0
    read_bytes = 0
    read_operations = 0
    observed = False
    for line in raw.splitlines():
        parts = line.split()
        if not parts:
            continue
        values: dict[str, int] = {}
        for item in parts[1:]:
            if '=' not in item:
                continue
            key, value = item.split('=', 1)
            try:
                values[key] = int(value)
            except ValueError:
                continue
        if 'rbytes' in values or 'rios' in values:
            observed = True
        read_bytes += values.get('rbytes', 0)
        read_operations += values.get('rios', 0)
    if not observed:
        raise RuntimeError('F-007 io.stat does not expose rbytes/rios counters')
    return read_bytes, read_operations


def _required_key(values: dict[str, int], key: str, source: str) -> int:
    try:
        return values[key]
    except KeyError as error:
        raise RuntimeError(f'F-007 required cgroup metric is missing: {source}:{key}') from error


def _percentile(values: tuple[float, ...], percentile: int) -> float:
    if not values:
        raise ValueError('percentile requires at least one value')
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((percentile / 100) * (len(ordered) - 1))))
    return float(ordered[index])
