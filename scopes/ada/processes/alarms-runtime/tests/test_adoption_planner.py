from dataclasses import replace

from ada.alarms.core import (
    AlarmEvaluation,
    AlarmKind,
    AlarmRouting,
    Criticality,
    DeactivationPolicy,
    RoutingDestination,
)
from ada.processes.alarms_runtime import (
    AlarmConfigurationRevision,
    AlarmEvaluatorContract,
    AlarmEvaluatorRegistry,
    ConfigurationAdoptionDisposition,
    ConfigurationAdoptionRejectionReason,
    build_alarm_execution_session,
    plan_configuration_adoption,
)
from tests.support import plan


def _evaluator(_context) -> AlarmEvaluation | None:
    return None


def _revision(
    revision: str,
    *,
    executable=(),
    defined=None,
    tool_revision: str = 'T18',
    parameters=None,
) -> AlarmConfigurationRevision:
    plans = tuple(
        replace(
            item,
            alarm_configuration_revision=revision,
            tool_registry_revision=tool_revision,
        )
        for item in executable
    )
    identities = tuple(item.identity for item in plans) if defined is None else tuple(defined)
    contracts_by_key = {}
    for item in plans:
        key = item.identity.family_key, item.evaluator_key
        contracts_by_key[key] = AlarmEvaluatorContract(
            family_key=key[0],
            evaluator_key=key[1],
            evaluator=_evaluator,
        )
    session = build_alarm_execution_session(
        alarm_configuration_revision=revision,
        tool_registry_revision=tool_revision,
        planned_alarms=plans,
        evaluator_registry=AlarmEvaluatorRegistry(tuple(contracts_by_key.values())),
        parameters_by_alarm=parameters,
    )
    return AlarmConfigurationRevision(
        alarm_configuration_revision=revision,
        tool_registry_revision=tool_revision,
        defined_alarm_identities=identities,
        session=session,
    )


def _single_change(source, target):
    adoption = plan_configuration_adoption(source, target)
    assert len(adoption.changes) == 1
    return adoption, adoption.changes[0]


def test_revision_only_change_is_unchanged() -> None:
    alarm = plan()
    source = _revision('R42', executable=(alarm,))
    target = _revision('R43', executable=(alarm,))

    adoption, change = _single_change(source, target)

    assert adoption.is_adoptable
    assert change.disposition is ConfigurationAdoptionDisposition.UNCHANGED


def test_tool_registry_revision_only_change_is_unchanged() -> None:
    alarm = plan()
    source = _revision('R42', executable=(alarm,), tool_revision='T18')
    target = _revision('R42', executable=(alarm,), tool_revision='T19')

    _, change = _single_change(source, target)

    assert change.disposition is ConfigurationAdoptionDisposition.UNCHANGED


def test_parameter_change_is_compatible() -> None:
    alarm = plan()
    source = _revision('R42', executable=(alarm,), parameters={alarm.identity: {'threshold': 80.0}})
    target = _revision('R43', executable=(alarm,), parameters={alarm.identity: {'threshold': 85.0}})

    _, change = _single_change(source, target)

    assert change.disposition is ConfigurationAdoptionDisposition.COMPATIBLE


def test_priority_order_change_is_compatible() -> None:
    alarm = plan()
    source = _revision('R42', executable=(alarm,))
    target = _revision('R43', executable=(replace(alarm, priority_order=2),))

    _, change = _single_change(source, target)

    assert change.disposition is ConfigurationAdoptionDisposition.COMPATIBLE


def test_delivery_enabled_change_is_compatible() -> None:
    alarm = plan()
    source = _revision('R42', executable=(alarm,))
    target = _revision('R43', executable=(replace(alarm, delivery_enabled=False),))

    _, change = _single_change(source, target)

    assert change.disposition is ConfigurationAdoptionDisposition.COMPATIBLE


def test_deactivation_policy_change_is_compatible_for_future_requests() -> None:
    alarm = replace(plan(), deactivation_policy=DeactivationPolicy(approval_required=True))
    source = _revision('R42', executable=(alarm,))
    target = _revision(
        'R43',
        executable=(
            replace(alarm, deactivation_policy=DeactivationPolicy(approval_required=False)),
        ),
    )

    _, change = _single_change(source, target)

    assert change.disposition is ConfigurationAdoptionDisposition.COMPATIBLE


def test_disabled_alarm_remains_defined_but_leaves_execution_session() -> None:
    alarm = plan()
    source = _revision('R42', executable=(alarm,))
    target = _revision('R43', executable=(), defined=(alarm.identity,))

    _, change = _single_change(source, target)

    assert change.disposition is ConfigurationAdoptionDisposition.DISABLED


def test_removed_alarm_leaves_definition_and_execution_session() -> None:
    alarm = plan()
    source = _revision('R42', executable=(alarm,))
    target = _revision('R43', executable=(), defined=())

    _, change = _single_change(source, target)

    assert change.disposition is ConfigurationAdoptionDisposition.REMOVED


