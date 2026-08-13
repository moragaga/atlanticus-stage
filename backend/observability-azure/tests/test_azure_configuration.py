from __future__ import annotations

import math

import pytest

from atlanticus.observability_azure import (
    AzureObservabilityConfigurationError,
    AzureObservabilityMode,
    AzureObservabilityProfile,
    AzureObservabilitySettings,
)


def test_defaults_are_off_and_slim() -> None:
    settings = AzureObservabilitySettings.from_sources(environ={})

    assert settings.mode is AzureObservabilityMode.OFF
    assert settings.profile is AzureObservabilityProfile.SLIM
    assert not settings.tracing_enabled


def test_export_requires_the_global_connection_string() -> None:
    with pytest.raises(AzureObservabilityConfigurationError, match='required'):
        AzureObservabilitySettings.from_sources(
            environ={'ATLANTICUS_AZURE_OBSERVABILITY_MODE': 'export'}
        )


def test_connection_string_is_not_exposed_by_repr() -> None:
    settings = AzureObservabilitySettings.from_sources(
        environ={
            'ATLANTICUS_AZURE_OBSERVABILITY_MODE': 'export',
            'ATLANTICUS_AZURE_OBSERVABILITY_PROFILE': 'diagnostic',
            'APPLICATION_INSIGHTS_CONNECTION_STRING': 'InstrumentationKey=secret',
        }
    )

    assert settings.tracing_enabled
    assert 'InstrumentationKey=secret' not in repr(settings)


@pytest.mark.parametrize('mode', ['export', 1, None])
def test_direct_settings_require_mode_enum(mode) -> None:
    with pytest.raises(TypeError, match='mode'):
        AzureObservabilitySettings(mode=mode)


@pytest.mark.parametrize('profile', ['diagnostic', 1, None])
def test_direct_settings_require_profile_enum(profile) -> None:
    with pytest.raises(TypeError, match='profile'):
        AzureObservabilitySettings(profile=profile)


@pytest.mark.parametrize('timeout', [True, '3', None])
def test_timeout_requires_a_number(timeout) -> None:
    with pytest.raises(TypeError, match='flush_timeout_seconds'):
        AzureObservabilitySettings(flush_timeout_seconds=timeout)


@pytest.mark.parametrize('timeout', [0, -1, math.nan, math.inf])
def test_timeout_requires_a_positive_finite_value(timeout) -> None:
    with pytest.raises(ValueError, match='flush_timeout_seconds'):
        AzureObservabilitySettings(flush_timeout_seconds=timeout)


@pytest.mark.parametrize(
    'mode',
    [AzureObservabilityMode.OFF, AzureObservabilityMode.PREVIEW],
)
def test_non_export_modes_discard_connection_string(mode) -> None:
    settings = AzureObservabilitySettings(
        mode=mode,
        connection_string='InstrumentationKey=must-not-be-retained',
    )

    assert settings.connection_string is None


def test_from_sources_does_not_read_connection_string_when_export_is_disabled() -> None:
    class _Environment(dict):
        def get(self, key, default=None):
            if key == 'APPLICATION_INSIGHTS_CONNECTION_STRING':
                raise AssertionError('connection string must not be read')
            return super().get(key, default)

    settings = AzureObservabilitySettings.from_sources(
        environ=_Environment({'ATLANTICUS_AZURE_OBSERVABILITY_MODE': 'preview'})
    )

    assert settings.connection_string is None


def test_connection_string_is_preserved_exactly() -> None:
    connection_string = '  InstrumentationKey=secret  '

    settings = AzureObservabilitySettings.from_sources(
        environ={
            'ATLANTICUS_AZURE_OBSERVABILITY_MODE': 'export',
            'APPLICATION_INSIGHTS_CONNECTION_STRING': connection_string,
        }
    )

    assert settings.connection_string == connection_string


def test_from_sources_requires_an_explicit_mapping() -> None:
    with pytest.raises(TypeError, match='environ must be a mapping'):
        AzureObservabilitySettings.from_sources(environ=None)


def test_environment_values_must_be_strings() -> None:
    with pytest.raises(AzureObservabilityConfigurationError, match='must be a string'):
        AzureObservabilitySettings.from_sources(environ={'ATLANTICUS_AZURE_OBSERVABILITY_MODE': 1})
