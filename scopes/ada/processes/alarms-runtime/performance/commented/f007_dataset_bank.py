from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from statistics import median
from typing import Any

_MANIFEST_VERSION = '1.1'
_ORIGIN_CLASS = 'controlled_synthetic_physical'
_GENERATOR_VERSION = '0.3.0'
_DATASET_BANK_ID = 'f007-controlled-physical-bank-v1'
_PHYSICAL_SIGNAL_POOL_SIZE = 1000
_PI_DAILY_SIGNAL_COUNT = 2
_PI_DAILY_INTERVAL_SECONDS = 10
_PI_DAILY_ROWS = 24 * 60 * 60 // _PI_DAILY_INTERVAL_SECONDS
_DISPATCH_ROWS_PER_TURN = 256
_DISPATCH_VALUE_COLUMNS = 24
_WINDOW_COUNT = 61
_WINDOW_STRIDE_DAYS = 8
_WINDOW_DAYS = 7
_WINDOW_AS_OF_UTC_HOUR = 16
_BASE_WINDOW_DATE = date(2026, 8, 30)
_SEED = 7007
_SAMPLE_REBUILD_WINDOWS = (0, 30, 60)

# La geometría queda congelada a partir del piloto temporal 0.2.0. El generador final no
# expone knobs que puedan alterar silenciosamente la evidencia física de la campaña.


@dataclass(frozen=True, slots=True)
class BankConfiguration:
    physical_signal_pool_size: int = _PHYSICAL_SIGNAL_POOL_SIZE
    pi_daily_signal_count: int = _PI_DAILY_SIGNAL_COUNT
    pi_daily_interval_seconds: int = _PI_DAILY_INTERVAL_SECONDS
    pi_daily_rows: int = _PI_DAILY_ROWS
    dispatch_rows_per_turn: int = _DISPATCH_ROWS_PER_TURN
    dispatch_value_columns: int = _DISPATCH_VALUE_COLUMNS
    window_count: int = _WINDOW_COUNT
    window_stride_days: int = _WINDOW_STRIDE_DAYS
    window_days: int = _WINDOW_DAYS
    base_window_date: date = _BASE_WINDOW_DATE
    window_as_of_utc_hour: int = _WINDOW_AS_OF_UTC_HOUR
    seed: int = _SEED

    def __post_init__(self) -> None:
        if self.physical_signal_pool_size != _PHYSICAL_SIGNAL_POOL_SIZE:
            raise ValueError('physical_signal_pool_size must remain 1000')
        if self.pi_daily_signal_count != _PI_DAILY_SIGNAL_COUNT:
            raise ValueError('pi_daily_signal_count must remain 2')
        if self.pi_daily_interval_seconds != _PI_DAILY_INTERVAL_SECONDS:
            raise ValueError('pi_daily_interval_seconds must remain 10')
        if self.pi_daily_rows != _PI_DAILY_ROWS:
            raise ValueError('pi_daily_rows must remain 8640')
        if self.dispatch_rows_per_turn != _DISPATCH_ROWS_PER_TURN:
            raise ValueError('dispatch_rows_per_turn must remain 256')
        if self.dispatch_value_columns != _DISPATCH_VALUE_COLUMNS:
            raise ValueError('dispatch_value_columns must remain 24')
        if self.window_count != _WINDOW_COUNT:
            raise ValueError('window_count must remain 61')
        if self.window_stride_days != _WINDOW_STRIDE_DAYS:
            raise ValueError('window_stride_days must remain 8')
        if self.window_days != _WINDOW_DAYS:
            raise ValueError('window_days must remain 7')
        if self.window_as_of_utc_hour != _WINDOW_AS_OF_UTC_HOUR:
            raise ValueError('window_as_of_utc_hour must remain 16')
        if not isinstance(self.base_window_date, date):
            raise TypeError('base_window_date must be a date')
        if self.seed < 0:
            raise ValueError('seed must be greater than or equal to zero')


@dataclass(frozen=True, slots=True)
class BankPaths:
    output_root: Path
    input_root: Path
    dataset_root: Path
    manifest_path: Path
    conformance_path: Path


@dataclass(frozen=True, slots=True)
class WindowPlan:
    index: int
    as_of_utc: datetime
    pi_dates: tuple[date, ...]
    dispatch_turns: tuple[tuple[date, str], ...]


@dataclass(frozen=True, slots=True)
class FileSpec:
    source: str
    materialization: str
    partition_date: date | None = None
    turn: str | None = None


