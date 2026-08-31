from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from ada.data.core import normalize_utc_second
from ada.processes.alarms_runtime.adoption import plan_configuration_adoption
from ada.processes.alarms_runtime.adoption_execution import AlarmConfigurationAdoptionExecutor
from ada.processes.alarms_runtime.composition import AlarmRuntimeComposition
from ada.processes.alarms_runtime.consumer import AlarmDurableInputConsumer
from ada.processes.alarms_runtime.cycle import AlarmOperationalCycle
from ada.processes.alarms_runtime.iteration import AlarmIterationLoader, AlarmIterationSourceLoader
from ada.processes.alarms_runtime.revision_resolution import (
    RuntimeRevisionOrigin,
    RuntimeRevisionResolution,
)
from ada.processes.alarms_runtime.revision_resolver import RuntimeRevisionResolver
from ada.processes.alarms_runtime.session import AlarmExecutionSession
from atlanticus.runtime import (
    JobDefinition,
    JobRuntimeContext,
    RuntimeExecutionResult,
    execute_job,
)

DEFAULT_ALARM_RUNTIME_ITERATION_PERIOD_SECONDS = 5.0
_RECOVERY_MEMORY_KEY = 'ada.alarms.runtime.job_composition.recovered'


class AlarmRuntimeJobCompositionError(RuntimeError):
    pass


class AlarmRuntimeJobAdoptionOutcome(StrEnum):
    NOT_REQUIRED = 'not_required'
    BOOTSTRAPPED = 'bootstrapped'
    ADOPTED = 'adopted'
    REJECTED = 'rejected'


@dataclass(frozen=True, slots=True)
class AlarmRuntimeJobIterationResult:
    revision_origin: RuntimeRevisionOrigin
    effective_revision_key: tuple[str, str]
    adoption_outcome: AlarmRuntimeJobAdoptionOutcome
    cycle_executed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.revision_origin, RuntimeRevisionOrigin):
            raise TypeError('revision_origin must be a RuntimeRevisionOrigin')
        if (
            not isinstance(self.effective_revision_key, tuple)
            or len(self.effective_revision_key) != 2
            or not all(
                isinstance(item, str) and item.strip() for item in self.effective_revision_key
            )
        ):
            raise ValueError('effective_revision_key must contain two non-empty strings')
        if not isinstance(self.adoption_outcome, AlarmRuntimeJobAdoptionOutcome):
            raise TypeError('adoption_outcome must be an AlarmRuntimeJobAdoptionOutcome')
        if not isinstance(self.cycle_executed, bool):
            raise TypeError('cycle_executed must be a bool')

    @property
    def degraded(self) -> bool:
        return (
            self.revision_origin is RuntimeRevisionOrigin.CACHE_FALLBACK
            or self.adoption_outcome is AlarmRuntimeJobAdoptionOutcome.REJECTED
        )


