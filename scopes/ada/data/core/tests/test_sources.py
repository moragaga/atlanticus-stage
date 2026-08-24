from ada.data.core import DataSource


def test_pi_sources_are_provider_neutral() -> None:
    assert DataSource.PI_INTERPOLATED.value == 'pi.interpolated'
    assert DataSource.PI_RECORDED.value == 'pi.recorded'
    assert not hasattr(DataSource, 'NOTPII_INTERPOLATED')
    assert not hasattr(DataSource, 'NOTPII_RECORDED')
