from __future__ import annotations

import signal
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ada.alarms.core import (
    AlarmEvaluation,
    AlarmStatus,
    EvidenceContractRef,
    EvidenceSnapshot,
)
from ada.data.sources import DataSourceRegistry, LoadedDataSources
from ada.processes.alarms_runtime import (
    RUNTIME_MANIFEST_SCHEMA_VERSION,
    AlarmConfigurationAdoptionExecutor,
    AlarmConfigurationRevision,
    AlarmDurableInputConsumer,
    AlarmEvaluatorContract,
    AlarmEvaluatorRegistry,
    AlarmExecutionIteration,
    AlarmInputCursor,
    AlarmInputLocator,
    AlarmInputRecord,
    AlarmInputStream,
    AlarmOperationalCycle,
    AlarmRuntimeJobAdoptionOutcome,
    AlarmRuntimeJobComposition,
    AlarmRuntimeJobCompositionError,
    RuntimeManifest,
    RuntimeRevisionBundle,
    RuntimeRevisionContractError,
    RuntimeRevisionOrigin,
    RuntimeRevisionResolver,
    RuntimeRevisionSourceError,
    build_alarm_execution_session,
    build_alarm_runtime_composition,
)
from atlanticus.runtime import (
    JobDefinition,
    JobRuntimeContext,
    LeaseOwnershipLostError,
    RuntimeConfiguration,
)
from atlanticus.state import AtomicJsonStore
from tests.support import NOW, plan

PUBLISHED_AT = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


class CommitClock:
    def committed_at(self, *, cycle_at: datetime) -> datetime:
        return cycle_at + timedelta(seconds=1)


class EmptyInputSource:
    def read_after(
        self,
        *,
        stream: AlarmInputStream,
        cursor: AlarmInputCursor | None,
    ) -> tuple[AlarmInputRecord, ...]:
        return ()

    def read_at(
        self,
        *,
        stream: AlarmInputStream,
        locator: AlarmInputLocator,
    ) -> AlarmInputRecord:
        raise AssertionError('no pending input should be read')


class EmptyIterationSourceLoader:
    def load(self, *, plan, as_of: datetime) -> LoadedDataSources:
        return LoadedDataSources(
            as_of=as_of,
            plan=plan,
            registry=DataSourceRegistry({}),
            loaded={},
            failures={},
        )


@dataclass
class Cache:
    bundle: RuntimeRevisionBundle | None = None
    replace_calls: list[RuntimeRevisionBundle] = field(default_factory=list)
    replace_error: Exception | None = None

    def load_effective(self) -> RuntimeRevisionBundle | None:
        return self.bundle

    def replace_effective(self, *, bundle: RuntimeRevisionBundle) -> None:
        if self.replace_error is not None:
            raise self.replace_error
        self.replace_calls.append(bundle)
        self.bundle = bundle


@dataclass
class Source:
    manifest: RuntimeManifest
    documents: dict[tuple[str, str], dict[str, object]]
    fail_manifest: bool = False

    def read_manifest(self) -> RuntimeManifest:
        if self.fail_manifest:
            raise RuntimeRevisionSourceError('source unavailable')
        return self.manifest

    def read_alarm_configuration(self, *, revision: str) -> dict[str, object]:
        return self.documents[('alarm', revision)]

    def read_tool_registry(self, *, revision: str) -> dict[str, object]:
        return self.documents[('tool', revision)]


@dataclass
class Decoder:
    revisions: dict[tuple[str, str], AlarmConfigurationRevision]

    def decode(self, *, bundle: RuntimeRevisionBundle) -> AlarmConfigurationRevision:
        if (
            bundle.alarm_configuration.get('revision')
            != bundle.manifest.alarm_configuration_revision
        ):
            raise RuntimeRevisionContractError('alarm configuration revision mismatch')
        if bundle.tool_registry.get('revision') != bundle.manifest.tool_registry_revision:
            raise RuntimeRevisionContractError('tool registry revision mismatch')
        return self.revisions[bundle.revision_key]


@dataclass
class CycleFactory:
    composition: object
    sessions: list[object] = field(default_factory=list)
    occurrence_counter: int = 0
    episode_counter: int = 0

    def __call__(self, session):
        self.sessions.append(session)

        def occurrence_id(_identity, _at):
            self.occurrence_counter += 1
            return f'O{self.occurrence_counter}'

        def episode_id(_priority_group, _at):
            self.episode_counter += 1
            return f'E{self.episode_counter}'

        return AlarmOperationalCycle(
            session=session,
            composition=self.composition,
            occurrence_id_factory=occurrence_id,
            episode_id_factory=episode_id,
            commit_time_provider=CommitClock(),
            runtime_artifact_version='ada-alarms-runtime/0.14.1',
            technical_evidence_contract=EvidenceContractRef(
                contract_key='evaluation-error',
                contract_version='v1',
            ),
        )


