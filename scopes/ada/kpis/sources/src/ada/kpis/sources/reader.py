from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

import pandas as pd

from atlanticus.datasets.models import DatasetDefinition, DatasetTarget


@runtime_checkable
class SourceDatasetReader(Protocol):
    def read_frame(
        self,
        *,
        definition: DatasetDefinition,
        target: DatasetTarget,
        columns: tuple[str, ...],
        timestamp_column: str | None = None,
        start_utc: datetime | None = None,
        end_utc: datetime | None = None,
    ) -> pd.DataFrame | None: ...
