from __future__ import annotations

import pytest

from ada.processes.blockgrade.settings import BlockgradeSqlRetryPolicy


def test_retry_policy_uses_process_defaults() -> None:
    policy = BlockgradeSqlRetryPolicy.from_mapping({})

    assert policy.attempts == 10
    assert policy.delay_seconds == 5.0


def test_retry_policy_reads_process_configuration() -> None:
    policy = BlockgradeSqlRetryPolicy.from_mapping(
        {
            'BLOCKGRADE_SQL_RETRY_ATTEMPTS': '6',
            'BLOCKGRADE_SQL_RETRY_DELAY_SECONDS': '2.5',
        }
    )

    assert policy == BlockgradeSqlRetryPolicy(attempts=6, delay_seconds=2.5)


@pytest.mark.parametrize(
    ('values', 'message'),
    [
        ({'BLOCKGRADE_SQL_RETRY_ATTEMPTS': '0'}, 'integer greater than zero'),
        ({'BLOCKGRADE_SQL_RETRY_ATTEMPTS': '1.5'}, 'integer greater than zero'),
        ({'BLOCKGRADE_SQL_RETRY_DELAY_SECONDS': '-1'}, 'greater than or equal to zero'),
        ({'BLOCKGRADE_SQL_RETRY_DELAY_SECONDS': 'invalid'}, 'greater than or equal to zero'),
    ],
)
def test_retry_policy_rejects_invalid_values(values, message) -> None:
    with pytest.raises(ValueError, match=message):
        BlockgradeSqlRetryPolicy.from_mapping(values)
