from __future__ import annotations

from collections.abc import Callable

from ada.alarms.core.models import (
    AlarmEvaluation,
    AlarmStatus,
    EvaluationContext,
    EvaluationError,
    EvaluationErrorOrigin,
    PlannedAlarm,
)

Evaluator = Callable[[EvaluationContext], AlarmEvaluation | None]


def execute_evaluator(
    planned_alarm: PlannedAlarm,
    context: EvaluationContext,
    evaluator: Evaluator,
) -> AlarmEvaluation:
    if context.alarm_identity != planned_alarm.identity:
        return _runtime_error(
            planned_alarm,
            context,
            error_key='context_identity_mismatch',
            message='Evaluation context identity does not match planned alarm',
        )
    try:
        result = evaluator(context)
    except Exception:
        return _error(
            planned_alarm,
            context,
            origin=EvaluationErrorOrigin.EVALUATOR,
            error_key='evaluator_exception',
            message='Evaluator execution failed',
        )
    if result is None:
        return _runtime_error(
            planned_alarm,
            context,
            error_key='missing_evaluation',
            message='Evaluator did not return an evaluation',
        )
    if not isinstance(result, AlarmEvaluation):
        return _runtime_error(
            planned_alarm,
            context,
            error_key='invalid_evaluation',
            message='Evaluator returned an invalid evaluation object',
        )
    if result.alarm_identity != planned_alarm.identity:
        return _runtime_error(
            planned_alarm,
            context,
            error_key='identity_mismatch',
            message='Evaluation identity does not match planned alarm',
        )
    if result.evaluated_at != context.now:
        return _runtime_error(
            planned_alarm,
            context,
            error_key='evaluated_at_mismatch',
            message='Evaluation timestamp does not match frozen iteration time',
        )
    return result


def _runtime_error(
    planned_alarm: PlannedAlarm,
    context: EvaluationContext,
    *,
    error_key: str,
    message: str,
) -> AlarmEvaluation:
    return _error(
        planned_alarm,
        context,
        origin=EvaluationErrorOrigin.RUNTIME,
        error_key=error_key,
        message=message,
    )


def _error(
    planned_alarm: PlannedAlarm,
    context: EvaluationContext,
    *,
    origin: EvaluationErrorOrigin,
    error_key: str,
    message: str,
) -> AlarmEvaluation:
    return AlarmEvaluation(
        alarm_identity=planned_alarm.identity,
        status=AlarmStatus.ERROR,
        evaluated_at=context.now,
        error=EvaluationError(
            origin=origin,
            error_key=error_key,
            message=message,
        ),
    )
