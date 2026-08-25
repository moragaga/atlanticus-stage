import ada.kpis.persistence as persistence


def test_public_api_is_explicit() -> None:
    assert set(persistence.__all__) == {
        'KpiCommitStore',
        'KpiEvaluationCommitter',
        'KpiEvaluationConflictError',
        'KpiEvaluationRepository',
        'KpiEvaluationWriteStatus',
        'KpiLatestRepository',
        'KpiPersistenceCorruptionError',
        'KpiPersistenceError',
        'KpiPersistencePaths',
        'KpiPersistenceValidationError',
        'KpiWatermarkRegressionError',
        '__version__',
    }
    assert persistence.__version__ == '0.1.1'
