from __future__ import annotations

# El protocolo permite consumir resolutores de semana operacional sin acoplarse a una implementación concreta.
# Los comentarios explican intención y fronteras sin modificar estructura ni comportamiento.

from datetime import datetime
from typing import Protocol, runtime_checkable

from ada.operational_calendar.models import OperationalWeekPartition


@runtime_checkable
class OperationalCalendarResolver(Protocol):
    @property
    def key(self) -> str: ...

    def resolve(self, timestamp_utc: datetime) -> OperationalWeekPartition: ...
