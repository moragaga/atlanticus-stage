from __future__ import annotations

# Espejo pedagógico: los resultados y trazas siguen siendo propiedad de KPI aunque la fuente sea un DataSource compartido.
import math
from collections.abc import Mapping
from dataclasses import dataclass

from ada.data.core import DataSource
from ada.kpis.core.enums import KpiArea, KpiStatus, KpiValueKind
from ada.kpis.core.values import KpiNativeValue, KpiScalar
from ada.kpis.core.watermark import KpiWatermark


@dataclass(frozen=True, slots=True)
class KpiResult:
    key: str
    area: KpiArea
    status: KpiStatus
    value_kind: KpiValueKind
    persist_history: bool
    value: KpiNativeValue = None
    parsed_value: KpiScalar | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        key = _required_text(self.key, 'key')
        _require_enum(self.area, KpiArea, 'area')
        _require_enum(self.status, KpiStatus, 'status')
        _require_enum(self.value_kind, KpiValueKind, 'value_kind')
        if not isinstance(self.persist_history, bool):
            raise ValueError(f'{key}: persist_history must be boolean')
        error = _optional_text(self.error, f'{key}: error')
        if error is not None and len(error) > 500:
            error = f'{error[:500]}...<truncated>'
        if self.status is KpiStatus.ERROR:
            if self.value is not None or self.parsed_value is not None:
                raise ValueError(f'{key}: error result must not contain value or parsed_value')
            if error is None:
                raise ValueError(f'{key}: error result requires error')
        elif error is not None:
            raise ValueError(f'{key}: ok result must not contain error')
        elif self.value_kind is KpiValueKind.VALUE:
            _validate_scalar(self.value, f'{key}: value', allow_none=False)
            _validate_scalar(self.parsed_value, f'{key}: parsed_value', allow_none=False)
        else:
            _validate_json_container(self.value, f'{key}: value')
            if self.parsed_value is not None:
                raise ValueError(f'{key}: json result must not contain parsed_value')
        object.__setattr__(self, 'key', key)
        object.__setattr__(self, 'error', error)

    def as_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            'area': self.area.value,
            'status': self.status.value,
            'persist_history': self.persist_history,
            'value_kind': self.value_kind.value,
            'value': self.value,
        }
        if self.value_kind is KpiValueKind.VALUE:
            payload['parsed_value'] = self.parsed_value
        if self.status is KpiStatus.ERROR:
            payload['error'] = self.error
        return payload

    @classmethod
    def from_payload(cls, *, key: str, payload: Mapping[str, object]) -> KpiResult:
        if not isinstance(payload, Mapping):
            raise TypeError('kpi result payload must be a mapping')
        area = _parse_enum(payload.get('area'), KpiArea, f'{key}: area')
        status = _parse_enum(payload.get('status'), KpiStatus, f'{key}: status')
        value_kind = _parse_enum(payload.get('value_kind'), KpiValueKind, f'{key}: value_kind')
        persist_history = payload.get('persist_history')
        if not isinstance(persist_history, bool):
            raise ValueError(f'{key}: persist_history must be boolean')
        error = payload.get('error')
        if error is not None and not isinstance(error, str):
            raise ValueError(f'{key}: error must be string or null')
        return cls(
            key=key,
            area=area,
            status=status,
            value_kind=value_kind,
            persist_history=persist_history,
            value=payload.get('value'),
            parsed_value=payload.get('parsed_value'),
            error=error,
        )


@dataclass(frozen=True, slots=True)
class KpiSourceTrace:
    source: DataSource
    watermark: KpiWatermark | None = None

    def __post_init__(self) -> None:
        _require_enum(self.source, DataSource, 'source')
        if self.watermark is not None and not isinstance(self.watermark, KpiWatermark):
            raise TypeError(f'{self.source.value}: watermark must be KpiWatermark')

    def as_payload(self) -> dict[str, str | None]:
        return {'watermark_utc': None if self.watermark is None else self.watermark.text}

    @classmethod
    def from_payload(
        cls,
        *,
        source: DataSource,
        payload: Mapping[str, object],
    ) -> KpiSourceTrace:
        if not isinstance(payload, Mapping):
            raise TypeError('source trace payload must be a mapping')
        value = payload.get('watermark_utc')
        if value is None:
            watermark = None
        elif isinstance(value, str):
            watermark = KpiWatermark.parse(value)
        else:
            raise ValueError(f'{source.value}: source trace watermark_utc must be string or null')
        return cls(source=source, watermark=watermark)


