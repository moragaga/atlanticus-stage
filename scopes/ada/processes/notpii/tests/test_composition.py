from types import SimpleNamespace

from ada.processes.notpii.composition import build_composition
from atlanticus.configuration import ConfigurationSource, ResolvedConfiguration
from atlanticus.integrations.pi.contracts import (
    NotPiiSource,
    PiCatalog,
    PiExtractionMode,
    PiMaterialization,
    PiTagDefinition,
    PiValueKind,
)
from atlanticus.kernel import Environment


def _catalog() -> PiCatalog:
    return PiCatalog(
        source=NotPiiSource(),
        definitions=(
            PiTagDefinition(
                tag_name='TAG_A',
                alias='tag_a',
                value_kind=PiValueKind.NUMBER,
                extraction_mode=PiExtractionMode.INTERPOLATED,
                materializations=(PiMaterialization.LATEST,),
            ),
        ),
    )


def _configuration(tmp_path) -> ResolvedConfiguration:
    values = {
        'ENVIRONMENT': 'local',
        'APPLICATION': 'ada',
        'VOLUMEN_PATH': str(tmp_path),
        'NOTPII_INTERPOLATED_SERVICE_BUS_CONNECTION_STRING': (
            'Endpoint=sb://one/;SharedAccessKeyName=a;SharedAccessKey=b'
        ),
        'NOTPII_INTERPOLATED_SERVICE_BUS_TOPIC_NAME': 'interpolated',
        'NOTPII_INTERPOLATED_SERVICE_BUS_SUBSCRIPTION_NAME': 'materialization',
        'NOTPII_INTERPOLATED_SERVICE_BUS_MAX_WAIT_TIME_SECONDS': '10',
        'NOTPII_RAW_BATCH_SIZE': '100000',
        'NOTPII_MAX_MESSAGE_COUNT': '10',
    }
    return ResolvedConfiguration(
        environment=Environment.from_value('local'),
        values=values,
        sources={key: ConfigurationSource.PROCESS for key in values},
    )


def test_composition_passes_notpii_identity_to_data_producer(monkeypatch, tmp_path) -> None:
    import ada.processes.notpii.composition as module

    captured = {}
    sentinel = SimpleNamespace(
        receivers={},
        job=SimpleNamespace(run_iteration=lambda context: None),
    )

    def fake_builder(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(module, 'build_notpii_data_producer', fake_builder)
    composition = build_composition(configuration=_configuration(tmp_path), catalog=_catalog())

    assert composition.producer is sentinel
    assert captured['producer_key'] == 'notpii'
    assert captured['dataset_namespace'] == ('pi', 'not_pii')
    assert captured['max_message_count'] == 10
    assert tuple(captured['service_buses']) == (PiExtractionMode.INTERPOLATED,)
