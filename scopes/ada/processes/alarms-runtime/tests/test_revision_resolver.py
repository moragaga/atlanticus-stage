from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

import pytest

from ada.alarms.core import AlarmEvaluation
from ada.processes.alarms_runtime import (
    RUNTIME_MANIFEST_SCHEMA_VERSION,
    AlarmConfigurationRevision,
    AlarmEvaluatorContract,
    AlarmEvaluatorRegistry,
    RuntimeManifest,
    RuntimeRevisionBundle,
    RuntimeRevisionCacheError,
    RuntimeRevisionContractError,
    RuntimeRevisionOrigin,
    RuntimeRevisionResolver,
    RuntimeRevisionResolverError,
    RuntimeRevisionSourceError,
    build_alarm_execution_session,
)
from tests.support import plan

PUBLISHED_AT = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


def _evaluator(_context) -> AlarmEvaluation | None:
    return None


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
        manifest=_manifest(alarm_revision=alarm_revision, tool_revision=tool_revision),
        alarm_configuration={'revision': alarm_revision},
        tool_registry={'revision': tool_revision},
    )


def _revision(bundle: RuntimeRevisionBundle) -> AlarmConfigurationRevision:
    alarm_revision, tool_revision = bundle.revision_key
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
    session = build_alarm_execution_session(
        alarm_configuration_revision=alarm_revision,
        tool_registry_revision=tool_revision,
        planned_alarms=(planned_alarm,),
        evaluator_registry=registry,
    )
    return AlarmConfigurationRevision(
        alarm_configuration_revision=alarm_revision,
        tool_registry_revision=tool_revision,
        defined_alarm_identities=session.identities,
        session=session,
    )


class Decoder:
    def decode(self, *, bundle: RuntimeRevisionBundle) -> AlarmConfigurationRevision:
        if (
            bundle.alarm_configuration.get('revision')
            != bundle.manifest.alarm_configuration_revision
        ):
            raise RuntimeRevisionContractError('alarm configuration revision mismatch')
        if bundle.tool_registry.get('revision') != bundle.manifest.tool_registry_revision:
            raise RuntimeRevisionContractError('tool registry revision mismatch')
        return _revision(bundle)


@dataclass
class Cache:
    bundle: RuntimeRevisionBundle | None = None
    replace_calls: list[RuntimeRevisionBundle] = field(default_factory=list)

    def load_effective(self) -> RuntimeRevisionBundle | None:
        return self.bundle

    def replace_effective(self, *, bundle: RuntimeRevisionBundle) -> None:
        self.replace_calls.append(bundle)
        self.bundle = bundle


@dataclass
class Source:
    manifest: RuntimeManifest | None = None
    alarm_documents: dict[str, dict[str, object]] = field(default_factory=dict)
    tool_documents: dict[str, dict[str, object]] = field(default_factory=dict)
    fail_manifest: bool = False
    fail_alarm: bool = False
    fail_tool: bool = False
    reads: list[tuple[str, str | None]] = field(default_factory=list)

    def read_manifest(self) -> RuntimeManifest:
        self.reads.append(('manifest', None))
        if self.fail_manifest:
            raise RuntimeRevisionSourceError('source unavailable')
        assert self.manifest is not None
        return self.manifest

    def read_alarm_configuration(self, *, revision: str) -> dict[str, object]:
        self.reads.append(('alarm', revision))
        if self.fail_alarm:
            raise RuntimeRevisionSourceError('alarm configuration unavailable')
        return self.alarm_documents[revision]

    def read_tool_registry(self, *, revision: str) -> dict[str, object]:
        self.reads.append(('tool', revision))
        if self.fail_tool:
            raise RuntimeRevisionSourceError('tool registry unavailable')
        return self.tool_documents[revision]


def _source_for(bundle: RuntimeRevisionBundle) -> Source:
    return Source(
        manifest=bundle.manifest,
        alarm_documents={
            bundle.manifest.alarm_configuration_revision: dict(bundle.alarm_configuration)
        },
        tool_documents={bundle.manifest.tool_registry_revision: dict(bundle.tool_registry)},
    )


def test_resolver_uses_cache_current_without_reading_source_artifacts() -> None:
    cached = _bundle()
    source = _source_for(cached)
    resolver = RuntimeRevisionResolver(source=source, decoder=Decoder(), cache=Cache(cached))

    resolution = resolver.resolve()

    assert resolution.origin is RuntimeRevisionOrigin.CACHE_CURRENT
    assert resolution.effective is resolution.target
    assert resolution.target.revision_key == ('AC-52', 'TR-18')
    assert source.reads == [('manifest', None)]


def test_resolver_treats_same_revision_key_as_current_even_if_published_at_changed() -> None:
    cached = _bundle()
    source = _source_for(cached)
    source.manifest = replace(
        cached.manifest, published_at=datetime(2026, 8, 25, 13, 0, tzinfo=UTC)
    )

    resolution = RuntimeRevisionResolver(
        source=source,
        decoder=Decoder(),
        cache=Cache(cached),
    ).resolve()

    assert resolution.origin is RuntimeRevisionOrigin.CACHE_CURRENT
    assert resolution.effective is resolution.target
    assert resolution.target.bundle.manifest.published_at == PUBLISHED_AT


