from dataclasses import replace

import pytest

from ada.alarms.core import AlarmEvaluation, AlarmIdentity
from ada.processes.alarms_runtime import (
    AlarmConfigurationRevision,
    AlarmConfigurationRevisionError,
    AlarmEvaluatorContract,
    AlarmEvaluatorRegistry,
    ConfigurationAdoptionChange,
    ConfigurationAdoptionDisposition,
    ConfigurationAdoptionPlan,
    ConfigurationAdoptionPlanError,
    ConfigurationAdoptionRejectionReason,
    build_alarm_execution_session,
)
from tests.support import plan


def _evaluator(_context) -> AlarmEvaluation | None:
    return None


def _session(*plans, revision: str = 'R42'):
    contracts_by_key = {}
    for item in plans:
        key = item.identity.family_key, item.evaluator_key
        contracts_by_key[key] = AlarmEvaluatorContract(
            family_key=key[0],
            evaluator_key=key[1],
            evaluator=_evaluator,
        )
    return build_alarm_execution_session(
        alarm_configuration_revision=plans[0].alarm_configuration_revision if plans else revision,
        tool_registry_revision=plans[0].tool_registry_revision if plans else 'T18',
        planned_alarms=plans,
        evaluator_registry=AlarmEvaluatorRegistry(tuple(contracts_by_key.values())),
    )


def _revision(
    revision: str,
    *,
    executable=(),
    defined: tuple[AlarmIdentity, ...] | None = None,
) -> AlarmConfigurationRevision:
    plans = tuple(
        replace(
            item,
            alarm_configuration_revision=revision,
        )
        for item in executable
    )
    identities = tuple(item.identity for item in plans) if defined is None else defined
    return AlarmConfigurationRevision(
        alarm_configuration_revision=revision,
        tool_registry_revision='T18',
        defined_alarm_identities=identities,
        session=_session(*plans, revision=revision),
    )


def test_revision_distinguishes_defined_from_executable_alarm() -> None:
    alarm = plan()
    revision = _revision('R43', executable=(), defined=(alarm.identity,))

    assert revision.is_defined(alarm.identity)
    assert not revision.is_executable(alarm.identity)
    assert revision.plan_for(alarm.identity) is None


def test_revision_requires_every_executable_alarm_to_remain_defined() -> None:
    alarm = replace(plan(), alarm_configuration_revision='R43')

    with pytest.raises(AlarmConfigurationRevisionError, match='must be defined'):
        AlarmConfigurationRevision(
            alarm_configuration_revision='R43',
            tool_registry_revision='T18',
            defined_alarm_identities=(),
            session=_session(alarm),
        )


def test_revision_requires_session_revision_pair_to_match() -> None:
    alarm = plan()

    with pytest.raises(AlarmConfigurationRevisionError, match='alarm configuration revision'):
        AlarmConfigurationRevision(
            alarm_configuration_revision='R43',
            tool_registry_revision='T18',
            defined_alarm_identities=(alarm.identity,),
            session=_session(alarm),
        )


def test_adoption_plan_exactly_covers_source_executable_alarms() -> None:
    first = plan('risk', priority_order=1)
    second = plan('impact', priority_order=2)
    source = _revision('R42', executable=(first, second))
    target = _revision('R43', executable=(first, second))

    with pytest.raises(ConfigurationAdoptionPlanError, match='exactly cover'):
        ConfigurationAdoptionPlan(
            source=source,
            target=target,
            changes=(
                ConfigurationAdoptionChange(
                    identity=first.identity,
                    disposition=ConfigurationAdoptionDisposition.UNCHANGED,
                ),
            ),
        )


