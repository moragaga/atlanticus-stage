from ada.kpis.core import KpiArea, KpiStatus, KpiValueKind
from ada.kpis.evaluation.values import build_result


def test_truncated_value_preserves_numeric_value_and_builds_chilean_presentation() -> None:
    result = build_result(
        key='value',
        area=KpiArea.MINA,
        value_kind=KpiValueKind.VALUE,
        persist_history=True,
        decimals=2,
        is_truncated=True,
        value=1234.567,
    )

    assert result.status is KpiStatus.OK
    assert result.value == 1234.56
    assert result.parsed_value == '1.234,56'


def test_default_decimals_are_two_when_spec_decimals_is_none() -> None:
    result = build_result(
        key='value',
        area=KpiArea.MINA,
        value_kind=KpiValueKind.VALUE,
        persist_history=False,
        decimals=None,
        is_truncated=True,
        value=-2.719,
    )

    assert result.value == -2.71
    assert result.parsed_value == '-2,71'


def test_non_truncated_scalar_is_not_presentation_transformed() -> None:
    result = build_result(
        key='status',
        area=KpiArea.PLANTA,
        value_kind=KpiValueKind.VALUE,
        persist_history=False,
        decimals=None,
        is_truncated=False,
        value='operando',
    )

    assert result.status is KpiStatus.OK
    assert result.value == 'operando'
    assert result.parsed_value == 'operando'


def test_none_is_missing_but_invalid_scalar_is_invalid() -> None:
    missing = build_result(
        key='missing',
        area=KpiArea.GENERAL,
        value_kind=KpiValueKind.VALUE,
        persist_history=False,
        decimals=2,
        is_truncated=True,
        value=None,
    )
    invalid = build_result(
        key='invalid',
        area=KpiArea.GENERAL,
        value_kind=KpiValueKind.VALUE,
        persist_history=False,
        decimals=2,
        is_truncated=True,
        value='not-a-number',
    )

    assert missing.status is KpiStatus.MISSING
    assert invalid.status is KpiStatus.INVALID
    assert missing.value is None
    assert invalid.value is None


def test_nan_and_infinity_are_invalid() -> None:
    for value in (float('nan'), float('inf'), float('-inf')):
        result = build_result(
            key='invalid',
            area=KpiArea.GENERAL,
            value_kind=KpiValueKind.VALUE,
            persist_history=False,
            decimals=2,
            is_truncated=True,
            value=value,
        )
        assert result.status is KpiStatus.INVALID


def test_json_remains_native_and_has_no_parsed_value() -> None:
    result = build_result(
        key='json',
        area=KpiArea.PLANTA,
        value_kind=KpiValueKind.JSON,
        persist_history=True,
        decimals=None,
        is_truncated=False,
        value={'series': [1, 2.5, None, True]},
    )

    assert result.status is KpiStatus.OK
    assert result.value == {'series': [1, 2.5, None, True]}
    assert result.parsed_value is None


def test_json_string_is_not_auto_parsed() -> None:
    result = build_result(
        key='json',
        area=KpiArea.PLANTA,
        value_kind=KpiValueKind.JSON,
        persist_history=False,
        decimals=None,
        is_truncated=False,
        value='{"a": 1}',
    )

    assert result.status is KpiStatus.INVALID
