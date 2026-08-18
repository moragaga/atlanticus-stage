# Captura una sola fotografía de markers por ejecución y construye el orden de fuentes pendientes.
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from ada.operational_calendar import get_shift_id_turn_window
from ada.processes.blockgrade.extraction import BlockgradeSqlReader
from ada.processes.blockgrade.models import (
    BlockgradeExecutionPlan,
    BlockgradeLoadStrategy,
    BlockgradeSourceDefinition,
    BlockgradeSourcePlan,
)
from ada.processes.blockgrade.producer_state import BlockgradeProducerState, marker_changed
from atlanticus.runtime import JobRuntimeContext

_SHIFT_WINDOW_SIZE = 2


class BlockgradePlanner:
    def __init__(
        self,
        *,
        reader: BlockgradeSqlReader,
        producer_state: BlockgradeProducerState,
    ) -> None:
        if not isinstance(reader, BlockgradeSqlReader):
            raise TypeError('reader must be a BlockgradeSqlReader')
        if not isinstance(producer_state, BlockgradeProducerState):
            raise TypeError('producer_state must be a BlockgradeProducerState')
        self._reader = reader
        self._producer_state = producer_state

    def capture(
        self,
        definitions: Sequence[BlockgradeSourceDefinition],
        *,
        captured_at_utc: datetime | None = None,
        context: JobRuntimeContext | None = None,
    ) -> BlockgradeExecutionPlan:
        normalized = tuple(definitions)
        captured_at = _normalize_utc(captured_at_utc or datetime.now(UTC))
        markers = self._reader.read_change_markers(normalized, context=context)
        candidates: list[tuple[datetime | None, int, BlockgradeSourcePlan]] = []
        uses_shift_window = any(
            definition.load_strategy is BlockgradeLoadStrategy.SHIFT_WINDOW
            for definition in normalized
        )
        shift_ids = _shift_ids(captured_at) if uses_shift_window else ()
        shift_scope = _scope_token(shift_ids) if shift_ids else None
        for catalog_index, definition in enumerate(normalized):
            marker = markers[definition.source_key]
            state = self._producer_state.source_state(definition.source_key)
            source_shift_ids = (
                shift_ids
                if definition.load_strategy is BlockgradeLoadStrategy.SHIFT_WINDOW
                else ()
            )
            source_scope = (
                shift_scope
                if definition.load_strategy is BlockgradeLoadStrategy.SHIFT_WINDOW
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
                    BlockgradeSourcePlan(
                        definition=definition,
                        change_marker=marker,
                        scope_token=source_scope,
                        shift_ids=source_shift_ids,
                    ),
                )
            )
        candidates.sort(key=_candidate_order)
        return BlockgradeExecutionPlan(
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
    candidate: tuple[datetime | None, int, BlockgradeSourcePlan],
) -> tuple[datetime, int]:
    last_synced_at_utc, catalog_index, _plan = candidate
    return (last_synced_at_utc or datetime.min.replace(tzinfo=UTC), catalog_index)
