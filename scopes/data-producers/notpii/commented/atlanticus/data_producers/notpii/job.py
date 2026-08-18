# Orquesta recepción, materialización, estado y settlement en orden seguro.
from __future__ import annotations

from collections.abc import Mapping
from contextlib import ExitStack

from atlanticus.connectivity.service_bus import ServiceBusDelivery, ServiceBusTopicReceiver
from atlanticus.data_producers.notpii.errors import NotPiiSourceError
from atlanticus.data_producers.notpii.models import NotPiiBatch, NotPiiProcessingResult
from atlanticus.data_producers.notpii.processor import NotPiiProcessor
from atlanticus.data_producers.notpii.producer_state import (
    NotPiiProducerState,
    NotPiiStreamObservation,
)
from atlanticus.datasets.results import PublicationStatus
from atlanticus.integrations.pi.contracts import PiExtractionMode
from atlanticus.runtime import JobRuntimeContext

_DEAD_LETTER_DESCRIPTION_LIMIT = 4096
_LOCK_RENEWAL_DURATION_SECONDS = 240
_MODE_ORDER = (PiExtractionMode.INTERPOLATED, PiExtractionMode.RECORDED)


class NotPiiJob:
    def __init__(
        self,
        *,
        receivers: Mapping[PiExtractionMode, ServiceBusTopicReceiver],
        processors: Mapping[PiExtractionMode, NotPiiProcessor],
        producer_state: NotPiiProducerState,
        max_message_count: int,
    ) -> None:
        active_modes = tuple(mode for mode in _MODE_ORDER if mode in receivers)
        if not active_modes:
            raise ValueError('receivers must contain at least one configured extraction mode')
        if set(receivers) != set(processors):
            raise ValueError('receivers and processors must contain the same extraction modes')
        if any(mode not in _MODE_ORDER for mode in receivers):
            raise ValueError('receivers contain an unsupported extraction mode')
        if not isinstance(producer_state, NotPiiProducerState):
            raise ValueError('producer_state must be a NotPiiProducerState')
        if (
            not isinstance(max_message_count, int)
            or isinstance(max_message_count, bool)
            or max_message_count <= 0
        ):
            raise ValueError('max_message_count must be greater than zero')
        self._receivers = dict(receivers)
        self._processors = dict(processors)
        self._producer_state = producer_state
        self._active_modes = active_modes
        self._max_message_count = max_message_count

    def run_iteration(self, context: JobRuntimeContext) -> None:
        self._initialize_execution_facts(context)
        context.set_iteration_fact(
            'extraction_modes',
            ','.join(mode.value for mode in self._active_modes),
        )
        received_any = False
        committed_any = False
        invalid_any = False

        for mode in self._active_modes:
            deliveries = self._receivers[mode].receive_batch(
                max_message_count=self._max_message_count
            )
            if not deliveries:
                continue

            received_any = True
            context.increment_iteration_counter('messages_received', len(deliveries))
            context.increment_execution_counter('messages_received', len(deliveries))
            context.increment_execution_counter(mode.value, len(deliveries))

            with ExitStack() as locks:
                for delivery in deliveries:
                    locks.enter_context(
                        delivery.auto_renew_lock(
                            max_duration_seconds=_LOCK_RENEWAL_DURATION_SECONDS,
                        )
                    )
                batches = self._read_batch(
                    context=context,
                    mode=mode,
                    deliveries=deliveries,
                )
                if batches is None:
                    invalid_any = True
                    continue
                context.raise_if_cancelled()
                try:
                    result = self._processors[mode].publish(batches)
                except Exception:
                    self._abandon_deliveries(deliveries)
                    context.increment_execution_counter('messages_abandoned', len(deliveries))
                    raise

                mode_committed = any(
                    publication.status is PublicationStatus.COMMITTED
                    for publication in result.publications
                )
                observation = NotPiiStreamObservation(
                    source_last_updated_at_utc=result.source_last_updated_at_utc,
                    changed=mode_committed,
                )
                context.raise_if_cancelled()
                try:
                    manifest = self._producer_state.advance({mode: observation})
                except Exception:
                    self._abandon_deliveries(deliveries)
                    context.increment_execution_counter('messages_abandoned', len(deliveries))
                    raise

                context.raise_if_cancelled()
                completed_count = 0
                try:
                    for delivery in deliveries:
                        delivery.complete()
                        completed_count += 1
                except Exception:
                    context.increment_iteration_counter('messages_completed', completed_count)
                    context.increment_execution_counter('messages_completed', completed_count)
                    raise
                context.increment_iteration_counter('messages_completed', completed_count)
                context.increment_execution_counter('messages_completed', completed_count)
                self._set_processing_facts(
                    context=context,
                    result=result,
                    source_watermark=manifest.source_watermark_utc,
                    data_revision=manifest.revision,
                )
                committed_any = committed_any or mode_committed

        if committed_any:
            context.mark_iteration_work()
            context.set_iteration_fact('outcome', 'completed')
            return
        context.set_iteration_fact('outcome', 'skipped')
        if invalid_any:
            context.mark_iteration_work()
            context.set_iteration_fact('reason', 'invalid_message')
            return
        context.set_iteration_fact('reason', 'no_relevant_data' if received_any else 'no_message')

    def _read_batch(
        self,
        *,
        context: JobRuntimeContext,
        mode: PiExtractionMode,
        deliveries: tuple[ServiceBusDelivery, ...],
    ) -> tuple[NotPiiBatch, ...] | None:
        batches: list[NotPiiBatch] = []
        for delivery in deliveries:
            context.raise_if_cancelled()
            try:
                batches.append(self._processors[mode].read(delivery.message))
            except NotPiiSourceError as error:
                active_before = sum(item.can_settle for item in deliveries)
                if delivery.can_settle:
                    delivery.dead_letter(
                        reason='InvalidNotPiiPayload',
                        error_description=str(error)[:_DEAD_LETTER_DESCRIPTION_LIMIT],
                    )
                    context.increment_execution_counter('messages_dead_lettered')
                self._abandon_deliveries(deliveries)
                context.increment_execution_counter(
                    'messages_abandoned',
                    max(0, active_before - 1),
                )
                context.logger.warning(
                    'Invalid NOT PII batch was rejected before materialization',
                    event_name='notpii.batch.invalid_message',
                    extraction_mode=mode.value,
                    message_id=delivery.message.message_id,
                    message_count=len(deliveries),
                    reason_type=type(error).__name__,
                )
                return None
            except Exception:
                active_count = sum(item.can_settle for item in deliveries)
                self._abandon_deliveries(deliveries)
                context.increment_execution_counter('messages_abandoned', active_count)
                raise
        return tuple(batches)

    def _initialize_execution_facts(self, context: JobRuntimeContext) -> None:
        for key in (
            'messages_received',
            'messages_completed',
            'messages_abandoned',
            'messages_dead_lettered',
            'rows_received',
            'rows_materialized',
            'publications_committed',
        ):
            if context.get_execution_fact(key) is None:
                context.set_execution_fact(key, 0)
        for mode in self._active_modes:
            if context.get_execution_fact(mode.value) is None:
                context.set_execution_fact(mode.value, 0)
        if context.get_execution_fact('data_revision') is None:
            manifest = self._producer_state.current()
            context.set_execution_fact('data_revision', manifest.revision)
            if manifest.source_watermark_utc is not None:
                context.set_execution_fact(
                    'source_last_updated_at_utc',
                    manifest.source_watermark_utc,
                )

    def _set_processing_facts(
        self,
        *,
        context: JobRuntimeContext,
        result: NotPiiProcessingResult,
        source_watermark,
        data_revision: int,
    ) -> None:
        committed = sum(
            publication.status is PublicationStatus.COMMITTED for publication in result.publications
        )
        context.increment_iteration_counter('rows_received', result.row_count)
        context.increment_iteration_counter('rows_materialized', result.materialized_row_count)
        context.increment_iteration_counter('publications', len(result.publications))
        context.increment_iteration_counter('publications_committed', committed)
        context.set_iteration_fact('data_revision', data_revision)
        context.increment_execution_counter('rows_received', result.row_count)
        context.increment_execution_counter('rows_materialized', result.materialized_row_count)
        context.increment_execution_counter('publications_committed', committed)
        context.set_execution_fact('data_revision', data_revision)
        if source_watermark is not None:
            context.set_iteration_fact('source_last_updated_at_utc', source_watermark)
            context.set_execution_fact('source_last_updated_at_utc', source_watermark)

    @staticmethod
    def _abandon_deliveries(deliveries: tuple[ServiceBusDelivery, ...]) -> None:
        for delivery in deliveries:
            if delivery.can_settle:
                delivery.abandon()
