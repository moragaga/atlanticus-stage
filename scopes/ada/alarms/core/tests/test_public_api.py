import ada.alarms.core as core


def test_version() -> None:
    assert core.__version__ == '0.3.0'


def test_public_api_contains_lifecycle_foundation() -> None:
    expected = {
        'AlarmEvaluation',
        'AlarmIdentity',
        'AlarmOccurrence',
        'AlarmEpisode',
        'AlarmRuntimeState',
        'GroupLifecycleState',
        'ManagementAction',
        'ManagementEffect',
        'CascadeSuppression',
        'AlarmRouting',
        'RoutingDestination',
        'ToolAssignment',
        'PendingToolAssignment',
        'resolve_group_priority',
        'resolve_group_routing',
        'ReappearanceChange',
        'is_directly_managed',
        'resolve_management_cascades',
        'execute_evaluator',
        'reduce_group_cycle',
        'reset_group_for_reconfiguration',
    }
    assert expected <= set(core.__all__)
