from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

# El piloto mantiene el manifest 1.1, pero cambia la geometría temporal y la versión del generador.
_MANIFEST_VERSION = '1.1'
_ORIGIN_CLASS = 'controlled_synthetic_physical'
_DEFAULT_SIGNAL_COUNT = 1000
_DEFAULT_PI_DAILY_TARGET_BYTES = 200 * 1024
_DEFAULT_DISPATCH_DAY_TARGET_BYTES = 150 * 1024
_DEFAULT_DISPATCH_VALUE_COLUMNS = 24
_DEFAULT_SEED = 7007
_DEFAULT_DATE = date(2026, 8, 30)
_PI_DAILY_INTERVAL_SECONDS = 10
_PI_DAILY_ROWS = 24 * 60 * 60 // _PI_DAILY_INTERVAL_SECONDS
_DISPATCH_ROWS_PER_TURN = 256
_GENERATOR_VERSION = '0.2.0'
_DATASET_BANK_ID = 'f007-controlled-physical-temporal-pilot-v2'


# La configuración conserva sólo los parámetros que realmente pueden variar entre ejecuciones del piloto.
@dataclass(frozen=True, slots=True)
class PilotConfiguration:
    signal_count: int = _DEFAULT_SIGNAL_COUNT
    pi_daily_target_bytes: int = _DEFAULT_PI_DAILY_TARGET_BYTES
    dispatch_day_target_bytes: int = _DEFAULT_DISPATCH_DAY_TARGET_BYTES
    dispatch_value_columns: int = _DEFAULT_DISPATCH_VALUE_COLUMNS
    seed: int = _DEFAULT_SEED
    partition_date: date = _DEFAULT_DATE

    def __post_init__(self) -> None:
        if self.signal_count < 1000:
            raise ValueError('signal_count must be at least 1000')
        if self.pi_daily_target_bytes <= 0:
            raise ValueError('pi_daily_target_bytes must be greater than zero')
        if self.dispatch_day_target_bytes <= 0:
            raise ValueError('dispatch_day_target_bytes must be greater than zero')
        if self.dispatch_value_columns <= 0:
            raise ValueError('dispatch_value_columns must be greater than zero')
        if self.seed < 0:
            raise ValueError('seed must be greater than or equal to zero')
        if not isinstance(self.partition_date, date):
            raise TypeError('partition_date must be a date')


# Este resultado expresa la calibración por cantidad de señales con filas e intervalo temporal fijos.
@dataclass(frozen=True, slots=True)
class SignalCalibrationResult:
    signal_count: int
    row_count: int
    interval_seconds: int
    actual_bytes: int
    target_bytes: int
    attempts: tuple[dict[str, int], ...]

    @property
    def delta_bytes(self) -> int:
        return self.actual_bytes - self.target_bytes

    @property
    def delta_ratio(self) -> float:
        return self.delta_bytes / self.target_bytes


@dataclass(frozen=True, slots=True)
class PilotPaths:
    output_root: Path
    input_root: Path
    dataset_root: Path
    manifest_path: Path
    conformance_path: Path


