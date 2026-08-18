from __future__ import annotations

import pytest

from atlanticus.data_producers.remanentes.job import RemanentesJob


def test_job_requires_materializers() -> None:
    with pytest.raises(TypeError, match='materializers'):
        RemanentesJob(materializers=(), producer_state=object(), idle_seconds=30)
