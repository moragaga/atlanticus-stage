import pytest

from ada.data.core import DataColumn, DataColumnType, DataPartition, DataSource
from ada.kpis.core import KpiArea, KpiMode, KpiSpec, OverKpiSpec
from ada.processes.kpis.catalog import registry
from ada.processes.kpis.errors import KpiProcessCatalogError


def _resolver(_):
    return 1


def _base(key: str) -> KpiSpec:
    return KpiSpec(
        key=key,
        area=KpiArea.GENERAL,
        mode=KpiMode.LATEST_NUMBER,
        source=DataSource.PI_INTERPOLATED,
        partition=DataPartition.LATEST,
        columns=(DataColumn('value', DataColumnType.FLOAT),),
    )


def test_initial_catalog_is_deliberately_empty() -> None:
    assert registry.KPI_SPECS == ()
    assert registry.OVER_KPI_SPECS == ()
    with pytest.raises(KpiProcessCatalogError, match='at least one configured KPI'):
        registry.build_catalog()


def test_registry_accepts_over_dependencies_on_base_kpis(monkeypatch) -> None:
    base = _base('base')
    over = OverKpiSpec(
        key='over',
        area=KpiArea.GENERAL,
        dependencies=('base',),
        resolver=_resolver,
    )
    monkeypatch.setattr(registry, 'KPI_SPECS', (base,))
    monkeypatch.setattr(registry, 'OVER_KPI_SPECS', (over,))

    catalog = registry.build_catalog()

    assert catalog.specs == (base,)
    assert catalog.over_specs == (over,)


def test_registry_preserves_explicit_order_for_prior_over_dependencies(monkeypatch) -> None:
    base = _base('base')
    first = OverKpiSpec(
        key='first',
        area=KpiArea.GENERAL,
        dependencies=('base',),
        resolver=_resolver,
    )
    second = OverKpiSpec(
        key='second',
        area=KpiArea.GENERAL,
        dependencies=('first',),
        resolver=_resolver,
    )
    monkeypatch.setattr(registry, 'KPI_SPECS', (base,))
    monkeypatch.setattr(registry, 'OVER_KPI_SPECS', (first, second))

    catalog = registry.build_catalog()

    assert catalog.over_specs == (first, second)


def test_registry_rejects_dependency_not_available_in_execution_order(
    monkeypatch,
) -> None:
    base = _base('base')
    over = OverKpiSpec(
        key='over',
        area=KpiArea.GENERAL,
        dependencies=('missing',),
        resolver=_resolver,
    )
    monkeypatch.setattr(registry, 'KPI_SPECS', (base,))
    monkeypatch.setattr(registry, 'OVER_KPI_SPECS', (over,))

    with pytest.raises(KpiProcessCatalogError) as error:
        registry.build_catalog()

    assert "missing=('missing',)" in str(error.value)