# El generador materializa el banco fuera de la medición. El harness posterior sólo montará
# este input como read-only y recorrerá los perfiles definidos en el manifest.
class ControlledDatasetBankGenerator:
    def __init__(self, *, paths: BankPaths, configuration: BankConfiguration) -> None:
        self._paths = paths
        self._configuration = configuration

    def run(self) -> dict[str, Any]:
        imports = _load_runtime_imports()
        self._prepare_output()
        definitions = _source_definitions(imports)
        windows = _build_window_plans(imports=imports, configuration=self._configuration)
        specs_by_path = _build_file_specs(
            imports=imports,
            definitions=definitions,
            windows=windows,
            configuration=self._configuration,
        )
        store = imports['ParquetDatasetStore'](root=self._paths.dataset_root)
        runtime = imports['DatasetRuntime'](store=store)
        for relative_path, spec in sorted(specs_by_path.items()):
            _write_spec(
                imports=imports,
                runtime=runtime,
                definitions=definitions,
                configuration=self._configuration,
                spec=spec,
                expected_relative_path=relative_path,
                dataset_root=self._paths.dataset_root,
            )

        inspection = _inspect_dataset_tree(
            imports=imports,
            input_root=self._paths.input_root,
            dataset_root=self._paths.dataset_root,
        )
        window_documents = _window_documents(
            windows=windows,
            inspection=inspection,
            dataset_root=self._paths.dataset_root,
            definitions=definitions,
            imports=imports,
        )
        determinism = self._verify_sample_determinism(
            imports=imports,
            definitions=definitions,
            windows=windows,
            specs_by_path=specs_by_path,
            inspection=inspection,
        )
        manifest = _manifest(
            configuration=self._configuration,
            inspection=inspection,
            windows=window_documents,
            determinism=determinism,
        )
        _write_json(self._paths.manifest_path, manifest)
        conformance = _conformance(manifest=manifest)
        _write_json(self._paths.conformance_path, conformance)
        return {
            'manifest': str(self._paths.manifest_path),
            'conformance': str(self._paths.conformance_path),
            'aggregate_sha256': manifest['fingerprints']['aggregate_sha256'],
            'bank_sha256': manifest['fingerprints']['bank_sha256'],
            'file_count': manifest['aggregate_physical']['file_count'],
            'window_count': len(manifest['windows']),
            'partitioned_working_set_bytes': manifest['aggregate_physical'][
                'partitioned_working_set_bytes'
            ],
            'sample_deterministic_rebuild_verified': manifest['determinism']['verified'],
            'status': conformance['status'],
        }

    def _prepare_output(self) -> None:
        if self._paths.output_root.exists():
            raise FileExistsError(f'output directory already exists: {self._paths.output_root}')
        self._paths.dataset_root.mkdir(parents=True, exist_ok=False)

    def _verify_sample_determinism(
        self,
        *,
        imports: dict[str, Any],
        definitions: dict[str, Any],
        windows: tuple[WindowPlan, ...],
        specs_by_path: dict[str, FileSpec],
        inspection: dict[str, Any],
    ) -> dict[str, Any]:
        selected_paths = {'datasets/pi/not_pii/interpolated/latest/data.parquet'}
        window_map = {window.index: window for window in windows}
        for index in _SAMPLE_REBUILD_WINDOWS:
            selected_paths.update(
                _window_relative_paths(
                    window=window_map[index],
                    definitions=definitions,
                    imports=imports,
                )
            )
        source_hashes = {
            item['mount_relative_path']: item['sha256']
            for item in inspection['files']
            if item['mount_relative_path'] in selected_paths
        }
        with tempfile.TemporaryDirectory(prefix='f007-bank-rebuild-') as temporary:
            rebuild_input_root = Path(temporary) / 'input'
            rebuild_dataset_root = rebuild_input_root / 'datasets'
            rebuild_dataset_root.mkdir(parents=True)
            rebuild_store = imports['ParquetDatasetStore'](root=rebuild_dataset_root)
            rebuild_runtime = imports['DatasetRuntime'](store=rebuild_store)
            for relative_path in sorted(selected_paths):
                _write_spec(
                    imports=imports,
                    runtime=rebuild_runtime,
                    definitions=definitions,
                    configuration=self._configuration,
                    spec=specs_by_path[relative_path],
                    expected_relative_path=relative_path,
                    dataset_root=rebuild_dataset_root,
                )
            rebuilt_hashes = {
                path.relative_to(rebuild_input_root).as_posix(): _sha256_file(path)
                for path in _parquet_files(rebuild_input_root)
            }
        return {
            'verified': source_hashes == rebuilt_hashes,
            'sample_windows': list(_SAMPLE_REBUILD_WINDOWS),
            'sample_file_count': len(selected_paths),
            'source_sample_sha256': _aggregate_fingerprint(tuple(source_hashes.items())),
            'rebuilt_sample_sha256': _aggregate_fingerprint(tuple(rebuilt_hashes.items())),
        }


