# Las transformaciones conservan la semántica legacy validada.
# Stocks produce una fila wide por snapshot; extraíbles/no extraíbles conservan una fila por registro fuente.
# Al reprocesar un timestamp se reemplaza el snapshot completo sin deduplicar sus filas internas.

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

import pandas as pd
import pyarrow as pa

from atlanticus.data_producers.remanentes.models import (
    RemanentesRowsStreamDefinition,
    RemanentesStocksStreamDefinition,
    RemanentesStreamDefinition,
)


@dataclass(frozen=True, slots=True)
class RemanentesTransformResult:
    dataframe: pd.DataFrame
    source_row_count: int
    present_metric_keys: tuple[str, ...] = ()
    missing_metric_keys: tuple[str, ...] = ()
    unknown_source_values: tuple[str, ...] = ()


def transform_snapshot(
    *,
    table: pa.Table,
    definition: RemanentesStreamDefinition,
    source_timestamp_utc: datetime,
) -> RemanentesTransformResult:
    if isinstance(definition, RemanentesStocksStreamDefinition):
        return _transform_stocks(
            table=table,
            definition=definition,
            source_timestamp_utc=source_timestamp_utc,
        )
    if isinstance(definition, RemanentesRowsStreamDefinition):
        return _transform_rows(table=table, source_timestamp_utc=source_timestamp_utc)
    raise TypeError('definition must be a Remanentes stream definition')


def merge_snapshot(
    *,
    current: pd.DataFrame | None,
    incoming: pd.DataFrame,
    source_timestamp_utc: datetime,
) -> pd.DataFrame:
    if current is None or current.empty:
        return incoming.reset_index(drop=True)
    existing = current.reindex(columns=incoming.columns).copy()
    existing['timestamp'] = pd.to_datetime(existing['timestamp'], utc=True, errors='coerce')
    source_timestamp = pd.Timestamp(source_timestamp_utc)
    existing = existing[existing['timestamp'].ne(source_timestamp)]
    merged = pd.concat((existing, incoming), ignore_index=True, sort=False)
    return merged.sort_values('timestamp', kind='mergesort').reset_index(drop=True)


def _transform_stocks(
    *,
    table: pa.Table,
    definition: RemanentesStocksStreamDefinition,
    source_timestamp_utc: datetime,
) -> RemanentesTransformResult:
    source = table.to_pandas()
    stock_values = source['STOCK'].map(_unwrap).astype('string').str.strip().str.upper()
    ton_values = pd.to_numeric(source['Ton (kt)'].map(_unwrap), errors='coerce').astype('Float64')
    working = pd.DataFrame({'stock': stock_values, 'ton_kt': ton_values})
    known = {metric.source_value for metric in definition.stock_metrics}
    unknown = tuple(
        sorted(
            value
            for value in working['stock'].dropna().unique().tolist()
            if value and value not in known
        )
    )
    row: dict[str, object] = {'timestamp': pd.Timestamp(source_timestamp_utc)}
    present: set[str] = set()
    for metric in definition.stock_metrics:
        values = working[working['stock'].eq(metric.source_value)]['ton_kt'].dropna()
        if values.empty:
            row[metric.metric_key] = pd.NA
            continue
        row[metric.metric_key] = values.iloc[-1]
        present.add(metric.metric_key)
    dataframe = pd.DataFrame([row])
    dataframe['timestamp'] = pd.to_datetime(dataframe['timestamp'], utc=True)
    for metric in definition.stock_metrics:
        dataframe[metric.metric_key] = pd.to_numeric(
            dataframe[metric.metric_key], errors='coerce'
        ).astype('Float64')
    present_ordered = tuple(
        metric.metric_key for metric in definition.stock_metrics if metric.metric_key in present
    )
    missing = tuple(
        metric.metric_key for metric in definition.stock_metrics if metric.metric_key not in present
    )
    return RemanentesTransformResult(
        dataframe=dataframe,
        source_row_count=len(source),
        present_metric_keys=present_ordered,
        missing_metric_keys=missing,
        unknown_source_values=unknown,
    )


def _transform_rows(
    *,
    table: pa.Table,
    source_timestamp_utc: datetime,
) -> RemanentesTransformResult:
    source = table.to_pandas()
    output = pd.DataFrame(index=source.index)
    output['timestamp'] = pd.Timestamp(source_timestamp_utc)
    output['fase'] = _text(source['Fase'])
    output['banco'] = pd.to_numeric(source['Banco'].map(_unwrap), errors='coerce').astype('Float64')
    output['tipo_material'] = _text(source['Tipo de material'])
    output['observacion'] = _text(source['Observación'])
    output['ton_kt'] = pd.to_numeric(source['Ton (kt)'].map(_unwrap), errors='coerce').astype(
        'Float64'
    )
    output['timestamp'] = pd.to_datetime(output['timestamp'], utc=True)
    return RemanentesTransformResult(
        dataframe=output.reset_index(drop=True),
        source_row_count=len(source),
    )


def _text(values: pd.Series) -> pd.Series:
    output = values.map(_unwrap).astype('string').str.strip()
    return output.mask(output.eq(''), pd.NA)


def _unwrap(value: object) -> object:
    if hasattr(value, 'as_py'):
        value = value.as_py()
    if isinstance(value, Mapping):
        return value.get('value', value)
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not (text.startswith('{') and text.endswith('}')):
        return value
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return value
    if isinstance(parsed, Mapping):
        return parsed.get('value', value)
    return value
