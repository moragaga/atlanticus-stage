import pytest

from ada.processes.kpis_delivery import (
    KpiDeliveryBindingsRepository,
    KpiDeliveryConfigurationError,
)


class FakeCosmosClient:
    def __init__(self, document):
        self.document = document
        self.calls = []

    def find_item(
        self,
        *,
        container_name,
        item_id,
        partition_key,
        include_metadata=False,
    ):
        self.calls.append(
            {
                'container_name': container_name,
                'item_id': item_id,
                'partition_key': partition_key,
                'include_metadata': include_metadata,
            }
        )
        return self.document


def _reader(document):
    return KpiDeliveryBindingsRepository(
        client=FakeCosmosClient(document),
        container_name='application-data',
    )


def test_reader_projects_only_kpi_entries_and_uses_outer_store_key() -> None:
    reader = _reader(
        {
            'id': 'snapshot',
            'partition_id': 'configuration',
            'stores': {
                'chancado': [
                    {'key': 'tonelaje', 'kind': 'kpi'},
                    {'key': 'estado_operacional', 'kind': 'operational'},
                ],
                'time-view': [
                    {'key': 'hora_pi', 'kind': 'kpi'},
                ],
            },
        }
    )

    bindings = reader.read_bindings()

    assert tuple((item.store_key, item.kpi_key) for item in bindings) == (
        ('chancado', 'tonelaje'),
        ('time-view', 'hora_pi'),
    )
    assert reader.client.calls == [
        {
            'container_name': 'application-data',
            'item_id': 'snapshot',
            'partition_key': 'configuration',
            'include_metadata': False,
        }
    ]


def test_reader_accepts_valid_empty_configuration_snapshot() -> None:
    reader = _reader(
        {
            'id': 'snapshot',
            'partition_id': 'configuration',
            'stores': {},
        }
    )

    assert reader.read_bindings() == ()


def test_reader_deduplicates_same_kpi_binding_deterministically() -> None:
    reader = _reader(
        {
            'stores': {
                'chancado': [
                    {'key': 'tonelaje', 'kind': 'kpi'},
                    {'key': 'tonelaje', 'kind': 'kpi'},
                ]
            }
        }
    )

    bindings = reader.read_bindings()

    assert tuple((item.store_key, item.kpi_key) for item in bindings) == (('chancado', 'tonelaje'),)


def test_reader_fails_when_configuration_snapshot_does_not_exist() -> None:
    reader = _reader(None)

    with pytest.raises(KpiDeliveryConfigurationError, match='was not found'):
        reader.read_bindings()


@pytest.mark.parametrize(
    'document',
    [
        {},
        {'stores': None},
        {'stores': []},
        {'stores': {'chancado': {}}},
        {'stores': {'chancado': [None]}},
        {'stores': {'chancado': [{'kind': 'kpi'}]}},
        {'stores': {'chancado': [{'key': 'tonelaje'}]}},
        {'stores': {' chancado ': [{'key': 'tonelaje', 'kind': 'kpi'}]}},
        {'stores': {'chancado': [{'key': ' tonelaje ', 'kind': 'kpi'}]}},
        {'stores': {'chancado': [{'key': 'tonelaje', 'kind': ' kpi '}]}},
    ],
)
def test_reader_rejects_invalid_configuration_snapshot(document) -> None:
    reader = _reader(document)

    with pytest.raises(KpiDeliveryConfigurationError):
        reader.read_bindings()


def test_reader_requires_clean_container_name() -> None:
    client = FakeCosmosClient({'stores': {}})

    with pytest.raises(KpiDeliveryConfigurationError):
        KpiDeliveryBindingsRepository(client=client, container_name='')
    with pytest.raises(KpiDeliveryConfigurationError):
        KpiDeliveryBindingsRepository(client=client, container_name=' application-data ')