def _load_runtime_imports() -> dict[str, Any]:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq

        from ada.data.core import (
            DataPartition,
            DataSource,
            DataSourceView,
            ShiftScope,
            ShiftSelection,
            TimeWindow,
            TimeWindowUnit,
        )
        from ada.data.sources import (
            MineShiftResolver,
            PiSourceProvider,
            build_current_source_registry,
        )
        from atlanticus.datasets.parquet import ParquetDatasetStore
        from atlanticus.datasets.runtime import DatasetRuntime
    except ImportError as error:
        raise RuntimeError(
            'F-007 final dataset bank generator requires the alarm-runtime data contracts plus '
            'atlanticus-datasets-runtime and atlanticus-datasets-parquet. Run it with UV using '
            'the backend dataset projects.'
        ) from error
    return {
        'pa': pa,
        'pq': pq,
        'DataPartition': DataPartition,
        'DataSource': DataSource,
        'DataSourceView': DataSourceView,
        'ShiftScope': ShiftScope,
        'ShiftSelection': ShiftSelection,
        'TimeWindow': TimeWindow,
        'TimeWindowUnit': TimeWindowUnit,
        'MineShiftResolver': MineShiftResolver,
        'PiSourceProvider': PiSourceProvider,
        'build_current_source_registry': build_current_source_registry,
        'ParquetDatasetStore': ParquetDatasetStore,
        'DatasetRuntime': DatasetRuntime,
    }


def _source_definitions(imports: dict[str, Any]) -> dict[str, Any]:
    registry = imports['build_current_source_registry'](
        pi_source=imports['PiSourceProvider'].NOTPII
    )
    pi_binding, _ = registry.get_view(
        imports['DataSourceView'](
            source=imports['DataSource'].PI_INTERPOLATED,
            partition=imports['DataPartition'].LATEST,
        )
    )
    dispatch_binding, _ = registry.get_view(
        imports['DataSourceView'](
            source=imports['DataSource'].DISPATCH_STD_SHIFT_STATE,
            partition=imports['DataPartition'].SHIFT,
        )
    )
    return {
        'pi.interpolated': pi_binding.definition,
        'dispatch.std_shift_state': dispatch_binding.definition,
    }


# Cada ventana usa el contrato real de TimeWindow y MineShiftResolver. El as_of de las
# ventanas se fija a 16:00 UTC para caer dentro del turno 002 de la mina.
def _build_window_plans(
    *, imports: dict[str, Any], configuration: BankConfiguration
) -> tuple[WindowPlan, ...]:
    time_window = imports['TimeWindow'](_WINDOW_DAYS, imports['TimeWindowUnit'].DAYS)
    shift_selection = imports['ShiftSelection'](imports['ShiftScope'].DAYS, days=_WINDOW_DAYS)
    shift_resolver = imports['MineShiftResolver']()
    output = []
    for index in range(configuration.window_count):
        as_of_utc = _window_as_of(configuration=configuration, index=index)
        start_utc = time_window.start_from(as_of_utc)
        pi_dates = _inclusive_dates(start_utc.date(), as_of_utc.date())
        resolved_turns = shift_resolver.resolve(selection=shift_selection, as_of=as_of_utc)
        dispatch_turns = tuple((turn.nominal_date, turn.shift_suffix) for turn in resolved_turns)
        if len(pi_dates) != 8:
            raise RuntimeError(
                f'PI window {index} expected 8 daily partitions, got {len(pi_dates)}'
            )
        if len(dispatch_turns) != 14:
            raise RuntimeError(
                f'Dispatch window {index} expected 14 shift partitions, got {len(dispatch_turns)}'
            )
        if dispatch_turns[-1] != (as_of_utc.date(), '002'):
            raise RuntimeError(f'Dispatch window {index} is not pinned to turn 002')
        output.append(
            WindowPlan(
                index=index,
                as_of_utc=as_of_utc,
                pi_dates=pi_dates,
                dispatch_turns=dispatch_turns,
            )
        )
    return tuple(output)


def _window_as_of(*, configuration: BankConfiguration, index: int) -> datetime:
    if not 0 <= index < configuration.window_count:
        raise ValueError('window index is outside the configured bank')
    window_date = configuration.base_window_date - timedelta(
        days=index * configuration.window_stride_days
    )
    return datetime.combine(
        window_date,
        time(hour=configuration.window_as_of_utc_hour),
        tzinfo=UTC,
    )


