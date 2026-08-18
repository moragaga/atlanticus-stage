import json
from pathlib import Path

import pytest

from atlanticus.data_producers.pi import PiDataProducerWebIdRegistryError, WebIdRegistry
from atlanticus.runtime import RuntimeConfiguration


def test_registry_path_uses_application_runtime_cache(tmp_path: Path) -> None:
    configuration = RuntimeConfiguration.from_sources(
        environ={
            'ENVIRONMENT': 'local',
            'APPLICATION': 'ada',
            'VOLUMEN_PATH': str(tmp_path),
        }
    )

    registry = WebIdRegistry.from_runtime_configuration(configuration)

    assert registry.path == tmp_path / 'ada' / '.runtime' / 'cache' / 'pi-web-api' / 'webids.json'


def test_registry_starts_empty_and_persists_incremental_entries(tmp_path: Path) -> None:
    path = tmp_path / 'webids.json'
    registry = WebIdRegistry(path=path)

    assert dict(registry.current()) == {}
    registry.merge({'TAG_A': 'WEB_A'})
    registry.merge({'TAG_B': 'WEB_B'})

    payload = json.loads(path.read_text(encoding='utf-8'))
    assert payload == {
        'schema_version': 1,
        'web_ids': {'TAG_A': 'WEB_A', 'TAG_B': 'WEB_B'},
    }
    assert dict(WebIdRegistry(path=path).current()) == {
        'TAG_A': 'WEB_A',
        'TAG_B': 'WEB_B',
    }


def test_registry_does_not_remove_unused_entries(tmp_path: Path) -> None:
    registry = WebIdRegistry(path=tmp_path / 'webids.json')
    registry.merge({'TAG_A': 'WEB_A', 'TAG_OLD': 'WEB_OLD'})

    registry.merge({'TAG_A': 'WEB_A'})

    assert dict(registry.current()) == {'TAG_A': 'WEB_A', 'TAG_OLD': 'WEB_OLD'}


def test_registry_updates_a_webid_when_a_future_refresh_resolves_a_new_value(
    tmp_path: Path,
) -> None:
    registry = WebIdRegistry(path=tmp_path / 'webids.json')
    registry.merge({'TAG_A': 'OLD'})

    registry.merge({'TAG_A': 'NEW'})

    assert dict(registry.current()) == {'TAG_A': 'NEW'}


def test_registry_rejects_corrupt_documents_instead_of_silently_rebuilding(tmp_path: Path) -> None:
    path = tmp_path / 'webids.json'
    path.write_text('{not-json', encoding='utf-8')

    with pytest.raises(PiDataProducerWebIdRegistryError, match='could not be read'):
        WebIdRegistry(path=path).current()


def test_registry_requires_an_absolute_path() -> None:
    with pytest.raises(PiDataProducerWebIdRegistryError, match='must be absolute'):
        WebIdRegistry(path='relative/webids.json')
