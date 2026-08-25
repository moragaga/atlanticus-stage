# Proceso Latest: congela configuración por job, observa watermark fresco y publica sólo cuando corresponde.
# Mantiene el checkpoint durable independiente del documento publicado.

from __future__ import annotations

from ada.kpis.core import KpiWatermark
from ada.processes.kpis_delivery.errors import KpiDeliveryRepositoryError
from ada.processes.kpis_delivery.models import KpiDeliveryCheckpoint
from atlanticus.state import AtomicStateStore, StateKey

# Constante interna o contractual centralizada para evitar literales dispersos.
_CHECKPOINT_KEY = StateKey(namespace=('kpis-delivery',), name='checkpoint')


# La clase encapsula una responsabilidad con estado o contrato propio.
class KpiLatestDeliveryCheckpointStore:
    def __init__(self, *, store: AtomicStateStore) -> None:
        if not isinstance(store, AtomicStateStore):
            raise TypeError('store must be AtomicStateStore')
        self._store = store

    def read(self) -> KpiDeliveryCheckpoint | None:
        document = self._store.read(_CHECKPOINT_KEY)
        if document is None:
            return None
        if set(document.value) != {'watermark_utc', 'configuration_revision'}:
            raise KpiDeliveryRepositoryError('KPI delivery checkpoint has unexpected fields')
        raw_watermark = document.value.get('watermark_utc')
        if raw_watermark is None:
            watermark = None
        elif isinstance(raw_watermark, str):
            try:
                watermark = KpiWatermark.parse(raw_watermark)
            except ValueError as error:
                raise KpiDeliveryRepositoryError(
                    'KPI delivery checkpoint watermark is invalid'
                ) from error
        else:
            raise KpiDeliveryRepositoryError('KPI delivery checkpoint watermark is invalid')
        revision = document.value.get('configuration_revision')
        if not isinstance(revision, str):
            raise KpiDeliveryRepositoryError(
                'KPI delivery checkpoint configuration_revision is invalid'
            )
        return KpiDeliveryCheckpoint(
            watermark=watermark,
            configuration_revision=revision,
        )

    def commit(self, checkpoint: KpiDeliveryCheckpoint) -> KpiDeliveryCheckpoint:
        if not isinstance(checkpoint, KpiDeliveryCheckpoint):
            raise TypeError('checkpoint must be KpiDeliveryCheckpoint')
        current = self.read()
        if (
            current is not None
            and current.watermark is not None
            and (checkpoint.watermark is None or checkpoint.watermark < current.watermark)
        ):
            raise KpiDeliveryRepositoryError('KPI delivery checkpoint watermark must not regress')
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
