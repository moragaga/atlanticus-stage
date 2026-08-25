from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ada.data.core import DataSource
from ada.kpis.core import (
    KpiArea,
    KpiEvaluation,
    KpiResult,
    KpiSourceTrace,
    KpiStatus,
    KpiValueKind,
    KpiWatermark,
)


def _watermark(second: int = 20) -> KpiWatermark:
    return KpiWatermark(datetime(2026, 8, 19, 18, 15, second, tzinfo=UTC))


def test_value_result_keeps_raw_and_parsed_value() -> None:
    result = KpiResult(
        key='value-kpi',
        area=KpiArea.MINA,
        status=KpiStatus.OK,
        value_kind=KpiValueKind.VALUE,
        persist_history=True,
        value=83.45,
        parsed_value='83,45',
    )

    assert result.as_payload() == {
        'area': 'mina',
        'status': 'ok',
        'persist_history': True,
        'value_kind': 'value',
        'value': 83.45,
        'parsed_value': '83,45',
    }


def test_json_result_keeps_native_dict_or_list_without_parsed_value() -> None:
    result = KpiResult(
        key='json-kpi',
        area=KpiArea.PLANTA,
        status=KpiStatus.OK,
        value_kind=KpiValueKind.JSON,
        persist_history=False,
        value={'operating': 18, 'stopped': 4},
    )

    assert result.as_payload() == {
        'area': 'planta',
        'status': 'ok',
        'persist_history': False,
        'value_kind': 'json',
        'value': {'operating': 18, 'stopped': 4},
    }

    with pytest.raises(ValueError, match='must not contain parsed_value'):
        KpiResult(
            key='bad-json',
            area=KpiArea.PLANTA,
            status=KpiStatus.OK,
            value_kind=KpiValueKind.JSON,
            persist_history=False,
            value={'a': 1},
            parsed_value='bad',
        )


def test_error_result_has_no_values_and_keeps_safe_diagnostic() -> None:
    result = KpiResult(
        key='failed',
        area=KpiArea.GENERAL,
        status=KpiStatus.ERROR,
        value_kind=KpiValueKind.VALUE,
        persist_history=True,
        error='Required source column was not available',
    )

    assert result.value is None
    assert result.parsed_value is None
    assert result.error == 'Required source column was not available'
    assert result.as_payload()['error'] == 'Required source column was not available'

    with pytest.raises(ValueError, match='requires error'):
        KpiResult(
            key='failed-without-error',
            area=KpiArea.GENERAL,
            status=KpiStatus.ERROR,
            value_kind=KpiValueKind.VALUE,
            persist_history=True,
        )


def test_evaluation_exposes_history_and_error_projections_independently() -> None:
    evaluation = KpiEvaluation(
        watermark=_watermark(),
        sources=(KpiSourceTrace(DataSource.PI_INTERPOLATED, _watermark()),),
        results=(
            KpiResult(
                key='historical-ok',
                area=KpiArea.MINA,
                status=KpiStatus.OK,
                value_kind=KpiValueKind.VALUE,
                persist_history=True,
                value=12.3,
                parsed_value='12,30',
            ),
            KpiResult(
                key='historical-error',
                area=KpiArea.MINA,
                status=KpiStatus.ERROR,
                value_kind=KpiValueKind.VALUE,
                persist_history=True,
                error='Resolver failed',
            ),
            KpiResult(
                key='latest-only-error',
                area=KpiArea.PLANTA,
                status=KpiStatus.ERROR,
                value_kind=KpiValueKind.JSON,
                persist_history=False,
                error='Source unavailable',
            ),
        ),
    )

    assert tuple(result.key for result in evaluation.historical_results) == (
        'historical-ok',
        'historical-error',
    )
    assert tuple(result.key for result in evaluation.error_results) == (
        'historical-error',
        'latest-only-error',
    )
    assert KpiEvaluation.from_document(evaluation.as_document()) == evaluation


def test_value_result_rejects_json_and_json_result_rejects_scalar() -> None:
    with pytest.raises(ValueError, match='string, integer, or float'):
        KpiResult(
            key='bad-value',
            area=KpiArea.MINA,
            status=KpiStatus.OK,
            value_kind=KpiValueKind.VALUE,
            persist_history=False,
            value={'a': 1},
            parsed_value='1',
        )

    with pytest.raises(ValueError, match='JSON object or array'):
        KpiResult(
            key='bad-json',
            area=KpiArea.MINA,
            status=KpiStatus.OK,
            value_kind=KpiValueKind.JSON,
            persist_history=False,
            value='not-json-container',
        )
