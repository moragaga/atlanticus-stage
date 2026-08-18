from types import SimpleNamespace

from atlanticus.data_producers.pi import build_pi_data_producer
from atlanticus.integrations.pi.web_api import PiWebApiLimits
from atlanticus.runtime import RuntimeConfiguration


class FakePoints:
    def resolve_web_ids(self, tag_names):
        return ()


class FakeStreamSets:
    def get_interpolated(self, web_ids, *, start_time_utc, end_time_utc, interpolation_seconds):
        return ()

    def get_recorded(self, web_ids, *, start_time_utc, end_time_utc):
        return ()


class FakeClient:
    def __init__(self) -> None:
        self.points = FakePoints()
        self.streamsets = FakeStreamSets()
        self.settings = SimpleNamespace(limits=PiWebApiLimits())


def test_builder_keeps_default_pi_paths_and_accepts_reusable_identity(tmp_path, catalog) -> None:
    configuration = RuntimeConfiguration.from_sources(
        environ={
            'ENVIRONMENT': 'local',
            'APPLICATION': 'ada',
            'VOLUMEN_PATH': str(tmp_path),
        }
    )

    default = build_pi_data_producer(
        runtime_configuration=configuration,
        catalog=catalog,
        client=FakeClient(),
    )
    custom = build_pi_data_producer(
        runtime_configuration=configuration,
        catalog=catalog,
        client=FakeClient(),
        producer_key='secondary-pi',
        dataset_namespace=('sources', 'secondary-pi'),
    )

    assert default.registry.path == (
        tmp_path / 'ada' / '.runtime' / 'cache' / 'pi-web-api' / 'webids.json'
    )
    assert custom.registry.path == (
        tmp_path / 'ada' / '.runtime' / 'cache' / 'secondary-pi' / 'webids.json'
    )
    assert default.materializer.dataset_for(
        catalog.definitions[0].extraction_mode
    ).key.namespace == (
        'pi',
        'web-api',
    )
    assert custom.materializer.dataset_for(
        catalog.definitions[0].extraction_mode
    ).key.namespace == (
        'sources',
        'secondary-pi',
    )