@dataclass(frozen=True, slots=True)
class KpiEvaluation:
    watermark: KpiWatermark
    results: tuple[KpiResult, ...]
    sources: tuple[KpiSourceTrace, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.watermark, KpiWatermark):
            raise TypeError('evaluation watermark must be KpiWatermark')
        results = tuple(self.results)
        sources = tuple(self.sources)
        if not results:
            raise ValueError('kpi evaluation requires at least one result')
        if not all(isinstance(result, KpiResult) for result in results):
            raise TypeError('evaluation results must contain KpiResult values')
        if not all(isinstance(source, KpiSourceTrace) for source in sources):
            raise TypeError('evaluation sources must contain KpiSourceTrace values')
        result_keys = tuple(result.key for result in results)
        if len(result_keys) != len(set(result_keys)):
            raise ValueError('kpi evaluation result keys must be unique')
        source_keys = tuple(source.source for source in sources)
        if len(source_keys) != len(set(source_keys)):
            raise ValueError('kpi evaluation sources must be unique')
        object.__setattr__(self, 'results', results)
        object.__setattr__(self, 'sources', sources)

    @property
    def historical_results(self) -> tuple[KpiResult, ...]:
        return tuple(result for result in self.results if result.persist_history)

    @property
    def error_results(self) -> tuple[KpiResult, ...]:
        return tuple(result for result in self.results if result.status is KpiStatus.ERROR)

    def as_document(self) -> dict[str, object]:
        return {
            'watermark_utc': self.watermark.text,
            'sources': {source.source.value: source.as_payload() for source in self.sources},
            'kpis': {result.key: result.as_payload() for result in self.results},
        }

    @classmethod
    def from_document(cls, document: Mapping[str, object]) -> KpiEvaluation:
        if not isinstance(document, Mapping):
            raise TypeError('kpi evaluation document must be a mapping')
        watermark_value = document.get('watermark_utc')
        if not isinstance(watermark_value, str):
            raise ValueError('kpi evaluation requires watermark_utc')
        source_payload = document.get('sources', {})
        kpi_payload = document.get('kpis')
        if not isinstance(source_payload, Mapping):
            raise ValueError('kpi evaluation sources must be a mapping')
        if not isinstance(kpi_payload, Mapping):
            raise ValueError('kpi evaluation kpis must be a mapping')
        sources = tuple(
            KpiSourceTrace.from_payload(
                source=DataSource(str(key)),
                payload=_require_mapping(value),
            )
            for key, value in source_payload.items()
        )
        results = tuple(
            KpiResult.from_payload(key=str(key), payload=_require_mapping(value))
            for key, value in kpi_payload.items()
        )
        return cls(
            watermark=KpiWatermark.parse(watermark_value),
            results=results,
            sources=sources,
        )


def _require_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError('nested KPI document value must be a mapping')
    return value


def _validate_scalar(value: object, field_name: str, *, allow_none: bool = True) -> None:
    if value is None:
        if allow_none:
            return
        raise ValueError(f'{field_name} must not be null')
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        raise ValueError(f'{field_name} must be string, integer, or float')
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f'{field_name} must be finite')


def _validate_json_container(value: object, field_name: str) -> None:
    if not isinstance(value, dict | list):
        raise ValueError(f'{field_name} must be a JSON object or array')
    _validate_json_value(value, field_name)


def _validate_json_value(value: object, field_name: str) -> None:
    if value is None or isinstance(value, str | int | bool):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f'{field_name} must not contain NaN or Infinity')
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item, field_name)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f'{field_name} object keys must be strings')
            _validate_json_value(item, field_name)
        return
    raise ValueError(f'{field_name} contains a non-JSON value')


def _parse_enum(value: object, enum_type: type, field_name: str):
    if not isinstance(value, str):
        raise ValueError(f'{field_name} must be a string')
    try:
        return enum_type(value)
    except ValueError as error:
        raise ValueError(f'{field_name} is invalid') from error


def _require_enum(value: object, expected_type: type, field_name: str) -> None:
    if not isinstance(value, expected_type):
        raise TypeError(f'{field_name} must be {expected_type.__name__}')


def _optional_text(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field_name)


def _required_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field_name} must be a non-empty string')
    return value.strip()
