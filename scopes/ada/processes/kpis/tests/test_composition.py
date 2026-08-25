import pytest

from ada.data.core import DataColumn, DataColumnType, DataPartition, DataSource
from ada.data.sources import PiSourceProvider
from ada.kpis.core import KpiArea, KpiCatalog, KpiMode, KpiSpec
from ada.processes.kpis.composition import build_composition
from ada.processes.kpis.errors import KpiProcessConfigurationError
from atlanticus.configuration import ConfigurationSource, ResolvedConfiguration
from atlanticus.kernel import Environment
from atlanticus.state import AtomicStateStore, StateKey


def _catalog(source=DataSource.PI_INTERPOLATED) -> KpiCatalog:
    return KpiCatalog(
        specs=(
            KpiSpec(
                key='value',
                area=KpiArea.GENERAL,
                mode=KpiMode.LATEST_NUMBER,
                source=source,
                partition=DataPartition.LATEST,
                columns=(DataColumn('value', DataColumnType.FLOAT),),
            ),
        )
    )


def _configuration(tmp_path, *, pi_source='PI_WEB_API', poll='1', **extra) -> ResolvedConfiguration:
    values = {
        'ENVIRONMENT': 'local',
        'APPLICATION': 'ada-kpis-local',
        'VOLUMEN_PATH': str(tmp_path),
        'PI_SOURCE': pi_source,
        'PI_APPLICATION': 'ada-pi-web-api-local',
        'KPI_POLL_INTERVAL_SECONDS': poll,
        **extra,
    }
    return ResolvedConfiguration(
        environment=Environment.from_value('local'),
        values=values,
        sources={key: ConfigurationSource.PROCESS for key in values},
    )


def test_composition_uses_explicit_pi_provider_application_and_poll_interval(tmp_path) -> None:
    composition = build_composition(
        configuration=_configuration(
            tmp_path,
            pi_source='NOTPII',
            poll='7',
            PI_APPLICATION='ada-notpii-local',
        ),
        catalog=_catalog(),
    )

    assert composition.settings.pi_source is PiSourceProvider.NOTPII
    assert composition.settings.source_applications.pi == 'ada-notpii-local'
    assert composition.job_definition.sleep_seconds == 7
    assert composition.runtime_configuration.application_root == tmp_path / 'ada-kpis-local'


def test_pi_clock_reads_selected_provider_application_not_kpi_application(tmp_path) -> None:
    pi_store = AtomicStateStore(volume_path=tmp_path, application='ada-pi-web-api-local')
    pi_store.replace(
        StateKey(namespace=('sources',), name='pi-web-api'),
        {
            'source': 'pi-web-api',
            'source_watermark_utc': '2026-08-19T20:10:20Z',
        },
    )
    composition = build_composition(
        configuration=_configuration(tmp_path),
        catalog=_catalog(),
    )

    snapshot = composition.job._clock.current()

    assert snapshot.watermark.text == '2026-08-19T20:10:20Z'
    kpi_store = AtomicStateStore(volume_path=tmp_path, application='ada-kpis-local')
    assert kpi_store.read(StateKey(namespace=('sources',), name='pi-web-api')) is None


def test_composition_requires_non_pi_application_only_when_catalog_uses_it(tmp_path) -> None:
    configuration = _configuration(tmp_path)

    build_composition(configuration=configuration, catalog=_catalog())

    with pytest.raises(KpiProcessConfigurationError, match='application route is not configured'):
        build_composition(
            configuration=configuration,
            catalog=_catalog(DataSource.REMANENTES_STOCKS),
        )
