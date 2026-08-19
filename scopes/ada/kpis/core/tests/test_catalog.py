from __future__ import annotations

from collections.abc import Mapping

import pytest

from ada.kpis.core import KpiArea, KpiCatalog, KpiMode, KpiSpec, OverKpiSpec
from ada.kpis.core.values import KpiNativeValue


def _over(values: Mapping[str, KpiNativeValue]) -> KpiNativeValue:
    return values['base']


def test_catalog_preserves_base_then_over_order_and_history_keys() -> None:
    base = KpiSpec(
        key='base',
        area=KpiArea.MINA,
        mode=KpiMode.CONSTANT,
        constant_value=1,
        persist_history=False,
    )
    over = OverKpiSpec(
        key='final',
        area=KpiArea.MINA,
        dependencies=('base',),
        resolver=_over,
        persist_history=True,
    )
    catalog = KpiCatalog(specs=(base,), over_specs=(over,))

    assert catalog.keys == ('base', 'final')
    assert catalog.persisted_history_keys == ('final',)


def test_catalog_rejects_over_dependency_not_available_yet() -> None:
    base = KpiSpec(
        key='base',
        area=KpiArea.MINA,
        mode=KpiMode.CONSTANT,
        constant_value=1,
    )
    over = OverKpiSpec(
        key='final',
        area=KpiArea.MINA,
        dependencies=('missing',),
        resolver=_over,
    )
    with pytest.raises(ValueError, match='missing='):
        KpiCatalog(specs=(base,), over_specs=(over,))
