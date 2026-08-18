from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from atlanticus.data_producers.core.models import SourceScope


@runtime_checkable
class SourceScopeProvider(Protocol):
    def capture(self, *, captured_at_utc: datetime) -> SourceScope: ...
