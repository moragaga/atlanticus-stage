from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from ada.alarms.core import AlarmEvaluation
from ada.processes.alarms_runtime import (
    RUNTIME_MANIFEST_SCHEMA_VERSION,
    AlarmConfigurationRevision,
    AlarmEvaluatorContract,
    AlarmEvaluatorRegistry,
    RuntimeManifest,
    RuntimeRevisionBundle,
    RuntimeRevisionCache,
    RuntimeRevisionCacheError,
    RuntimeRevisionContractError,
    RuntimeRevisionDecoder,
    RuntimeRevisionOrigin,
    RuntimeRevisionResolution,
    RuntimeRevisionSource,
    RuntimeRevisionSourceError,
    build_alarm_execution_session,
)
from tests.support import plan

PUBLISHED_AT = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


def _evaluator(_context) -> AlarmEvaluation | None:
    return None


def _revision(
    *,
    alarm_revision: str = 'AC-52',
    tool_revision: str = 'TR-18',
) -> AlarmConfigurationRevision:
    planned_alarm = replace(
        plan(),
        alarm_configuration_revision=alarm_revision,
        tool_registry_revision=tool_revision,
    )
    registry = AlarmEvaluatorRegistry(
        (
            AlarmEvaluatorContract(
                family_key=planned_alarm.identity.family_key,
                evaluator_key=planned_alarm.evaluator_key,
                evaluator=_evaluator,
            ),
        )
    )
    execution_session = build_alarm_execution_session(
        alarm_configuration_revision=alarm_revision,
        tool_registry_revision=tool_revision,
        planned_alarms=(planned_alarm,),
        evaluator_registry=registry,
    )
    return AlarmConfigurationRevision(
        alarm_configuration_revision=alarm_revision,
        tool_registry_revision=tool_revision,
        defined_alarm_identities=execution_session.identities,
        session=execution_session,
    )


def _manifest(
    *,
    alarm_revision: str = 'AC-52',
    tool_revision: str = 'TR-18',
) -> RuntimeManifest:
    return RuntimeManifest(
        schema_version=RUNTIME_MANIFEST_SCHEMA_VERSION,
        alarm_configuration_revision=alarm_revision,
        tool_registry_revision=tool_revision,
        published_at=PUBLISHED_AT,
    )


def _bundle(
    *,
    alarm_revision: str = 'AC-52',
    tool_revision: str = 'TR-18',
) -> RuntimeRevisionBundle:
    return RuntimeRevisionBundle(
        manifest=_manifest(
            alarm_revision=alarm_revision,
            tool_revision=tool_revision,
        ),
        alarm_configuration={'revision': alarm_revision},
        tool_registry={'revision': tool_revision},
    )


def test_runtime_manifest_normalizes_revisions_and_exposes_revision_key() -> None:
    manifest = RuntimeManifest(
        schema_version=f' {RUNTIME_MANIFEST_SCHEMA_VERSION} ',
        alarm_configuration_revision=' AC-52 ',
        tool_registry_revision=' TR-18 ',
        published_at=PUBLISHED_AT,
    )

    assert manifest.schema_version == RUNTIME_MANIFEST_SCHEMA_VERSION
    assert manifest.alarm_configuration_revision == 'AC-52'
    assert manifest.tool_registry_revision == 'TR-18'
    assert manifest.revision_key == ('AC-52', 'TR-18')


def test_runtime_manifest_published_at_does_not_change_revision_identity() -> None:
    first = _manifest()
    second = RuntimeManifest(
        schema_version=RUNTIME_MANIFEST_SCHEMA_VERSION,
        alarm_configuration_revision='AC-52',
        tool_registry_revision='TR-18',
        published_at=PUBLISHED_AT + timedelta(minutes=5),
    )

    assert first.revision_key == second.revision_key


def test_runtime_manifest_rejects_unsupported_schema_version() -> None:
    with pytest.raises(RuntimeRevisionContractError):
        RuntimeManifest(
            schema_version='alarm-runtime-manifest.v2',
            alarm_configuration_revision='AC-52',
            tool_registry_revision='TR-18',
            published_at=PUBLISHED_AT,
        )


@pytest.mark.parametrize(
    ('field', 'value'),
    [
        ('alarm_configuration_revision', ''),
        ('tool_registry_revision', '   '),
    ],
)
def test_runtime_manifest_requires_revision_identity(field: str, value: str) -> None:
    values = {
        'schema_version': RUNTIME_MANIFEST_SCHEMA_VERSION,
        'alarm_configuration_revision': 'AC-52',
        'tool_registry_revision': 'TR-18',
        'published_at': PUBLISHED_AT,
    }
    values[field] = value

    with pytest.raises(ValueError):
        RuntimeManifest(**values)


