from __future__ import annotations

import pytest

from atlanticus.connectivity.storage import StorageConnectionStringCredential, StorageSettings
from atlanticus.data_producers.remanentes import (
    RemanentesStorageConnection,
    build_remanentes_data_producer,
)
from atlanticus.kernel import Environment
from atlanticus.runtime import RuntimeConfiguration

from .support import build_test_catalog


def _runtime(tmp_path) -> RuntimeConfiguration:
    return RuntimeConfiguration(
        environment=Environment.from_value('local'),
        application='ada',
        volume_path=tmp_path,
    )


def _connection() -> RemanentesStorageConnection:
    return RemanentesStorageConnection(
        settings=StorageSettings(
            credential=StorageConnectionStringCredential('UseDevelopmentStorage=true')
        ),
        container_name='dataproduct',
    )


def test_composition_uses_one_storage_client_for_all_streams(tmp_path) -> None:
    components = build_remanentes_data_producer(
        runtime_configuration=_runtime(tmp_path),
        definitions=build_test_catalog(),
        connection=_connection(),
        idle_seconds=30,
    )

    assert len(components.materializers) == 2
    assert all(item._source._client is components.storage for item in components.materializers)
    assert tuple(item.definition.stream_key for item in components.materializers) == (
        'stocks',
        'extraibles',
    )


def test_composition_rejects_duplicate_stream_keys(tmp_path) -> None:
    definition = build_test_catalog()[1]
    with pytest.raises(ValueError, match='stream keys must be unique'):
        build_remanentes_data_producer(
            runtime_configuration=_runtime(tmp_path),
            definitions=(definition, definition),
            connection=_connection(),
            idle_seconds=30,
        )