def _manifest(alarm_revision: str, tool_revision: str = 'TR-18') -> RuntimeManifest:
    return RuntimeManifest(
        schema_version=RUNTIME_MANIFEST_SCHEMA_VERSION,
        alarm_configuration_revision=alarm_revision,
        tool_registry_revision=tool_revision,
        published_at=PUBLISHED_AT,
    )


def _bundle(alarm_revision: str, tool_revision: str = 'TR-18') -> RuntimeRevisionBundle:
    return RuntimeRevisionBundle(
        manifest=_manifest(alarm_revision, tool_revision),
        alarm_configuration={'revision': alarm_revision},
        tool_registry={'revision': tool_revision},
    )


def _revision(
    alarm_revision: str,
    *,
    tool_revision: str = 'TR-18',
    priority_group: str = 'mill-feed',
    priority_order: int = 1,
    executable: bool = True,
    defined: bool = True,
    status: AlarmStatus = AlarmStatus.INACTIVE,
) -> AlarmConfigurationRevision:
    planned = replace(
        plan(),
        priority_group=priority_group,
        priority_order=priority_order,
        alarm_configuration_revision=alarm_revision,
        tool_registry_revision=tool_revision,
    )
    contracts = ()
    plans = ()
    if executable:
        contracts = (
            AlarmEvaluatorContract(
                family_key=planned.identity.family_key,
                evaluator_key=planned.evaluator_key,
                evaluator=lambda context: AlarmEvaluation(
                    alarm_identity=planned.identity,
                    status=status,
                    evaluated_at=context.now,
                    evidence_snapshot=EvidenceSnapshot(
                        contract_key='threshold',
                        contract_version='v1',
                        payload={'status': status.value},
                    ),
                ),
            ),
        )
        plans = (planned,)
    session = build_alarm_execution_session(
        alarm_configuration_revision=alarm_revision,
        tool_registry_revision=tool_revision,
        planned_alarms=plans,
        evaluator_registry=AlarmEvaluatorRegistry(contracts),
    )
    return AlarmConfigurationRevision(
        alarm_configuration_revision=alarm_revision,
        tool_registry_revision=tool_revision,
        defined_alarm_identities=(planned.identity,) if defined else (),
        session=session,
    )


def _source_for(*bundles: RuntimeRevisionBundle, current: RuntimeRevisionBundle) -> Source:
    documents: dict[tuple[str, str], dict[str, object]] = {}
    for bundle in bundles:
        documents[('alarm', bundle.manifest.alarm_configuration_revision)] = dict(
            bundle.alarm_configuration
        )
        documents[('tool', bundle.manifest.tool_registry_revision)] = dict(bundle.tool_registry)
    return Source(manifest=current.manifest, documents=documents)


def _context(
    tmp_path: Path,
    *,
    fence_events: list[str] | None = None,
    strict_non_reentrant_fence: bool = False,
) -> JobRuntimeContext:
    configuration = RuntimeConfiguration.from_sources(
        environ={
            'ENVIRONMENT': 'local',
            'APPLICATION': 'ada-alarms-runtime-test',
            'VOLUMEN_PATH': str(tmp_path),
        }
    )
    context = JobRuntimeContext.create(
        definition=JobDefinition(
            module_name='ada.processes.alarms_runtime',
            service_name='alarms-runtime',
            job_key='alarms-runtime',
            iteration_timeout_seconds=10,
            execution_timeout_seconds=30,
            shutdown_grace_seconds=5,
            lease_timeout_seconds=10,
            lease_renew_seconds=3,
            lease_wait_seconds=0,
            resource_sample_seconds=1,
        ),
        configuration=configuration,
        run_id='11111111-1111-1111-1111-111111111111',
        correlation_id='22222222-2222-2222-2222-222222222222',
    )

    fence_state = {'active': False}

    def checker():
        if strict_non_reentrant_fence and fence_state['active']:
            raise AssertionError('authority must not be reacquired inside a mutation fence')

    @contextmanager
    def fence():
        if strict_non_reentrant_fence and fence_state['active']:
            raise AssertionError('mutation fence must not be re-entered')
        fence_state['active'] = True
        if fence_events is not None:
            fence_events.append('enter')
        try:
            yield
        finally:
            if fence_events is not None:
                fence_events.append('exit')
            fence_state['active'] = False

    context._bind_lease_authority(generation=1, checker=checker, fence=fence)
    return context


