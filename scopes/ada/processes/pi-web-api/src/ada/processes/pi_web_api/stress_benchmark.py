from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import perf_counter

from ada.processes.pi_web_api.acquisition import PiStreamSetAcquirer
from ada.processes.pi_web_api.catalog.stress_definitions import STRESS_TAGS
from ada.processes.pi_web_api.errors import PiWebApiProcessConfigurationError
from ada.processes.pi_web_api.job import PiWebApiJob
from ada.processes.pi_web_api.materialization import PiWebApiMaterializer
from ada.processes.pi_web_api.models import (
    PiAcquisitionResult,
    PiAcquisitionWindow,
    PiMaterializationResult,
    PiSample,
)
from ada.processes.pi_web_api.planning import PiSlotPlanner
from atlanticus.configuration import ConfigurationValueError, ResolvedConfiguration
from atlanticus.datasets.runtime import DatasetRuntime
from atlanticus.integrations.pi.contracts import (
    PiCatalog,
    PiExtractionMode,
    PiMaterialization,
    PiTagDefinition,
    PiWebApiSource,
)
from atlanticus.runtime import JobRuntimeContext


@dataclass(frozen=True, slots=True)
class PiStressBenchmarkSettings:
    enabled: bool
    kind: str = 'capacity'
    logical_tag_count: int = 1000
    lookback_hours: int = 24
    end_utc: datetime | None = None
    physical_tag_limit: int = 0
    io_chunk_limit: int = 40
    io_max_workers: int = 3

    @classmethod
    def from_configuration(
        cls,
        configuration: ResolvedConfiguration,
    ) -> PiStressBenchmarkSettings:
        if not isinstance(configuration, ResolvedConfiguration):
            raise PiWebApiProcessConfigurationError('configuration must be a ResolvedConfiguration')
        try:
            enabled = configuration.get_bool('PI_WEB_API_STRESS_BENCHMARK', False)
        except ConfigurationValueError as error:
            raise PiWebApiProcessConfigurationError(str(error)) from error
        if not enabled:
            return cls(enabled=False)
        kind = (
            (configuration.get('PI_WEB_API_STRESS_KIND', 'capacity') or 'capacity').strip().lower()
        )
        if kind not in {'capacity', 'io'}:
            raise PiWebApiProcessConfigurationError('PI_WEB_API_STRESS_KIND must be capacity or io')
        try:
            physical_tag_limit = configuration.get_int(
                'PI_WEB_API_STRESS_PHYSICAL_TAG_LIMIT',
                0,
            )
            if kind == 'capacity':
                logical_tag_count = configuration.get_int(
                    'PI_WEB_API_STRESS_LOGICAL_TAGS',
                    1000,
                )
                lookback_hours = configuration.get_int(
                    'PI_WEB_API_STRESS_LOOKBACK_HOURS',
                    24,
                )
                io_chunk_limit = 40
                io_max_workers = 3
            else:
                logical_tag_count = 1000
                lookback_hours = 24
                io_chunk_limit = configuration.get_int(
                    'PI_WEB_API_STRESS_IO_CHUNK_LIMIT',
                    40,
                )
                io_max_workers = configuration.get_int(
                    'PI_WEB_API_STRESS_IO_MAX_WORKERS',
                    3,
                )
        except ConfigurationValueError as error:
            raise PiWebApiProcessConfigurationError(str(error)) from error
        if physical_tag_limit is None or physical_tag_limit < 0:
            raise PiWebApiProcessConfigurationError(
                'PI_WEB_API_STRESS_PHYSICAL_TAG_LIMIT must be zero or greater'
            )
        if kind == 'capacity':
            for key, value in (
                ('PI_WEB_API_STRESS_LOGICAL_TAGS', logical_tag_count),
                ('PI_WEB_API_STRESS_LOOKBACK_HOURS', lookback_hours),
            ):
                if value is None or value <= 0:
                    raise PiWebApiProcessConfigurationError(f'{key} must be greater than zero')
        else:
            if io_chunk_limit is None or io_chunk_limit <= 0:
                raise PiWebApiProcessConfigurationError(
                    'PI_WEB_API_STRESS_IO_CHUNK_LIMIT must be greater than zero'
                )
            if io_max_workers is None or not 1 <= io_max_workers <= 3:
                raise PiWebApiProcessConfigurationError(
                    'PI_WEB_API_STRESS_IO_MAX_WORKERS must be between 1 and 3'
                )
        end_utc = _optional_utc_second(configuration.get('PI_WEB_API_STRESS_END_UTC'))
        application = configuration.require('APPLICATION')
        if not any(token in application.casefold() for token in ('stress', 'benchmark')):
            raise PiWebApiProcessConfigurationError(
                'APPLICATION must identify an isolated stress or benchmark application '
                'when PI_WEB_API_STRESS_BENCHMARK is enabled'
            )
        if end_utc is None:
            raise PiWebApiProcessConfigurationError(
                'PI_WEB_API_STRESS_END_UTC is required when PI_WEB_API_STRESS_BENCHMARK is enabled'
            )
        return cls(
            enabled=enabled,
            kind=kind,
            logical_tag_count=logical_tag_count,
            lookback_hours=lookback_hours,
            end_utc=end_utc,
            physical_tag_limit=physical_tag_limit,
            io_chunk_limit=io_chunk_limit,
            io_max_workers=io_max_workers,
        )


