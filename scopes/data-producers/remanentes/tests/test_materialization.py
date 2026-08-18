from __future__ import annotations

from atlanticus.data_producers.remanentes.materialization import _build_dataset_definition

from .support import build_test_catalog


def test_dataset_route_preserves_legacy_daily_contract() -> None:
    dataset = _build_dataset_definition(build_test_catalog()[0])
    target = dataset.resolve_target(
        materialization='daily',
        partition={'year': '2026', 'month': '08', 'day': '11'},
    )

    assert dataset.resolve_route_segments(target) == (
        'remanentes',
        'stocks',
        'year=2026',
        'month=08',
        'day=11',
    )
