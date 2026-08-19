from __future__ import annotations

from ada.kpis.core import KpiWatermark
from ada.kpis.persistence.errors import (
    KpiPersistenceCorruptionError,
    KpiWatermarkRegressionError,
)
from atlanticus.state import AtomicStateStore, StateKey

_COMMITTED_WATERMARK_KEY = StateKey(namespace=('kpis',), name='committed-watermark')


class KpiCommitStore:
    def __init__(self, *, store: AtomicStateStore) -> None:
        if not isinstance(store, AtomicStateStore):
            raise TypeError('store must be AtomicStateStore')
        self._store = store

    def read_watermark(self) -> KpiWatermark | None:
        document = self._store.read(_COMMITTED_WATERMARK_KEY)
        if document is None:
            return None
        if set(document.value) != {'watermark_utc'}:
            raise KpiPersistenceCorruptionError(
                'KPI committed watermark state has unexpected or missing fields'
            )
        try:
            return KpiWatermark.from_document(document.value)
        except (TypeError, ValueError) as error:
            raise KpiPersistenceCorruptionError(
                'KPI committed watermark state is invalid'
            ) from error

    def commit_watermark(self, watermark: KpiWatermark) -> KpiWatermark:
        if not isinstance(watermark, KpiWatermark):
            raise TypeError('watermark must be KpiWatermark')
        current = self.read_watermark()
        if current is not None and watermark < current:
            raise KpiWatermarkRegressionError('KPI committed watermark must not move backwards')
        if current == watermark:
            return current
        self._store.replace(_COMMITTED_WATERMARK_KEY, watermark.as_document())
        return watermark
