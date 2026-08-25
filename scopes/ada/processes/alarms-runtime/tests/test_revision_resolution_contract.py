from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from ada.alarms.core import AlarmEvaluation
from ada.processes.alarms_runtime import (
    RUNTIME_MANIFEST_SCHEMA_VERSION,
    AlarmConfigurationRevision,
    AlarmEvaluatorContract,
    AlarmEvaluatorRegistry,
    ResolvedRuntimeRevision,
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


def _resolved(
    *,
    alarm_revision: str = 'AC-52',
    tool_revision: str = 'TR-18',
) -> ResolvedRuntimeRevision:
    return ResolvedRuntimeRevision(
        bundle=_bundle(
            alarm_revision=alarm_revision,
            tool_revision=tool_revision,
        ),
        revision=_revision(
            alarm_revision=alarm_revision,
            tool_revision=tool_revision,
        ),
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


def test_resolved_runtime_revision_exposes_manifest_and_revision_key() -> None:
    resolved = _resolved()

    assert resolved.manifest == resolved.bundle.manifest
    assert resolved.revision_key == ('AC-52', 'TR-18')


def test_resolved_runtime_revision_rejects_decoded_revision_mismatch() -> None:
    with pytest.raises(RuntimeRevisionContractError):
        ResolvedRuntimeRevision(
            bundle=_bundle(),
            revision=_revision(alarm_revision='AC-51'),
        )


@pytest.mark.parametrize(
    ('field', 'value'),
    [
        ('bundle', object()),
        ('revision', object()),
    ],
)
def test_resolved_runtime_revision_requires_contract_types(field: str, value: object) -> None:
    values = {
        'bundle': _bundle(),
        'revision': _revision(),
    }
    values[field] = value

    with pytest.raises(TypeError):
        ResolvedRuntimeRevision(**values)


def test_runtime_revision_resolution_exposes_effective_and_target() -> None:
    effective = _resolved(alarm_revision='AC-51', tool_revision='TR-17')
    target = _resolved()
    resolution = RuntimeRevisionResolution(
        origin=RuntimeRevisionOrigin.SOURCE_CANDIDATE,
        effective=effective,
        target=target,
    )

    assert resolution.effective is effective
    assert resolution.target is target


def test_runtime_revision_resolution_allows_first_bootstrap_candidate() -> None:
    target = _resolved()

    resolution = RuntimeRevisionResolution(
        origin=RuntimeRevisionOrigin.SOURCE_CANDIDATE,
        effective=None,
        target=target,
    )

    assert resolution.effective is None
    assert resolution.target is target


@pytest.mark.parametrize(
    'origin',
    [RuntimeRevisionOrigin.CACHE_CURRENT, RuntimeRevisionOrigin.CACHE_FALLBACK],
)
def test_cache_resolution_requires_effective_revision(origin: RuntimeRevisionOrigin) -> None:
    with pytest.raises(RuntimeRevisionContractError, match='requires an effective'):
        RuntimeRevisionResolution(
            origin=origin,
            effective=None,
            target=_resolved(),
        )


@pytest.mark.parametrize(
    'origin',
    [RuntimeRevisionOrigin.CACHE_CURRENT, RuntimeRevisionOrigin.CACHE_FALLBACK],
)
def test_cache_resolution_target_must_match_effective(origin: RuntimeRevisionOrigin) -> None:
    with pytest.raises(RuntimeRevisionContractError, match='must match'):
        RuntimeRevisionResolution(
            origin=origin,
            effective=_resolved(alarm_revision='AC-51'),
            target=_resolved(alarm_revision='AC-52'),
        )


def test_source_candidate_must_differ_from_effective_revision() -> None:
    resolved = _resolved()

    with pytest.raises(RuntimeRevisionContractError, match='must differ'):
        RuntimeRevisionResolution(
            origin=RuntimeRevisionOrigin.SOURCE_CANDIDATE,
            effective=resolved,
            target=resolved,
        )


@pytest.mark.parametrize(
    ('field', 'value'),
    [
        ('origin', 'source_candidate'),
        ('effective', object()),
        ('target', object()),
    ],
)
def test_runtime_revision_resolution_requires_contract_types(field: str, value: object) -> None:
    values = {
        'origin': RuntimeRevisionOrigin.SOURCE_CANDIDATE,
        'effective': _resolved(alarm_revision='AC-51'),
        'target': _resolved(),
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
