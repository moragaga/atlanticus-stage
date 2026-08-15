import pytest

from ada.processes.pi_web_api import PiWebApiCatalogError
from atlanticus.integrations.pi.contracts import (
    PiExtractionMode,
    PiMaterialization,
    PiTagDefinition,
    PiValueKind,
    PiWebApiSource,
)


def _definition() -> PiTagDefinition:
    return PiTagDefinition(
        tag_name='TAG_A',
        alias='a',
        value_kind=PiValueKind.NUMBER,
        extraction_mode=PiExtractionMode.INTERPOLATED,
        materializations=(PiMaterialization.DAILY,),
    )


def test_catalog_provider_uses_developer_definitions(monkeypatch) -> None:
    import ada.processes.pi_web_api.catalog.provider as provider

    monkeypatch.setattr(provider, 'SOURCE', PiWebApiSource(interpolation_seconds=20))
    monkeypatch.setattr(provider, 'DEFINITIONS', (_definition(),))

    catalog = provider.build_catalog()

    assert catalog.source.interpolation_seconds == 20
    assert catalog.definitions == (_definition(),)


def test_catalog_provider_requires_interpolation_even_for_recorded(monkeypatch) -> None:
    import ada.processes.pi_web_api.catalog.provider as provider

    recorded = PiTagDefinition(
        tag_name='TAG_R',
        alias='r',
        value_kind=PiValueKind.TEXT,
        extraction_mode=PiExtractionMode.RECORDED,
        materializations=(PiMaterialization.DAILY,),
    )
    monkeypatch.setattr(provider, 'SOURCE', PiWebApiSource(interpolation_seconds=None))
    monkeypatch.setattr(provider, 'DEFINITIONS', (recorded,))

    with pytest.raises(PiWebApiCatalogError, match='must define interpolation_seconds'):
        provider.build_catalog()


def test_catalog_provider_fails_until_developer_adds_definitions(monkeypatch) -> None:
    import ada.processes.pi_web_api.catalog.provider as provider

    monkeypatch.setattr(provider, 'SOURCE', PiWebApiSource(interpolation_seconds=10))
    monkeypatch.setattr(provider, 'DEFINITIONS', ())

    with pytest.raises(PiWebApiCatalogError, match='definitions must not be empty'):
        provider.build_catalog()
