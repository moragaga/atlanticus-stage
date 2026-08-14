from dataclasses import FrozenInstanceError

import pytest
from atlanticus.integrations.pi.contracts import (
    NotPiiSource,
    PiCatalog,
    PiExtractionMode,
    PiMaterialization,
    PiTagDefinition,
    PiValueKind,
    PiWebApiSource,
)


def _interpolated_tag(
    *, alias: str = 'crusher_feed_rate', is_active: bool = True
) -> PiTagDefinition:
    return PiTagDefinition(
        tag_name='\\\\PISERVER\\Crusher Feed Rate',
        alias=alias,
        value_kind=PiValueKind.NUMBER,
        extraction_mode=PiExtractionMode.INTERPOLATED,
        materializations=(
            PiMaterialization.LATEST,
            PiMaterialization.DAILY,
            PiMaterialization.MONTHLY,
        ),
        is_active=is_active,
    )


def _recorded_tag(*, alias: str = 'crusher_state') -> PiTagDefinition:
    return PiTagDefinition(
        tag_name='\\\\PISERVER\\Crusher State',
        alias=alias,
        value_kind=PiValueKind.TEXT,
        extraction_mode=PiExtractionMode.RECORDED,
        materializations=(
            PiMaterialization.DAILY,
            PiMaterialization.MONTHLY,
        ),
    )


def test_tag_definition_contains_materialization_contract() -> None:
    tag = _interpolated_tag()

    assert tag.tag_name == '\\\\PISERVER\\Crusher Feed Rate'
    assert tag.alias == 'crusher_feed_rate'
    assert tag.value_kind is PiValueKind.NUMBER
    assert tag.extraction_mode is PiExtractionMode.INTERPOLATED
    assert tag.materializations == (
        PiMaterialization.LATEST,
        PiMaterialization.DAILY,
        PiMaterialization.MONTHLY,
    )
    assert tag.is_active is True


def test_tag_definition_is_immutable() -> None:
    tag = _interpolated_tag()

    with pytest.raises(FrozenInstanceError):
        tag.alias = 'changed'  # type: ignore[misc]


@pytest.mark.parametrize('field_name', ['tag_name', 'alias'])
@pytest.mark.parametrize('value', ['', ' ', ' value', 'value '])
def test_tag_definition_rejects_invalid_identifiers(field_name: str, value: str) -> None:
    values = {
        'tag_name': '\\\\PISERVER\\Crusher Feed Rate',
        'alias': 'crusher_feed_rate',
    }
    values[field_name] = value

    with pytest.raises(ValueError, match=field_name):
        PiTagDefinition(
            value_kind=PiValueKind.NUMBER,
            extraction_mode=PiExtractionMode.INTERPOLATED,
            materializations=(PiMaterialization.DAILY,),
            **values,
        )


def test_tag_definition_requires_value_kind_contract() -> None:
    with pytest.raises(TypeError, match='PiValueKind'):
        PiTagDefinition(
            tag_name='tag',
            alias='alias',
            value_kind='number',  # type: ignore[arg-type]
            extraction_mode=PiExtractionMode.INTERPOLATED,
            materializations=(PiMaterialization.DAILY,),
        )


def test_tag_definition_requires_extraction_mode_contract() -> None:
    with pytest.raises(TypeError, match='PiExtractionMode'):
        PiTagDefinition(
            tag_name='tag',
            alias='alias',
            value_kind=PiValueKind.NUMBER,
            extraction_mode='interpolated',  # type: ignore[arg-type]
            materializations=(PiMaterialization.DAILY,),
        )


def test_materializations_must_be_non_empty_tuple() -> None:
    with pytest.raises(TypeError, match='tuple'):
        PiTagDefinition(
            tag_name='tag',
            alias='alias',
            value_kind=PiValueKind.NUMBER,
            extraction_mode=PiExtractionMode.INTERPOLATED,
            materializations=[PiMaterialization.DAILY],  # type: ignore[arg-type]
        )

    with pytest.raises(ValueError, match='must not be empty'):
        PiTagDefinition(
            tag_name='tag',
            alias='alias',
            value_kind=PiValueKind.NUMBER,
            extraction_mode=PiExtractionMode.INTERPOLATED,
            materializations=(),
        )


