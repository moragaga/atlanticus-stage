import atlanticus.data_producers.core as core


def test_public_api_exposes_scope_contract() -> None:
    assert core.__version__ == '0.1.0'
    assert core.SourceScopeProvider is not None
    assert core.SourceScope is not None
    assert core.SourceScopeItem is not None
