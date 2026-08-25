from __future__ import annotations

from ada.kpis.core import KpiWatermark
from ada.processes.kpis_timeseries_delivery.errors import KpiTimeseriesDeliveryRepositoryError
from ada.processes.kpis_timeseries_delivery.models import KpiTimeseriesCheckpoint
from atlanticus.state import AtomicStateStore, StateKey

_HISTORIAN_WATERMARK_KEY = StateKey(namespace=('kpis-historian',), name='committed-watermark')
_CHECKPOINT_KEY = StateKey(namespace=('kpis-timeseries-delivery',), name='checkpoint')


class KpiHistorianWatermarkStore:
    def __init__(self, *, store: AtomicStateStore) -> None:
        if not isinstance(store, AtomicStateStore):
            raise TypeError('store must be AtomicStateStore')
        self._store = store

    def read_watermark(self) -> KpiWatermark | None:
        document = self._store.read(_HISTORIAN_WATERMARK_KEY)
        if document is None:
            return None
        if set(document.value) != {'watermark_utc'}:
            raise KpiTimeseriesDeliveryRepositoryError(
                'KPI historian committed watermark state has unexpected fields'
            )
        try:
            return KpiWatermark.from_document(document.value)
        except (TypeError, ValueError) as error:
            raise KpiTimeseriesDeliveryRepositoryError(
                'KPI historian committed watermark state is invalid'
            ) from error


class KpiTimeseriesDeliveryCheckpointStore:
    def __init__(self, *, store: AtomicStateStore) -> None:
        if not isinstance(store, AtomicStateStore):
            raise TypeError('store must be AtomicStateStore')
        self._store = store

    def read(self) -> KpiTimeseriesCheckpoint | None:
        document = self._store.read(_CHECKPOINT_KEY)
        if document is None:
            return None
        if set(document.value) != {'watermark_utc', 'configuration_revision'}:
            raise KpiTimeseriesDeliveryRepositoryError(
                'KPI timeseries delivery checkpoint has unexpected fields'
            )
        raw_watermark = document.value.get('watermark_utc')
        if raw_watermark is None:
            watermark = None
        elif isinstance(raw_watermark, str):
            try:
                watermark = KpiWatermark.parse(raw_watermark)
            except ValueError as error:
                raise KpiTimeseriesDeliveryRepositoryError(
                    'KPI timeseries delivery checkpoint watermark is invalid'
                ) from error
        else:
            raise KpiTimeseriesDeliveryRepositoryError(
                'KPI timeseries delivery checkpoint watermark is invalid'
            )
        revision = document.value.get('configuration_revision')
        if not isinstance(revision, str):
            raise KpiTimeseriesDeliveryRepositoryError(
                'KPI timeseries delivery checkpoint configuration_revision is invalid'
            )
        return KpiTimeseriesCheckpoint(
            watermark=watermark,
            configuration_revision=revision,
        )

    def commit(self, checkpoint: KpiTimeseriesCheckpoint) -> KpiTimeseriesCheckpoint:
        if not isinstance(checkpoint, KpiTimeseriesCheckpoint):
            raise TypeError('checkpoint must be KpiTimeseriesCheckpoint')
        current = self.read()
        if (
            current is not None
            and current.watermark is not None
            and (checkpoint.watermark is None or checkpoint.watermark < current.watermark)
        ):
            raise KpiTimeseriesDeliveryRepositoryError(
                'KPI timeseries delivery checkpoint watermark must not regress'
            )
        if current == checkpoint:
            return current
        self._store.replace(
            _CHECKPOINT_KEY,
            {
                'watermark_utc': None
                if checkpoint.watermark is None
                else checkpoint.watermark.text,
                'configuration_revision': checkpoint.configuration_revision,
            },
        )
        return checkpoint
