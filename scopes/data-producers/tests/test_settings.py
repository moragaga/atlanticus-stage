import pytest

from atlanticus.data_producers.sql import SqlRetryPolicy


def test_retry_policy_uses_common_defaults() -> None:
    policy = SqlRetryPolicy.from_mapping({}, prefix='SOURCE')

    assert policy.attempts == 10
    assert policy.delay_seconds == 5.0


def test_retry_policy_uses_process_prefix() -> None:
    policy = SqlRetryPolicy.from_mapping(
        {
            'BLOCKGRADE_SQL_RETRY_ATTEMPTS': '4',
            'BLOCKGRADE_SQL_RETRY_DELAY_SECONDS': '1.5',
        },
        prefix='blockgrade',
    )

    assert policy == SqlRetryPolicy(attempts=4, delay_seconds=1.5)


@pytest.mark.parametrize(
    'values',
    (
        {'SOURCE_SQL_RETRY_ATTEMPTS': '0'},
        {'SOURCE_SQL_RETRY_DELAY_SECONDS': '-1'},
    ),
)
def test_retry_policy_rejects_invalid_values(values) -> None:
    with pytest.raises(ValueError):
        SqlRetryPolicy.from_mapping(values, prefix='SOURCE')
