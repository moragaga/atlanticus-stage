import pytest

from ada.data.core import DataSource
from ada.data.sources import DataSourceApplications, DataSourceRoutingError


def test_application_routes_are_shared_by_operational_source_family() -> None:
    routes = DataSourceApplications(
        pi='pi-app',
        dispatch='dispatch-app',
        blockgrade='blockgrade-app',
        remanentes='remanentes-app',
        fabrica='fabrica-app',
    )

    assert routes.application_for(DataSource.PI_INTERPOLATED) == 'pi-app'
    assert routes.application_for(DataSource.PI_RECORDED) == 'pi-app'
    assert routes.application_for(DataSource.DISPATCH_STD_SHIFT_STATE) == 'dispatch-app'
    assert (
        routes.application_for(DataSource.BLOCKGRADE_MMS_BLOCKGRADE_DETAILS_BUCKET)
        == 'blockgrade-app'
    )
    assert routes.application_for(DataSource.REMANENTES_STOCKS) == 'remanentes-app'
    assert routes.application_for(DataSource.FABRICA_PLANES) == 'fabrica-app'


def test_missing_application_route_fails_only_when_source_is_required() -> None:
    routes = DataSourceApplications(pi='pi-app')

    routes.validate_sources((DataSource.PI_INTERPOLATED, DataSource.PI_RECORDED))

    with pytest.raises(DataSourceRoutingError, match='application route is not configured'):
        routes.validate_sources((DataSource.DISPATCH_STD_SHIFT_STATE,))