def test_resolver_reads_exact_target_revisions_and_returns_source_candidate() -> None:
    cached = _bundle(alarm_revision='AC-51', tool_revision='TR-17')
    target = _bundle(alarm_revision='AC-52', tool_revision='TR-18')
    source = _source_for(target)
    cache = Cache(cached)

    resolution = RuntimeRevisionResolver(source=source, decoder=Decoder(), cache=cache).resolve()

    assert resolution.origin is RuntimeRevisionOrigin.SOURCE_CANDIDATE
    assert resolution.effective is not None
    assert resolution.effective.revision_key == ('AC-51', 'TR-17')
    assert resolution.target.revision_key == ('AC-52', 'TR-18')
    assert source.reads == [('manifest', None), ('alarm', 'AC-52'), ('tool', 'TR-18')]
    assert cache.replace_calls == []
    assert cache.bundle is cached


def test_resolver_returns_source_candidate_for_first_bootstrap() -> None:
    target = _bundle()

    resolution = RuntimeRevisionResolver(
        source=_source_for(target),
        decoder=Decoder(),
        cache=Cache(),
    ).resolve()

    assert resolution.origin is RuntimeRevisionOrigin.SOURCE_CANDIDATE
    assert resolution.effective is None
    assert resolution.target.revision_key == ('AC-52', 'TR-18')


def test_resolver_falls_back_to_valid_cache_when_manifest_is_unavailable() -> None:
    cached = _bundle(alarm_revision='AC-51')
    source = Source(fail_manifest=True)

    resolution = RuntimeRevisionResolver(
        source=source,
        decoder=Decoder(),
        cache=Cache(cached),
    ).resolve()

    assert resolution.origin is RuntimeRevisionOrigin.CACHE_FALLBACK
    assert resolution.effective is resolution.target
    assert resolution.target.revision_key == cached.revision_key


@pytest.mark.parametrize('failure', ['alarm', 'tool'])
def test_resolver_falls_back_when_candidate_bundle_cannot_be_completed(failure: str) -> None:
    cached = _bundle(alarm_revision='AC-51', tool_revision='TR-17')
    target = _bundle()
    source = _source_for(target)
    setattr(source, f'fail_{failure}', True)

    resolution = RuntimeRevisionResolver(
        source=source,
        decoder=Decoder(),
        cache=Cache(cached),
    ).resolve()

    assert resolution.origin is RuntimeRevisionOrigin.CACHE_FALLBACK
    assert resolution.effective is resolution.target
    assert resolution.target.revision_key == cached.revision_key


def test_resolver_falls_back_when_candidate_fails_cross_validation() -> None:
    cached = _bundle(alarm_revision='AC-51')
    target = _bundle()
    source = _source_for(target)
    source.alarm_documents['AC-52'] = {'revision': 'AC-999'}

    resolution = RuntimeRevisionResolver(
        source=source,
        decoder=Decoder(),
        cache=Cache(cached),
    ).resolve()

    assert resolution.origin is RuntimeRevisionOrigin.CACHE_FALLBACK
    assert resolution.effective is resolution.target
    assert resolution.target.revision_key == cached.revision_key


def test_resolver_without_cache_fails_when_source_is_unavailable() -> None:
    with pytest.raises(RuntimeRevisionResolverError, match='no effective cache'):
        RuntimeRevisionResolver(
            source=Source(fail_manifest=True),
            decoder=Decoder(),
            cache=Cache(),
        ).resolve()


def test_resolver_without_cache_fails_when_first_candidate_is_invalid() -> None:
    target = _bundle()
    source = _source_for(target)
    source.tool_documents['TR-18'] = {'revision': 'TR-999'}

    with pytest.raises(RuntimeRevisionResolverError, match='invalid'):
        RuntimeRevisionResolver(
            source=source,
            decoder=Decoder(),
            cache=Cache(),
        ).resolve()


def test_resolver_fails_closed_when_effective_cache_is_invalid() -> None:
    invalid_cache = _bundle()
    invalid_cache = RuntimeRevisionBundle(
        manifest=invalid_cache.manifest,
        alarm_configuration={'revision': 'AC-corrupt'},
        tool_registry=invalid_cache.tool_registry,
    )

    with pytest.raises(RuntimeRevisionCacheError, match='cache is invalid'):
        RuntimeRevisionResolver(
            source=_source_for(_bundle()),
            decoder=Decoder(),
            cache=Cache(invalid_cache),
        ).resolve()


def test_resolver_validates_dependency_contracts() -> None:
    source = _source_for(_bundle())
    cache = Cache()
    decoder = Decoder()

    with pytest.raises(TypeError, match='source'):
        RuntimeRevisionResolver(source=object(), decoder=decoder, cache=cache)
    with pytest.raises(TypeError, match='decoder'):
        RuntimeRevisionResolver(source=source, decoder=object(), cache=cache)
    with pytest.raises(TypeError, match='cache'):
        RuntimeRevisionResolver(source=source, decoder=decoder, cache=object())