def test_runtime_manifest_requires_datetime_published_at() -> None:
    with pytest.raises(TypeError):
        RuntimeManifest(
            schema_version=RUNTIME_MANIFEST_SCHEMA_VERSION,
            alarm_configuration_revision='AC-52',
            tool_registry_revision='TR-18',
            published_at='2026-08-25T12:00:00Z',
        )


@pytest.mark.parametrize(
    'published_at',
    [
        datetime(2026, 8, 25, 12, 0),
        datetime(2026, 8, 25, 12, 0, tzinfo=timezone(timedelta(hours=-4))),
    ],
)
def test_runtime_manifest_requires_utc_published_at(published_at: datetime) -> None:
    with pytest.raises(ValueError):
        RuntimeManifest(
            schema_version=RUNTIME_MANIFEST_SCHEMA_VERSION,
            alarm_configuration_revision='AC-52',
            tool_registry_revision='TR-18',
            published_at=published_at,
        )


def test_runtime_revision_bundle_exposes_manifest_revision_key() -> None:
    bundle = _bundle()

    assert bundle.revision_key == ('AC-52', 'TR-18')


def test_runtime_revision_bundle_requires_runtime_manifest() -> None:
    with pytest.raises(TypeError):
        RuntimeRevisionBundle(
            manifest=object(),
            alarm_configuration={},
            tool_registry={},
        )


@pytest.mark.parametrize(
    ('field', 'value'),
    [
        ('alarm_configuration', object()),
        ('tool_registry', object()),
    ],
)
def test_runtime_revision_bundle_requires_mapping_documents(field: str, value: object) -> None:
    values = {
        'manifest': _manifest(),
        'alarm_configuration': {},
        'tool_registry': {},
    }
    values[field] = value

    with pytest.raises(TypeError):
        RuntimeRevisionBundle(**values)


def test_runtime_revision_resolution_exposes_manifest_and_revision_key() -> None:
    resolution = RuntimeRevisionResolution(
        bundle=_bundle(),
        revision=_revision(),
        origin=RuntimeRevisionOrigin.SOURCE_CANDIDATE,
    )

    assert resolution.manifest == resolution.bundle.manifest
    assert resolution.revision_key == ('AC-52', 'TR-18')


def test_runtime_revision_resolution_rejects_decoded_revision_mismatch() -> None:
    with pytest.raises(RuntimeRevisionContractError):
        RuntimeRevisionResolution(
            bundle=_bundle(),
            revision=_revision(alarm_revision='AC-51'),
            origin=RuntimeRevisionOrigin.SOURCE_CANDIDATE,
        )


@pytest.mark.parametrize(
    ('field', 'value'),
    [
        ('bundle', object()),
        ('revision', object()),
        ('origin', 'source_candidate'),
    ],
)
def test_runtime_revision_resolution_requires_contract_types(field: str, value: object) -> None:
    values = {
        'bundle': _bundle(),
        'revision': _revision(),
        'origin': RuntimeRevisionOrigin.SOURCE_CANDIDATE,
    }
    values[field] = value

    with pytest.raises(TypeError):
        RuntimeRevisionResolution(**values)


def test_runtime_revision_operational_errors_are_runtime_errors() -> None:
    assert issubclass(RuntimeRevisionSourceError, RuntimeError)
    assert issubclass(RuntimeRevisionCacheError, RuntimeError)


def test_runtime_revision_origin_values_are_stable() -> None:
    assert RuntimeRevisionOrigin.CACHE_CURRENT.value == 'cache_current'
    assert RuntimeRevisionOrigin.SOURCE_CANDIDATE.value == 'source_candidate'
    assert RuntimeRevisionOrigin.CACHE_FALLBACK.value == 'cache_fallback'


def test_runtime_revision_source_protocol_requires_exact_revision_reads() -> None:
    class Source:
        def read_manifest(self) -> RuntimeManifest:
            return _manifest()

        def read_alarm_configuration(self, *, revision: str) -> dict[str, object]:
            return {'revision': revision}

        def read_tool_registry(self, *, revision: str) -> dict[str, object]:
            return {'revision': revision}

    assert isinstance(Source(), RuntimeRevisionSource)


def test_runtime_revision_decoder_protocol_accepts_bundle() -> None:
    class Decoder:
        def decode(self, *, bundle: RuntimeRevisionBundle) -> AlarmConfigurationRevision:
            return _revision(
                alarm_revision=bundle.manifest.alarm_configuration_revision,
                tool_revision=bundle.manifest.tool_registry_revision,
            )

    assert isinstance(Decoder(), RuntimeRevisionDecoder)


def test_runtime_revision_cache_protocol_owns_effective_bundle_only() -> None:
    class Cache:
        def load_effective(self) -> RuntimeRevisionBundle | None:
            return None

        def replace_effective(self, *, bundle: RuntimeRevisionBundle) -> None:
            self.bundle = bundle

    assert isinstance(Cache(), RuntimeRevisionCache)
