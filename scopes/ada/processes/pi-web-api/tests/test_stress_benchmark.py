from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ada.processes.pi_web_api.composition import build_composition
from ada.processes.pi_web_api.errors import PiWebApiProcessConfigurationError
from ada.processes.pi_web_api.models import PiAcquisitionResult, PiSample
from ada.processes.pi_web_api.stress_benchmark import (
    PiStressBenchmarkAcquirer,
    PiStressBenchmarkJob,
    PiStressBenchmarkMaterializer,
    PiStressBenchmarkPlanner,
    PiStressBenchmarkSettings,
    _build_logical_catalog,
    _expand_acquisition,
    build_stress_physical_catalog,
    stress_physical_tag_count,
)
from atlanticus.configuration import ConfigurationSource, ResolvedConfiguration
from atlanticus.integrations.pi.contracts import PiExtractionMode, PiMaterialization


class _Context:
    def raise_if_cancelled(self) -> None:
        return None


def _with_stress(configuration, **overrides: str) -> ResolvedConfiguration:
    values = {
        **configuration.values,
        'APPLICATION': 'ada-pi-web-api-stress',
        'PI_WEB_API_STRESS_BENCHMARK': 'true',
        'PI_WEB_API_STRESS_LOGICAL_TAGS': '1000',
        'PI_WEB_API_STRESS_LOOKBACK_HOURS': '24',
        'PI_WEB_API_STRESS_END_UTC': '2026-08-14T23:59:50Z',
        'PI_WEB_API_STRESS_PHYSICAL_TAG_LIMIT': '0',
        **overrides,
    }
    sources = dict(configuration.sources)
    for key in values.keys() - configuration.values.keys():
        sources[key] = ConfigurationSource.PROCESS
    for key in overrides:
        sources[key] = ConfigurationSource.PROCESS
    sources['APPLICATION'] = ConfigurationSource.PROCESS
    return ResolvedConfiguration(
        environment=configuration.environment,
        values=values,
        sources=sources,
        sensitive_keys=configuration.sensitive_keys,
    )


def test_stress_benchmark_is_disabled_by_default(configuration) -> None:
    settings = PiStressBenchmarkSettings.from_configuration(configuration)

    assert settings.enabled is False
    assert settings.logical_tag_count == 1000
    assert settings.lookback_hours == 24
    assert settings.physical_tag_limit == 0
    assert settings.end_utc is None


def test_disabled_stress_ignores_inactive_benchmark_values(configuration) -> None:
    values = {
        **configuration.values,
        'PI_WEB_API_STRESS_BENCHMARK': 'false',
        'PI_WEB_API_STRESS_LOGICAL_TAGS': 'invalid',
        'PI_WEB_API_STRESS_END_UTC': 'invalid',
    }
    sources = dict(configuration.sources)
    sources['PI_WEB_API_STRESS_BENCHMARK'] = ConfigurationSource.PROCESS
    sources['PI_WEB_API_STRESS_LOGICAL_TAGS'] = ConfigurationSource.PROCESS
    sources['PI_WEB_API_STRESS_END_UTC'] = ConfigurationSource.PROCESS
    disabled = ResolvedConfiguration(
        environment=configuration.environment,
        values=values,
        sources=sources,
        sensitive_keys=configuration.sensitive_keys,
    )

    settings = PiStressBenchmarkSettings.from_configuration(disabled)

    assert settings.enabled is False


def test_stress_benchmark_requires_isolated_application(configuration) -> None:
    stressed = _with_stress(configuration, APPLICATION='ada')

    with pytest.raises(
        PiWebApiProcessConfigurationError,
        match='isolated stress or benchmark application',
    ):
        PiStressBenchmarkSettings.from_configuration(stressed)


def test_stress_catalog_uses_only_real_interpolated_monthly_tags() -> None:
    assert stress_physical_tag_count() == 185

    catalog = build_stress_physical_catalog(interpolation_seconds=10)

    assert len(catalog.definitions) == 185
    assert len({item.tag_name.casefold() for item in catalog.definitions}) == 185
    assert sum(item.value_kind.value == 'number' for item in catalog.definitions) == 140
    assert sum(item.value_kind.value == 'text' for item in catalog.definitions) == 45
    assert all(
        item.extraction_mode is PiExtractionMode.INTERPOLATED for item in catalog.definitions
    )
    assert all(
        item.materializations == (PiMaterialization.MONTHLY,) for item in catalog.definitions
    )


