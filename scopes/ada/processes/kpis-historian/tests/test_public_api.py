import ada.processes.kpis_historian as historian


def test_public_api_is_small_and_versioned() -> None:
    assert historian.__version__ == '0.1.2'
    assert set(historian.__all__) == {
        'KpiHistorianCommitStore',
        'KpiHistorianComposition',
        'KpiHistorianIterationResult',
        'KpiHistorianIterationStatus',
        'KpiHistorianJob',
        'KpiHistorianSettings',
        'KpiHistoryWriteResult',
        'KpiHistoryWriter',
        '__version__',
        'build_composition',
        'error_history_definition',
        'error_history_schema',
        'history_definition',
        'history_schema',
        'load_configuration',
        'run',
    }