# El orquestador genera el fixture fuera del benchmark medido y emite manifest + conformance.
class ControlledDatasetPilot:
    def __init__(self, *, paths: PilotPaths, configuration: PilotConfiguration) -> None:
        self._paths = paths
        self._configuration = configuration

    # Latest conserva 1000 señales físicas; Daily calibra sólo el subconjunto histórico.
    def run(self) -> dict[str, Any]:
        imports = _load_dataset_imports()
        self._prepare_output()
        store = imports['ParquetDatasetStore'](root=self._paths.dataset_root)
        runtime = imports['DatasetRuntime'](store=store)
        pi_definition = _pi_definition(imports)
        dispatch_definition = _dispatch_definition(imports)

        latest_target = pi_definition.resolve_target(materialization='latest')
        latest_table = _pi_table(
            imports,
            row_count=1,
            signal_count=self._configuration.signal_count,
            interval_seconds=_PI_DAILY_INTERVAL_SECONDS,
            seed=self._configuration.seed,
            start=_start_of_day(self._configuration.partition_date),
        )
        runtime.replace(definition=pi_definition, target=latest_target, data=latest_table)

        pi_calibration = self._calibrate_pi_daily_signals(
            imports=imports,
            definition=pi_definition,
        )

        for suffix, seed_offset in (('001', 200), ('002', 201)):
            target = dispatch_definition.resolve_target(
                materialization='shift',
                partition=_shift_partition(self._configuration.partition_date, suffix),
            )
            table = _dispatch_table(
                imports,
                row_count=_DISPATCH_ROWS_PER_TURN,
                value_columns=self._configuration.dispatch_value_columns,
                shift_id=_shift_id(self._configuration.partition_date, suffix),
                seed=self._configuration.seed + seed_offset,
            )
            runtime.replace(definition=dispatch_definition, target=target, data=table)

        inspection = _inspect_dataset_tree(
            imports=imports,
            input_root=self._paths.input_root,
            dataset_root=self._paths.dataset_root,
            projectable_signal_count=self._configuration.signal_count,
        )
        temporal_geometry = _inspect_pi_daily_temporal_geometry(
            imports=imports,
            input_root=self._paths.input_root,
            inspection=inspection,
        )
        determinism = self._verify_determinism(imports=imports)
        manifest = self._manifest(
            inspection=inspection,
            pi_calibration=pi_calibration,
            temporal_geometry=temporal_geometry,
            determinism=determinism,
        )
        _write_json(self._paths.manifest_path, manifest)
        conformance = self._conformance(
            manifest=manifest,
            determinism=determinism,
        )
        _write_json(self._paths.conformance_path, conformance)
        return {
            'manifest': str(self._paths.manifest_path),
            'conformance': str(self._paths.conformance_path),
            'aggregate_sha256': manifest['fingerprints']['aggregate_sha256'],
            'projectable_signal_count': self._configuration.signal_count,
            'pi_daily_signal_count': pi_calibration.signal_count,
            'pi_daily_rows': pi_calibration.row_count,
            'pi_daily_bytes': pi_calibration.actual_bytes,
            'dispatch_day_bytes': manifest['calibration']['dispatch_fixed']['day_actual_bytes'],
            'deterministic_rebuild_verified': determinism['verified'],
            'status': conformance['status'],
        }

    def _prepare_output(self) -> None:
        if self._paths.output_root.exists():
            raise FileExistsError(f'output directory already exists: {self._paths.output_root}')
        self._paths.dataset_root.mkdir(parents=True, exist_ok=False)

    # Daily siempre representa un día completo a 10 s; sólo cambia cuántas señales históricas contiene.
    def _calibrate_pi_daily_signals(
        self,
        *,
        imports: dict[str, Any],
        definition: Any,
    ) -> SignalCalibrationResult:
        target = definition.resolve_target(
            materialization='daily',
            partition=_day_partition(self._configuration.partition_date),
        )
        store = imports['ParquetDatasetStore'](root=self._paths.dataset_root)
        runtime = imports['DatasetRuntime'](store=store)
        path = store.path_for(definition=definition, target=target) / 'data.parquet'
        attempts: list[dict[str, int]] = []
        previous: tuple[int, int] | None = None

        for signal_count in _signal_calibration_candidates(self._configuration.signal_count):
            table = _pi_table(
                imports,
                row_count=_PI_DAILY_ROWS,
                signal_count=signal_count,
                interval_seconds=_PI_DAILY_INTERVAL_SECONDS,
                seed=self._configuration.seed + 100,
                start=_start_of_day(self._configuration.partition_date),
            )
            runtime.replace(definition=definition, target=target, data=table)
            actual_bytes = path.stat().st_size
            attempts.append({'signal_count': signal_count, 'bytes': actual_bytes})
            current = (signal_count, actual_bytes)
            if (
                actual_bytes >= self._configuration.pi_daily_target_bytes
                or signal_count == self._configuration.signal_count
            ):
                chosen = _nearest_calibration(
                    target_bytes=self._configuration.pi_daily_target_bytes,
                    left=previous,
                    right=current,
                )
                if chosen != current:
                    chosen_signal_count, _ = chosen
                    table = _pi_table(
                        imports,
                        row_count=_PI_DAILY_ROWS,
                        signal_count=chosen_signal_count,
                        interval_seconds=_PI_DAILY_INTERVAL_SECONDS,
                        seed=self._configuration.seed + 100,
                        start=_start_of_day(self._configuration.partition_date),
                    )
                    runtime.replace(definition=definition, target=target, data=table)
                    actual_bytes = path.stat().st_size
                    signal_count = chosen_signal_count
                return SignalCalibrationResult(
                    signal_count=signal_count,
                    row_count=_PI_DAILY_ROWS,
                    interval_seconds=_PI_DAILY_INTERVAL_SECONDS,
                    actual_bytes=actual_bytes,
                    target_bytes=self._configuration.pi_daily_target_bytes,
                    attempts=tuple(attempts),
                )
            previous = current

        raise RuntimeError('PI daily signal calibration produced no candidate')

    # La reconstrucción temporal debe producir exactamente los mismos bytes y hashes.
    def _verify_determinism(self, *, imports: dict[str, Any]) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix='f007-pilot-rebuild-') as temporary:
            rebuild_root = Path(temporary) / 'input'
            rebuild_dataset_root = rebuild_root / 'datasets'
            rebuild_dataset_root.mkdir(parents=True)
            rebuild_store = imports['ParquetDatasetStore'](root=rebuild_dataset_root)
            rebuild_runtime = imports['DatasetRuntime'](store=rebuild_store)
            pi_definition = _pi_definition(imports)
            dispatch_definition = _dispatch_definition(imports)

            source_files = _parquet_files(self._paths.input_root)
            for source_path in source_files:
                relative = source_path.relative_to(self._paths.dataset_root)
                definition, target, table = _rebuild_request_for_path(
                    imports=imports,
                    relative=relative,
                    configuration=self._configuration,
                    source_path=source_path,
                    pi_definition=pi_definition,
                    dispatch_definition=dispatch_definition,
                )
                rebuild_runtime.replace(definition=definition, target=target, data=table)

            original_entries = _file_hash_entries(self._paths.input_root)
            rebuilt_entries = _file_hash_entries(rebuild_root)
            return {
                'verified': original_entries == rebuilt_entries,
                'original_aggregate_sha256': _aggregate_fingerprint(original_entries),
                'rebuilt_aggregate_sha256': _aggregate_fingerprint(rebuilt_entries),
            }

    def _manifest(
        self,
        *,
        inspection: dict[str, Any],
        pi_calibration: SignalCalibrationResult,
        temporal_geometry: dict[str, Any],
        determinism: dict[str, Any],
    ) -> dict[str, Any]:
        dispatch_files = [
            item
            for item in inspection['files']
            if item['source'] == 'dispatch.std_shift_state'
        ]
        dispatch_day_actual = sum(item['bytes'] for item in dispatch_files)
        dispatch_delta_ratio = (
            dispatch_day_actual - self._configuration.dispatch_day_target_bytes
        ) / self._configuration.dispatch_day_target_bytes
        return {
            'manifest_version': _MANIFEST_VERSION,
            'dataset_bank_id': _DATASET_BANK_ID,
            'origin_class': _ORIGIN_CLASS,
            'operational_representative': False,
            'generated_at_utc': datetime.now(UTC).isoformat().replace('+00:00', 'Z'),
            'generator': {
                'name': 'performance.f007_dataset_pilot',
                'version': _GENERATOR_VERSION,
                'configuration': _configuration_document(self._configuration),
            },
            'mount_contract': {'container_path': '/f007/input', 'read_only': True},
            'profiles': {
                'WARM_FIXED': {
                    'authoritative_for_alarm_capacity': True,
                    'source': 'pi.interpolated',
                    'partition': 'latest',
                    'physical_signal_pool_size': self._configuration.signal_count,
                },
                'FIRST_TOUCH_PARTITIONED': {
                    'authoritative_for_alarm_capacity': False,
                    'pilot_only': True,
                    'pi_partition': 'daily',
                    'dispatch_partition': 'shift',
                    'final_window_count': 61,
                },
            },
            'temporal_contract': {
                'pi_daily_interval_seconds': _PI_DAILY_INTERVAL_SECONDS,
                'pi_daily_expected_rows': _PI_DAILY_ROWS,
                'dispatch_rows_per_turn': _DISPATCH_ROWS_PER_TURN,
            },
            'calibration': {
                'pi_daily': _signal_calibration_document(pi_calibration),
                'pi_daily_temporal_geometry': temporal_geometry,
                'dispatch_fixed': {
                    'row_count_per_turn': _DISPATCH_ROWS_PER_TURN,
                    'value_columns': self._configuration.dispatch_value_columns,
                    'day_target_bytes': self._configuration.dispatch_day_target_bytes,
                    'day_actual_bytes': dispatch_day_actual,
                    'day_delta_ratio': dispatch_delta_ratio,
                    'turns': [
                        {
                            'mount_relative_path': item['mount_relative_path'],
                            'bytes': item['bytes'],
                            'row_count': item['row_count'],
                            'column_count': item['column_count'],
                        }
                        for item in dispatch_files
                    ],
                },
            },
            'aggregate_physical': inspection['aggregate_physical'],
            'schemas': inspection['schemas'],
            'files': inspection['files'],
            'fingerprints': {
                'algorithm': 'sha256',
                'canonicalization': "sort by mount-relative path; '<path>\t<file_sha256>\n'",
                'aggregate_sha256': inspection['aggregate_sha256'],
            },
            'determinism': determinism,
            'limitations': [
                'No operational capture was available.',
                'Observed PI and Dispatch sizes are calibration anchors only.',
                'PI Latest and PI Daily intentionally use different signal counts.',
                'This pilot does not generate the final 61-window bank.',
                'This pilot does not freeze the final alarm-count ladder.',
            ],
        }

    def _conformance(
        self,
        *,
        manifest: dict[str, Any],
        determinism: dict[str, Any],
    ) -> dict[str, Any]:
        files = manifest['files']
        pi_daily = manifest['calibration']['pi_daily']
        temporal = manifest['calibration']['pi_daily_temporal_geometry']
        dispatch = manifest['calibration']['dispatch_fixed']
        checks = {
            'origin_class_is_controlled_synthetic_physical': manifest['origin_class']
            == _ORIGIN_CLASS,
            'operational_representative_is_false': manifest['operational_representative'] is False,
            'projectable_signal_count_established': manifest['aggregate_physical'][
                'projectable_signal_count'
            ]
            == self._configuration.signal_count,
            'pi_latest_exists': any(
                item['source'] == 'pi.interpolated' and item['materialization'] == 'latest'
                for item in files
            ),
            'pi_daily_exists': any(
                item['source'] == 'pi.interpolated' and item['materialization'] == 'daily'
                for item in files
            ),
            'pi_daily_signal_count_established': 1
            <= pi_daily['signal_count']
            <= self._configuration.signal_count,
            'pi_daily_row_count_is_8640': temporal['row_count'] == _PI_DAILY_ROWS,
            'pi_daily_spacing_is_10_seconds': temporal['spacing_verified'] is True,
            'pi_daily_full_day_span_verified': temporal['full_day_span_verified'] is True,
            'dispatch_two_shifts_exist': len(dispatch['turns']) == 2,
            'dispatch_geometry_is_fixed': all(
                item['row_count'] == _DISPATCH_ROWS_PER_TURN
                and item['column_count'] == self._configuration.dispatch_value_columns + 1
                for item in dispatch['turns']
            ),
            'all_file_hashes_present': all(bool(item['sha256']) for item in files),
            'all_row_group_metadata_present': all(item['row_group_count'] >= 1 for item in files),
            'aggregate_fingerprint_present': bool(manifest['fingerprints']['aggregate_sha256']),
            'deterministic_rebuild_verified': determinism['verified'],
        }
        status = 'PASS' if all(checks.values()) else 'FAIL'
        return {
            'report_version': _MANIFEST_VERSION,
            'dataset_bank_id': manifest['dataset_bank_id'],
            'status': status,
            'claims': {
                'physical_writer_conformance': status == 'PASS',
                'deterministic_fixture_identity': determinism['verified'],
                'operational_representativeness': False,
                'pi_daily_temporal_geometry_conformance': temporal['spacing_verified']
                and temporal['full_day_span_verified'],
            },
            'checks': checks,
            'size_anchor_calibration': {
                'pi_daily': pi_daily,
                'dispatch_fixed': dispatch,
            },
            'next_gate': (
                'Freeze the final 61-window bank geometry and alarm-count mapping before '
                'harness changes.'
            ),
        }


