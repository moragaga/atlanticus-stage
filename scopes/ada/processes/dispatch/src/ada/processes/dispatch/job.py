from ada.processes.dispatch.errors import DispatchProcessError
from atlanticus.data_producers.sql import SqlDataProducerJob


class DispatchJob(SqlDataProducerJob):
    def __init__(self, *, definitions, planner, producer_state, executor) -> None:
        super().__init__(
            producer_key='dispatch',
            definitions=definitions,
            planner=planner,
            producer_state=producer_state,
            executor=executor,
            missing_scope_fact_name='missing_shift_ids',
        )

    def _raise_execution_failure(self, context, failures: int) -> None:
        error = DispatchProcessError(
            f'Dispatch execution finished with {failures} source failure(s)'
        )
        cause = context.get_memory(self._first_failure_memory_key)
        if isinstance(cause, BaseException):
            raise error from cause
        raise error
