from pathlib import Path

import pytest

from ada.kpis.persistence import KpiPersistencePaths
from atlanticus.json import JsonDocumentStore
from atlanticus.state import AtomicStateStore


@pytest.fixture
def paths(tmp_path: Path) -> KpiPersistencePaths:
    return KpiPersistencePaths(tmp_path / 'ada')


@pytest.fixture
def json_store() -> JsonDocumentStore:
    return JsonDocumentStore()


@pytest.fixture
def state_store(tmp_path: Path) -> AtomicStateStore:
    return AtomicStateStore(volume_path=tmp_path, application='ada')