def _load_dataset_imports() -> dict[str, Any]:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq

        from atlanticus.datasets.layouts import SingleArtifactLayout
        from atlanticus.datasets.models import (
            DatasetDefinition,
            DatasetKey,
            MaterializationDefinition,
        )
        from atlanticus.datasets.parquet import ParquetDatasetStore
        from atlanticus.datasets.runtime import DatasetRuntime
    except ImportError as error:
        raise RuntimeError(
            'F-007 dataset pilot requires atlanticus-datasets-runtime and '
            'atlanticus-datasets-parquet. Run it with UV using the '
            'backend/datasets-runtime project.'
        ) from error
    return {
        'pa': pa,
        'pq': pq,
        'SingleArtifactLayout': SingleArtifactLayout,
        'DatasetDefinition': DatasetDefinition,
        'DatasetKey': DatasetKey,
        'MaterializationDefinition': MaterializationDefinition,
        'ParquetDatasetStore': ParquetDatasetStore,
        'DatasetRuntime': DatasetRuntime,
    }


def _pi_definition(imports: dict[str, Any]) -> Any:
    return imports['DatasetDefinition'](
        key=imports['DatasetKey'](namespace=('pi', 'not_pii'), name='interpolated'),
        materializations=(
            imports['MaterializationDefinition'](
                name='latest',
                layout=imports['SingleArtifactLayout'](),
            ),
            imports['MaterializationDefinition'](
                name='daily',
                layout=imports['SingleArtifactLayout'](),
                partition_dimensions=('year', 'month', 'day'),
            ),
        ),
    )


