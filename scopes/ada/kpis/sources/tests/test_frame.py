import pandas as pd
import pytest

from ada.kpis.core import KpiColumnNotRequestedError
from ada.kpis.sources import PandasRuntimeFrameContext


def test_runtime_frame_context_exposes_exact_frame_and_latest_helpers() -> None:
    frame = pd.DataFrame(
        {
            'a': [1.0, 2.5],
            'b': ['x', '3.5'],
        }
    )
    context = PandasRuntimeFrameContext(frame, ('a', 'b'))

    assert list(context.dataframe.columns) == ['a', 'b']
    assert context.last_row().to_dict() == {'a': 2.5, 'b': '3.5'}
    assert context.last_value('a') == 2.5
    assert context.last_value('b') == '3.5'
    assert context.last_value_number('b') == 3.5


def test_runtime_frame_context_returns_defaults_for_empty_or_non_numeric_values() -> None:
    empty = PandasRuntimeFrameContext(pd.DataFrame(columns=['a']), ('a',))
    assert empty.last_row().empty
    assert empty.last_value('a') is None
    assert empty.last_value_number('a') is None

    text = PandasRuntimeFrameContext(pd.DataFrame({'a': ['BAD']}), ('a',))
    assert text.last_value_number('a') is None


def test_runtime_frame_context_rejects_helper_access_to_undeclared_column() -> None:
    context = PandasRuntimeFrameContext(pd.DataFrame({'a': [1]}), ('a',))

    with pytest.raises(KpiColumnNotRequestedError):
        context.last_value('b')
    with pytest.raises(KpiColumnNotRequestedError):
        context.last_value_number('b')
