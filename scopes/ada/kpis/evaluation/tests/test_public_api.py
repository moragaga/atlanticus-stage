import ada.kpis.evaluation as evaluation


def test_public_api() -> None:
    assert evaluation.__version__ == '0.1.0'
    assert evaluation.KpiEvaluator is not None
    assert evaluation.KpiEvaluationSourceLoader is not None
    assert evaluation.KpiDependencies is not None
    assert evaluation.KpiEvaluationError is not None
    assert evaluation.KpiInvalidValueError is not None
    assert evaluation.KpiDependencyNotRequestedError is not None
