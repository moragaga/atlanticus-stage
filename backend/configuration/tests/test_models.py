from __future__ import annotations

from types import MappingProxyType

import pytest

from atlanticus.configuration import (
    ConfigurationSource,
    ConfigurationValueError,
    ConfigurationVariableSpec,
    ResolvedConfiguration,
)
from atlanticus.kernel import Environment


def test_resolved_configuration_is_immutable_and_hides_values_from_repr() -> None:
    configuration = ResolvedConfiguration(
        environment=Environment.from_value('local'),
        values={'ENVIRONMENT': 'local', 'TOKEN': 'top-secret'},
        sources={
            'ENVIRONMENT': ConfigurationSource.PROCESS,
            'TOKEN': ConfigurationSource.DOTENV,
        },
        sensitive_keys=frozenset({'TOKEN'}),
    )

    assert isinstance(configuration.values, MappingProxyType)
    assert configuration.require('TOKEN') == 'top-secret'
    assert configuration.to_dict()['TOKEN'] == '***'
    assert configuration.to_dict(mask_sensitive=False)['TOKEN'] == 'top-secret'
    assert 'top-secret' not in repr(configuration)

    with pytest.raises(TypeError):
        configuration.values['TOKEN'] = 'changed'  # type: ignore[index]


@pytest.mark.parametrize(
    ('value', 'expected'),
    [('true', True), ('YES', True), ('0', False), ('off', False)],
)
def test_boolean_conversion_is_strict(value: str, expected: bool) -> None:
    configuration = ResolvedConfiguration(
        environment=Environment.from_value('local'),
        values={'ENVIRONMENT': 'local', 'FLAG': value},
        sources={
            'ENVIRONMENT': ConfigurationSource.PROCESS,
            'FLAG': ConfigurationSource.PROCESS,
        },
    )

    assert configuration.get_bool('FLAG') is expected


def test_default_value_is_preserved_exactly() -> None:
    spec = ConfigurationVariableSpec(key='TOPIC', default='  fallback value  ')
    space_spec = ConfigurationVariableSpec(key='SPACE', default=' ')

    assert spec.default == '  fallback value  '
    assert space_spec.default == ' '


def test_invalid_boolean_and_integer_report_the_variable() -> None:
    configuration = ResolvedConfiguration(
        environment=Environment.from_value('local'),
        values={'ENVIRONMENT': 'local', 'FLAG': 'sometimes', 'COUNT': 'many'},
        sources={
            'ENVIRONMENT': ConfigurationSource.PROCESS,
            'FLAG': ConfigurationSource.PROCESS,
            'COUNT': ConfigurationSource.PROCESS,
        },
    )

    with pytest.raises(ConfigurationValueError, match='FLAG'):
        configuration.get_bool('FLAG')
    with pytest.raises(ConfigurationValueError, match='COUNT'):
        configuration.get_int('COUNT')


def test_sensitive_spec_cannot_define_a_default_secret() -> None:
    with pytest.raises(ConfigurationValueError, match='TOKEN'):
        ConfigurationVariableSpec(key='TOKEN', default='embedded', sensitive=True)


@pytest.mark.parametrize(
    'kwargs',
    [
        {'key': 123},
        {'key': 'TOPIC', 'required': 1},
        {'key': 'TOPIC', 'sensitive': 'yes'},
        {'key': 'TOPIC', 'default': 123},
    ],
)
def test_variable_spec_rejects_invalid_runtime_types(kwargs: dict[str, object]) -> None:
    with pytest.raises(ConfigurationValueError):
        ConfigurationVariableSpec(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    'kwargs',
    [
        {
            'environment': 'local',
            'values': {'ENVIRONMENT': 'local'},
            'sources': {'ENVIRONMENT': ConfigurationSource.PROCESS},
        },
        {
            'environment': Environment.from_value('local'),
            'values': {'TOKEN': 'value'},
            'sources': {'TOKEN': ConfigurationSource.PROCESS},
        },
        {
            'environment': Environment.from_value('local'),
            'values': {'ENVIRONMENT': 'dev'},
            'sources': {'ENVIRONMENT': ConfigurationSource.PROCESS},
        },
        {
            'environment': Environment.from_value('local'),
            'values': {'ENVIRONMENT': 'local', 'COUNT': 1},
            'sources': {
                'ENVIRONMENT': ConfigurationSource.PROCESS,
                'COUNT': ConfigurationSource.PROCESS,
            },
        },
        {
            'environment': Environment.from_value('local'),
            'values': {'ENVIRONMENT': 'local'},
            'sources': {'ENVIRONMENT': 'process'},
        },
        {
            'environment': Environment.from_value('local'),
            'values': {'ENVIRONMENT': 'local'},
            'sources': {'ENVIRONMENT': ConfigurationSource.PROCESS},
            'sensitive_keys': frozenset({'TOKEN'}),
        },
    ],
)
def test_resolved_configuration_rejects_invalid_contracts(kwargs: dict[str, object]) -> None:
    with pytest.raises(ConfigurationValueError):
        ResolvedConfiguration(**kwargs)  # type: ignore[arg-type]
