from __future__ import annotations

import math
from numbers import Integral, Real

from ada.kpis.core import KpiArea, KpiNativeValue, KpiResult, KpiStatus, KpiValueKind


def build_result(
    *,
    key: str,
    area: KpiArea,
    value_kind: KpiValueKind,
    persist_history: bool,
    decimals: int | None,
    is_truncated: bool,
    value: KpiNativeValue,
) -> KpiResult:
    if value is None:
        return error_result(
            key=key,
            area=area,
            value_kind=value_kind,
            persist_history=persist_history,
            error='KPI resolver returned no value',
        )
    if value_kind is KpiValueKind.JSON:
        json_value = _normalize_json_container(value)
        if json_value is None:
            return error_result(
                key=key,
                area=area,
                value_kind=value_kind,
                persist_history=persist_history,
                error='KPI resolver returned an invalid JSON value',
            )
        return KpiResult(
            key=key,
            area=area,
            status=KpiStatus.OK,
            value_kind=value_kind,
            persist_history=persist_history,
            value=json_value,
        )
    scalar = _normalize_scalar(value)
    if scalar is None:
        return error_result(
            key=key,
            area=area,
            value_kind=value_kind,
            persist_history=persist_history,
            error='KPI resolver returned an invalid scalar value',
        )
    if not is_truncated:
        return KpiResult(
            key=key,
            area=area,
            status=KpiStatus.OK,
            value_kind=value_kind,
            persist_history=persist_history,
            value=scalar,
            parsed_value=scalar,
        )
    number = _to_number(scalar)
    if number is None:
        return error_result(
            key=key,
            area=area,
            value_kind=value_kind,
            persist_history=persist_history,
            error='KPI value cannot be converted to a finite number',
        )
    resolved_decimals = 2 if decimals is None else decimals
    truncated = _truncate_number(number, resolved_decimals)
    return KpiResult(
        key=key,
        area=area,
        status=KpiStatus.OK,
        value_kind=value_kind,
        persist_history=persist_history,
        value=truncated,
        parsed_value=_format_number_cl(truncated, resolved_decimals),
    )


def error_result(
    *,
    key: str,
    area: KpiArea,
    value_kind: KpiValueKind,
    persist_history: bool,
    error: str,
) -> KpiResult:
    return KpiResult(
        key=key,
        area=area,
        status=KpiStatus.ERROR,
        value_kind=value_kind,
        persist_history=persist_history,
        error=error,
    )


def _normalize_scalar(value: object) -> str | int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        number = float(value)
        return number if math.isfinite(number) else None
    return None


def _to_number(value: str | int | float) -> float | None:
    if isinstance(value, int | float):
        number = float(value)
        return number if math.isfinite(number) else None
    cleaned = value.strip()
    if not cleaned:
        return None
    if ',' in cleaned and '.' in cleaned:
        if cleaned.rfind(',') > cleaned.rfind('.'):
            cleaned = cleaned.replace('.', '').replace(',', '.')
        else:
            cleaned = cleaned.replace(',', '')
    elif ',' in cleaned:
        cleaned = cleaned.replace(',', '.')
    try:
        number = float(cleaned)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _truncate_number(value: float, decimals: int) -> float:
    factor = 10**decimals
    return math.trunc(value * factor) / factor


def _format_number_cl(value: float, decimals: int) -> str:
    formatted = f'{value:,.{decimals}f}'
    return formatted.replace(',', 'X').replace('.', ',').replace('X', '.')


def _normalize_json_container(value: object) -> dict[str, object] | list[object] | None:
    if isinstance(value, dict):
        output: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                return None
            normalized, valid = _normalize_json_value(item)
            if not valid:
                return None
            output[key] = normalized
        return output
    if isinstance(value, list):
        output_list: list[object] = []
        for item in value:
            normalized, valid = _normalize_json_value(item)
            if not valid:
                return None
            output_list.append(normalized)
        return output_list
    return None


def _normalize_json_value(value: object) -> tuple[object, bool]:
    if value is None or isinstance(value, str | bool):
        return value, True
    if isinstance(value, Integral):
        return int(value), True
    if isinstance(value, Real):
        number = float(value)
        return (number, True) if math.isfinite(number) else (None, False)
    if isinstance(value, dict | list):
        normalized = _normalize_json_container(value)
        return normalized, normalized is not None
    return None, False
