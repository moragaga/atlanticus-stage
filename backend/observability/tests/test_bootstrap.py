from __future__ import annotations

import pytest

# import atlanticus.observability as observability
from atlanticus.observability import (
    ObservabilitySettings,
    close_observability,
    configure_volume_observability,
    resolve_observability_root,
)


def test_observability_root_is_derived_from_volume_path(tmp_path) -> None:
    assert resolve_observability_root(tmp_path, application='ada') == (tmp_path / 'ada' / 'logs')


def test_volume_bootstrap_requires_and_uses_volume_path(tmp_path) -> None:
    settings = ObservabilitySettings.build(
        application='ada',
        service='dispatch-ingestion-job',
        module='dispatch_ingestion',
        environment='local',
        volume_path=tmp_path,
    )

    configured = configure_volume_observability(
        settings=settings,
        include_console=False,
    )

    assert configured.settings.volume_path == tmp_path
    close_observability()


#
# def test_observability_does_not_expose_backend_resource_monitoring() -> None:
#     assert not hasattr(observability, 'ResourceMonitor')
#     assert not hasattr(observability, 'CgroupResourceSampler')


def test_volume_bootstrap_can_disable_file_logs_without_volume() -> None:
    settings = ObservabilitySettings.build(
        application='ada',
        service='web',
        environment='local',
        file_logs_enabled=False,
    )

    configured = configure_volume_observability(
        settings=settings,
        include_console=True,
    )

    assert configured.settings.file_logs_enabled is False
    assert configured.settings.volume_path is None
    close_observability()


def test_volume_bootstrap_still_requires_volume_when_file_logs_are_enabled() -> None:
    settings = ObservabilitySettings.build(
        application='ada',
        service='job',
        environment='local',
    )

    with pytest.raises(ValueError, match='when file logs are enabled'):
        configure_volume_observability(settings=settings, include_console=False)
