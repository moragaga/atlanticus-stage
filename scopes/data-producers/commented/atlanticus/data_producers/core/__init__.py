# Expone los contratos mínimos de scope reutilizables por cualquier productor.
from atlanticus.data_producers.core.contracts import SourceScopeProvider
from atlanticus.data_producers.core.models import ScopeValue, SourceScope, SourceScopeItem

__all__ = [
    'ScopeValue',
    'SourceScope',
    'SourceScopeItem',
    'SourceScopeProvider',
]