def _dispatch_definition(imports: dict[str, Any]) -> Any:
    return imports['DatasetDefinition'](
        key=imports['DatasetKey'](namespace=('dispatch',), name='std_shift_state'),
        materializations=(
            imports['MaterializationDefinition'](
                name='shift',
                layout=imports['SingleArtifactLayout'](),
                partition_dimensions=('year', 'month', 'day', 'turn'),
            ),
        ),
    )


# PI usa una columna timestamp y una columna float64 por señal. El intervalo se pasa explícitamente.
def _pi_table(
    imports: dict[str, Any],
    *,
    row_count: int,
    signal_count: int,
    interval_seconds: int,
    seed: int,
    start: datetime,
) -> Any:
    pa = imports['pa']
    timestamps = [
        start + timedelta(seconds=index * interval_seconds) for index in range(row_count)
    ]
    arrays = [pa.array(timestamps, type=pa.timestamp('us', tz='UTC'))]
    names = ['timestamp_utc']
    for signal_index in range(signal_count):
        values = [
            _deterministic_float(seed=seed, row_index=row_index, column_index=signal_index)
            for row_index in range(row_count)
        ]
        arrays.append(pa.array(values, type=pa.float64()))
        names.append(f'signal_{signal_index + 1:06d}')
    return pa.Table.from_arrays(arrays, names=names)