def _job(
    tmp_path: Path,
    *,
    source_revision: AlarmConfigurationRevision,
    target_revision: AlarmConfigurationRevision,
    cached_bundle: RuntimeRevisionBundle | None,
    target_bundle: RuntimeRevisionBundle,
    context: JobRuntimeContext,
    as_of: datetime = NOW,
):
    composition = build_alarm_runtime_composition(runtime_configuration=context.configuration)
    cache = Cache(cached_bundle)
    source_bundles = tuple(
        bundle for bundle in (cached_bundle, target_bundle) if bundle is not None
    )
    source = _source_for(*source_bundles, current=target_bundle)
    decoder = Decoder(
        {
            source_revision.revision_key: source_revision,
            target_revision.revision_key: target_revision,
        }
    )
    resolver = RuntimeRevisionResolver(source=source, decoder=decoder, cache=cache)
    adoption_executor = AlarmConfigurationAdoptionExecutor(
        composition=composition,
        commit_time_provider=CommitClock(),
        runtime_artifact_version='ada-alarms-runtime/0.14.1',
    )
    consumer = AlarmDurableInputConsumer(composition=composition, source=EmptyInputSource())
    cycle_factory = CycleFactory(composition)
    job = AlarmRuntimeJobComposition(
        composition=composition,
        revision_resolver=resolver,
        adoption_executor=adoption_executor,
        input_consumer=consumer,
        iteration_source_loader=EmptyIterationSourceLoader(),
        cycle_factory=cycle_factory,
        as_of_provider=lambda _context: as_of,
    )
    return job, composition, cache, cycle_factory, consumer


def test_recovery_hook_only_delegates_alarm_persistence_recovery(tmp_path: Path) -> None:
    context = _context(tmp_path)
    revision = _revision('AC-1')
    bundle = _bundle('AC-1')
    job, composition, _, _, _ = _job(
        tmp_path,
        source_revision=revision,
        target_revision=revision,
        cached_bundle=bundle,
        target_bundle=bundle,
        context=context,
    )

    result = job.recover(context)

    assert result.applied_count == 0
    assert composition.durability.persistence.read_head().aligned


