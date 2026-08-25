from datetime import UTC, datetime

from ada.kpis.core import (
    KpiArea,
    KpiEvaluation,
    KpiResult,
    KpiStatus,
    KpiValueKind,
    KpiWatermark,
)
from ada.kpis.delivery import KpiDeliveryConfiguration, KpiDeliveryStatus, project_kpi_latest

_NOW = datetime(2026, 8, 25, 10, 0, 1, tzinfo=UTC)


def _configuration(*, revision: str = 'config-1') -> KpiDeliveryConfiguration:
    return KpiDeliveryConfiguration.from_document(
        {
            'id': 'kpis',
            'partition_key': 'kpis',
            'document_type': 'ada_kpi_configuration_projection',
            'schema_version': 1,
            'revision': revision,
            'tool_projection_revision': 'tools-1',
            'configuration': {
                'bindings': [
                    {
                        'key': 'tonelaje',
                        'destination_keys': ['chancado', 'global'],
                        'latest_enabled': True,
                        'series_enabled': False,
                        'series_hours': None,
                    },
                    {
                        'key': 'produccion',
                        'destination_keys': ['chancado'],
                        'latest_enabled': True,
                        'series_enabled': False,
                        'series_hours': None,
                    },
                    {
                        'key': 'utilizacion',
                        'destination_keys': ['chancado'],
                        'latest_enabled': True,
                        'series_enabled': False,
                        'series_hours': None,
                    },
                    {
                        'key': 'configurado_sin_latest',
                        'destination_keys': ['chancado'],
                        'latest_enabled': True,
                        'series_enabled': False,
                        'series_hours': None,
                    },
                ]
            },
        }
    )


def _evaluation(watermark: KpiWatermark) -> KpiEvaluation:
    return KpiEvaluation(
        watermark=watermark,
        results=(
            KpiResult(
                key='tonelaje',
                area=KpiArea.PLANTA,
                status=KpiStatus.OK,
                value_kind=KpiValueKind.VALUE,
                persist_history=True,
                value=66.0,
                parsed_value='66,00',
            ),
            KpiResult(
                key='produccion',
                area=KpiArea.PLANTA,
                status=KpiStatus.OK,
                value_kind=KpiValueKind.JSON,
                persist_history=True,
                value={'actual': 100, 'plan': 120},
            ),
            KpiResult(
                key='utilizacion',
                area=KpiArea.PLANTA,
                status=KpiStatus.ERROR,
                value_kind=KpiValueKind.VALUE,
                persist_history=True,
                error='RuntimeError',
            ),
        ),
    )


def test_projects_value_json_error_and_missing_by_destination() -> None:
    watermark = KpiWatermark(datetime(2026, 8, 25, 10, 0, tzinfo=UTC))
    snapshot = project_kpi_latest(
        evaluation=_evaluation(watermark),
        configuration=_configuration(),
        watermark=watermark,
        published_at_utc=_NOW,
    )

    document = snapshot.as_document()

    assert document['id'] == 'latest'
    assert document['partition_id'] == 'kpis'
    assert document['document_type'] == 'ada_kpi_latest_delivery'
    assert document['destinations']['chancado'] == {
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
    assert snapshot.destinations['global']['tonelaje'].value == '66,00'


def test_empty_latest_configuration_projects_empty_destinations() -> None:
    config = KpiDeliveryConfiguration.from_document(
        {
            'id': 'kpis',
            'partition_key': 'kpis',
            'document_type': 'ada_kpi_configuration_projection',
            'schema_version': 1,
            'revision': 'config-empty',
            'tool_projection_revision': 'tools-1',
            'configuration': {'bindings': []},
        }
    )

    snapshot = project_kpi_latest(
        evaluation=None,
        configuration=config,
        watermark=None,
        published_at_utc=_NOW,
    )

    assert snapshot.destinations == {}


def test_revision_ignores_publish_time_but_includes_watermark() -> None:
    first_watermark = KpiWatermark(datetime(2026, 8, 25, 10, 0, tzinfo=UTC))
    second_watermark = KpiWatermark(datetime(2026, 8, 25, 10, 0, 30, tzinfo=UTC))
    first = project_kpi_latest(
        evaluation=_evaluation(first_watermark),
        configuration=_configuration(),
        watermark=first_watermark,
        published_at_utc=_NOW,
    )
    same = project_kpi_latest(
        evaluation=_evaluation(first_watermark),
        configuration=_configuration(),
        watermark=first_watermark,
        published_at_utc=datetime(2026, 8, 25, 10, 0, 20, tzinfo=UTC),
    )
    advanced = project_kpi_latest(
        evaluation=_evaluation(second_watermark),
        configuration=_configuration(),
        watermark=second_watermark,
        published_at_utc=datetime(2026, 8, 25, 10, 0, 31, tzinfo=UTC),
    )

    assert first.manifest.revision == same.manifest.revision
    assert advanced.manifest.revision != first.manifest.revision
    assert first.destinations['chancado']['utilizacion'].status is KpiDeliveryStatus.ERROR
