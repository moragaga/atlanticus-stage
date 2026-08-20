from datetime import UTC, datetime

from ada.kpis.core import (
    KpiArea,
    KpiEvaluation,
    KpiResult,
    KpiStatus,
    KpiValueKind,
    KpiWatermark,
)
from ada.kpis.delivery import (
    KpiDeliveryBinding,
    KpiDeliveryStatus,
    calculate_kpi_latest_revision,
    project_kpi_latest,
)

_NOW = datetime(2026, 8, 20, 14, 30, tzinfo=UTC)


def _evaluation(*results: KpiResult) -> KpiEvaluation:
    return KpiEvaluation(
        watermark=KpiWatermark(datetime(2026, 8, 20, 12, 55, 30, tzinfo=UTC)),
        results=results,
    )


def _value_result(key: str, *, parsed_value: str) -> KpiResult:
    return KpiResult(
        key=key,
        area=KpiArea.PLANTA,
        status=KpiStatus.OK,
        value_kind=KpiValueKind.VALUE,
        persist_history=True,
        value=66.0,
        parsed_value=parsed_value,
    )


def _json_result(key: str) -> KpiResult:
    return KpiResult(
        key=key,
        area=KpiArea.PLANTA,
        status=KpiStatus.OK,
        value_kind=KpiValueKind.JSON,
        persist_history=True,
        value={'actual': 100, 'plan': 120},
    )


def _error_result(key: str) -> KpiResult:
    return KpiResult(
        key=key,
        area=KpiArea.PLANTA,
        status=KpiStatus.ERROR,
        value_kind=KpiValueKind.VALUE,
        persist_history=True,
        error='RuntimeError',
    )


def test_projects_value_json_error_and_missing_into_store_snapshot() -> None:
    snapshot = project_kpi_latest(
        evaluation=_evaluation(
            _value_result('tonelaje', parsed_value='66,00'),
            _json_result('produccion'),
            _error_result('utilizacion'),
        ),
        bindings=(
            KpiDeliveryBinding(store_key='chancado', kpi_key='tonelaje'),
            KpiDeliveryBinding(store_key='chancado', kpi_key='produccion'),
            KpiDeliveryBinding(store_key='chancado', kpi_key='utilizacion'),
            KpiDeliveryBinding(store_key='chancado', kpi_key='configurado_sin_latest'),
        ),
        updated_at_utc=_NOW,
    )

    document = snapshot.as_document()

    assert document['id'] == 'snapshot'
    assert document['partition_id'] == 'kpis'
    assert document['manifest']['schema_version'] == 1
    assert document['manifest']['updated_at_utc'] == '2026-08-20T14:30:00.000000Z'
    assert len(document['manifest']['revision']) == 16
    assert document['stores']['chancado'] == {
        'tonelaje': {'status': 'ok', 'value_kind': 'value', 'value': '66,00'},
        'produccion': {
            'status': 'ok',
            'value_kind': 'json',
            'value': {'actual': 100, 'plan': 120},
        },
        'utilizacion': {'status': 'error', 'value_kind': 'value', 'value': None},
        'configurado_sin_latest': {
            'status': 'missing',
            'value_kind': None,
            'value': None,
        },
    }


def test_empty_configuration_projects_minimum_empty_snapshot() -> None:
    snapshot = project_kpi_latest(
        evaluation=_evaluation(_value_result('unused', parsed_value='1')),
        bindings=(),
        updated_at_utc=_NOW,
    )

    document = snapshot.as_document()

    assert document['stores'] == {}
    assert document['manifest']['revision'] == calculate_kpi_latest_revision({})


def test_revision_is_stable_when_only_timestamp_changes() -> None:
    evaluation = _evaluation(_value_result('tonelaje', parsed_value='66,00'))
    bindings = (KpiDeliveryBinding(store_key='chancado', kpi_key='tonelaje'),)

    first = project_kpi_latest(
        evaluation=evaluation,
        bindings=bindings,
        updated_at_utc=_NOW,
    )
    second = project_kpi_latest(
        evaluation=evaluation,
        bindings=bindings,
        updated_at_utc=datetime(2026, 8, 20, 14, 31, tzinfo=UTC),
    )

    assert first.manifest.revision == second.manifest.revision


def test_revision_changes_when_consumable_value_changes() -> None:
    bindings = (KpiDeliveryBinding(store_key='chancado', kpi_key='tonelaje'),)

    first = project_kpi_latest(
        evaluation=_evaluation(_value_result('tonelaje', parsed_value='66,00')),
        bindings=bindings,
        updated_at_utc=_NOW,
    )
    second = project_kpi_latest(
        evaluation=_evaluation(_value_result('tonelaje', parsed_value='67,00')),
        bindings=bindings,
        updated_at_utc=_NOW,
    )

    assert first.manifest.revision != second.manifest.revision


def test_duplicate_binding_is_idempotent() -> None:
    binding = KpiDeliveryBinding(store_key='chancado', kpi_key='tonelaje')

    snapshot = project_kpi_latest(
        evaluation=_evaluation(_value_result('tonelaje', parsed_value='66,00')),
        bindings=(binding, binding),
        updated_at_utc=_NOW,
    )

    assert tuple(snapshot.stores['chancado']) == ('tonelaje',)
    assert snapshot.stores['chancado']['tonelaje'].status is KpiDeliveryStatus.OK


def test_same_kpi_can_be_projected_to_multiple_stores() -> None:
    snapshot = project_kpi_latest(
        evaluation=_evaluation(_value_result('tonelaje', parsed_value='66,00')),
        bindings=(
            KpiDeliveryBinding(store_key='chancado', kpi_key='tonelaje'),
            KpiDeliveryBinding(store_key='global', kpi_key='tonelaje'),
        ),
        updated_at_utc=_NOW,
    )

    assert snapshot.stores['chancado']['tonelaje'].value == '66,00'
    assert snapshot.stores['global']['tonelaje'].value == '66,00'