def test_disabled_and_removed_are_distinguished_by_target_definition() -> None:
    alarm = plan()
    source = _revision('R42', executable=(alarm,))
    disabled_target = _revision('R43', executable=(), defined=(alarm.identity,))
    removed_target = _revision('R44', executable=(), defined=())

    disabled = ConfigurationAdoptionPlan(
        source=source,
        target=disabled_target,
        changes=(
            ConfigurationAdoptionChange(
                identity=alarm.identity,
                disposition=ConfigurationAdoptionDisposition.DISABLED,
            ),
        ),
    )
    removed = ConfigurationAdoptionPlan(
        source=source,
        target=removed_target,
        changes=(
            ConfigurationAdoptionChange(
                identity=alarm.identity,
                disposition=ConfigurationAdoptionDisposition.REMOVED,
            ),
        ),
    )

    assert disabled.is_adoptable
    assert removed.is_adoptable
    with pytest.raises(ConfigurationAdoptionPlanError, match='must remain defined'):
        ConfigurationAdoptionPlan(
            source=source,
            target=removed_target,
            changes=(
                ConfigurationAdoptionChange(
                    identity=alarm.identity,
                    disposition=ConfigurationAdoptionDisposition.DISABLED,
                ),
            ),
        )
    with pytest.raises(ConfigurationAdoptionPlanError, match='must not be defined'):
        ConfigurationAdoptionPlan(
            source=source,
            target=disabled_target,
            changes=(
                ConfigurationAdoptionChange(
                    identity=alarm.identity,
                    disposition=ConfigurationAdoptionDisposition.REMOVED,
                ),
            ),
        )


def test_rejected_change_requires_explicit_reason_and_blocks_adoption() -> None:
    alarm = plan()
    source = _revision('R42', executable=(alarm,))
    target = _revision('R43', executable=(alarm,))

    with pytest.raises(ConfigurationAdoptionPlanError, match='requires rejection_reason'):
        ConfigurationAdoptionChange(
            identity=alarm.identity,
            disposition=ConfigurationAdoptionDisposition.REJECTED,
        )

    rejected = ConfigurationAdoptionChange(
        identity=alarm.identity,
        disposition=ConfigurationAdoptionDisposition.REJECTED,
        rejection_reason=ConfigurationAdoptionRejectionReason.EVALUATOR_CHANGED,
    )
    adoption = ConfigurationAdoptionPlan(
        source=source,
        target=target,
        changes=(rejected,),
    )

    assert not adoption.is_adoptable
    assert adoption.rejected_changes == (rejected,)


def test_structural_reset_groups_are_derived_once_from_source_group() -> None:
    first = plan('risk', priority_order=1)
    second = plan('impact', priority_order=2)
    source = _revision('R42', executable=(first, second))
    target = _revision('R43', executable=(first, second))

    adoption = ConfigurationAdoptionPlan(
        source=source,
        target=target,
        changes=(
            ConfigurationAdoptionChange(
                identity=first.identity,
                disposition=ConfigurationAdoptionDisposition.STRUCTURAL_RESET,
            ),
            ConfigurationAdoptionChange(
                identity=second.identity,
                disposition=ConfigurationAdoptionDisposition.STRUCTURAL_RESET,
            ),
        ),
    )

    assert adoption.structural_reset_groups == ('mill-feed',)


def test_new_or_reenabled_target_alarm_does_not_require_source_adoption_change() -> None:
    existing = plan('risk', priority_order=1)
    incoming = plan('impact', priority_order=2)
    source = _revision(
        'R42',
        executable=(existing,),
        defined=(existing.identity, incoming.identity),
    )
    target = _revision('R43', executable=(existing, incoming))

    adoption = ConfigurationAdoptionPlan(
        source=source,
        target=target,
        changes=(
            ConfigurationAdoptionChange(
                identity=existing.identity,
                disposition=ConfigurationAdoptionDisposition.UNCHANGED,
            ),
        ),
    )

    assert adoption.is_adoptable
    assert target.is_executable(incoming.identity)
    assert incoming.identity not in tuple(change.identity for change in adoption.changes)


def test_same_revision_pair_does_not_create_adoption_plan() -> None:
    alarm = plan()
    source = _revision('R42', executable=(alarm,))

    with pytest.raises(ConfigurationAdoptionPlanError, match='must differ'):
        ConfigurationAdoptionPlan(
            source=source,
            target=source,
            changes=(
                ConfigurationAdoptionChange(
                    identity=alarm.identity,
                    disposition=ConfigurationAdoptionDisposition.UNCHANGED,
                ),
            ),
        )


def test_rejection_reason_values_keep_fail_closed_routing_contract() -> None:
    assert ConfigurationAdoptionRejectionReason.C1_ROUTING_MUTATION_UNSUPPORTED.value == (
        'c1_routing_mutation_unsupported'
    )
    assert ConfigurationAdoptionRejectionReason.C3_ROUTING_MUTATION_UNSUPPORTED.value == (
        'c3_routing_mutation_unsupported'
    )
