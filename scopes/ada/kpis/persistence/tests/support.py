from __future__ import annotations

from datetime import UTC, datetime

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


def watermark(second: int) -> KpiWatermark:
    return KpiWatermark(datetime(2026, 8, 19, 18, 15, second, tzinfo=UTC))


def evaluation(second: int, *, value: float | None = None) -> KpiEvaluation:
    resolved_value = float(second) if value is None else value
    return KpiEvaluation(
        watermark=watermark(second),
        sources=(KpiSourceTrace(DataSource.PI_INTERPOLATED, watermark(second)),),
        results=(
            KpiResult(
                key='test_kpi',
                area=KpiArea.MINA,
                status=KpiStatus.OK,
                value_kind=KpiValueKind.VALUE,
                persist_history=True,
                value=resolved_value,
                parsed_value=str(resolved_value),
            ),
        ),
    )
