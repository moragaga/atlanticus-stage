from __future__ import annotations

from collections.abc import Callable, Mapping
from threading import Lock
from typing import TypeVar

from ada.processes.pi_web_api.errors import PiWebApiTimeoutExhaustedError
from atlanticus.integrations.pi.web_api import PiWebApiTimeoutError
from atlanticus.runtime import JobRuntimeContext

_T = TypeVar('_T')
_TIMEOUT_RETRY_DELAYS_SECONDS = (2.0, 3.0, 5.0)


def execute_with_timeout_retries(
    operation: Callable[[], _T],
    *,
    context: JobRuntimeContext,
    operation_name: str,
    attributes: Mapping[str, object] | None = None,
    counter_lock: Lock | None = None,
) -> tuple[_T, int]:
    retry_count = 0
    last_timeout_phase: str | None = None
    event_attributes = dict(attributes or {})
    while True:
        context.raise_if_cancelled()
        try:
            result = operation()
        except PiWebApiTimeoutError as error:
            last_timeout_phase = error.phase
            if retry_count >= len(_TIMEOUT_RETRY_DELAYS_SECONDS):
                context.logger.warning(
                    'PI Web API timeout retries were exhausted for this iteration',
                    event_name='pi_web_api.timeout.exhausted',
                    operation=operation_name,
                    phase=error.phase,
                    retry_count=retry_count,
                    **event_attributes,
                )
                raise PiWebApiTimeoutExhaustedError(
                    phase=error.phase,
                    retry_count=retry_count,
                ) from None
            delay_seconds = _TIMEOUT_RETRY_DELAYS_SECONDS[retry_count]
            retry_count += 1
            if counter_lock is None:
                context.increment_execution_counter('pi_timeout_retries')
            else:
                with counter_lock:
                    context.increment_execution_counter('pi_timeout_retries')
            context.logger.warning(
                'PI Web API request timed out and will be retried',
                event_name='pi_web_api.timeout.retry',
                operation=operation_name,
                phase=error.phase,
                retry_number=retry_count,
                retry_limit=len(_TIMEOUT_RETRY_DELAYS_SECONDS),
                delay_seconds=delay_seconds,
                **event_attributes,
            )
            if not context.wait(delay_seconds):
                context.raise_if_cancelled()
            continue
        if retry_count:
            context.logger.info(
                'PI Web API request recovered after timeout retries',
                event_name='pi_web_api.timeout.request_recovered',
                operation=operation_name,
                retry_count=retry_count,
                phase=last_timeout_phase,
                **event_attributes,
            )
        return result, retry_count
