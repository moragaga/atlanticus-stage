import pytest

from ada.kpis.core import KpiArea, KpiCatalog, KpiMode, KpiSource, KpiSpec
from ada.kpis.sources import PiSourceProvider
from ada.processes.kpis.errors import KpiProcessConfigurationError
from ada.processes.kpis.settings import (
    KpiProcessSettings,
    KpiSourceApplications,
    catalog_sources,
    configuration_specs,
)
from atlanticus.configuration import ConfigurationSource, ResolvedConfiguration
from atlanticus.kernel import Environment


def _configuration(**values):
    base = {
        'ENVIRONMENT': 'local',
        'APPLICATION': 'ada-kpis-local',
        'VOLUMEN_PATH': '/tmp/atlanticus',
        'PI_SOURCE': 'PI_WEB_API',
        'PI_APPLICATION': 'ada-pi-web-api-local',
        'KPI_POLL_INTERVAL_SECONDS': '1',
    }
    base.update(values)
    return ResolvedConfiguration(
        environment=Environment.from_value('local'),
        values=base,
        sources={key: ConfigurationSource.PROCESS for key in base},
    )


def _catalog(*sources: KpiSource) -> KpiCatalog:
    specs = tuple(
        KpiSpec(
            key=f'kpi_{index}',
            area=KpiArea.GENERAL,
            mode=KpiMode.LATEST_NUMBER,
            source=source,
            columns=('value',),
        )
        for index, source in enumerate(sources)
    )
    return KpiCatalog(specs=specs)


def test_pi_source_is_explicit_and_maps_to_provider() -> None:
    assert (
        KpiProcessSettings.from_configuration(_configuration(PI_SOURCE='PI_WEB_API')).pi_source
        is PiSourceProvider.PI_WEB_API
    )
    assert (
        KpiProcessSettings.from_configuration(
            _configuration(PI_SOURCE='NOTPII', PI_APPLICATION='ada-notpii-local')
        ).pi_source
        is PiSourceProvider.NOTPII
    )


def test_unknown_pi_source_is_rejected_without_normalization() -> None:
    with pytest.raises(KpiProcessConfigurationError, match='PI_SOURCE must be one of'):
        KpiProcessSettings.from_configuration(_configuration(PI_SOURCE='pi_web_api'))


def test_pi_application_is_explicit_and_not_derived_from_kpi_application() -> None:
    settings = KpiProcessSettings.from_configuration(
        _configuration(
            APPLICATION='ada-kpis-local',
            PI_APPLICATION='ada-notpii-local',
            PI_SOURCE='NOTPII',
        )
    )

    assert settings.source_applications.pi == 'ada-notpii-local'
    assert settings.source_applications.pi != 'ada-kpis-local'


def test_non_pi_applications_are_required_only_when_catalog_uses_the_source() -> None:
    settings = KpiProcessSettings.from_configuration(_configuration())
    settings.source_applications.validate_catalog(_catalog(KpiSource.PI_INTERPOLATED))

    with pytest.raises(KpiProcessConfigurationError, match='DISPATCH_APPLICATION is required'):
        settings.source_applications.validate_catalog(_catalog(KpiSource.DISPATCH_STD_SHIFT_STATE))

    routed = KpiSourceApplications(
        pi='ada-pi-web-api-local',
        dispatch='ada-dispatch-local',
    )
    routed.validate_catalog(_catalog(KpiSource.DISPATCH_STD_SHIFT_STATE))
    assert routed.application_for(KpiSource.DISPATCH_STD_SHIFT_STATE) == 'ada-dispatch-local'


def test_blank_optional_source_applications_are_treated_as_unconfigured() -> None:
    settings = KpiProcessSettings.from_configuration(
        _configuration(
            DISPATCH_APPLICATION='',
            BLOCKGRADE_APPLICATION='',
            REMANENTES_APPLICATION='',
            FABRICA_APPLICATION='',
        )
    )

    assert settings.source_applications.dispatch is None
    assert settings.source_applications.blockgrade is None
    assert settings.source_applications.remanentes is None
    assert settings.source_applications.fabrica is None


def test_source_application_contract_covers_every_typed_kpi_source() -> None:
    applications = KpiSourceApplications(
        pi='pi',
        dispatch='dispatch',
        blockgrade='blockgrade',
        remanentes='remanentes',
        fabrica='fabrica',
    )

    assert {source: applications.application_for(source) for source in KpiSource}


def test_catalog_sources_are_derived_from_spec_requirements() -> None:
    catalog = _catalog(
        KpiSource.PI_INTERPOLATED,
        KpiSource.REMANENTES_STOCKS,
    )

    assert catalog_sources(catalog) == (
        KpiSource.PI_INTERPOLATED,
        KpiSource.REMANENTES_STOCKS,
    )


def test_poll_interval_is_operational_only_and_must_be_positive() -> None:
    assert (
        KpiProcessSettings.from_configuration(
            _configuration(KPI_POLL_INTERVAL_SECONDS='7')
        ).poll_interval_seconds
        == 7
    )
    with pytest.raises(KpiProcessConfigurationError, match='greater than zero'):
        KpiProcessSettings.from_configuration(_configuration(KPI_POLL_INTERVAL_SECONDS='0'))


def test_configuration_contract_contains_no_watermark_tolerance_settings() -> None:
    keys = {spec.key for spec in configuration_specs()}
    assert 'PI_SOURCE' in keys
    assert 'PI_APPLICATION' in keys
    assert 'DISPATCH_APPLICATION' in keys
    assert 'BLOCKGRADE_APPLICATION' in keys
    assert 'REMANENTES_APPLICATION' in keys
    assert 'FABRICA_APPLICATION' in keys
    assert 'KPI_POLL_INTERVAL_SECONDS' in keys
    assert not any('TOLERANCE' in key for key in keys)
    assert not any('EXPECTED_PI' in key for key in keys)
    assert not any('MINIMUM_DELTA' in key for key in keys)
