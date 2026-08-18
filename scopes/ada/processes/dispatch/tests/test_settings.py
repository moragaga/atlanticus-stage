from __future__ import annotations

import pytest

from ada.processes.dispatch.settings import DispatchSqlRetryPolicy


def test_retry_policy_uses_process_defaults() -> None:
    policy = DispatchSqlRetryPolicy.from_mapping({})

    assert policy.attempts == 10
    assert policy.delay_seconds == 5.0


def test_retry_policy_reads_process_configuration() -> None:
    policy = DispatchSqlRetryPolicy.from_mapping(
        {
            'DISPATCH_SQL_RETRY_ATTEMPTS': '6',
            'DISPATCH_SQL_RETRY_DELAY_SECONDS': '2.5',
        }
    )

    assert policy == DispatchSqlRetryPolicy(attempts=6, delay_seconds=2.5)


@pytest.mark.parametrize(
    ('values', 'message'),
    [
        ({'DISPATCH_SQL_RETRY_ATTEMPTS': '0'}, 'integer greater than zero'),
        ({'DISPATCH_SQL_RETRY_ATTEMPTS': '1.5'}, 'integer greater than zero'),
        ({'DISPATCH_SQL_RETRY_DELAY_SECONDS': '-1'}, 'greater than or equal to zero'),
        ({'DISPATCH_SQL_RETRY_DELAY_SECONDS': 'invalid'}, 'greater than or equal to zero'),
    ],
)
def test_retry_policy_rejects_invalid_values(values, message) -> None:
    with pytest.raises(ValueError, match=message):
        DispatchSqlRetryPolicy.from_mapping(values)
