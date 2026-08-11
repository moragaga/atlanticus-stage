from __future__ import annotations

from datetime import UTC, datetime

import pytest

from atlanticus.state import (
    StateDocument,
    StateKey,
    StateValidationError,
    build_state_signature,
)


def test_state_key_resolves_inside_an_extensible_namespace() -> None:
    key = StateKey(namespace=('ingestion', 'pi', 'state'), name='publication')

    assert key.identifier == 'ingestion/pi/state/publication'
    assert key.relative_path.as_posix() == 'ingestion/pi/state/publication.json'


@pytest.mark.parametrize(
    ('namespace', 'name'),
    [
        ((), 'publication'),
        ('ingestion', 'publication'),
        (('ingestion', '..'), 'publication'),
        (('ingestion/pi',), 'publication'),
        (('ingestion',), 'publication.json'),
    ],
)
def test_state_key_rejects_ambiguous_or_unsafe_paths(namespace: tuple[str, ...], name: str) -> None:
    with pytest.raises(StateValidationError):
        StateKey(namespace=namespace, name=name)


def test_document_contract_contains_only_envelope_and_value() -> None:
    key = StateKey(namespace=('kpis',), name='runtime')
    document = StateDocument(
        key=key,
        updated_at_utc=datetime(2026, 7, 20, 12, 30, tzinfo=UTC),
        value={'status': 'warning', 'failed_count': 3},
    )

    assert document.to_payload() == {
        'schema_version': 1,
        'updated_at_utc': '2026-07-20T12:30:00.000000Z',
        'value': {'status': 'warning', 'failed_count': 3},
    }


def test_document_is_a_deeply_immutable_snapshot() -> None:
    source = {'nested': {'items': [1, 2]}}
    document = StateDocument(
        key=StateKey(namespace=('kpis',), name='runtime'),
        updated_at_utc=datetime(2026, 7, 20, 12, 30, tzinfo=UTC),
        value=source,
    )

    source['nested']['items'].append(3)

    assert document.to_payload()['value'] == {'nested': {'items': [1, 2]}}
    with pytest.raises(TypeError):
        document.value['nested'] = {}
    with pytest.raises(TypeError):
        document.value['nested']['items'][0] = 9


def test_document_requires_a_state_key() -> None:
    with pytest.raises(StateValidationError, match='StateKey'):
        StateDocument(
            key='kpis/runtime',
            updated_at_utc=datetime(2026, 7, 20, 12, 30, tzinfo=UTC),
            value={},
        )


def test_signature_is_independent_from_mapping_order() -> None:
    first = build_state_signature({'watermark': '2026-07-20T12:00:00Z', 'rows': 10})
    second = build_state_signature({'rows': 10, 'watermark': '2026-07-20T12:00:00Z'})
    changed = build_state_signature({'rows': 11, 'watermark': '2026-07-20T12:00:00Z'})

    assert first == second
    assert changed != first


def test_json_contract_rejects_lossy_or_non_finite_values() -> None:
    with pytest.raises(StateValidationError):
        build_state_signature({'invalid': {1: 'coerced'}})
    with pytest.raises(StateValidationError):
        build_state_signature({'invalid': float('nan')})
    with pytest.raises(StateValidationError):
        build_state_signature({'invalid': '\ud800'})