def _inclusive_dates(start: date, end: date) -> tuple[date, ...]:
    if start > end:
        raise ValueError('start date must not be after end date')
    return tuple(start + timedelta(days=offset) for offset in range((end - start).days + 1))


# Primero se resuelve el conjunto completo de paths. Si dos ventanas reutilizaran una
# partición, el conteo único dejaría de coincidir con 1 + 61 * (8 + 14) y el gate fallaría.
def _build_file_specs(
    *,
    imports: dict[str, Any],
    definitions: dict[str, Any],
    windows: tuple[WindowPlan, ...],
    configuration: BankConfiguration,
) -> dict[str, FileSpec]:
    specs: dict[str, FileSpec] = {}
    latest_spec = FileSpec(source='pi.interpolated', materialization='latest')
    latest_path = _path_for_spec(
        imports=imports,
        definitions=definitions,
        dataset_root=Path('datasets'),
        spec=latest_spec,
    )
    specs[latest_path.as_posix()] = latest_spec
    for window in windows:
        for value in window.pi_dates:
            spec = FileSpec(
                source='pi.interpolated',
                materialization='daily',
                partition_date=value,
            )
            relative = _path_for_spec(
                imports=imports,
                definitions=definitions,
                dataset_root=Path('datasets'),
                spec=spec,
            )
            _register_spec(specs=specs, path=relative.as_posix(), spec=spec)
        for value, turn in window.dispatch_turns:
            spec = FileSpec(
                source='dispatch.std_shift_state',
                materialization='shift',
                partition_date=value,
                turn=turn,
            )
            relative = _path_for_spec(
                imports=imports,
                definitions=definitions,
                dataset_root=Path('datasets'),
                spec=spec,
            )
            _register_spec(specs=specs, path=relative.as_posix(), spec=spec)
    expected = 1 + configuration.window_count * (8 + 14)
    if len(specs) != expected:
        raise RuntimeError(f'final bank expected {expected} unique files, got {len(specs)}')
    return specs


def _register_spec(*, specs: dict[str, FileSpec], path: str, spec: FileSpec) -> None:
    existing = specs.get(path)
    if existing is not None and existing != spec:
        raise RuntimeError(f'conflicting dataset specification for path: {path}')
    specs[path] = spec


def _path_for_spec(
    *,
    imports: dict[str, Any],
    definitions: dict[str, Any],
    dataset_root: Path,
    spec: FileSpec,
) -> Path:
    store = imports['ParquetDatasetStore'](root=dataset_root)
    definition = definitions[spec.source]
    target = _target_for_spec(definition=definition, spec=spec)
    return store.path_for(definition=definition, target=target) / 'data.parquet'


def _target_for_spec(*, definition: Any, spec: FileSpec) -> Any:
    if spec.materialization == 'latest':
        return definition.resolve_target(materialization='latest')
    if spec.partition_date is None:
        raise ValueError('partitioned dataset spec requires partition_date')
    if spec.materialization == 'daily':
        return definition.resolve_target(
            materialization='daily',
            partition=_day_partition(spec.partition_date),
        )
    if spec.materialization == 'shift':
        if spec.turn is None:
            raise ValueError('shift dataset spec requires turn')
        return definition.resolve_target(
            materialization='shift',
            partition=_shift_partition(spec.partition_date, spec.turn),
        )
    raise ValueError(f'unsupported dataset materialization: {spec.materialization}')