def test_criticality_change_requires_structural_reset() -> None:
    alarm = replace(
        plan(),
        criticality=Criticality.C1,
        routing=AlarmRouting(
            origin_tool_key='io',
            destinations=(RoutingDestination(tool_key='strategic'),),
        ),
    )
    target_alarm = replace(
        alarm,
        criticality=Criticality.C2,
        routing=AlarmRouting(
            origin_tool_key='io',
            destinations=(RoutingDestination(tool_key='strategic', delay_seconds=600),),
        ),
    )
    source = _revision('R42', executable=(alarm,))
    target = _revision('R43', executable=(target_alarm,))

    adoption, change = _single_change(source, target)

    assert change.disposition is ConfigurationAdoptionDisposition.STRUCTURAL_RESET
    assert adoption.structural_reset_groups == ('mill-feed',)


def test_c2_routing_change_is_compatible() -> None:
    alarm = replace(
        plan(),
        criticality=Criticality.C2,
        routing=AlarmRouting(
            origin_tool_key='io',
            destinations=(RoutingDestination(tool_key='strategic', delay_seconds=1800),),
        ),
    )
    target_alarm = replace(
        alarm,
        routing=AlarmRouting(
            origin_tool_key='flotation',
            destinations=(
                RoutingDestination(tool_key='io', delay_seconds=0),
                RoutingDestination(tool_key='strategic', delay_seconds=600),
            ),
        ),
    )
    source = _revision('R42', executable=(alarm,))
    target = _revision('R43', executable=(target_alarm,))

    _, change = _single_change(source, target)

    assert change.disposition is ConfigurationAdoptionDisposition.COMPATIBLE


def test_c1_routing_change_is_rejected_fail_closed() -> None:
    alarm = replace(
        plan(),
        criticality=Criticality.C1,
        routing=AlarmRouting(origin_tool_key='io'),
    )
    target_alarm = replace(
        alarm,
        routing=AlarmRouting(
            origin_tool_key='io',
            destinations=(RoutingDestination(tool_key='strategic'),),
        ),
    )
    source = _revision('R42', executable=(alarm,))
    target = _revision('R43', executable=(target_alarm,))

    adoption, change = _single_change(source, target)

    assert not adoption.is_adoptable
    assert change.disposition is ConfigurationAdoptionDisposition.REJECTED
    assert (
        change.rejection_reason
        is ConfigurationAdoptionRejectionReason.C1_ROUTING_MUTATION_UNSUPPORTED
    )


def test_c3_origin_change_is_rejected_fail_closed() -> None:
    alarm = plan()
    source = _revision('R42', executable=(alarm,))
    target = _revision(
        'R43',
        executable=(replace(alarm, routing=AlarmRouting(origin_tool_key='strategic')),),
    )

    adoption, change = _single_change(source, target)

    assert not adoption.is_adoptable
    assert change.disposition is ConfigurationAdoptionDisposition.REJECTED
    assert (
        change.rejection_reason
        is ConfigurationAdoptionRejectionReason.C3_ROUTING_MUTATION_UNSUPPORTED
    )


def test_priority_group_change_is_rejected_before_other_mutations() -> None:
    alarm = plan()
    source = _revision('R42', executable=(alarm,))
    target_alarm = replace(
        alarm,
        priority_group='other-group',
        kind=AlarmKind.IMPACT,
        evaluator_key='other-evaluator',
    )
    target = _revision('R43', executable=(target_alarm,))

    adoption, change = _single_change(source, target)

    assert not adoption.is_adoptable
    assert change.rejection_reason is ConfigurationAdoptionRejectionReason.PRIORITY_GROUP_CHANGED


def test_alarm_kind_change_is_rejected() -> None:
    alarm = plan()
    source = _revision('R42', executable=(alarm,))
    target = _revision('R43', executable=(replace(alarm, kind=AlarmKind.IMPACT),))

    _, change = _single_change(source, target)

    assert change.disposition is ConfigurationAdoptionDisposition.REJECTED
    assert change.rejection_reason is ConfigurationAdoptionRejectionReason.ALARM_KIND_CHANGED


def test_evaluator_change_is_rejected() -> None:
    alarm = plan()
    source = _revision('R42', executable=(alarm,))
    target = _revision('R43', executable=(replace(alarm, evaluator_key='other-evaluator'),))

    _, change = _single_change(source, target)

    assert change.disposition is ConfigurationAdoptionDisposition.REJECTED
    assert change.rejection_reason is ConfigurationAdoptionRejectionReason.EVALUATOR_CHANGED


def test_new_or_reenabled_target_alarm_is_not_misclassified_as_source_change() -> None:
    existing = plan('risk', priority_order=1)
    incoming = plan('impact', priority_order=2)
    source = _revision(
        'R42',
        executable=(existing,),
        defined=(existing.identity, incoming.identity),
    )
    target = _revision('R43', executable=(existing, incoming))

    adoption = plan_configuration_adoption(source, target)

    assert tuple(change.identity for change in adoption.changes) == (existing.identity,)
    assert adoption.changes[0].disposition is ConfigurationAdoptionDisposition.UNCHANGED


def test_multiple_changes_are_sorted_by_canonical_identity() -> None:
    second = plan('zeta', priority_order=2)
    first = plan('alpha', priority_order=1)
    source = _revision('R42', executable=(second, first))
    target = _revision('R43', executable=(second, first))

    adoption = plan_configuration_adoption(source, target)

    assert tuple(change.identity.alarm_key for change in adoption.changes) == ('alpha', 'zeta')
