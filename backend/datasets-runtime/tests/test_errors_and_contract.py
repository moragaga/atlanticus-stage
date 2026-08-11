from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pytest

from atlanticus.datasets import DatasetDefinition
from atlanticus.datasets.parquet import ParquetDatasetStore, ParquetSchemaError
from atlanticus.datasets.runtime import (
    DatasetRuntime,
    DatasetRuntimeNotFoundError,
    DatasetRuntimeReadError,
    DatasetRuntimeValidationError,
    DatasetRuntimeWriteError,
)


def _target(definition: DatasetDefinition):
    return definition.resolve_target(
        materialization='granular',
        partition={'year': '2026', 'month': '07', 'day': '21'},
    )


class _FailingStore(ParquetDatasetStore):
    def replace(self, **_kwargs):
        raise OSError('controlled write failure')

    def read(self, **_kwargs):
        raise OSError('controlled read failure')


def test_runtime_requires_the_concrete_parquet_store() -> None:
    with pytest.raises(DatasetRuntimeValidationError, match='ParquetDatasetStore'):
        DatasetRuntime(store=object())  # type: ignore[arg-type]


def test_runtime_does_not_expose_the_physical_store(
    dataset_runtime: DatasetRuntime,
) -> None:
    assert not hasattr(dataset_runtime, 'store')


def test_store_write_errors_are_typed_and_keep_the_original_cause(
    tmp_path: Path,
    clock: datetime,
    pi_definition: DatasetDefinition,
) -> None:
    runtime = DatasetRuntime(
        store=_FailingStore(root=tmp_path, clock=lambda: clock),
        clock=lambda: clock,
    )
    target = _target(pi_definition)

    with pytest.raises(DatasetRuntimeWriteError) as captured:
        runtime.replace(
            definition=pi_definition,
            target=target,
            data=pa.table({'value': [1]}),
        )

    assert isinstance(captured.value.__cause__, OSError)


def test_store_read_errors_are_typed_and_keep_the_original_cause(
    tmp_path: Path,
    clock: datetime,
    pi_definition: DatasetDefinition,
) -> None:
    runtime = DatasetRuntime(
        store=_FailingStore(root=tmp_path, clock=lambda: clock),
        clock=lambda: clock,
    )
    target = _target(pi_definition)

    with pytest.raises(DatasetRuntimeReadError) as captured:
        runtime.read_table(definition=pi_definition, target=target)

    assert isinstance(captured.value.__cause__, OSError)


def test_missing_publication_has_a_distinct_runtime_error(
    dataset_runtime: DatasetRuntime,
    pi_definition: DatasetDefinition,
) -> None:
    with pytest.raises(DatasetRuntimeNotFoundError) as captured:
        dataset_runtime.read_table(
            definition=pi_definition,
            target=_target(pi_definition),
        )

    assert isinstance(captured.value, DatasetRuntimeReadError)
    assert isinstance(captured.value, FileNotFoundError)


def test_store_validation_errors_are_not_classified_as_write_failures(
    tmp_path: Path,
    clock: datetime,
    pi_definition: DatasetDefinition,
) -> None:
    class _SchemaFailingStore(ParquetDatasetStore):
        def replace(self, **_kwargs):
            raise ParquetSchemaError('controlled schema failure')

    runtime = DatasetRuntime(
        store=_SchemaFailingStore(root=tmp_path, clock=lambda: clock),
        clock=lambda: clock,
    )

    with pytest.raises(DatasetRuntimeValidationError) as captured:
        runtime.replace(
            definition=pi_definition,
            target=_target(pi_definition),
            data=pa.table({'value': [1]}),
        )

    assert isinstance(captured.value.__cause__, ParquetSchemaError)


def test_invalid_definition_and_layout_are_rejected_before_empty_shortcuts(
    dataset_runtime: DatasetRuntime,
    pi_definition: DatasetDefinition,
    dispatch_definition: DatasetDefinition,
) -> None:
    empty = pa.table({'value': pa.array([], type=pa.int64())})
    dispatch_target = dispatch_definition.resolve_target(
        materialization='operational-day',
        partition={'year': '2026', 'month': '07', 'day': '21'},
    )

    with pytest.raises(DatasetRuntimeValidationError, match='does not belong'):
        dataset_runtime.replace(
            definition=pi_definition,
            target=dispatch_target,
            data=empty,
        )

    with pytest.raises(DatasetRuntimeValidationError, match='not supported'):
        dataset_runtime.replace(
            definition=dispatch_definition,
            target=dispatch_target,
            data=empty,
        )

    with pytest.raises(DatasetRuntimeValidationError, match='not supported'):
        dataset_runtime.publish_parts(
            definition=pi_definition,
            target=_target(pi_definition),
        )


def test_none_filters_are_reported_as_runtime_validation_errors(
    dataset_runtime: DatasetRuntime,
    pi_definition: DatasetDefinition,
) -> None:
    with pytest.raises(DatasetRuntimeValidationError, match='filters must be an iterable'):
        dataset_runtime.scan_table(
            definition=pi_definition,
            targets=(_target(pi_definition),),
            filters=None,  # type: ignore[arg-type]
        )


def test_empty_result_rejects_a_naive_clock(
    parquet_store: ParquetDatasetStore,
    pi_definition: DatasetDefinition,
) -> None:
    runtime = DatasetRuntime(store=parquet_store, clock=lambda: datetime(2026, 7, 21))

    with pytest.raises(DatasetRuntimeValidationError, match='timezone-aware'):
        runtime.replace(
            definition=pi_definition,
            target=_target(pi_definition),
            data=pa.table({'value': pa.array([], type=pa.int64())}),
        )