# Todos los Parquet se escriben por DatasetRuntime -> ParquetDatasetStore. No existe un
# writer alternativo dentro del benchmark ni acceso directo desde Alarm Runtime.
def _write_spec(
    *,
    imports: dict[str, Any],
    runtime: Any,
    definitions: dict[str, Any],
    configuration: BankConfiguration,
    spec: FileSpec,
    expected_relative_path: str,
    dataset_root: Path,
) -> None:
    definition = definitions[spec.source]
    target = _target_for_spec(definition=definition, spec=spec)
    if spec.materialization == 'latest':
        table = _pi_table(
            imports=imports,
            row_count=1,
            signal_count=configuration.physical_signal_pool_size,
            start=datetime.combine(configuration.base_window_date, time(), tzinfo=UTC),
            seed=configuration.seed,
        )
    elif spec.materialization == 'daily':
        assert spec.partition_date is not None
        table = _pi_table(
            imports=imports,
            row_count=configuration.pi_daily_rows,
            signal_count=configuration.pi_daily_signal_count,
            start=datetime.combine(spec.partition_date, time(), tzinfo=UTC),
            seed=_seed_for_date(configuration.seed + 100_000, spec.partition_date),
        )
    else:
        assert spec.partition_date is not None and spec.turn is not None
        table = _dispatch_table(
            imports=imports,
            row_count=configuration.dispatch_rows_per_turn,
            value_columns=configuration.dispatch_value_columns,
            shift_id=_shift_id(spec.partition_date, spec.turn),
            seed=_seed_for_shift(configuration.seed + 200_000, spec.partition_date, spec.turn),
        )
    runtime.replace(definition=definition, target=target, data=table)
    actual_path = _path_for_spec(
        imports=imports,
        definitions=definitions,
        dataset_root=dataset_root,
        spec=spec,
    )
    relative = actual_path.as_posix()
    if not relative.endswith(expected_relative_path):
        raise RuntimeError(
            f'written dataset path does not match frozen route: {relative} != '
            f'{expected_relative_path}'
        )


