from __future__ import annotations

import math

import pytest

from atlanticus.json import (
    JsonCorruptionError,
    JsonValidationError,
    decode_json_document,
    encode_json_document,
    normalize_json_document,
)


def test_round_trip_is_strict_and_deterministic() -> None:
    document = {
        'z': [1, 2, {'name': 'área'}],
        'a': {'enabled': True, 'value': 42.5, 'missing': None},
    }

    encoded = encode_json_document(document)

    assert encoded == (
        b'{"a":{"enabled":true,"missing":null,"value":42.5},"z":[1,2,{"name":"\xc3\xa1rea"}]}'
    )
    assert decode_json_document(encoded) == document


def test_normalization_copies_mutable_nested_values() -> None:
    nested = {'items': [1, 2]}

    normalized = normalize_json_document(nested)
    nested['items'].append(3)

    assert normalized == {'items': [1, 2]}


@pytest.mark.parametrize('value', [math.nan, math.inf, -math.inf])
def test_non_finite_numbers_are_rejected(value: float) -> None:
    with pytest.raises(JsonValidationError, match='finite'):
        encode_json_document({'value': value})


def test_non_string_keys_are_rejected() -> None:
    with pytest.raises(JsonValidationError, match='non-string'):
        encode_json_document({1: 'invalid'})


def test_cyclic_values_are_rejected() -> None:
    value: list[object] = []
    value.append(value)

    with pytest.raises(JsonValidationError, match='cyclic'):
        encode_json_document({'value': value})


@pytest.mark.parametrize(
    'content',
    [
        b'{"a":1,"a":2}',
        b'{"value":NaN}',
        b'{"value":Infinity}',
        b'[]',
        b'\xff',
    ],
)
def test_decode_rejects_ambiguous_or_invalid_documents(content: bytes) -> None:
    with pytest.raises(JsonCorruptionError):
        decode_json_document(content)
