# Este adapter es la única frontera que conoce la forma mínima del snapshot runtime de configuración.
# Lee por point read, valida stores/key/kind y traduce únicamente kind=kpi al contrato puro de Delivery.
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ada.kpis.delivery import KpiDeliveryBinding
from ada.processes.kpis_delivery.errors import KpiDeliveryConfigurationError
from atlanticus.connectivity.cosmos import CosmosClient

_CONFIGURATION_ID = 'snapshot'
_CONFIGURATION_PARTITION_ID = 'configuration'
_KPI_KIND = 'kpi'


@dataclass(slots=True)
class KpiDeliveryBindingsRepository:
    client: CosmosClient
    container_name: str

    def __post_init__(self) -> None:
        self.container_name = _required_text(
            self.container_name,
            label='container_name',
        )

    def read_bindings(self) -> tuple[KpiDeliveryBinding, ...]:
        document = self.client.find_item(
            container_name=self.container_name,
            item_id=_CONFIGURATION_ID,
            partition_key=_CONFIGURATION_PARTITION_ID,
        )
        if document is None:
            raise KpiDeliveryConfigurationError('KPI delivery configuration snapshot was not found')
        stores = document.get('stores')
        if not isinstance(stores, dict):
            raise KpiDeliveryConfigurationError(
                'KPI delivery configuration snapshot stores must be an object'
            )
        bindings: set[tuple[str, str]] = set()
        for raw_store_key, entries in stores.items():
            store_key = _required_text(raw_store_key, label='store key')
            if not isinstance(entries, list):
                raise KpiDeliveryConfigurationError(
                    f'KPI delivery configuration store {store_key!r} must be an array'
                )
            for index, entry in enumerate(entries):
                key, kind = _entry(entry, store_key=store_key, index=index)
                if kind == _KPI_KIND:
                    bindings.add((store_key, key))
        return tuple(
            KpiDeliveryBinding(store_key=store_key, kpi_key=kpi_key)
            for store_key, kpi_key in sorted(bindings)
        )


def _entry(entry: Any, *, store_key: str, index: int) -> tuple[str, str]:
    if not isinstance(entry, dict):
        raise KpiDeliveryConfigurationError(
            f'KPI delivery configuration entry {store_key}[{index}] must be an object'
        )
    key = _required_text(entry.get('key'), label=f'{store_key}[{index}].key')
    kind = _required_text(entry.get('kind'), label=f'{store_key}[{index}].kind')
    return key, kind


def _required_text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise KpiDeliveryConfigurationError(f'{label} must be a non-empty string')
    if value != value.strip():
        raise KpiDeliveryConfigurationError(f'{label} must not contain surrounding whitespace')
    return value