@dataclass(frozen=True, slots=True)
class PiStressBenchmarkPlanner(PiSlotPlanner):
    benchmark_end_utc: datetime = datetime(1970, 1, 1, tzinfo=UTC)
    lookback_hours: int = 24

    def __post_init__(self) -> None:
        PiSlotPlanner.__post_init__(self)
        end_utc = _require_aligned_utc_second(
            self.benchmark_end_utc,
            interpolation_seconds=self.interpolation_seconds,
            field_name='benchmark_end_utc',
        )
        if not isinstance(self.lookback_hours, int) or isinstance(self.lookback_hours, bool):
            raise PiWebApiProcessConfigurationError('lookback_hours must be an integer')
        if self.lookback_hours <= 0:
            raise PiWebApiProcessConfigurationError('lookback_hours must be greater than zero')
        start_utc = (
            end_utc
            - timedelta(hours=self.lookback_hours)
            + timedelta(seconds=self.interpolation_seconds)
        )
        if (start_utc.year, start_utc.month) != (end_utc.year, end_utc.month):
            raise PiWebApiProcessConfigurationError(
                'stress benchmark interval must remain inside one UTC month'
            )
        object.__setattr__(self, 'benchmark_end_utc', end_utc)

    @property
    def benchmark_start_utc(self) -> datetime:
        return (
            self.benchmark_end_utc
            - timedelta(hours=self.lookback_hours)
            + timedelta(seconds=self.interpolation_seconds)
        )

    def plan(
        self,
        *,
        now_utc: datetime,
        committed_watermark_utc: datetime | None,
    ) -> PiAcquisitionWindow | None:
        del now_utc
        if committed_watermark_utc is None:
            first_pending = self.benchmark_start_utc
        else:
            committed = _require_aligned_utc_second(
                committed_watermark_utc,
                interpolation_seconds=self.interpolation_seconds,
                field_name='committed_watermark_utc',
            )
            first_pending = max(
                committed + timedelta(seconds=self.interpolation_seconds),
                self.benchmark_start_utc,
            )
        if first_pending > self.benchmark_end_utc:
            return None
        max_slots = self.max_recovery_window_seconds // self.interpolation_seconds
        remaining_slots = (
            int((self.benchmark_end_utc - first_pending).total_seconds())
            // self.interpolation_seconds
        ) + 1
        slot_count = min(max_slots, remaining_slots)
        last_slot = first_pending + timedelta(seconds=(slot_count - 1) * self.interpolation_seconds)
        return PiAcquisitionWindow(
            first_slot_utc=first_pending,
            last_slot_utc=last_slot,
            interpolation_seconds=self.interpolation_seconds,
            recovery_truncated=False,
        )


class PiStressBenchmarkJob(PiWebApiJob):
    __slots__ = ('_benchmark_end_utc',)

    def __init__(self, *, benchmark_end_utc: datetime, **kwargs) -> None:
        super().__init__(**kwargs)
        self._benchmark_end_utc = benchmark_end_utc

    def run_iteration(self, context: JobRuntimeContext) -> None:
        super().run_iteration(context)
        committed = self.producer_state.current().committed_watermark_utc
        if committed is None or committed < self._benchmark_end_utc:
            return
        context.set_iteration_fact('stress_benchmark_complete', True)
        context.set_execution_fact('stress_benchmark_complete', True)
        context.logger.info(
            'PI Web API stress benchmark completed',
            event_name='pi_web_api.stress.completed',
            benchmark_end_utc=self._benchmark_end_utc,
            committed_watermark_utc=committed,
        )
        context.request_stop('stress_benchmark_complete')


