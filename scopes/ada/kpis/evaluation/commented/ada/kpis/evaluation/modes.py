from __future__ import annotations

# Espejo pedagógico: cada modo KPI interpreta columnas tipadas; los nombres físicos provienen de DataColumn.
from numbers import Integral, Real

from ada.data.core import DataRuntimeContext
from ada.kpis.core import KpiMode, KpiNativeValue, KpiSpec
from ada.kpis.evaluation.errors import KpiInvalidValueError


def resolve_base_value(*, spec: KpiSpec, data_context: DataRuntimeContext) -> KpiNativeValue:
    if not isinstance(spec, KpiSpec):
        raise TypeError('spec must be KpiSpec')
    if not isinstance(data_context, DataRuntimeContext):
        raise TypeError('data_context must be DataRuntimeContext')
    if spec.mode is KpiMode.CONSTANT:
        return spec.constant_value
    if spec.mode is KpiMode.CUSTOM:
        assert spec.custom_resolver is not None
        return spec.custom_resolver(data_context)
    assert spec.source is not None
    assert spec.partition is not None
    source_context = data_context.get(spec.source, spec.partition)
    if spec.mode is KpiMode.LATEST:
        return source_context.last_value(spec.columns[0].name)
    if spec.mode is KpiMode.LATEST_NUMBER:
        return source_context.last_value_number(spec.columns[0].name)
    if spec.mode is KpiMode.STATUS:
        value = source_context.last_value(spec.columns[0].name)
        if value is None:
            return None
        return _resolve_status(value)
    if spec.mode is KpiMode.SUM_LATESTS_NUMBERS:
        return sum(
            source_context.last_value_number(column.name, default=0.0) or 0.0
            for column in spec.columns
        )
    if spec.mode is KpiMode.MAX_LATESTS_NUMBERS:
        values = tuple(
            value
            for column in spec.columns
            if (value := source_context.last_value_number(column.name, default=None)) is not None
        )
        return max(values) if values else None
    raise ValueError(f'{spec.key}: unsupported KPI mode: {spec.mode.value}')


def _resolve_status(value: object) -> str:
    if isinstance(value, bool):
        raise KpiInvalidValueError('status value is invalid')
    if isinstance(value, Integral | Real):
        if value == 0:
            return 'detenido'
        if value == 1:
            return 'operando'
        raise KpiInvalidValueError('status value is invalid')
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {'0', '0.0', 'detenido'}:
            return 'detenido'
        if normalized in {'1', '1.0', 'funcionando'}:
            return 'operando'
    raise KpiInvalidValueError('status value is invalid')
