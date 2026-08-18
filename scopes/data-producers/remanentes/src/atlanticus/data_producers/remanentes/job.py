from __future__ import annotations

from datetime import UTC, datetime
from time import monotonic

from atlanticus.data_producers.remanentes.materialization import RemanentesMaterializer
from atlanticus.data_producers.remanentes.producer_state import RemanentesProducerState
from atlanticus.observability import trace_span
from atlanticus.runtime import JobRuntimeContext


class RemanentesJob:
    def __init__(
        self,
        *,
        materializers: tuple[RemanentesMaterializer, ...],
        producer_state: RemanentesProducerState,
        idle_seconds: int,
    ) -> None:
        if not materializers or not all(
            isinstance(item, RemanentesMaterializer) for item in materializers
        ):
            raise TypeError('materializers must contain RemanentesMaterializer values')
        if not isinstance(producer_state, RemanentesProducerState):
            raise TypeError('producer_state must be a RemanentesProducerState')
        if not isinstance(idle_seconds, int) or isinstance(idle_seconds, bool) or idle_seconds <= 0:
            raise ValueError('idle_seconds must be an integer greater than zero')
        self._materializers = materializers
        self._producer_state = producer_state
        self._idle_seconds = idle_seconds

    def run_iteration(self, context: JobRuntimeContext) -> None:
        self._initialize_execution_facts(context)
        streams_planned = 0
        streams_completed = 0
        streams_failed = 0
        streams_changed = 0
        files_processed = 0
        source_rows = 0
        output_rows = 0
        changed_partition_ids: set[str] = set()
        now_utc = datetime.now(UTC)
        for materializer in self._ordered_materializers():
            definition = materializer.definition
            state = self._producer_state.stream(definition.stream_key)
            cursor_timestamp = state.source_watermark_utc
            cursor_name = state.source_blob_name
            cursor_etag = state.source_blob_etag
            cursor_last_modified = state.source_blob_last_modified_utc
            try:
                pending = materializer.pending_sources(
                    now_utc=now_utc,
                    cursor_timestamp_utc=cursor_timestamp,
                    cursor_blob_name=cursor_name,
                    cursor_blob_etag=cursor_etag,
                    cursor_blob_last_modified_utc=cursor_last_modified,
                )
            except Exception as error:
                streams_failed += 1
                context.increment_execution_counter('streams_failed')
                context.logger.exception(
                    'Source discovery failed',
                    error,
                    event_name='remanentes.stream.discovery_failed',
                    stream=definition.stream_key,
                )
                continue
            if not pending:
                continue
            if not context.iteration_has_work:
                context.mark_iteration_work()
            streams_planned += 1
            stream_changed = False
            stream_failed = False
            stream_source_rows = 0
            stream_output_rows = 0
            stream_changed_partition_ids: set[str] = set()
            stream_files = 0
            started = monotonic()
            for source_blob in pending:
                self._producer_state.mark_attempt(definition.stream_key)
                context.logger.debug(
                    'Source file started',
                    event_name='remanentes.source.started',
                    stream=definition.stream_key,
                    source_blob=source_blob.name,
                    source_timestamp=source_blob.source_file_timestamp_utc.isoformat(),
                )
                previous_revision = self._producer_state.stream(definition.stream_key).revision
                try:
                    with trace_span(
                        'remanentes.source',
                        attributes={
                            'atlanticus.span_kind': 'dependency',
                            'component': 'atlanticus.data_producers.remanentes',
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
                    stream_failed = True
                    streams_failed += 1
                    context.increment_execution_counter('streams_failed')
                    context.logger.exception(
                        'Source file failed',
                        error,
                        event_name='remanentes.source.failed',
                        stream=definition.stream_key,
                        source_blob=source_blob.name,
                        source_timestamp=source_blob.source_file_timestamp_utc.isoformat(),
                    )
                    break
                stream_state = manifest.streams[definition.stream_key]
                effective_change = stream_state.revision > previous_revision
                stream_changed = stream_changed or effective_change
                stream_files += 1
                stream_source_rows += result.source_row_count
                stream_output_rows += result.output_row_count
                if result.new_data:
                    stream_changed_partition_ids.add(result.publication.target.identifier)
                    changed_partition_ids.add(result.publication.target.identifier)
                if result.unknown_source_values:
                    context.logger.warning(
                        'Unknown stock values ignored',
                        event_name='remanentes.stocks.unknown_value',
                        stream=definition.stream_key,
                        unknown_count=len(result.unknown_source_values),
                        unknown_values=','.join(result.unknown_source_values),
                    )
                if result.missing_metric_keys:
                    context.logger.debug(
                        'Configured stock values missing from snapshot',
                        event_name='remanentes.stocks.metrics_missing',
                        stream=definition.stream_key,
                        missing_count=len(result.missing_metric_keys),
                        missing_metrics=','.join(result.missing_metric_keys),
                    )
                context.logger.debug(
                    'Source file completed',
                    event_name='remanentes.source.completed',
                    stream=definition.stream_key,
                    source_blob=source_blob.name,
                    source_rows=result.source_row_count,
                    output_rows=result.output_row_count,
                    new_data=effective_change,
                    stream_revision=stream_state.revision,
                )
            files_processed += stream_files
            source_rows += stream_source_rows
            output_rows += stream_output_rows
            stream_partitions_changed = len(stream_changed_partition_ids)
            context.increment_execution_counter('files_processed', stream_files)
            context.increment_execution_counter('source_rows', stream_source_rows)
            context.increment_execution_counter('output_rows', stream_output_rows)
            context.increment_execution_counter('partitions_changed', stream_partitions_changed)
            if stream_failed:
                continue
            streams_completed += 1
            context.increment_execution_counter('streams_completed')
            if stream_changed:
                streams_changed += 1
                context.increment_execution_counter('streams_changed')
                context.set_execution_fact('new_data', True)
            stream_state = self._producer_state.stream(definition.stream_key)
            context.set_execution_fact('data_revision', self._producer_state.current().revision)
            context.logger.info(
                'Stream completed',
                event_name='remanentes.stream.completed',
                stream=definition.stream_key,
                source_duration_seconds=round(monotonic() - started, 3),
                files_processed=stream_files,
                source_rows=stream_source_rows,
                output_rows=stream_output_rows,
                partitions_changed=stream_partitions_changed,
                new_data=stream_changed,
                stream_revision=stream_state.revision,
                producer_revision=self._producer_state.current().revision,
                source_last_update=(
                    None
                    if stream_state.source_watermark_utc is None
                    else stream_state.source_watermark_utc.isoformat()
                ),
            )
        context.set_iteration_fact('streams_planned', streams_planned)
        context.set_iteration_fact('streams_completed', streams_completed)
        context.set_iteration_fact('streams_failed', streams_failed)
        context.set_iteration_fact('streams_changed', streams_changed)
        context.set_iteration_fact('files_processed', files_processed)
        context.set_iteration_fact('source_rows', source_rows)
        context.set_iteration_fact('output_rows', output_rows)
        context.set_iteration_fact('partitions_changed', len(changed_partition_ids))
        context.set_iteration_fact('new_data', streams_changed > 0)
        context.set_iteration_fact('data_revision', self._producer_state.current().revision)
        context.set_iteration_fact('outcome', 'completed' if streams_changed else 'skipped')
        if streams_planned and not streams_changed and not streams_failed:
            context.set_iteration_fact('reason', 'no_new_data')
        elif streams_failed:
            context.set_iteration_fact('reason', 'stream_failed')
        if not streams_planned and not context.configuration.environment.is_local:
            context.wait(self._idle_seconds)

    def _ordered_materializers(self) -> tuple[RemanentesMaterializer, ...]:
        return tuple(
            sorted(
                self._materializers,
                key=lambda item: (
                    self._producer_state.stream(item.definition.stream_key).last_success_at_utc
                    is not None,
                    self._producer_state.stream(item.definition.stream_key).last_success_at_utc
                    or datetime.min.replace(tzinfo=UTC),
                    item.definition.stream_key,
                ),
            )
        )

    def _initialize_execution_facts(self, context: JobRuntimeContext) -> None:
        for key in (
            'streams_completed',
            'streams_failed',
            'streams_changed',
            'files_processed',
            'source_rows',
            'output_rows',
            'partitions_changed',
        ):
            if context.get_execution_fact(key) is None:
                context.set_execution_fact(key, 0)
        if context.get_execution_fact('new_data') is None:
            context.set_execution_fact('new_data', False)
        if context.get_execution_fact('data_revision') is None:
            context.set_execution_fact('data_revision', self._producer_state.current().revision)
