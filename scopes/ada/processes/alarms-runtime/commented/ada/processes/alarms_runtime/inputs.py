# Contratos puros de entrada para desacoplar Runtime del formato físico y del transporte.
# La fuente entrega valores Core ya normalizados junto con cursores y locators persistibles.
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from ada.alarms.core import DeactivationDecision, DeactivationRequest, ManagementAction


# Separamos Management y Decisions porque A03.5 exige cursores independientes.
class AlarmInputStream(StrEnum):
    MANAGEMENT = 'MANAGEMENT'
    DEACTIVATION_DECISION = 'DEACTIVATION_DECISION'


# El cursor representa la posición confirmada del stream, no un estado de negocio.
@dataclass(frozen=True, slots=True)
class AlarmInputCursor:
    hour_bucket: str
    byte_offset: int

    def __post_init__(self) -> None:
        if not isinstance(self.hour_bucket, str) or not self.hour_bucket.strip():
            raise ValueError('hour_bucket must be a non-empty string')
        if isinstance(self.byte_offset, bool) or not isinstance(self.byte_offset, int):
            raise TypeError('byte_offset must be an int')
        if self.byte_offset < 0:
            raise ValueError('byte_offset must be greater than or equal to zero')
        object.__setattr__(self, 'hour_bucket', self.hour_bucket.strip())


# El locator permite releer exactamente un input pendiente sin retroceder el cursor global.
@dataclass(frozen=True, slots=True)
class AlarmInputLocator:
    input_id: str
    hour_bucket: str
    byte_offset: int
    byte_length: int

    def __post_init__(self) -> None:
        if not isinstance(self.input_id, str) or not self.input_id.strip():
            raise ValueError('input_id must be a non-empty string')
        if not isinstance(self.hour_bucket, str) or not self.hour_bucket.strip():
            raise ValueError('hour_bucket must be a non-empty string')
        if isinstance(self.byte_offset, bool) or not isinstance(self.byte_offset, int):
            raise TypeError('byte_offset must be an int')
        if self.byte_offset < 0:
            raise ValueError('byte_offset must be greater than or equal to zero')
        if isinstance(self.byte_length, bool) or not isinstance(self.byte_length, int):
            raise TypeError('byte_length must be an int')
        if self.byte_length <= 0:
            raise ValueError('byte_length must be greater than zero')
        object.__setattr__(self, 'input_id', self.input_id.strip())
        object.__setattr__(self, 'hour_bucket', self.hour_bucket.strip())


AlarmInputValue = ManagementAction | DeactivationDecision


# Cada registro une identidad física, siguiente cursor y contrato de dominio ya validado.
@dataclass(frozen=True, slots=True)
class AlarmInputRecord:
    locator: AlarmInputLocator
    next_cursor: AlarmInputCursor
    value: AlarmInputValue

    def __post_init__(self) -> None:
        if not isinstance(self.locator, AlarmInputLocator):
            raise TypeError('locator must be AlarmInputLocator')
        if not isinstance(self.next_cursor, AlarmInputCursor):
            raise TypeError('next_cursor must be AlarmInputCursor')
        if not isinstance(self.value, ManagementAction | DeactivationDecision):
            raise TypeError('value must be ManagementAction or DeactivationDecision')
        input_id = (
            self.value.input_id
            if isinstance(self.value, ManagementAction)
            else self.value.decision_id
        )
        if self.locator.input_id != input_id:
            raise ValueError('locator input_id must match input value identity')


# Este Protocol es la frontera: JSONL, Storage u otra fuente se implementan fuera de Runtime.
@runtime_checkable
class AlarmInputSource(Protocol):
    def read_after(
        self,
        *,
        stream: AlarmInputStream,
        cursor: AlarmInputCursor | None,
    ) -> Sequence[AlarmInputRecord]: ...

    def read_at(
        self,
        *,
        stream: AlarmInputStream,
        locator: AlarmInputLocator,
    ) -> AlarmInputRecord: ...


# El ciclo recibe sólo inputs correlacionables; una decisión requiere su request durable.
@dataclass(frozen=True, slots=True)
class AlarmOperationalInputs:
    management_actions: tuple[ManagementAction, ...] = ()
    pending_deactivation_requests: tuple[DeactivationRequest, ...] = ()
    deactivation_decisions: tuple[DeactivationDecision, ...] = ()

    def __post_init__(self) -> None:
        _require_typed_tuple(self.management_actions, ManagementAction, 'management_actions')
        _require_typed_tuple(
            self.pending_deactivation_requests,
            DeactivationRequest,
            'pending_deactivation_requests',
        )
        _require_typed_tuple(
            self.deactivation_decisions,
            DeactivationDecision,
            'deactivation_decisions',
        )
        _require_unique(self.management_actions, 'input_id', 'management_actions')
        _require_unique(
            self.pending_deactivation_requests,
            'request_id',
            'pending_deactivation_requests',
        )
        _require_unique(self.deactivation_decisions, 'decision_id', 'deactivation_decisions')
        request_ids = {request.request_id for request in self.pending_deactivation_requests}
        for decision in self.deactivation_decisions:
            if decision.request_id not in request_ids:
                raise ValueError('deactivation decision must reference a pending durable request')


def _require_typed_tuple(value: object, expected: type, name: str) -> None:
    if not isinstance(value, tuple) or not all(isinstance(item, expected) for item in value):
        raise TypeError(f'{name} must contain {expected.__name__} values')


def _require_unique(values: tuple[object, ...], attribute: str, name: str) -> None:
    identifiers = [getattr(value, attribute) for value in values]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError(f'{name} must not contain duplicate {attribute}')