@dataclass(slots=True)
class AlarmRuntimeJobComposition:
    composition: AlarmRuntimeComposition
    revision_resolver: RuntimeRevisionResolver
    adoption_executor: AlarmConfigurationAdoptionExecutor
    input_consumer: AlarmDurableInputConsumer
    iteration_source_loader: AlarmIterationSourceLoader
    cycle_factory: Callable[[AlarmExecutionSession], AlarmOperationalCycle]
    as_of_provider: Callable[[JobRuntimeContext], datetime]

    def __post_init__(self) -> None:
        if not isinstance(self.composition, AlarmRuntimeComposition):
            raise TypeError('composition must be an AlarmRuntimeComposition')
        if not isinstance(self.revision_resolver, RuntimeRevisionResolver):
            raise TypeError('revision_resolver must be a RuntimeRevisionResolver')
        if not isinstance(self.adoption_executor, AlarmConfigurationAdoptionExecutor):
            raise TypeError('adoption_executor must be an AlarmConfigurationAdoptionExecutor')
        if self.adoption_executor.composition is not self.composition:
            raise AlarmRuntimeJobCompositionError(
                'adoption executor must use the job runtime composition'
            )
        if not isinstance(self.input_consumer, AlarmDurableInputConsumer):
            raise TypeError('input_consumer must be an AlarmDurableInputConsumer')
        if self.input_consumer.composition is not self.composition:
            raise AlarmRuntimeJobCompositionError(
                'input consumer must use the job runtime composition'
            )
        if not isinstance(self.iteration_source_loader, AlarmIterationSourceLoader):
            raise TypeError('iteration_source_loader must implement AlarmIterationSourceLoader')
        if not callable(self.cycle_factory):
            raise TypeError('cycle_factory must be callable')
        if not callable(self.as_of_provider):
            raise TypeError('as_of_provider must be callable')

    def recover(self, context: JobRuntimeContext):
        if not isinstance(context, JobRuntimeContext):
            raise TypeError('context must be a JobRuntimeContext')
        result = self.composition.recover(context)
        context.set_memory(_RECOVERY_MEMORY_KEY, self)
        return result

    def drain(self, context: JobRuntimeContext):
        if not isinstance(context, JobRuntimeContext):
            raise TypeError('context must be a JobRuntimeContext')
        context.assert_lease_current()
        self._require_recovered(context)
        return self.composition.reconcile_drain(context)

    def iteration(self, context: JobRuntimeContext) -> AlarmRuntimeJobIterationResult:
        if not isinstance(context, JobRuntimeContext):
            raise TypeError('context must be a JobRuntimeContext')
        context.assert_lease_current()
        self._require_recovered(context)
        as_of = normalize_utc_second(self.as_of_provider(context), field_name='as_of')
        resolution = self.revision_resolver.resolve()
        if resolution.effective is None:
            return self._bootstrap(context, resolution, as_of=as_of)
        if resolution.origin is RuntimeRevisionOrigin.SOURCE_CANDIDATE:
            return self._adopt_candidate(context, resolution, as_of=as_of)
        return self._run_operational_cycle(
            context,
            session=resolution.target.revision.session,
            as_of=as_of,
            revision_origin=resolution.origin,
            adoption_outcome=AlarmRuntimeJobAdoptionOutcome.NOT_REQUIRED,
            effective_revision_key=resolution.target.revision_key,
        )

    def _bootstrap(
        self,
        context: JobRuntimeContext,
        resolution: RuntimeRevisionResolution,
        *,
        as_of: datetime,
    ) -> AlarmRuntimeJobIterationResult:
        if resolution.origin is not RuntimeRevisionOrigin.SOURCE_CANDIDATE:
            raise AlarmRuntimeJobCompositionError(
                'runtime bootstrap requires a source candidate revision'
            )
        if self._has_prior_durable_state():
            raise AlarmRuntimeJobCompositionError(
                'runtime revision cache is missing while durable runtime state exists'
            )
        self._promote_cache(context, resolution)
        return self._run_operational_cycle(
            context,
            session=resolution.target.revision.session,
            as_of=as_of,
            revision_origin=resolution.origin,
            adoption_outcome=AlarmRuntimeJobAdoptionOutcome.BOOTSTRAPPED,
            effective_revision_key=resolution.target.revision_key,
        )

    def _adopt_candidate(
        self,
        context: JobRuntimeContext,
        resolution: RuntimeRevisionResolution,
        *,
        as_of: datetime,
    ) -> AlarmRuntimeJobIterationResult:
        effective = resolution.effective
        if effective is None:
            raise AlarmRuntimeJobCompositionError(
                'runtime adoption requires an effective source revision'
            )
        plan = plan_configuration_adoption(effective.revision, resolution.target.revision)
        if not plan.is_adoptable:
            return self._run_operational_cycle(
                context,
                session=effective.revision.session,
                as_of=as_of,
                revision_origin=resolution.origin,
                adoption_outcome=AlarmRuntimeJobAdoptionOutcome.REJECTED,
                effective_revision_key=effective.revision_key,
            )
        adoption = self.adoption_executor.execute(context, plan, effective_at=as_of)
        self._promote_cache(context, resolution)
        if adoption.commit_result is not None:
            return AlarmRuntimeJobIterationResult(
                revision_origin=resolution.origin,
                effective_revision_key=resolution.target.revision_key,
                adoption_outcome=AlarmRuntimeJobAdoptionOutcome.ADOPTED,
                cycle_executed=False,
            )
        return self._run_operational_cycle(
            context,
            session=resolution.target.revision.session,
            as_of=as_of,
            revision_origin=resolution.origin,
            adoption_outcome=AlarmRuntimeJobAdoptionOutcome.ADOPTED,
            effective_revision_key=resolution.target.revision_key,
        )

    def _run_operational_cycle(
        self,
        context: JobRuntimeContext,
        *,
        session: AlarmExecutionSession,
        as_of: datetime,
        revision_origin: RuntimeRevisionOrigin,
        adoption_outcome: AlarmRuntimeJobAdoptionOutcome,
        effective_revision_key: tuple[str, str],
    ) -> AlarmRuntimeJobIterationResult:
        context.assert_lease_current()
        iteration = AlarmIterationLoader(
            session=session,
            source_loader=self.iteration_source_loader,
        ).load(as_of=as_of)
        cycle = self.cycle_factory(session)
        if not isinstance(cycle, AlarmOperationalCycle):
            raise TypeError('cycle_factory must return an AlarmOperationalCycle')
        if cycle.session is not session:
            raise AlarmRuntimeJobCompositionError(
                'cycle factory returned a cycle for another session'
            )
        if cycle.composition is not self.composition:
            raise AlarmRuntimeJobCompositionError(
                'cycle factory must use the job runtime composition'
            )
        self.input_consumer.execute(context, cycle=cycle, iteration=iteration)
        return AlarmRuntimeJobIterationResult(
            revision_origin=revision_origin,
            effective_revision_key=effective_revision_key,
            adoption_outcome=adoption_outcome,
            cycle_executed=True,
        )

    def _promote_cache(
        self,
        context: JobRuntimeContext,
        resolution: RuntimeRevisionResolution,
    ) -> None:
        context.assert_lease_current()
        with context.fenced_mutation():
            self.revision_resolver.cache.replace_effective(bundle=resolution.target.bundle)
        context.mark_iteration_work()

    def _require_recovered(self, context: JobRuntimeContext) -> None:
        if context.get_memory(_RECOVERY_MEMORY_KEY) is not self:
            raise AlarmRuntimeJobCompositionError(
                'Alarm Engine recovery hook must complete before job iteration'
            )
        if not self.composition.durability.persistence.read_head().aligned:
            raise AlarmRuntimeJobCompositionError(
                'Alarm Engine journal must be recovered before job iteration'
            )

    def _has_prior_durable_state(self) -> bool:
        head = self.composition.durability.persistence.read_head()
        return head.durable is not None or self.input_consumer.has_durable_state()


