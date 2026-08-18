# Fija identidad y observabilidad Blockgrade sobre el job común.
from ada.processes.blockgrade.errors import BlockgradeProcessError
from atlanticus.data_producers.sql import SqlDataProducerJob


class BlockgradeJob(SqlDataProducerJob):
    def __init__(self, *, definitions, planner, producer_state, executor) -> None:
        super().__init__(
            producer_key='blockgrade',
            definitions=definitions,
            planner=planner,
            producer_state=producer_state,
            executor=executor,
            missing_scope_fact_name='missing_shift_ids',
        )

    def _raise_execution_failure(self, context, failures: int) -> None:
        error = BlockgradeProcessError(
            f'Blockgrade execution finished with {failures} source failure(s)'
        )
        cause = context.get_memory(self._first_failure_memory_key)
        if isinstance(cause, BaseException):
            raise error from cause
        raise error