# Dispatch conserva la geometría piloto validada: 256 filas y 24 columnas de valor por turno.
def _dispatch_table(
    imports: dict[str, Any],
    *,
    row_count: int,
    value_columns: int,
    shift_id: int,
    seed: int,
) -> Any:
    pa = imports['pa']
    arrays = [pa.array([shift_id] * row_count, type=pa.int64())]
    names = ['shift_id']
    for column_index in range(value_columns):
        values = [
            _deterministic_float(seed=seed, row_index=row_index, column_index=column_index)
            for row_index in range(row_count)
        ]
        arrays.append(pa.array(values, type=pa.float64()))
        names.append(f'value_{column_index + 1:03d}')
    return pa.Table.from_arrays(arrays, names=names)


def _deterministic_float(*, seed: int, row_index: int, column_index: int) -> float:
    value = (
        seed * 1_000_003
        + row_index * 97_409
        + column_index * 65_537
        + row_index * column_index * 17
    ) % 10_000_019
    return value / 10_003.0


def _nearest_calibration(
    *,
    target_bytes: int,
    left: tuple[int, int] | None,
    right: tuple[int, int],
) -> tuple[int, int]:
    if left is None:
        return right
    left_distance = abs(left[1] - target_bytes)
    right_distance = abs(right[1] - target_bytes)
    return left if left_distance <= right_distance else right


