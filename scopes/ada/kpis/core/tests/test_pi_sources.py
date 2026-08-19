from ada.kpis.core import KpiSource


def test_pi_sources_are_provider_neutral() -> None:
    assert KpiSource.PI_INTERPOLATED.value == 'pi.interpolated'
    assert KpiSource.PI_RECORDED.value == 'pi.recorded'
    assert not hasattr(KpiSource, 'NOTPII_INTERPOLATED')
    assert not hasattr(KpiSource, 'NOTPII_RECORDED')
