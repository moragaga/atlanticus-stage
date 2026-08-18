# Espejo comentado del Data Producer Fábrica. La lógica ejecutable es idéntica al source productivo; este archivo existe para revisión en español.
from __future__ import annotations

from time import monotonic

from atlanticus.data_producers.fabrica.materialization import FabricaMaterializer
from atlanticus.data_producers.fabrica.models import FabricaPlanStreamDefinition
from atlanticus.data_producers.fabrica.producer_state import FabricaProducerState
from atlanticus.observability import trace_span
from atlanticus.runtime import JobRuntimeContext


class FabricaJob:
    def __init__(
        self,
        *,
        materializers: tuple[FabricaMaterializer, ...],
        producer_state: FabricaProducerState,
        idle_seconds: int,
    ) -> None:
        if not materializers or not all(
            isinstance(item, FabricaMaterializer) for item in materializers
        ):
            raise TypeError('materializers must contain FabricaMaterializer values')
        if not isinstance(producer_state, FabricaProducerState):
            raise TypeError('producer_state must be a FabricaProducerState')
        if not isinstance(idle_seconds, int) or isinstance(idle_seconds, bool) or idle_seconds <= 0:
            raise ValueError('idle_seconds must be an integer greater than zero')
        self._materializers = materializers
        self._producer_state = producer_state
        self._idle_seconds = idle_seconds

    def run_iteration(self, context: JobRuntimeContext) -> None:
        self._initialize_execution_facts(context)
        planned = 0
        completed = 0
        failed = 0
        changed = 0
        rows = 0
        partitions_changed = 0
        for materializer in self._ordered_materializers():
            definition = materializer.definition
            if not definition.metrics:
                context.logger.debug(
                    'Stream disabled',
                    event_name='fabrica.stream.disabled',
                    stream=definition.stream_key,
                    reason='empty_catalog',
                )
                continue
            prefix = definition.source_day_prefix(_utc_now())
            try:
                source_blob = materializer.latest_source(prefix=prefix)
            except Exception as error:
                failed += 1
                context.increment_execution_counter('streams_failed')
                context.logger.exception(
                    'Source discovery failed',
                    error,
                    event_name='fabrica.stream.discovery_failed',
                    stream=definition.stream_key,
                )
                continue
            if source_blob is None:
                continue
            if self._producer_state.source_is_current(
                stream_key=definition.stream_key,
                source_blob=source_blob,
                catalog_signature=materializer.catalog_signature,
            ):
                context.logger.debug(
                    'Stream unchanged',
                    event_name='fabrica.stream.skipped',
                    stream=definition.stream_key,
                    reason='source_unchanged',
                )
                continue
            if not context.iteration_has_work:
                context.mark_iteration_work()
            planned += 1
            self._producer_state.mark_attempt(definition.stream_key)
            context.logger.debug(
                'Stream started',
                event_name='fabrica.stream.started',
                stream=definition.stream_key,
                source_blob=source_blob.name,
            )
            started = monotonic()
            previous_revision = self._producer_state.stream(definition.stream_key).revision
            try:
                with trace_span(
                    'fabrica.stream',
                    attributes={
                        'atlanticus.span_kind': 'dependency',
                        'component': 'atlanticus.data_producers.fabrica',
                        'stream': definition.stream_key,
                    },
                ):
                    result = materializer.materialize(source_blob=source_blob)
                manifest = self._producer_state.commit_stream(
                    stream_key=definition.stream_key,
                    source_blob=source_blob,
                    catalog_signature=materializer.catalog_signature,
                    changed=result.new_data,
                    publication_signatures=result.publication_signatures,
                )
            except Exception as error:
                failed += 1
                context.increment_execution_counter('streams_failed')
                context.logger.exception(
                    'Stream failed',
                    error,
                    event_name='fabrica.stream.failed',
                    stream=definition.stream_key,
                    source_duration_seconds=round(monotonic() - started, 3),
                )
                continue
            stream_state = manifest.streams[definition.stream_key]
            effective_change = stream_state.revision > previous_revision
            completed += 1
            rows += result.source_row_count
            partitions_changed += result.partitions_changed
            context.increment_execution_counter('streams_completed')
            context.increment_execution_counter('rows', result.source_row_count)
            context.increment_execution_counter('partitions_changed', result.partitions_changed)
            context.set_execution_fact('data_revision', manifest.revision)
            if effective_change:
                changed += 1
                context.increment_execution_counter('streams_changed')
                context.set_execution_fact('new_data', True)
            if result.unknown_source_values and isinstance(definition, FabricaPlanStreamDefinition):
                context.logger.warning(
                    'Unknown source partition value ignored',
                    event_name='fabrica.stream.unknown_partition',
                    stream=definition.stream_key,
                    expected_partitions=','.join(
                        f'{partition.key.value}:{partition.source_value}'
                        for partition in definition.partitions
                    ),
                    unknown_count=len(result.unknown_source_values),
                    unknown_source_values=','.join(result.unknown_source_values),
                )
            if result.missing_metric_keys:
                context.logger.debug(
                    'Configured metrics missing from source',
                    event_name='fabrica.stream.metrics_missing',
                    stream=definition.stream_key,
                    missing_count=result.metrics_expected - result.metrics_present,
                    missing_metrics=','.join(result.missing_metric_keys),
                    missing_by_output=_format_missing_by_output(
                        result.missing_metric_keys_by_output
                    ),
                )
            context.logger.info(
                'Stream completed',
                event_name='fabrica.stream.completed',
                stream=definition.stream_key,
                source_duration_seconds=round(monotonic() - started, 3),
                rows=result.source_row_count,
                publications=len(result.publications),
                partitions_changed=result.partitions_changed,
                metrics_expected=result.metrics_expected,
                metrics_present=result.metrics_present,
                metrics_missing=result.metrics_expected - result.metrics_present,
                new_data=effective_change,
                stream_revision=stream_state.revision,
                producer_revision=manifest.revision,
                source_last_update=source_blob.source_file_timestamp_utc.isoformat(),
            )
        context.set_iteration_fact('streams_planned', planned)
        context.set_iteration_fact('streams_completed', completed)
        context.set_iteration_fact('streams_failed', failed)
        context.set_iteration_fact('streams_changed', changed)
        context.set_iteration_fact('rows', rows)
        context.set_iteration_fact('partitions_changed', partitions_changed)
        context.set_iteration_fact('new_data', changed > 0)
        context.set_iteration_fact('data_revision', self._producer_state.current().revision)
        context.set_iteration_fact('outcome', 'completed' if changed else 'skipped')
        if planned and not changed and not failed:
            context.set_iteration_fact('reason', 'no_new_data')
        elif failed:
            context.set_iteration_fact('reason', 'stream_failed')
        if not planned and not context.configuration.environment.is_local:
            context.wait(self._idle_seconds)

    def _ordered_materializers(self) -> tuple[FabricaMaterializer, ...]:
        return tuple(
            sorted(
                self._materializers,
                key=lambda item: (
                    self._producer_state.stream(item.definition.stream_key).last_success_at_utc
                    is not None,
                    self._producer_state.stream(item.definition.stream_key).last_success_at_utc
                    or _minimum_utc(),
                    item.definition.stream_key,
                ),
            )
        )

    def _initialize_execution_facts(self, context: JobRuntimeContext) -> None:
        for key in (
            'streams_completed',
            'streams_failed',
            'streams_changed',
            'rows',
            'partitions_changed',
        ):
            if context.get_execution_fact(key) is None:
                context.set_execution_fact(key, 0)
        if context.get_execution_fact('new_data') is None:
            context.set_execution_fact('new_data', False)
        if context.get_execution_fact('data_revision') is None:
            context.set_execution_fact('data_revision', self._producer_state.current().revision)


def _format_missing_by_output(
    values: tuple[tuple[str, tuple[str, ...]], ...],
) -> str:
    return ';'.join(f'{output}:{",".join(metrics)}' for output, metrics in values if metrics)


def _utc_now():
    from datetime import UTC, datetime

    return datetime.now(UTC)


def _minimum_utc():
    from datetime import UTC, datetime

    return datetime.min.replace(tzinfo=UTC)
