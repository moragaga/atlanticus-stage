from ada.alarms.core import (
    AlarmEvaluation,
    AlarmStatus,
    EvaluationContext,
    EvaluationErrorOrigin,
    execute_evaluator,
)

from .support import NOW, identity, physical, plan


def _context(alarm_key: str = 'risk') -> EvaluationContext:
    return EvaluationContext(
        alarm_identity=identity(alarm_key),
        now=NOW,
        parameters={},
        data={'value': 10.0},
    )


def test_execute_evaluator_returns_valid_result() -> None:
    result = execute_evaluator(
        plan('risk'),
        _context(),
        lambda _context: physical('risk', AlarmStatus.ACTIVE),
    )
    assert result.status is AlarmStatus.ACTIVE


def test_execute_evaluator_isolates_unexpected_exception() -> None:
    def broken(_context: EvaluationContext) -> AlarmEvaluation:
        raise RuntimeError('secret detail')

    result = execute_evaluator(plan('risk'), _context(), broken)
    assert result.status is AlarmStatus.ERROR
    assert result.error is not None
    assert result.error.origin is EvaluationErrorOrigin.EVALUATOR
    assert result.error.error_key == 'evaluator_exception'
    assert 'secret detail' not in result.error.message


def test_execute_evaluator_converts_missing_result_to_runtime_error() -> None:
    result = execute_evaluator(plan('risk'), _context(), lambda _context: None)
    assert result.error is not None
    assert result.error.origin is EvaluationErrorOrigin.RUNTIME
    assert result.error.error_key == 'missing_evaluation'


def test_execute_evaluator_rejects_crossed_identity() -> None:
    result = execute_evaluator(
        plan('risk'),
        _context(),
        lambda _context: physical('impact', AlarmStatus.ACTIVE),
    )
    assert result.error is not None
    assert result.error.error_key == 'identity_mismatch'
    assert result.alarm_identity == identity('risk')


def test_execute_evaluator_rejects_non_frozen_timestamp() -> None:
    result = execute_evaluator(
        plan('risk'),
        _context(),
        lambda _context: physical(
            'risk',
            AlarmStatus.ACTIVE,
            at=NOW.replace(second=1),
        ),
    )
    assert result.error is not None
    assert result.error.error_key == 'evaluated_at_mismatch'


def test_execute_evaluator_rejects_context_identity_mismatch() -> None:
    result = execute_evaluator(
        plan('risk'),
        _context('impact'),
        lambda context: physical(context.alarm_identity.alarm_key, AlarmStatus.ACTIVE),
    )
    assert result.status is AlarmStatus.ERROR
    assert result.error is not None
    assert result.error.error_key == 'context_identity_mismatch'