# Se prueban potencias de dos y siempre se incluye el límite exacto del pool Latest.
def _signal_calibration_candidates(max_signal_count: int) -> tuple[int, ...]:
    candidates = []
    signal_count = 1
    while signal_count < max_signal_count:
        candidates.append(signal_count)
        signal_count *= 2
    if not candidates or candidates[-1] != max_signal_count:
        candidates.append(max_signal_count)
    return tuple(candidates)


def _inspect_dataset_tree(
    *,
    imports: dict[str, Any],
    input_root: Path,
    dataset_root: Path,
    projectable_signal_count: int,
) -> dict[str, Any]:
    pq = imports['pq']
    files = []
    schemas: dict[str, dict[str, Any]] = {}
    total_rows = 0
    total_row_groups = 0
    codecs: set[str] = set()
    for path in _parquet_files(input_root):
        parquet_file = pq.ParquetFile(path)
        metadata = parquet_file.metadata
        schema_document = _schema_document(parquet_file.schema_arrow)
        schema_sha256 = _json_sha256(schema_document)
        file_codecs = sorted(_parquet_codecs(metadata))
        codecs.update(file_codecs)
        source, materialization = _classify_path(path.relative_to(dataset_root))
        rows = metadata.num_rows
        row_groups = metadata.num_row_groups
        total_rows += rows
        total_row_groups += row_groups
        files.append(
            {
                'mount_relative_path': path.relative_to(input_root).as_posix(),
                'source': source,
                'materialization': materialization,
                'sha256': _sha256_file(path),
                'bytes': path.stat().st_size,
                'row_count': rows,
                'column_count': metadata.num_columns,
                'row_group_count': row_groups,
                'compression_codecs': file_codecs,
                'schema_fingerprint_sha256': schema_sha256,
            }
        )
        schemas[schema_sha256] = {
            'fingerprint_sha256': schema_sha256,
            'fields': schema_document,
        }
    files.sort(key=lambda item: item['mount_relative_path'])
    entries = tuple((item['mount_relative_path'], item['sha256']) for item in files)
    return {
        'files': files,
        'schemas': list(schemas.values()),
        'aggregate_sha256': _aggregate_fingerprint(entries),
        'aggregate_physical': {
            'file_count': len(files),
            'total_bytes': sum(item['bytes'] for item in files),
            'row_count_sum': total_rows,
            'row_group_count_total': total_row_groups,
            'compression_codecs': sorted(codecs),
            'projectable_signal_count': projectable_signal_count,
        },
    }


# Esta inspección lee únicamente timestamp_utc para demostrar 8640 slots consecutivos de 10 segundos.
def _inspect_pi_daily_temporal_geometry(
    *,
    imports: dict[str, Any],
    input_root: Path,
    inspection: dict[str, Any],
) -> dict[str, Any]:
    item = next(
        file
        for file in inspection['files']
        if file['source'] == 'pi.interpolated' and file['materialization'] == 'daily'
    )
    path = input_root / item['mount_relative_path']
    table = imports['pq'].ParquetFile(path).read(columns=['timestamp_utc'])
    timestamps = table.column(0).to_pylist()
    spacings = [
        int((current - previous).total_seconds())
        for previous, current in zip(timestamps, timestamps[1:], strict=False)
    ]
    first = timestamps[0]
    last = timestamps[-1]
    expected_last = first + timedelta(
        seconds=(_PI_DAILY_ROWS - 1) * _PI_DAILY_INTERVAL_SECONDS
    )
    return {
        'row_count': len(timestamps),
        'interval_seconds': _PI_DAILY_INTERVAL_SECONDS,
        'first_timestamp_utc': _utc_isoformat(first),
        'last_timestamp_utc': _utc_isoformat(last),
        'spacing_verified': bool(spacings)
        and all(value == _PI_DAILY_INTERVAL_SECONDS for value in spacings),
        'full_day_span_verified': len(timestamps) == _PI_DAILY_ROWS
        and first.hour == 0
        and first.minute == 0
        and first.second == 0
        and last == expected_last,
    }


