from datetime import UTC, datetime

from ada.kpis.core import KpiArea, KpiEvaluation, KpiResult, KpiStatus, KpiValueKind, KpiWatermark
from atlanticus.kernel import Environment
from atlanticus.runtime import JobDefinition, JobRuntimeContext, RuntimeConfiguration


def watermark(day: int, second: int = 0) -> KpiWatermark:
    return KpiWatermark(datetime(2026, 8, day, 20, 0, second, tzinfo=UTC))


def evaluation(
    target: KpiWatermark,
    *,
    key: str = 'kpi-a',
    value: object = 10,
    persist_history: bool = True,
    status: KpiStatus = KpiStatus.OK,
    value_kind: KpiValueKind = KpiValueKind.VALUE,
) -> KpiEvaluation:
    if status is KpiStatus.OK:
        parsed_value = value if value_kind is KpiValueKind.VALUE else None
        result = KpiResult(
            key=key,
            area=KpiArea.GENERAL,
            status=status,
            value_kind=value_kind,
            persist_history=persist_history,
            value=value,
            parsed_value=parsed_value,
        )
    else:
        result = KpiResult(
            key=key,
            area=KpiArea.GENERAL,
            status=status,
            value_kind=value_kind,
            persist_history=persist_history,
        )
    return KpiEvaluation(watermark=target, results=(result,))


def context(tmp_path) -> JobRuntimeContext:
    definition = JobDefinition(
        module_name='ada.processes.kpis_historian',
        service_name='kpis-historian',
        job_key='kpis-historian-test',
        iteration_timeout_seconds=30,
        execution_timeout_seconds=60,
        shutdown_grace_seconds=5,
        lease_timeout_seconds=30,
        lease_renew_seconds=10,
    )
    configuration = RuntimeConfiguration(
        environment=Environment.from_value('local'),
        application='ada-operaciones-integradas-local',
        volume_path=tmp_path,
    )
    return JobRuntimeContext.create(
        definition=definition,
        configuration=configuration,
        run_id='run-id',
        correlation_id='correlation-id',
    )
