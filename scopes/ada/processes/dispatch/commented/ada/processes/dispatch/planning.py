# Captura una sola fotografía de markers por ejecución y construye el orden de fuentes pendientes.
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from ada.operational_calendar import get_shift_id_turn_window
from ada.processes.dispatch.extraction import DispatchSqlReader
from ada.processes.dispatch.models import (
    DispatchExecutionPlan,
    DispatchLoadStrategy,
    DispatchSourceDefinition,
    DispatchSourcePlan,
)
from ada.processes.dispatch.producer_state import DispatchProducerState, marker_changed
from atlanticus.runtime import JobRuntimeContext

_SHIFT_WINDOW_SIZE = 2


class DispatchPlanner:
    def __init__(
        self,
        *,
        reader: DispatchSqlReader,
        producer_state: DispatchProducerState,
    ) -> None:
        if not isinstance(reader, DispatchSqlReader):
            raise TypeError('reader must be a DispatchSqlReader')
        if not isinstance(producer_state, DispatchProducerState):
            raise TypeError('producer_state must be a DispatchProducerState')
        self._reader = reader
        self._producer_state = producer_state

    def capture(
        self,
        definitions: Sequence[DispatchSourceDefinition],
        *,
        captured_at_utc: datetime | None = None,
        context: JobRuntimeContext | None = None,
    ) -> DispatchExecutionPlan:
        normalized = tuple(definitions)
        captured_at = _normalize_utc(captured_at_utc or datetime.now(UTC))
        markers = self._reader.read_change_markers(normalized, context=context)
        candidates: list[tuple[datetime | None, int, DispatchSourcePlan]] = []
        uses_shift_window = any(
            definition.load_strategy is DispatchLoadStrategy.SHIFT_WINDOW
            for definition in normalized
        )
        shift_ids = _shift_ids(captured_at) if uses_shift_window else ()
        shift_scope = _scope_token(shift_ids) if shift_ids else None
        for catalog_index, definition in enumerate(normalized):
            marker = markers[definition.source_key]
            state = self._producer_state.source_state(definition.source_key)
            source_shift_ids = (
                shift_ids
                if definition.load_strategy is DispatchLoadStrategy.SHIFT_WINDOW
                else ()
            )
            source_scope = (
                shift_scope
                if definition.load_strategy is DispatchLoadStrategy.SHIFT_WINDOW
                else None
            )
            if not marker_changed(state.source_change_marker, marker) and (
                state.source_scope_token == source_scope
            ):
                continue
            candidates.append(
                (
                    state.last_synced_at_utc,
                    catalog_index,
                    DispatchSourcePlan(
                        definition=definition,
                        change_marker=marker,
                        scope_token=source_scope,
                        shift_ids=source_shift_ids,
                    ),
                )
            )
        candidates.sort(key=_candidate_order)
        return DispatchExecutionPlan(
            captured_at_utc=captured_at,
            sources=tuple(candidate[2] for candidate in candidates),
        )


def _shift_ids(captured_at_utc: datetime) -> tuple[int, ...]:
    return tuple(
        turn.shift_id
        for turn in get_shift_id_turn_window(
            value=captured_at_utc,
            window_size=_SHIFT_WINDOW_SIZE,
        )
    )


def _scope_token(shift_ids: tuple[int, ...]) -> str:
    return '|'.join(str(value) for value in shift_ids)


def _normalize_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError('captured_at_utc must be timezone-aware')
    return value.astimezone(UTC)


def _candidate_order(
    candidate: tuple[datetime | None, int, DispatchSourcePlan],
) -> tuple[datetime, int]:
    last_synced_at_utc, catalog_index, _plan = candidate
    return (last_synced_at_utc or datetime.min.replace(tzinfo=UTC), catalog_index)