def test_materializations_reject_invalid_values_and_duplicates() -> None:
    with pytest.raises(TypeError, match='PiMaterialization'):
        PiTagDefinition(
            tag_name='tag',
            alias='alias',
            value_kind=PiValueKind.NUMBER,
            extraction_mode=PiExtractionMode.INTERPOLATED,
            materializations=('daily',),  # type: ignore[arg-type]
        )

    with pytest.raises(ValueError, match='duplicates'):
        PiTagDefinition(
            tag_name='tag',
            alias='alias',
            value_kind=PiValueKind.NUMBER,
            extraction_mode=PiExtractionMode.INTERPOLATED,
            materializations=(PiMaterialization.DAILY, PiMaterialization.DAILY),
        )


def test_recorded_tags_cannot_materialize_latest() -> None:
    with pytest.raises(ValueError, match='Recorded PI tags'):
        PiTagDefinition(
            tag_name='tag',
            alias='alias',
            value_kind=PiValueKind.NUMBER,
            extraction_mode=PiExtractionMode.RECORDED,
            materializations=(PiMaterialization.LATEST,),
        )


def test_is_active_requires_bool() -> None:
    with pytest.raises(TypeError, match='is_active'):
        PiTagDefinition(
            tag_name='tag',
            alias='alias',
            value_kind=PiValueKind.NUMBER,
            extraction_mode=PiExtractionMode.INTERPOLATED,
            materializations=(PiMaterialization.DAILY,),
            is_active=1,  # type: ignore[arg-type]
        )


def test_notpii_source_has_no_interpolation_configuration() -> None:
    source = NotPiiSource()

    assert not hasattr(source, 'interpolation_seconds')


def test_pi_web_api_source_accepts_one_shared_interpolation() -> None:
    source = PiWebApiSource(interpolation_seconds=10)

    assert source.interpolation_seconds == 10


@pytest.mark.parametrize('value', [0, -1, True, 1.5, '10'])
def test_pi_web_api_source_rejects_invalid_interpolation(value: object) -> None:
    with pytest.raises(ValueError, match='positive integer'):
        PiWebApiSource(interpolation_seconds=value)  # type: ignore[arg-type]


def test_pi_web_api_source_can_omit_interpolation_for_recorded_only_catalog() -> None:
    catalog = PiCatalog(
        source=PiWebApiSource(),
        definitions=(_recorded_tag(),),
    )

    assert catalog.source.interpolation_seconds is None


def test_pi_web_api_catalog_requires_shared_interpolation_for_active_interpolated_tags() -> None:
    with pytest.raises(ValueError, match='requires interpolation_seconds'):
        PiCatalog(
            source=PiWebApiSource(),
            definitions=(_interpolated_tag(),),
        )


def test_inactive_interpolated_tags_do_not_force_web_api_interpolation() -> None:
    catalog = PiCatalog(
        source=PiWebApiSource(),
        definitions=(_interpolated_tag(is_active=False),),
    )

    assert catalog.source.interpolation_seconds is None


def test_notpii_catalog_does_not_require_interpolation() -> None:
    catalog = PiCatalog(
        source=NotPiiSource(),
        definitions=(_interpolated_tag(), _recorded_tag()),
    )

    assert isinstance(catalog.source, NotPiiSource)
    assert len(catalog.definitions) == 2


def test_catalog_requires_non_empty_tuple_of_definitions() -> None:
    with pytest.raises(TypeError, match='tuple'):
        PiCatalog(
            source=NotPiiSource(),
            definitions=[_interpolated_tag()],  # type: ignore[arg-type]
        )

    with pytest.raises(ValueError, match='must not be empty'):
        PiCatalog(source=NotPiiSource(), definitions=())


def test_catalog_rejects_invalid_definition_values() -> None:
    with pytest.raises(TypeError, match='PiTagDefinition'):
        PiCatalog(source=NotPiiSource(), definitions=('invalid',))  # type: ignore[arg-type]


def test_catalog_rejects_duplicate_aliases() -> None:
    with pytest.raises(ValueError, match='unique aliases'):
        PiCatalog(
            source=NotPiiSource(),
            definitions=(
                _interpolated_tag(alias='same_alias'),
                _recorded_tag(alias='same_alias'),
            ),
        )


def test_catalog_rejects_unknown_source() -> None:
    with pytest.raises(TypeError, match='source'):
        PiCatalog(source=object(), definitions=(_interpolated_tag(),))  # type: ignore[arg-type]