def execute_alarm_runtime_job(
    *,
    definition: JobDefinition,
    composition: AlarmRuntimeJobComposition,
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> RuntimeExecutionResult:
    if not isinstance(definition, JobDefinition):
        raise TypeError('definition must be a JobDefinition')
    if not isinstance(composition, AlarmRuntimeJobComposition):
        raise TypeError('composition must be an AlarmRuntimeJobComposition')

    def run_iteration(context: JobRuntimeContext) -> AlarmRuntimeJobIterationResult:
        result = composition.iteration(context)
        context.set_iteration_fact('revision_origin', result.revision_origin.value)
        context.set_iteration_fact(
            'alarm_configuration_revision',
            result.effective_revision_key[0],
        )
        context.set_iteration_fact(
            'tool_registry_revision',
            result.effective_revision_key[1],
        )
        context.set_iteration_fact('adoption_outcome', result.adoption_outcome.value)
        context.set_iteration_fact('cycle_executed', result.cycle_executed)
        if (
            result.adoption_outcome is AlarmRuntimeJobAdoptionOutcome.ADOPTED
            and not result.cycle_executed
        ):
            context.set_next_iteration_delay(0)
        return result

    return execute_job(
        definition=definition,
        recovery=composition.recover,
        iteration=run_iteration,
        drain=composition.drain,
        argv=argv,
        environ=environ,
    )