# La reconstrucción deduce el signal_count real de Daily desde el Parquet generado.
def _rebuild_request_for_path(
    *,
    imports: dict[str, Any],
    relative: Path,
    configuration: PilotConfiguration,
    source_path: Path,
    pi_definition: Any,
    dispatch_definition: Any,
) -> tuple[Any, Any, Any]:
    parts = relative.parts
    if parts[:3] == ('pi', 'not_pii', 'interpolated'):
        if parts[3] == 'latest':
            target = pi_definition.resolve_target(materialization='latest')
            table = _pi_table(
                imports,
                row_count=1,
                signal_count=configuration.signal_count,
                interval_seconds=_PI_DAILY_INTERVAL_SECONDS,
                seed=configuration.seed,
                start=_start_of_day(configuration.partition_date),
            )
            return pi_definition, target, table
        partition = _partition_from_route(parts[4:7], expected=('year', 'month', 'day'))
        target = pi_definition.resolve_target(materialization='daily', partition=partition)
        row_count = _parquet_row_count(imports, source_path)
        signal_count = _parquet_column_count(imports, source_path) - 1
        table = _pi_table(
            imports,
            row_count=row_count,
            signal_count=signal_count,
            interval_seconds=_PI_DAILY_INTERVAL_SECONDS,
            seed=configuration.seed + 100,
            start=_start_of_day(configuration.partition_date),
        )
        return pi_definition, target, table
    if parts[:2] == ('dispatch', 'std_shift_state'):
        partition = _partition_from_route(parts[3:7], expected=('year', 'month', 'day', 'turn'))
        target = dispatch_definition.resolve_target(materialization='shift', partition=partition)
        row_count = _parquet_row_count(imports, source_path)
        suffix = partition['turn']
        table = _dispatch_table(
            imports,
            row_count=row_count,
            value_columns=configuration.dispatch_value_columns,
            shift_id=_shift_id(configuration.partition_date, suffix),
            seed=configuration.seed + (200 if suffix == '001' else 201),
        )
        return dispatch_definition, target, table
    raise ValueError(f'unsupported pilot parquet path: {relative.as_posix()}')


def _parquet_row_count(imports: dict[str, Any], path: Path) -> int:
    return imports['pq'].ParquetFile(path).metadata.num_rows


def _parquet_column_count(imports: dict[str, Any], path: Path) -> int:
    return imports['pq'].ParquetFile(path).metadata.num_columns


def _partition_from_route(parts: tuple[str, ...], *, expected: tuple[str, ...]) -> dict[str, str]:
    if len(parts) != len(expected):
        raise ValueError('partition route does not match expected dimensions')
    output = {}
    for segment, dimension in zip(parts, expected, strict=True):
        prefix = f'{dimension}='
        if not segment.startswith(prefix):
            raise ValueError('partition route does not match expected dimensions')
        output[dimension] = segment.removeprefix(prefix)
    return output


def _classify_path(relative: Path) -> tuple[str, str]:
    parts = relative.parts
    if parts[:3] == ('pi', 'not_pii', 'interpolated'):
        return 'pi.interpolated', parts[3]
    if parts[:2] == ('dispatch', 'std_shift_state'):
        return 'dispatch.std_shift_state', parts[2]
    return 'unknown', 'unknown'


def _schema_document(schema: Any) -> list[dict[str, Any]]:
    return [
        {'name': field.name, 'type': str(field.type), 'nullable': field.nullable}
        for field in schema
    ]


def _parquet_codecs(metadata: Any) -> set[str]:
    codecs = set()
    for row_group_index in range(metadata.num_row_groups):
        row_group = metadata.row_group(row_group_index)
        for column_index in range(row_group.num_columns):
            codecs.add(str(row_group.column(column_index).compression).lower())
    return codecs