def test_iteration_requires_recovery_hook_even_when_empty_journal_is_aligned(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    revision = _revision('AC-1')
    bundle = _bundle('AC-1')
    job, composition, cache, cycle_factory, _ = _job(
        tmp_path,
        source_revision=revision,
        target_revision=revision,
        cached_bundle=bundle,
        target_bundle=bundle,
        context=context,
    )

    assert composition.durability.persistence.read_head().aligned
    with pytest.raises(AlarmRuntimeJobCompositionError, match='recovery hook must complete'):
        job.iteration(context)

    assert cache.replace_calls == []
    assert cycle_factory.sessions == []


def test_lease_loss_after_durable_adoption_stops_before_cache_promotion_and_target_cycle(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    source_revision = _revision('AC-1', status=AlarmStatus.ACTIVE)
    target_revision = _revision('AC-2', executable=False, defined=True)
    source_bundle = _bundle('AC-1')
    target_bundle = _bundle('AC-2')
    job, composition, cache, cycle_factory, _ = _job(
        tmp_path,
        source_revision=source_revision,
        target_revision=target_revision,
        cached_bundle=source_bundle,
        target_bundle=target_bundle,
        context=context,
        as_of=NOW + timedelta(minutes=1),
    )
    cycle_factory(source_revision.session).execute(
        context,
        AlarmExecutionIteration(
            session=source_revision.session,
            loaded_sources=EmptyIterationSourceLoader().load(
                plan=source_revision.session.data_plan,
                as_of=NOW,
            ),
        ),
    )
    cycle_factory.sessions.clear()
    job.recover(context)
    durable_before_adoption = composition.durability.persistence.read_head().durable

    @contextmanager
    def lose_after_adoption():
        head = composition.durability.persistence.read_head()
        if head.aligned and head.durable != durable_before_adoption:
            raise LeaseOwnershipLostError('lease lost before cache promotion')
        yield

    context._lease_authority_fence = lose_after_adoption

    with pytest.raises(LeaseOwnershipLostError, match='cache promotion'):
        job.iteration(context)

    persisted = composition.durability.persistence.read_snapshot('mill-feed')
    assert persisted is not None
    assert 'mill/risk' not in persisted.as_document()['alarms']
    assert composition.durability.persistence.read_head().aligned
    assert cache.bundle is source_bundle
    assert cache.replace_calls == []
    assert cycle_factory.sessions == []


def test_recovery_replays_durable_adoption_after_cache_promotion_was_lost(
    tmp_path: Path,
) -> None:
    first_context = _context(tmp_path)
    source_revision = _revision('AC-1', status=AlarmStatus.ACTIVE)
    target_revision = _revision('AC-2', executable=False, defined=True)
    source_bundle = _bundle('AC-1')
    target_bundle = _bundle('AC-2')
    first_job, first_composition, first_cache, first_cycles, _ = _job(
        tmp_path,
        source_revision=source_revision,
        target_revision=target_revision,
        cached_bundle=source_bundle,
        target_bundle=target_bundle,
        context=first_context,
        as_of=NOW + timedelta(minutes=1),
    )
    first_cycles(source_revision.session).execute(
        first_context,
        AlarmExecutionIteration(
            session=source_revision.session,
            loaded_sources=EmptyIterationSourceLoader().load(
                plan=source_revision.session.data_plan,
                as_of=NOW,
            ),
        ),
    )
    first_cycles.sessions.clear()
    first_job.recover(first_context)
    durable_before_adoption = first_composition.durability.persistence.read_head().durable

    @contextmanager
    def lose_after_adoption():
        head = first_composition.durability.persistence.read_head()
        if head.aligned and head.durable != durable_before_adoption:
            raise LeaseOwnershipLostError('lease lost before cache promotion')
        yield

    first_context._lease_authority_fence = lose_after_adoption
    with pytest.raises(LeaseOwnershipLostError):
        first_job.iteration(first_context)
    assert first_cache.bundle is source_bundle

    second_context = _context(tmp_path)
    second_job, second_composition, second_cache, second_cycles, _ = _job(
        tmp_path,
        source_revision=source_revision,
        target_revision=target_revision,
        cached_bundle=source_bundle,
        target_bundle=target_bundle,
        context=second_context,
        as_of=NOW + timedelta(minutes=2),
    )
    second_job.recover(second_context)

    result = second_job.iteration(second_context)

    assert result.adoption_outcome is AlarmRuntimeJobAdoptionOutcome.ADOPTED
    assert result.effective_revision_key == target_revision.revision_key
    assert result.cycle_executed is True
    assert second_cache.bundle == target_bundle
    assert second_cache.replace_calls == [target_bundle]
    assert second_cycles.sessions == [target_revision.session]
    assert second_composition.durability.persistence.read_head().aligned


def test_first_bootstrap_promotes_cache_before_running_target_cycle(tmp_path: Path) -> None:
    events: list[str] = []
    context = _context(tmp_path, fence_events=events)
    target = _revision('AC-1')
    target_bundle = _bundle('AC-1')
    job, _, cache, cycle_factory, _ = _job(
        tmp_path,
        source_revision=target,
        target_revision=target,
        cached_bundle=None,
        target_bundle=target_bundle,
        context=context,
    )
    job.recover(context)

    result = job.iteration(context)

    assert result.adoption_outcome is AlarmRuntimeJobAdoptionOutcome.BOOTSTRAPPED
    assert result.cycle_executed is True
    assert result.effective_revision_key == ('AC-1', 'TR-18')
    assert cache.replace_calls == [target_bundle]
    assert context.iteration_has_work is True
    assert cycle_factory.sessions == [target.session]
    assert events[:2] == ['enter', 'exit']
    assert len(events) >= 2


def test_bootstrap_and_consumer_state_do_not_reacquire_authority_inside_fence(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path, strict_non_reentrant_fence=True)
    target = _revision('AC-1')
    target_bundle = _bundle('AC-1')
    job, composition, cache, cycle_factory, _ = _job(
        tmp_path,
        source_revision=target,
        target_revision=target,
        cached_bundle=None,
        target_bundle=target_bundle,
        context=context,
    )
    job.recover(context)

    result = job.iteration(context)

    assert result.adoption_outcome is AlarmRuntimeJobAdoptionOutcome.BOOTSTRAPPED
    assert result.cycle_executed is True
    assert cache.replace_calls == [target_bundle]
    assert cycle_factory.sessions == [target.session]
    assert composition.durability.persistence.read_head().aligned


def test_bootstrap_cache_promotion_failure_prevents_first_operational_cycle(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    target = _revision('AC-1')
    target_bundle = _bundle('AC-1')
    job, _, cache, cycle_factory, _ = _job(
        tmp_path,
        source_revision=target,
        target_revision=target,
        cached_bundle=None,
        target_bundle=target_bundle,
        context=context,
    )
    cache.replace_error = RuntimeError('cache promotion failed')
    job.recover(context)

    with pytest.raises(RuntimeError, match='cache promotion failed'):
        job.iteration(context)

    assert cache.bundle is None
    assert cache.replace_calls == []
    assert cycle_factory.sessions == []


def test_bootstrap_fails_closed_when_durable_journal_exists_without_cache(tmp_path: Path) -> None:
    context = _context(tmp_path)
    target = _revision('AC-1', status=AlarmStatus.ACTIVE)
    target_bundle = _bundle('AC-1')
    job, composition, cache, cycle_factory, _ = _job(
        tmp_path,
        source_revision=target,
        target_revision=target,
        cached_bundle=None,
        target_bundle=target_bundle,
        context=context,
    )
    cycle_factory(target.session).execute(
        context,
        AlarmExecutionIteration(
            session=target.session,
            loaded_sources=EmptyIterationSourceLoader().load(
                plan=target.session.data_plan,
                as_of=NOW,
            ),
        ),
    )
    job.recover(context)

    with pytest.raises(AlarmRuntimeJobCompositionError, match='durable runtime state exists'):
        job.iteration(context)

    assert composition.durability.persistence.read_head().durable is not None
    assert cache.replace_calls == []


def test_bootstrap_fails_closed_when_consumer_state_exists_without_journal(tmp_path: Path) -> None:
    context = _context(tmp_path)
    target = _revision('AC-1')
    target_bundle = _bundle('AC-1')
    job, composition, cache, _, consumer = _job(
        tmp_path,
        source_revision=target,
        target_revision=target,
        cached_bundle=None,
        target_bundle=target_bundle,
        context=context,
    )
    AtomicJsonStore(root_path=composition.durability.persistence.paths.alarms_root).replace(
        'runtime/state/consumers/management.json',
        {
            'consumer_state_schema_version': 'alarm-runtime-input-consumer-state.v1',
            'management': {'cursor': None, 'pending': []},
            'decisions': {'cursor': None, 'pending': []},
            'pending_deactivation_request_ids': [],
        },
    )

    job.recover(context)
    assert consumer.has_durable_state() is True
    with pytest.raises(AlarmRuntimeJobCompositionError, match='durable runtime state exists'):
        job.iteration(context)
    assert cache.replace_calls == []


def test_cache_current_runs_operational_cycle_without_adoption(tmp_path: Path) -> None:
    context = _context(tmp_path)
    revision = _revision('AC-1')
    bundle = _bundle('AC-1')
    job, _, cache, cycle_factory, _ = _job(
        tmp_path,
        source_revision=revision,
        target_revision=revision,
        cached_bundle=bundle,
        target_bundle=bundle,
        context=context,
    )
    job.recover(context)

    result = job.iteration(context)

    assert result.revision_origin is RuntimeRevisionOrigin.CACHE_CURRENT
    assert result.adoption_outcome is AlarmRuntimeJobAdoptionOutcome.NOT_REQUIRED
    assert result.cycle_executed is True
    assert result.degraded is False
    assert cache.replace_calls == []
    assert cycle_factory.sessions == [revision.session]


def test_cache_fallback_runs_last_known_good_and_marks_result_degraded(tmp_path: Path) -> None:
    context = _context(tmp_path)
    revision = _revision('AC-1')
    bundle = _bundle('AC-1')
    job, _, cache, cycle_factory, _ = _job(
        tmp_path,
        source_revision=revision,
        target_revision=revision,
        cached_bundle=bundle,
        target_bundle=bundle,
        context=context,
    )
    job.revision_resolver.source.fail_manifest = True
    job.recover(context)

    result = job.iteration(context)

    assert result.revision_origin is RuntimeRevisionOrigin.CACHE_FALLBACK
    assert result.adoption_outcome is AlarmRuntimeJobAdoptionOutcome.NOT_REQUIRED
    assert result.degraded is True
    assert cache.replace_calls == []
    assert cycle_factory.sessions == [revision.session]


def test_rejected_candidate_keeps_effective_cache_and_runs_old_session(tmp_path: Path) -> None:
    context = _context(tmp_path)
    source_revision = _revision('AC-1', priority_group='mill-feed')
    target_revision = _revision('AC-2', priority_group='other-group')
    source_bundle = _bundle('AC-1')
    target_bundle = _bundle('AC-2')
    job, _, cache, cycle_factory, _ = _job(
        tmp_path,
        source_revision=source_revision,
        target_revision=target_revision,
        cached_bundle=source_bundle,
        target_bundle=target_bundle,
        context=context,
    )
    job.recover(context)

    result = job.iteration(context)

    assert result.adoption_outcome is AlarmRuntimeJobAdoptionOutcome.REJECTED
    assert result.effective_revision_key == source_revision.revision_key
    assert result.cycle_executed is True
    assert result.degraded is True
    assert cache.bundle is source_bundle
    assert cache.replace_calls == []
    assert cycle_factory.sessions == [source_revision.session]


def test_compatible_candidate_without_adoption_commit_promotes_and_runs_target_cycle(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    source_revision = _revision('AC-1', priority_order=1)
    target_revision = _revision('AC-2', priority_order=2)
    source_bundle = _bundle('AC-1')
    target_bundle = _bundle('AC-2')
    job, _, cache, cycle_factory, _ = _job(
        tmp_path,
        source_revision=source_revision,
        target_revision=target_revision,
        cached_bundle=source_bundle,
        target_bundle=target_bundle,
        context=context,
    )
    job.recover(context)

    result = job.iteration(context)

    assert result.adoption_outcome is AlarmRuntimeJobAdoptionOutcome.ADOPTED
    assert result.effective_revision_key == target_revision.revision_key
    assert result.cycle_executed is True
    assert cache.bundle == target_bundle
    assert cache.replace_calls == [target_bundle]
    assert cycle_factory.sessions == [target_revision.session]


def test_adoption_with_durable_commit_promotes_cache_and_ends_iteration_before_new_cycle(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    source_revision = _revision('AC-1', status=AlarmStatus.ACTIVE)
    target_revision = _revision('AC-2', executable=False, defined=True)
    source_bundle = _bundle('AC-1')
    target_bundle = _bundle('AC-2')
    job, composition, cache, cycle_factory, _ = _job(
        tmp_path,
        source_revision=source_revision,
        target_revision=target_revision,
        cached_bundle=source_bundle,
        target_bundle=target_bundle,
        context=context,
        as_of=NOW + timedelta(minutes=1),
    )
    cycle_factory(source_revision.session).execute(
        context,
        AlarmExecutionIteration(
            session=source_revision.session,
            loaded_sources=EmptyIterationSourceLoader().load(
                plan=source_revision.session.data_plan,
                as_of=NOW,
            ),
        ),
    )
    cycle_factory.sessions.clear()
    job.recover(context)

    result = job.iteration(context)

    assert result.adoption_outcome is AlarmRuntimeJobAdoptionOutcome.ADOPTED
    assert result.cycle_executed is False
    assert cache.replace_calls == [target_bundle]
    assert cycle_factory.sessions == []
    snapshot = composition.durability.persistence.read_snapshot('mill-feed')
    assert snapshot is not None
    assert 'mill/risk' not in snapshot.as_document()['alarms']


def test_cycle_factory_must_return_cycle_for_effective_session_and_composition(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    revision = _revision('AC-1')
    bundle = _bundle('AC-1')
    job, _, _, _, _ = _job(
        tmp_path,
        source_revision=revision,
        target_revision=revision,
        cached_bundle=bundle,
        target_bundle=bundle,
        context=context,
    )
    job.cycle_factory = lambda _session: object()
    job.recover(context)

    with pytest.raises(TypeError, match='AlarmOperationalCycle'):
        job.iteration(context)


def test_default_alarm_runtime_iteration_period_candidate_is_five_seconds() -> None:
    from ada.processes.alarms_runtime import DEFAULT_ALARM_RUNTIME_ITERATION_PERIOD_SECONDS

    assert DEFAULT_ALARM_RUNTIME_ITERATION_PERIOD_SECONDS == 5.0


def test_drain_only_reconciles_durability_without_resolving_revision(tmp_path: Path) -> None:
    context = _context(tmp_path)
    revision = _revision('AC-1')
    bundle = _bundle('AC-1')
    job, composition, _, _, _ = _job(
        tmp_path,
        source_revision=revision,
        target_revision=revision,
        cached_bundle=bundle,
        target_bundle=bundle,
        context=context,
    )
    job.recover(context)
    job.revision_resolver.source.fail_manifest = True
    context.request_stop('performance_drain_boundary')

    result = job.drain(context)

    assert result.applied_count == 0
    assert context.should_stop is True
    assert context.stop_reason == 'performance_drain_boundary'
    assert composition.durability.persistence.read_head().aligned


def test_execute_binding_cooperative_stop_drains_alarm_runtime_without_next_iteration(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ada.processes.alarms_runtime import execute_alarm_runtime_job

    context = _context(tmp_path)
    revision = _revision('AC-1')
    bundle = _bundle('AC-1')
    job, composition, _, _, _ = _job(
        tmp_path,
        source_revision=revision,
        target_revision=revision,
        cached_bundle=bundle,
        target_bundle=bundle,
        context=context,
    )
    original_iteration = AlarmRuntimeJobComposition.iteration
    calls = {'iteration': 0}

    def stop_after_iteration(self, runtime_context):
        result = original_iteration(self, runtime_context)
        calls['iteration'] += 1
        runtime_context.request_stop('performance_drain_boundary')
        return result

    monkeypatch.setattr(AlarmRuntimeJobComposition, 'iteration', stop_after_iteration)
    definition = JobDefinition(
        module_name='ada.processes.alarms_runtime',
        service_name='alarms-runtime',
        job_key='alarms-runtime-cooperative-drain-test',
        sleep_seconds=5,
        iteration_timeout_seconds=10,
        execution_timeout_seconds=30,
        shutdown_grace_seconds=5,
        lease_timeout_seconds=10,
        lease_renew_seconds=3,
        lease_wait_seconds=0,
        resource_sample_seconds=1,
    )

    result = execute_alarm_runtime_job(
        definition=definition,
        composition=job,
        argv=(),
        environ={
            'ENVIRONMENT': 'local',
            'APPLICATION': 'ada-alarms-runtime-test',
            'VOLUMEN_PATH': str(tmp_path),
        },
    )

    assert result.status.value == 'warning'
    assert result.stop_reason == 'performance_drain_boundary'
    assert result.iteration_count == 1
    assert calls['iteration'] == 1
    assert composition.durability.persistence.read_head().aligned


def test_execute_binding_sigterm_drains_alarm_runtime_and_preserves_reason(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ada.processes.alarms_runtime import execute_alarm_runtime_job

    context = _context(tmp_path)
    revision = _revision('AC-1')
    bundle = _bundle('AC-1')
    job, composition, _, _, _ = _job(
        tmp_path,
        source_revision=revision,
        target_revision=revision,
        cached_bundle=bundle,
        target_bundle=bundle,
        context=context,
    )
    original_iteration = AlarmRuntimeJobComposition.iteration
    previous_handler = signal.getsignal(signal.SIGTERM)

    def sigterm_after_iteration(self, runtime_context):
        result = original_iteration(self, runtime_context)
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        handler(signal.SIGTERM, None)
        return result

    monkeypatch.setattr(AlarmRuntimeJobComposition, 'iteration', sigterm_after_iteration)
    definition = JobDefinition(
        module_name='ada.processes.alarms_runtime',
        service_name='alarms-runtime',
        job_key='alarms-runtime-sigterm-drain-test',
        sleep_seconds=5,
        iteration_timeout_seconds=10,
        execution_timeout_seconds=30,
        shutdown_grace_seconds=5,
        lease_timeout_seconds=10,
        lease_renew_seconds=3,
        lease_wait_seconds=0,
        resource_sample_seconds=1,
    )

    result = execute_alarm_runtime_job(
        definition=definition,
        composition=job,
        argv=(),
        environ={
            'ENVIRONMENT': 'local',
            'APPLICATION': 'ada-alarms-runtime-test',
            'VOLUMEN_PATH': str(tmp_path),
        },
    )

    assert result.status.value == 'warning'
    assert result.stop_reason == 'sigterm'
    assert result.iteration_count == 1
    assert signal.getsignal(signal.SIGTERM) == previous_handler
    assert composition.durability.persistence.read_head().aligned


def test_drain_requires_recovery_hook_to_have_completed(tmp_path: Path) -> None:
    context = _context(tmp_path)
    revision = _revision('AC-1')
    bundle = _bundle('AC-1')
    job, _, _, _, _ = _job(
        tmp_path,
        source_revision=revision,
        target_revision=revision,
        cached_bundle=bundle,
        target_bundle=bundle,
        context=context,
    )

    with pytest.raises(AlarmRuntimeJobCompositionError, match='recovery hook'):
        job.drain(context)


def test_execute_binding_delegates_recovery_iteration_and_drain_to_job_runtime(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import ada.processes.alarms_runtime.job_composition as job_composition_module
    from ada.processes.alarms_runtime import (
        DEFAULT_ALARM_RUNTIME_ITERATION_PERIOD_SECONDS,
        execute_alarm_runtime_job,
    )
    from atlanticus.kernel import OperationStatus
    from atlanticus.runtime import RuntimeExecutionResult

    context = _context(tmp_path)
    revision = _revision('AC-1')
    bundle = _bundle('AC-1')
    job, _, _, _, _ = _job(
        tmp_path,
        source_revision=revision,
        target_revision=revision,
        cached_bundle=bundle,
        target_bundle=bundle,
        context=context,
    )
    definition = JobDefinition(
        module_name='ada.processes.alarms_runtime',
        service_name='alarms-runtime',
        job_key='alarms-runtime',
        sleep_seconds=DEFAULT_ALARM_RUNTIME_ITERATION_PERIOD_SECONDS,
        iteration_timeout_seconds=10,
        execution_timeout_seconds=30,
        shutdown_grace_seconds=5,
        lease_timeout_seconds=10,
        lease_renew_seconds=3,
        lease_wait_seconds=0,
        resource_sample_seconds=1,
    )
    expected = RuntimeExecutionResult(
        run_id='11111111-1111-1111-1111-111111111111',
        correlation_id='22222222-2222-2222-2222-222222222222',
        status=OperationStatus.SUCCESS,
        iteration_count=1,
        duration_seconds=1.0,
        stop_reason='completed',
    )
    calls: list[str] = []

    def fake_execute_job(*, definition, iteration, recovery, drain, argv, environ):
        assert definition.sleep_seconds == 5.0
        assert argv == ('--run-once',)
        assert environ == {'ENVIRONMENT': 'local'}
        calls.append('recovery')
        recovery(context)
        calls.append('iteration')
        result = iteration(context)
        assert result.cycle_executed is True
        assert context.get_iteration_fact('revision_origin') == 'cache_current'
        assert context.get_iteration_fact('alarm_configuration_revision') == 'AC-1'
        assert context.get_iteration_fact('tool_registry_revision') == 'TR-18'
        assert context.get_iteration_fact('adoption_outcome') == 'not_required'
        assert context.get_iteration_fact('cycle_executed') is True
        assert context._next_iteration_delay() is None
        calls.append('drain')
        drain(context)
        return expected

    monkeypatch.setattr(job_composition_module, 'execute_job', fake_execute_job)

    result = execute_alarm_runtime_job(
        definition=definition,
        composition=job,
        argv=('--run-once',),
        environ={'ENVIRONMENT': 'local'},
    )

    assert result is expected
    assert calls == ['recovery', 'iteration', 'drain']


def test_execute_binding_requests_immediate_next_iteration_after_durable_adoption(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import ada.processes.alarms_runtime.job_composition as job_composition_module
    from ada.processes.alarms_runtime import execute_alarm_runtime_job
    from atlanticus.kernel import OperationStatus
    from atlanticus.runtime import RuntimeExecutionResult

    context = _context(tmp_path)
    revision = _revision('AC-1')
    bundle = _bundle('AC-1')
    job, _, _, _, _ = _job(
        tmp_path,
        source_revision=revision,
        target_revision=revision,
        cached_bundle=bundle,
        target_bundle=bundle,
        context=context,
    )
    expected_iteration = job_composition_module.AlarmRuntimeJobIterationResult(
        revision_origin=RuntimeRevisionOrigin.SOURCE_CANDIDATE,
        effective_revision_key=('AC-2', 'TR-18'),
        adoption_outcome=AlarmRuntimeJobAdoptionOutcome.ADOPTED,
        cycle_executed=False,
    )
    monkeypatch.setattr(
        job_composition_module.AlarmRuntimeJobComposition,
        'iteration',
        lambda self, _context: expected_iteration,
    )
    definition = context.definition
    expected = RuntimeExecutionResult(
        run_id='11111111-1111-1111-1111-111111111111',
        correlation_id='22222222-2222-2222-2222-222222222222',
        status=OperationStatus.SUCCESS,
        iteration_count=1,
        duration_seconds=1.0,
        stop_reason='completed',
    )

    def fake_execute_job(*, iteration, recovery, drain, **_kwargs):
        recovery(context)
        result = iteration(context)
        assert result is expected_iteration
        assert context._next_iteration_delay() == 0.0
        return expected

    monkeypatch.setattr(job_composition_module, 'execute_job', fake_execute_job)

    result = execute_alarm_runtime_job(definition=definition, composition=job)

    assert result is expected
