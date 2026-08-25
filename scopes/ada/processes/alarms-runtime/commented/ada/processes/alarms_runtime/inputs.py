# Define contratos inmutables para inputs, locators y requests pendientes, incluida la proveniencia durable del priority_group.
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from ada.alarms.core import DeactivationDecision, DeactivationRequest, ManagementAction


# Contrato AlarmInputStream: agrupa datos y valida invariantes cerca de su frontera.
class AlarmInputStream(StrEnum):
    MANAGEMENT = 'MANAGEMENT'
    DEACTIVATION_DECISION = 'DEACTIVATION_DECISION'


# Contrato AlarmInputCursor: agrupa datos y valida invariantes cerca de su frontera.
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


# Contrato AlarmInputLocator: agrupa datos y valida invariantes cerca de su frontera.
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


# Contrato AlarmInputRecord: agrupa datos y valida invariantes cerca de su frontera.
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


# Contrato AlarmInputSource: agrupa datos y valida invariantes cerca de su frontera.
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


# Contrato AlarmPendingDeactivationRequest: agrupa datos y valida invariantes cerca de su frontera.
@dataclass(frozen=True, slots=True)
class AlarmPendingDeactivationRequest:
    request: DeactivationRequest
    priority_group: str

    def __post_init__(self) -> None:
        if not isinstance(self.request, DeactivationRequest):
            raise TypeError('request must be DeactivationRequest')
        if not isinstance(self.priority_group, str) or not self.priority_group.strip():
            raise ValueError('priority_group must be a non-empty string')
        object.__setattr__(self, 'priority_group', self.priority_group.strip())

    @property
    def request_id(self) -> str:
        return self.request.request_id


# Contrato AlarmOperationalInputs: agrupa datos y valida invariantes cerca de su frontera.
@dataclass(frozen=True, slots=True)
class AlarmOperationalInputs:
    management_actions: tuple[ManagementAction, ...] = ()
    pending_deactivation_requests: tuple[AlarmPendingDeactivationRequest, ...] = ()
    deactivation_decisions: tuple[DeactivationDecision, ...] = ()

    def __post_init__(self) -> None:
        _require_typed_tuple(self.management_actions, ManagementAction, 'management_actions')
        _require_typed_tuple(
            self.pending_deactivation_requests,
            AlarmPendingDeactivationRequest,
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
        request_ids = {pending.request.request_id for pending in self.pending_deactivation_requests}
        for decision in self.deactivation_decisions:
            if decision.request_id not in request_ids:
                raise ValueError('deactivation decision must reference a pending durable request')


# Auxiliar _require_typed_tuple: mantiene una responsabilidad interna acotada y determinista.
def _require_typed_tuple(value: object, expected: type, name: str) -> None:
    if not isinstance(value, tuple) or not all(isinstance(item, expected) for item in value):
        raise TypeError(f'{name} must contain {expected.__name__} values')


# Auxiliar _require_unique: mantiene una responsabilidad interna acotada y determinista.
def _require_unique(values: tuple[object, ...], attribute: str, name: str) -> None:
    identifiers = [getattr(value, attribute) for value in values]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError(f'{name} must not contain duplicate {attribute}')
