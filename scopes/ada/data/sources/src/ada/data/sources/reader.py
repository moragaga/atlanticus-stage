from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

import pandas as pd
import pyarrow as pa

from atlanticus.datasets.models import DatasetDefinition, DatasetTarget


@runtime_checkable
class SourceDatasetReader(Protocol):
    def read_frame(
        self,
        *,
        definition: DatasetDefinition,
        target: DatasetTarget,
        projection_schema: pa.Schema,
        timestamp_column: str | None = None,
        start_utc: datetime | None = None,
        end_utc: datetime | None = None,
    ) -> pd.DataFrame | None: ...