class PiStressBenchmarkAcquirer(PiStreamSetAcquirer):
    def acquire(
        self,
        *,
        plan,
        window: PiAcquisitionWindow,
        context: JobRuntimeContext,
    ) -> PiAcquisitionResult:
        started = perf_counter()
        result = super().acquire(plan=plan, window=window, context=context)
        duration = perf_counter() - started
        context.set_iteration_fact('stress_window_first_utc', window.first_slot_utc)
        context.set_iteration_fact('stress_window_last_utc', window.last_slot_utc)
        context.set_iteration_fact('stress_window_slots', window.slot_count)
        context.set_iteration_fact('stress_acquisition_seconds', round(duration, 6))
        context.set_iteration_fact('stress_physical_samples', len(result.interpolated))
        context.set_iteration_fact('stress_physical_tags', len(plan.interpolated))
        context.set_iteration_fact(
            'stress_chunk_limit',
            self._client.settings.limits.interpolated_max_web_ids,
        )
        context.set_execution_fact('stress_physical_tags', len(plan.interpolated))
        context.set_execution_fact(
            'stress_chunk_limit',
            self._client.settings.limits.interpolated_max_web_ids,
        )
        context.increment_execution_counter('stress_physical_samples', len(result.interpolated))
        context.increment_execution_counter('stress_acquisition_seconds', duration)
        return result


class PiStressBenchmarkMaterializer(PiWebApiMaterializer):
    def __init__(
        self,
        *,
        runtime: DatasetRuntime,
        physical_catalog: PiCatalog,
        logical_tag_count: int,
    ) -> None:
        logical_catalog, fanout = _build_logical_catalog(
            physical_catalog=physical_catalog,
            logical_tag_count=logical_tag_count,
        )
        super().__init__(runtime=runtime, catalog=logical_catalog)
        self._fanout = fanout
        self._logical_tag_count = logical_tag_count

    def publish(
        self,
        *,
        window: PiAcquisitionWindow,
        acquisition: PiAcquisitionResult,
        context: JobRuntimeContext,
    ) -> PiMaterializationResult:
        started = perf_counter()
        expanded = _expand_acquisition(
            acquisition=acquisition,
            fanout=self._fanout,
            context=context,
        )
        fanout_duration = perf_counter() - started
        materialization_started = perf_counter()
        result = super().publish(
            window=window,
            acquisition=expanded,
            context=context,
        )
        materialization_duration = perf_counter() - materialization_started
        publication_size_bytes = sum(
            publication.size_bytes or 0 for publication in result.publications
        )
        publication_duration_ms = sum(
            publication.duration_ms for publication in result.publications
        )
        publication_rows = max(
            (publication.item_count for publication in result.publications),
            default=0,
        )
        context.set_iteration_fact('stress_logical_tags', self._logical_tag_count)
        context.set_iteration_fact('stress_logical_samples', len(expanded.interpolated))
        context.set_iteration_fact('stress_fanout_seconds', round(fanout_duration, 6))
        context.set_iteration_fact(
            'stress_materialization_seconds',
            round(materialization_duration, 6),
        )
        context.set_iteration_fact('stress_artifact_size_bytes', publication_size_bytes)
        context.set_iteration_fact('stress_artifact_rows', publication_rows)
        context.set_iteration_fact(
            'stress_publication_duration_ms',
            round(publication_duration_ms, 3),
        )
        context.set_execution_fact('stress_logical_tags', self._logical_tag_count)
        context.set_execution_fact('stress_last_artifact_size_bytes', publication_size_bytes)
        context.set_execution_fact('stress_last_artifact_rows', publication_rows)
        context.increment_execution_counter(
            'stress_logical_samples',
            len(expanded.interpolated),
        )
        context.increment_execution_counter('stress_fanout_seconds', fanout_duration)
        context.increment_execution_counter(
            'stress_materialization_seconds',
            materialization_duration,
        )
        return result


def build_stress_physical_catalog(
    *,
    interpolation_seconds: int,
    physical_tag_limit: int = 0,
) -> PiCatalog:
    tags = STRESS_TAGS
    if physical_tag_limit > len(tags):
        raise PiWebApiProcessConfigurationError(
            'PI_WEB_API_STRESS_PHYSICAL_TAG_LIMIT exceeds available stress tags'
        )
    if physical_tag_limit:
        tags = tags[:physical_tag_limit]
    definitions = tuple(
        PiTagDefinition(
            tag_name=tag_name,
            alias=f'stress_source_{index:04d}',
            value_kind=value_kind,
            extraction_mode=PiExtractionMode.INTERPOLATED,
            materializations=(PiMaterialization.MONTHLY,),
        )
        for index, (tag_name, value_kind) in enumerate(tags, start=1)
    )
    return PiCatalog(
        source=PiWebApiSource(interpolation_seconds=interpolation_seconds),
        definitions=definitions,
    )


