from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ada.kpis.core import (
    KpiArea,
    KpiEvaluation,
    KpiResult,
    KpiSource,
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


def test_non_ok_result_carries_no_value_and_can_still_be_historical() -> None:
    result = KpiResult(
        key='missing',
        area=KpiArea.GENERAL,
        status=KpiStatus.MISSING,
        value_kind=KpiValueKind.VALUE,
        persist_history=True,
    )
    assert result.value is None
    assert result.persist_history is True


def test_evaluation_uses_typed_source_trace_and_native_json() -> None:
    evaluation = KpiEvaluation(
        watermark=_watermark(),
        sources=(KpiSourceTrace(KpiSource.PI_INTERPOLATED, _watermark()),),
        results=(
            KpiResult(
                key='value-kpi',
                area=KpiArea.MINA,
                status=KpiStatus.OK,
                value_kind=KpiValueKind.VALUE,
                persist_history=True,
                value=12.3,
                parsed_value='12,30',
            ),
            KpiResult(
                key='json-kpi',
                area=KpiArea.PLANTA,
                status=KpiStatus.OK,
                value_kind=KpiValueKind.JSON,
                persist_history=False,
                value=[{'name': 'A', 'value': 1}],
            ),
        ),
    )

    document = evaluation.as_document()
    assert document['watermark_utc'] == '2026-08-19T18:15:20Z'
    assert document['sources'] == {'pi.interpolated': {'watermark_utc': '2026-08-19T18:15:20Z'}}
    assert evaluation.historical_results[0].key == 'value-kpi'
    assert KpiEvaluation.from_document(document) == evaluation


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
