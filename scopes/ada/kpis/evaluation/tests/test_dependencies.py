import pytest

from ada.kpis.evaluation import KpiDependencies, KpiDependencyNotRequestedError


def test_dependencies_expose_only_declared_values() -> None:
    dependencies = KpiDependencies({'real': 10.0, 'plan': 12.0})

    assert dependencies['real'] == 10.0
    assert tuple(dependencies) == ('real', 'plan')
    assert len(dependencies) == 2

    with pytest.raises(KpiDependencyNotRequestedError):
        _ = dependencies['other']