def test_stress_catalog_rejects_a_fake_physical_capacity() -> None:
    with pytest.raises(
        PiWebApiProcessConfigurationError,
        match='exceeds available stress tags',
    ):
        build_stress_physical_catalog(
            interpolation_seconds=10,
            physical_tag_limit=200,
        )


def test_stress_planner_walks_a_full_day_forward_one_hour_at_a_time() -> None:
    planner = PiStressBenchmarkPlanner(
        interpolation_seconds=10,
        max_recovery_seconds=3600,
        benchmark_end_utc=datetime(2026, 8, 14, 23, 59, 50, tzinfo=UTC),
        lookback_hours=24,
    )

    first = planner.plan(
        now_utc=datetime(2026, 8, 15, 10, 0, tzinfo=UTC),
        committed_watermark_utc=None,
    )
    assert first is not None
    assert first.first_slot_utc == datetime(2026, 8, 14, 0, 0, 0, tzinfo=UTC)
    assert first.last_slot_utc == datetime(2026, 8, 14, 0, 59, 50, tzinfo=UTC)
    assert first.slot_count == 360
    assert first.recovery_truncated is False

    second = planner.plan(
        now_utc=datetime(2026, 8, 15, 10, 0, tzinfo=UTC),
        committed_watermark_utc=first.last_slot_utc,
    )
    assert second is not None
    assert second.first_slot_utc == datetime(2026, 8, 14, 1, 0, 0, tzinfo=UTC)
    assert second.last_slot_utc == datetime(2026, 8, 14, 1, 59, 50, tzinfo=UTC)
    assert second.slot_count == 360

    committed = None
    windows = []
    for _ in range(24):
        window = planner.plan(
            now_utc=datetime(2026, 8, 15, 10, 0, tzinfo=UTC),
            committed_watermark_utc=committed,
        )
        assert window is not None
        windows.append(window)
        committed = window.last_slot_utc
    assert sum(window.slot_count for window in windows) == 8640
    assert committed == datetime(2026, 8, 14, 23, 59, 50, tzinfo=UTC)
    assert (
        planner.plan(
            now_utc=datetime(2026, 8, 15, 10, 0, tzinfo=UTC),
            committed_watermark_utc=committed,
        )
        is None
    )


def test_stress_fanout_expands_after_pi_without_duplicating_physical_tags() -> None:
    physical = build_stress_physical_catalog(
        interpolation_seconds=10,
        physical_tag_limit=2,
    )
    _, fanout = _build_logical_catalog(
        physical_catalog=physical,
        logical_tag_count=5,
    )
    first, second = physical.definitions
    acquisition = PiAcquisitionResult(
        interpolated=(
            PiSample(
                tag_name=first.tag_name,
                timestamp_utc=datetime(2026, 8, 14, 0, 0, 0, tzinfo=UTC),
                value=1.0,
            ),
            PiSample(
                tag_name=second.tag_name,
                timestamp_utc=datetime(2026, 8, 14, 0, 0, 0, tzinfo=UTC),
                value=2.0,
            ),
        ),
        recorded=(),
        interpolated_request_count=1,
    )

    expanded = _expand_acquisition(
        acquisition=acquisition,
        fanout=fanout,
        context=_Context(),
    )

    assert len(expanded.interpolated) == 5
    assert expanded.interpolated_request_count == 1
    assert {item.value for item in expanded.interpolated} == {1.0, 2.0}
    assert len({item.tag_name for item in expanded.interpolated}) == 5


def test_composition_uses_stress_pipeline_only_when_enabled(configuration) -> None:
    stressed = _with_stress(configuration)

    composition = build_composition(configuration=stressed)

    assert isinstance(composition.planner, PiStressBenchmarkPlanner)
    assert isinstance(composition.acquirer, PiStressBenchmarkAcquirer)
    assert isinstance(composition.materializer, PiStressBenchmarkMaterializer)
    assert isinstance(composition.job, PiStressBenchmarkJob)
    assert len(composition.catalog.definitions) == 185
