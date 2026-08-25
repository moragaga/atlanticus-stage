from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol, runtime_checkable

from ada.processes.alarms_runtime.adoption import AlarmConfigurationRevision

RUNTIME_MANIFEST_SCHEMA_VERSION = 'alarm-runtime-manifest.v1'

RuntimeRevisionDocument = Mapping[str, object]


class RuntimeRevisionContractError(ValueError):
    pass


class RuntimeRevisionSourceError(RuntimeError):
    pass


class RuntimeRevisionCacheError(RuntimeError):
    pass


class RuntimeRevisionOrigin(StrEnum):
    CACHE_CURRENT = 'cache_current'
    SOURCE_CANDIDATE = 'source_candidate'
    CACHE_FALLBACK = 'cache_fallback'


@dataclass(frozen=True, slots=True)
class RuntimeManifest:
    schema_version: str
    alarm_configuration_revision: str
    tool_registry_revision: str
    published_at: datetime

    def __post_init__(self) -> None:
        schema_version = _required_text(self.schema_version, 'schema_version')
        if schema_version != RUNTIME_MANIFEST_SCHEMA_VERSION:
            raise RuntimeRevisionContractError('runtime manifest schema version is not supported')
        alarm_revision = _required_text(
            self.alarm_configuration_revision,
            'alarm_configuration_revision',
        )
        tool_revision = _required_text(self.tool_registry_revision, 'tool_registry_revision')
        _require_utc_datetime(self.published_at, 'published_at')
        object.__setattr__(self, 'schema_version', schema_version)
        object.__setattr__(self, 'alarm_configuration_revision', alarm_revision)
        object.__setattr__(self, 'tool_registry_revision', tool_revision)

    @property
    def revision_key(self) -> tuple[str, str]:
        return self.alarm_configuration_revision, self.tool_registry_revision


@dataclass(frozen=True, slots=True)
class RuntimeRevisionBundle:
    manifest: RuntimeManifest
    alarm_configuration: RuntimeRevisionDocument
    tool_registry: RuntimeRevisionDocument

    def __post_init__(self) -> None:
        if not isinstance(self.manifest, RuntimeManifest):
            raise TypeError('manifest must be a RuntimeManifest')
        if not isinstance(self.alarm_configuration, Mapping):
            raise TypeError('alarm_configuration must be a mapping')
        if not isinstance(self.tool_registry, Mapping):
            raise TypeError('tool_registry must be a mapping')

    @property
    def revision_key(self) -> tuple[str, str]:
        return self.manifest.revision_key


@dataclass(frozen=True, slots=True)
class RuntimeRevisionResolution:
    bundle: RuntimeRevisionBundle
    revision: AlarmConfigurationRevision
    origin: RuntimeRevisionOrigin

    def __post_init__(self) -> None:
        if not isinstance(self.bundle, RuntimeRevisionBundle):
            raise TypeError('bundle must be a RuntimeRevisionBundle')
        if not isinstance(self.revision, AlarmConfigurationRevision):
            raise TypeError('revision must be an AlarmConfigurationRevision')
        if not isinstance(self.origin, RuntimeRevisionOrigin):
            raise TypeError('origin must be a RuntimeRevisionOrigin')
        if self.bundle.revision_key != self.revision.revision_key:
            raise RuntimeRevisionContractError(
                'decoded configuration revision does not match runtime manifest'
            )

    @property
    def manifest(self) -> RuntimeManifest:
        return self.bundle.manifest

    @property
    def revision_key(self) -> tuple[str, str]:
        return self.bundle.revision_key


@runtime_checkable
class RuntimeRevisionSource(Protocol):
    def read_manifest(self) -> RuntimeManifest: ...

    def read_alarm_configuration(self, *, revision: str) -> RuntimeRevisionDocument: ...

    def read_tool_registry(self, *, revision: str) -> RuntimeRevisionDocument: ...


@runtime_checkable
class RuntimeRevisionDecoder(Protocol):
    def decode(self, *, bundle: RuntimeRevisionBundle) -> AlarmConfigurationRevision: ...


@runtime_checkable
class RuntimeRevisionCache(Protocol):
    def load_effective(self) -> RuntimeRevisionBundle | None: ...

    def replace_effective(self, *, bundle: RuntimeRevisionBundle) -> None: ...


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be a non-empty string')
    return value.strip()


def _require_utc_datetime(value: object, name: str) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f'{name} must be a datetime')
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f'{name} must be timezone-aware UTC')
