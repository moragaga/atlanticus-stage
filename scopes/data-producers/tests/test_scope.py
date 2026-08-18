import pytest

from atlanticus.data_producers.core import SourceScope, SourceScopeItem


def test_scope_preserves_order_and_partition_contract() -> None:
    scope = SourceScope(
        token='a|b',
        items=(
            SourceScopeItem(value='a', partition={'year': '2026', 'window': 'a'}),
            SourceScopeItem(value='b', partition={'year': '2026', 'window': 'b'}),
        ),
    )

    assert scope.values == ('a', 'b')
    assert dict(scope.items[0].partition) == {'year': '2026', 'window': 'a'}


def test_scope_rejects_duplicate_values() -> None:
    with pytest.raises(ValueError, match='unique'):
        SourceScope(
            token='same',
            items=(
                SourceScopeItem(value=1, partition={'part': 'a'}),
                SourceScopeItem(value=1, partition={'part': 'b'}),
            ),
        )