def stress_physical_tag_count() -> int:
    return len(STRESS_TAGS)


def _build_logical_catalog(
    *,
    physical_catalog: PiCatalog,
    logical_tag_count: int,
) -> tuple[PiCatalog, dict[str, tuple[str, ...]]]:
    physical = tuple(
        definition
        for definition in physical_catalog.definitions
        if definition.is_active and definition.extraction_mode is PiExtractionMode.INTERPOLATED
    )
    if not physical:
        raise PiWebApiProcessConfigurationError(
            'stress benchmark requires at least one active interpolated physical tag'
        )
    if logical_tag_count <= 0:
        raise PiWebApiProcessConfigurationError('logical_tag_count must be greater than zero')
    fanout_lists: dict[str, list[str]] = {
        definition.tag_name.casefold(): [] for definition in physical
    }
    definitions: list[PiTagDefinition] = []
    for logical_index in range(logical_tag_count):
        source = physical[logical_index % len(physical)]
        synthetic_tag_name = f'__stress_{logical_index + 1:04d}'
        fanout_lists[source.tag_name.casefold()].append(synthetic_tag_name)
        definitions.append(
            PiTagDefinition(
                tag_name=synthetic_tag_name,
                alias=f'kpi_stress_{logical_index + 1:04d}',
                value_kind=source.value_kind,
                extraction_mode=PiExtractionMode.INTERPOLATED,
                materializations=(PiMaterialization.MONTHLY,),
            )
        )
    return (
        PiCatalog(
            source=physical_catalog.source,
            definitions=tuple(definitions),
        ),
        {key: tuple(value) for key, value in fanout_lists.items()},
    )


def _expand_acquisition(
    *,
    acquisition: PiAcquisitionResult,
    fanout: dict[str, tuple[str, ...]],
    context: JobRuntimeContext,
) -> PiAcquisitionResult:
    expanded: list[PiSample] = []
    for index, sample in enumerate(acquisition.interpolated):
        if index % 4096 == 0:
            context.raise_if_cancelled()
        for synthetic_tag_name in fanout.get(sample.tag_name.casefold(), ()):
            expanded.append(
                PiSample(
                    tag_name=synthetic_tag_name,
                    timestamp_utc=sample.timestamp_utc,
                    value=sample.value,
                )
            )
    return PiAcquisitionResult(
        interpolated=tuple(expanded),
        recorded=(),
        interpolated_request_count=acquisition.interpolated_request_count,
        recorded_request_count=0,
        split_count=acquisition.split_count,
        interpolated_conflict_count=acquisition.interpolated_conflict_count,
        recorded_conflict_count=0,
        unexpected_record_count=acquisition.unexpected_record_count,
    )


def _optional_utc_second(value: str | None) -> datetime | None:
    if value is None or value == '':
        return None
    candidate = value[:-1] + '+00:00' if value.endswith('Z') else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as error:
        raise PiWebApiProcessConfigurationError(
            'PI_WEB_API_STRESS_END_UTC must be a valid ISO-8601 datetime'
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise PiWebApiProcessConfigurationError('PI_WEB_API_STRESS_END_UTC must use UTC')
    if parsed.microsecond:
        raise PiWebApiProcessConfigurationError(
            'PI_WEB_API_STRESS_END_UTC must not contain microseconds'
        )
    return parsed.astimezone(UTC)


def _require_aligned_utc_second(
    value: datetime,
    *,
    interpolation_seconds: int,
    field_name: str,
) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise PiWebApiProcessConfigurationError(f'{field_name} must be timezone-aware')
    if value.utcoffset() != timedelta(0):
        raise PiWebApiProcessConfigurationError(f'{field_name} must use UTC')
    normalized = value.astimezone(UTC)
    if normalized.microsecond:
        raise PiWebApiProcessConfigurationError(f'{field_name} must not contain microseconds')
    epoch_seconds = math.floor(normalized.timestamp())
    if epoch_seconds % interpolation_seconds:
        raise PiWebApiProcessConfigurationError(
            f'{field_name} must be aligned to interpolation_seconds'
        )
    return normalized
