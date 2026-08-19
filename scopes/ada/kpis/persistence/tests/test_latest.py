from ada.kpis.persistence import KpiLatestRepository
from tests.support import evaluation


def test_latest_repository_starts_empty(json_store, paths) -> None:
    repository = KpiLatestRepository(store=json_store, paths=paths)

    assert repository.read() is None


def test_latest_repository_replaces_complete_evaluation(json_store, paths) -> None:
    repository = KpiLatestRepository(store=json_store, paths=paths)
    first = evaluation(10)
    second = evaluation(20)

    repository.replace(first)
    repository.replace(second)

    assert repository.read() == second
    assert paths.latest_path.exists()
