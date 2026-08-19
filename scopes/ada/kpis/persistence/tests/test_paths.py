from pathlib import Path

import pytest

from ada.kpis.persistence import KpiPersistencePaths, KpiPersistenceValidationError
from tests.support import watermark


def test_paths_follow_canonical_kpi_dataset_layout(tmp_path: Path) -> None:
    paths = KpiPersistencePaths(tmp_path / 'ada')

    assert paths.datasets_root == tmp_path / 'ada/datasets/kpis'
    assert paths.evaluations_root == tmp_path / 'ada/datasets/kpis/evaluations'
    assert paths.latest_path == tmp_path / 'ada/datasets/kpis/latest/data.json'
    assert paths.evaluation_path(watermark(20)) == (
        tmp_path / 'ada/datasets/kpis/evaluations/year=2026/month=08/day=19/20260819T181520Z.json'
    )


def test_paths_require_absolute_application_root() -> None:
    with pytest.raises(KpiPersistenceValidationError, match='must be absolute'):
        KpiPersistencePaths(Path('relative/ada'))