def _pi_table(
    *, imports: dict[str, Any], row_count: int, signal_count: int, start: datetime, seed: int
) -> Any:
    pa = imports['pa']
    timestamps = [
        start + timedelta(seconds=row_index * _PI_DAILY_INTERVAL_SECONDS)
        for row_index in range(row_count)
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


def _dispatch_table(
    *,
    imports: dict[str, Any],
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


def _seed_for_date(seed: int, value: date) -> int:
    return seed + value.toordinal() * 17


def _seed_for_shift(seed: int, value: date, turn: str) -> int:
    return _seed_for_date(seed, value) + int(turn)


def _window_relative_paths(
    *,
    window: WindowPlan,
    definitions: dict[str, Any],
    imports: dict[str, Any],
) -> tuple[str, ...]:
    paths = []
    for value in window.pi_dates:
        spec = FileSpec(source='pi.interpolated', materialization='daily', partition_date=value)
        paths.append(
            _path_for_spec(
                imports=imports,
                definitions=definitions,
                dataset_root=Path('datasets'),
                spec=spec,
            ).as_posix()
        )
    for value, turn in window.dispatch_turns:
        spec = FileSpec(
            source='dispatch.std_shift_state',
            materialization='shift',
            partition_date=value,
            turn=turn,
        )
        paths.append(
            _path_for_spec(
                imports=imports,
                definitions=definitions,
                dataset_root=Path('datasets'),
                spec=spec,
            ).as_posix()
        )
    return tuple(paths)


# El fingerprint de cada ventana identifica exactamente los 22 archivos que el perfil
# particionado debe pedir. El replay caliente reutiliza estos mismos documentos y orden.
def _window_documents(
    *,
    windows: tuple[WindowPlan, ...],
    inspection: dict[str, Any],
    dataset_root: Path,
    definitions: dict[str, Any],
    imports: dict[str, Any],
) -> list[dict[str, Any]]:
    del dataset_root
    hashes = {item['mount_relative_path']: item['sha256'] for item in inspection['files']}
    documents = []
    seen_paths: set[str] = set()
    for window in windows:
        paths = _window_relative_paths(window=window, definitions=definitions, imports=imports)
        duplicates = tuple(path for path in paths if path in seen_paths)
        if duplicates:
            raise RuntimeError(f'partition window reuses frozen paths: {duplicates}')
        seen_paths.update(paths)
        entries = tuple((path, hashes[path]) for path in paths)
        documents.append(
            {
                'window_index': window.index,
                'as_of_utc': _utc_isoformat(window.as_of_utc),
                'pi_daily_target_count': len(window.pi_dates),
                'dispatch_shift_target_count': len(window.dispatch_turns),
                'target_count': len(paths),
                'target_paths': list(paths),
                'window_fingerprint_sha256': _aggregate_fingerprint(entries),
            }
        )
    return documents


# La inspección se hace sobre los bytes realmente materializados: hashes, codecs, row
# groups, tamaños y schema. Los valores sintéticos nunca se usan como evidencia operacional.
def _inspect_dataset_tree(
    *, imports: dict[str, Any], input_root: Path, dataset_root: Path
) -> dict[str, Any]:
    pq = imports['pq']
    files = []
    schemas: dict[str, dict[str, Any]] = {}
    codecs: set[str] = set()
    for path in _parquet_files(input_root):
        parquet_file = pq.ParquetFile(path)
        metadata = parquet_file.metadata
        schema_document = _schema_document(parquet_file.schema_arrow)
        schema_sha256 = _json_sha256(schema_document)
        file_codecs = sorted(_parquet_codecs(metadata))
        codecs.update(file_codecs)
        source, materialization = _classify_path(path.relative_to(dataset_root))
        files.append(
            {
                'mount_relative_path': path.relative_to(input_root).as_posix(),
                'source': source,
                'materialization': materialization,
                'sha256': _sha256_file(path),
                'bytes': path.stat().st_size,
                'row_count': metadata.num_rows,
                'column_count': metadata.num_columns,
                'row_group_count': metadata.num_row_groups,
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
    daily_files = [item for item in files if item['materialization'] == 'daily']
    dispatch_files = [item for item in files if item['materialization'] == 'shift']
    latest_files = [item for item in files if item['materialization'] == 'latest']
    _validate_pi_daily_files(imports=imports, input_root=input_root, files=daily_files)
    return {
        'files': files,
        'schemas': list(schemas.values()),
        'aggregate_sha256': _aggregate_fingerprint(entries),
        'aggregate_physical': {
            'file_count': len(files),
            'total_bytes': sum(item['bytes'] for item in files),
            'partitioned_working_set_bytes': sum(
                item['bytes'] for item in files if item['materialization'] != 'latest'
            ),
            'pi_latest_file_count': len(latest_files),
            'pi_daily_file_count': len(daily_files),
            'dispatch_shift_file_count': len(dispatch_files),
            'row_group_count_total': sum(item['row_group_count'] for item in files),
            'compression_codecs': sorted(codecs),
            'physical_signal_pool_size': _PHYSICAL_SIGNAL_POOL_SIZE,
        },
        'size_distributions': {
            'pi_latest_bytes': _size_distribution(latest_files),
            'pi_daily_bytes': _size_distribution(daily_files),
            'dispatch_shift_bytes': _size_distribution(dispatch_files),
        },
    }


def _validate_pi_daily_files(
    *, imports: dict[str, Any], input_root: Path, files: list[dict[str, Any]]
) -> None:
    for item in files:
        path = input_root / item['mount_relative_path']
        timestamps = imports['pq'].ParquetFile(path).read(columns=['timestamp_utc']).column(0)
        if len(timestamps) != _PI_DAILY_ROWS:
            raise RuntimeError(f'PI daily row count mismatch: {item["mount_relative_path"]}')
        values = timestamps.to_pylist()
        first = values[0]
        last = values[-1]
        if first.hour != 0 or first.minute != 0 or first.second != 0:
            raise RuntimeError(f'PI daily start timestamp mismatch: {item["mount_relative_path"]}')
        if any(
            int((current - previous).total_seconds()) != _PI_DAILY_INTERVAL_SECONDS
            for previous, current in zip(values, values[1:], strict=False)
        ):
            raise RuntimeError(
                f'PI daily timestamp spacing mismatch: {item["mount_relative_path"]}'
            )
        expected_last = first + timedelta(seconds=(_PI_DAILY_ROWS - 1) * _PI_DAILY_INTERVAL_SECONDS)
        if last != expected_last:
            raise RuntimeError(f'PI daily end timestamp mismatch: {item["mount_relative_path"]}')


def _size_distribution(files: list[dict[str, Any]]) -> dict[str, int]:
    if not files:
        return {'min_bytes': 0, 'median_bytes': 0, 'max_bytes': 0}
    sizes = sorted(item['bytes'] for item in files)
    return {
        'min_bytes': sizes[0],
        'median_bytes': int(median(sizes)),
        'max_bytes': sizes[-1],
    }


def _manifest(
    *,
    configuration: BankConfiguration,
    inspection: dict[str, Any],
    windows: list[dict[str, Any]],
    determinism: dict[str, Any],
) -> dict[str, Any]:
    window_entries = tuple(
        (f'{item["window_index"]:03d}', item['window_fingerprint_sha256']) for item in windows
    )
    return {
        'manifest_version': _MANIFEST_VERSION,
        'dataset_bank_id': _DATASET_BANK_ID,
        'origin_class': _ORIGIN_CLASS,
        'operational_representative': False,
        'generated_at_utc': datetime.now(UTC).isoformat().replace('+00:00', 'Z'),
        'generator': {
            'name': 'performance.f007_dataset_bank',
            'version': _GENERATOR_VERSION,
            'configuration': _configuration_document(configuration),
        },
        'mount_contract': {'container_path': '/f007/input', 'read_only': True},
        'profiles': {
            'WARM_FIXED': {
                'authoritative_for_alarm_capacity': True,
                'source': 'pi.interpolated',
                'partition': 'latest',
                'physical_signal_pool_size': configuration.physical_signal_pool_size,
            },
            'FIRST_TOUCH_PARTITIONED': {
                'authoritative_for_alarm_capacity': False,
                'window_count': configuration.window_count,
                'window_stride_days': configuration.window_stride_days,
                'pi_daily_targets_per_window': 8,
                'dispatch_shift_targets_per_window': 14,
                'window_order': [item['window_index'] for item in windows],
            },
            'WARM_REPLAY_PARTITIONED': {
                'authoritative_for_alarm_capacity': False,
                'replay_of': 'FIRST_TOUCH_PARTITIONED',
                'same_window_order': True,
                'window_order': [item['window_index'] for item in windows],
            },
        },
        'alarm_signal_mapping': {
            'strategy': 'round_robin_modulo',
            'physical_signal_pool_size': configuration.physical_signal_pool_size,
            'formula': 'signal_ordinal = ((alarm_ordinal - 1) % 1000) + 1',
            'alarm_count_ceiling': None,
            'ladder_frozen': False,
        },
        'temporal_contract': {
            'pi_daily_signal_count': configuration.pi_daily_signal_count,
            'pi_daily_interval_seconds': configuration.pi_daily_interval_seconds,
            'pi_daily_rows': configuration.pi_daily_rows,
            'dispatch_rows_per_turn': configuration.dispatch_rows_per_turn,
            'dispatch_value_columns': configuration.dispatch_value_columns,
            'window_days': configuration.window_days,
            'window_count': configuration.window_count,
            'window_stride_days': configuration.window_stride_days,
            'window_as_of_utc_hour': configuration.window_as_of_utc_hour,
        },
        'aggregate_physical': inspection['aggregate_physical'],
        'size_distributions': inspection['size_distributions'],
        'schemas': inspection['schemas'],
        'windows': windows,
        'files': inspection['files'],
        'fingerprints': {
            'algorithm': 'sha256',
            'file_aggregate_canonicalization': (
                "sort by mount-relative path; '<path>\\t<file_sha256>\\n'"
            ),
            'aggregate_sha256': inspection['aggregate_sha256'],
            'bank_window_canonicalization': (
                "sort by window index; '<window_index>\\t<window_fingerprint_sha256>\\n'"
            ),
            'bank_sha256': _aggregate_fingerprint(window_entries),
        },
        'determinism': determinism,
        'limitations': [
            'No operational capture was available.',
            'Observed PI and Dispatch sizes are calibration anchors only.',
            'FIRST_TOUCH means first benchmark request for a path, not guaranteed cold kernel cache.',
            'The final alarm-count ladder remains separate from the frozen alarm-to-signal mapping.',
        ],
    }


# Este gate prueba identidad y conformidad física del fixture. No afirma cold cache ni
# representatividad operacional.
def _conformance(*, manifest: dict[str, Any]) -> dict[str, Any]:
    files = manifest['files']
    windows = manifest['windows']
    aggregate = manifest['aggregate_physical']
    latest = [item for item in files if item['materialization'] == 'latest']
    daily = [item for item in files if item['materialization'] == 'daily']
    dispatch = [item for item in files if item['materialization'] == 'shift']
    all_window_paths = [path for window in windows for path in window['target_paths']]
    checks = {
        'origin_class_is_controlled_synthetic_physical': manifest['origin_class'] == _ORIGIN_CLASS,
        'operational_representative_is_false': manifest['operational_representative'] is False,
        'window_count_is_61': len(windows) == _WINDOW_COUNT,
        'every_window_has_8_pi_daily_targets': all(
            item['pi_daily_target_count'] == 8 for item in windows
        ),
        'every_window_has_14_dispatch_shift_targets': all(
            item['dispatch_shift_target_count'] == 14 for item in windows
        ),
        'every_window_has_22_targets': all(item['target_count'] == 22 for item in windows),
        'window_target_paths_are_globally_unique': len(all_window_paths)
        == len(set(all_window_paths)),
        'pi_latest_file_count_is_1': len(latest) == 1,
        'pi_daily_file_count_is_488': len(daily) == 488,
        'dispatch_shift_file_count_is_854': len(dispatch) == 854,
        'total_file_count_is_1343': aggregate['file_count'] == 1343,
        'pi_latest_geometry_is_1x1001': all(
            item['row_count'] == 1 and item['column_count'] == 1001 for item in latest
        ),
        'pi_daily_geometry_is_8640x3': all(
            item['row_count'] == 8640 and item['column_count'] == 3 for item in daily
        ),
        'dispatch_geometry_is_256x25': all(
            item['row_count'] == 256 and item['column_count'] == 25 for item in dispatch
        ),
        'all_files_use_single_row_group': all(item['row_group_count'] == 1 for item in files),
        'all_files_use_zstd': all(item['compression_codecs'] == ['zstd'] for item in files),
        'all_file_hashes_present': all(bool(item['sha256']) for item in files),
        'aggregate_fingerprint_present': bool(manifest['fingerprints']['aggregate_sha256']),
        'bank_fingerprint_present': bool(manifest['fingerprints']['bank_sha256']),
        'sample_deterministic_rebuild_verified': manifest['determinism']['verified'] is True,
        'warm_replay_uses_same_window_order': manifest['profiles']['WARM_REPLAY_PARTITIONED'][
            'window_order'
        ]
        == manifest['profiles']['FIRST_TOUCH_PARTITIONED']['window_order'],
        'alarm_signal_mapping_pool_is_1000': manifest['alarm_signal_mapping'][
            'physical_signal_pool_size'
        ]
        == 1000,
        'alarm_count_ladder_remains_unfrozen': manifest['alarm_signal_mapping']['ladder_frozen']
        is False,
    }
    status = 'PASS' if all(checks.values()) else 'FAIL'
    return {
        'report_version': _MANIFEST_VERSION,
        'dataset_bank_id': manifest['dataset_bank_id'],
        'status': status,
        'claims': {
            'physical_writer_conformance': status == 'PASS',
            'final_61_window_geometry_frozen': status == 'PASS',
            'deterministic_fixture_identity_sample_verified': manifest['determinism']['verified'],
            'operational_representativeness': False,
            'cold_cache_guaranteed': False,
        },
        'checks': checks,
        'physical_summary': {
            'file_count': aggregate['file_count'],
            'partitioned_working_set_bytes': aggregate['partitioned_working_set_bytes'],
            'pi_daily_file_count': aggregate['pi_daily_file_count'],
            'dispatch_shift_file_count': aggregate['dispatch_shift_file_count'],
            'size_distributions': manifest['size_distributions'],
        },
        'next_gate': (
            'Accept final bank evidence, then implement only the Physical Volume v2 harness '
            'binding, profiles and telemetry. Phase B remains blocked until that harness gate.'
        ),
    }


def _configuration_document(configuration: BankConfiguration) -> dict[str, Any]:
    value = asdict(configuration)
    value['base_window_date'] = configuration.base_window_date.isoformat()
    return value


def _alarm_signal_ordinal(alarm_ordinal: int, *, pool_size: int = 1000) -> int:
    if alarm_ordinal < 1:
        raise ValueError('alarm_ordinal must be greater than zero')
    if pool_size < 1:
        raise ValueError('pool_size must be greater than zero')
    return ((alarm_ordinal - 1) % pool_size) + 1


def _day_partition(value: date) -> dict[str, str]:
    return {'year': f'{value:%Y}', 'month': f'{value:%m}', 'day': f'{value:%d}'}


def _shift_partition(value: date, turn: str) -> dict[str, str]:
    if turn not in {'001', '002'}:
        raise ValueError('turn must be 001 or 002')
    return {**_day_partition(value), 'turn': turn}


def _shift_id(value: date, turn: str) -> int:
    if turn not in {'001', '002'}:
        raise ValueError('turn must be 001 or 002')
    return int(f'{value:%y%m%d}{turn}')


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


def _aggregate_fingerprint(entries: tuple[tuple[str, str], ...]) -> str:
    canonical = ''.join(f'{path}\t{digest}\n' for path, digest in sorted(entries))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _parquet_files(root: Path) -> tuple[Path, ...]:
    return tuple(sorted(path for path in root.rglob('*.parquet') if path.is_file()))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _json_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _utc_isoformat(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace('+00:00', 'Z')


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def _paths(output_root: Path) -> BankPaths:
    input_root = output_root / 'input'
    return BankPaths(
        output_root=output_root,
        input_root=input_root,
        dataset_root=input_root / 'datasets',
        manifest_path=output_root / 'f007-dataset-bank-manifest.json',
        conformance_path=output_root / 'f007-dataset-bank-conformance.json',
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog='f007-dataset-bank')
    parser.add_argument('--output-dir', type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    configuration = BankConfiguration()
    generator = ControlledDatasetBankGenerator(
        paths=_paths(args.output_dir),
        configuration=configuration,
    )
    try:
        result = generator.run()
    except Exception:
        if args.output_dir.exists():
            shutil.rmtree(args.output_dir)
        raise
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
