from __future__ import annotations

import pytest
from atlanticus.integrations.pi.web_api import PiPointWebIdResult


def test_resolved_point_result_contains_web_id() -> None:
    result = PiPointWebIdResult(
        tag_name='TAG_A',
        path=r'\\PISERVER01\TAG_A',
        point_name='TAG_A',
        web_id='WEBID-A',
    )

    assert result.resolved is True
    assert result.error is None


def test_unresolved_point_result_contains_error() -> None:
    result = PiPointWebIdResult(
        tag_name='TAG_A',
        path=r'\\PISERVER01\TAG_A',
        point_name=None,
        web_id=None,
        error='Point not found',
    )

    assert result.resolved is False
    assert result.error == 'Point not found'


def test_point_result_requires_exactly_one_resolution_outcome() -> None:
    with pytest.raises(ValueError, match='exactly one'):
        PiPointWebIdResult(
            tag_name='TAG_A',
            path=r'\\PISERVER01\TAG_A',
            point_name=None,
            web_id=None,
        )
