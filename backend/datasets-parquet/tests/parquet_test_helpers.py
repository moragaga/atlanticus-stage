from __future__ import annotations

from datetime import datetime

import pyarrow as pa


def timestamp_array(*values: str) -> pa.Array:
    parsed = tuple(datetime.fromisoformat(value.replace('Z', '+00:00')) for value in values)
    return pa.array(parsed, type=pa.timestamp('us', tz='UTC'))
