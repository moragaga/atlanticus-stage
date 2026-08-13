import pytest

from atlanticus.connectivity.redis import RedisTtl


def test_ttl_represents_missing_persistent_and_expiring_keys() -> None:
    missing = RedisTtl(exists=False, seconds=None)
    persistent = RedisTtl(exists=True, seconds=None)
    expiring = RedisTtl(exists=True, seconds=10)
    assert missing.has_expiry is False
    assert persistent.has_expiry is False
    assert expiring.has_expiry is True


@pytest.mark.parametrize('seconds', [-1, True, 1.5])
def test_ttl_rejects_invalid_seconds(seconds: object) -> None:
    with pytest.raises(ValueError):
        RedisTtl(exists=True, seconds=seconds)  # type: ignore[arg-type]


def test_ttl_rejects_seconds_for_missing_key() -> None:
    with pytest.raises(ValueError, match='exists=True'):
        RedisTtl(exists=False, seconds=1)


def test_ttl_requires_boolean_exists() -> None:
    with pytest.raises(ValueError, match='boolean'):
        RedisTtl(exists=1, seconds=None)  # type: ignore[arg-type]