def _file_hash_entries(root: Path) -> tuple[tuple[str, str], ...]:
    return tuple(
        (path.relative_to(root).as_posix(), _sha256_file(path))
        for path in _parquet_files(root)
    )


# El fingerprint agregado conserva la canonicalización congelada para F-007.
def _aggregate_fingerprint(entries: tuple[tuple[str, str], ...]) -> str:
    canonical = ''.join(f'{path}\t{digest}\n' for path, digest in sorted(entries))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _parquet_files(root: Path) -> tuple[Path, ...]:
    return tuple(sorted(path for path in root.rglob('*.parquet') if path.is_file()))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _json_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def _configuration_document(configuration: PilotConfiguration) -> dict[str, Any]:
    document = asdict(configuration)
    document['partition_date'] = configuration.partition_date.isoformat()
    return document


def _signal_calibration_document(result: SignalCalibrationResult) -> dict[str, Any]:
    return {
        'signal_count': result.signal_count,
        'row_count': result.row_count,
        'interval_seconds': result.interval_seconds,
        'actual_bytes': result.actual_bytes,
        'target_bytes': result.target_bytes,
        'delta_bytes': result.delta_bytes,
        'delta_ratio': result.delta_ratio,
        'attempts': list(result.attempts),
    }


def _day_partition(value: date) -> dict[str, str]:
    return {'year': f'{value.year:04d}', 'month': f'{value.month:02d}', 'day': f'{value.day:02d}'}


def _shift_partition(value: date, suffix: str) -> dict[str, str]:
    if suffix not in {'001', '002'}:
        raise ValueError('shift suffix must be 001 or 002')
    return {**_day_partition(value), 'turn': suffix}


def _shift_id(value: date, suffix: str) -> int:
    if suffix not in {'001', '002'}:
        raise ValueError('shift suffix must be 001 or 002')
    return int(f'{value:%y%m%d}{suffix}')


def _start_of_day(value: date) -> datetime:
    return datetime(value.year, value.month, value.day, tzinfo=UTC)


def _utc_isoformat(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace('+00:00', 'Z')


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def _paths(output_root: Path) -> PilotPaths:
    return PilotPaths(
        output_root=output_root,
        input_root=output_root / 'input',
        dataset_root=output_root / 'input' / 'datasets',
        manifest_path=output_root / 'f007-pilot-manifest.json',
        conformance_path=output_root / 'f007-pilot-conformance.json',
    )


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError('partition date must use YYYY-MM-DD') from error


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Generate the F-007 controlled physical dataset pilot.'
    )
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--signal-count', type=int, default=_DEFAULT_SIGNAL_COUNT)
    parser.add_argument('--pi-daily-target-bytes', type=int, default=_DEFAULT_PI_DAILY_TARGET_BYTES)
    parser.add_argument(
        '--dispatch-day-target-bytes',
        type=int,
        default=_DEFAULT_DISPATCH_DAY_TARGET_BYTES,
    )
    parser.add_argument(
        '--dispatch-value-columns',
        type=int,
        default=_DEFAULT_DISPATCH_VALUE_COLUMNS,
    )
    parser.add_argument('--seed', type=int, default=_DEFAULT_SEED)
    parser.add_argument('--partition-date', type=_parse_date, default=_DEFAULT_DATE)
    parser.add_argument('--replace', action='store_true')
    return parser.parse_args(argv)


# El CLI sigue siendo sólo una herramienta offline: no expone Phase B ni ladder de alarmas.
def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    output_root = args.output_dir.resolve()
    if args.replace and output_root.exists():
        shutil.rmtree(output_root)
    configuration = PilotConfiguration(
        signal_count=args.signal_count,
        pi_daily_target_bytes=args.pi_daily_target_bytes,
        dispatch_day_target_bytes=args.dispatch_day_target_bytes,
        dispatch_value_columns=args.dispatch_value_columns,
        seed=args.seed,
        partition_date=args.partition_date,
    )
    result = ControlledDatasetPilot(
        paths=_paths(output_root),
        configuration=configuration,
    ).run()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['status'] == 'PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
