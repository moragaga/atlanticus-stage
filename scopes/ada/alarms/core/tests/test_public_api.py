import ada.alarms.core as core


def test_version() -> None:
    assert core.__version__ == '0.1.0'


def test_public_api_contains_lifecycle_foundation() -> None:
    expected = {
        'AlarmEvaluation',
        'AlarmIdentity',
        'AlarmOccurrence',
        'AlarmEpisode',
        'AlarmRuntimeState',
        'GroupLifecycleState',
        'execute_evaluator',
        'reduce_group_cycle',
        'reset_group_for_reconfiguration',
    }
    assert expected <= set(core.__all__)
