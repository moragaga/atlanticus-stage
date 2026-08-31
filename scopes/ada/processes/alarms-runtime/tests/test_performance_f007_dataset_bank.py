import importlib.util
import io
import sys
import token
import tokenize
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[1]
_PRODUCTION = _ROOT / 'performance' / 'f007_dataset_bank.py'
_COMMENTED = _ROOT / 'performance' / 'commented' / 'f007_dataset_bank.py'
_SPEC = importlib.util.spec_from_file_location('f007_dataset_bank_under_test', _PRODUCTION)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)

BankConfiguration = _MODULE.BankConfiguration
_alarm_signal_ordinal = _MODULE._alarm_signal_ordinal
_classify_path = _MODULE._classify_path
_day_partition = _MODULE._day_partition
_inclusive_dates = _MODULE._inclusive_dates
_parse_args = _MODULE._parse_args
_seed_for_date = _MODULE._seed_for_date
_seed_for_shift = _MODULE._seed_for_shift
_shift_id = _MODULE._shift_id
_shift_partition = _MODULE._shift_partition
_window_as_of = _MODULE._window_as_of
_WINDOW_COUNT = _MODULE._WINDOW_COUNT
_WINDOW_STRIDE_DAYS = _MODULE._WINDOW_STRIDE_DAYS


def test_bank_defaults_freeze_accepted_temporal_geometry() -> None:
    configuration = BankConfiguration()

    assert configuration.physical_signal_pool_size == 1000
    assert configuration.pi_daily_signal_count == 2
    assert configuration.pi_daily_interval_seconds == 10
    assert configuration.pi_daily_rows == 8640
    assert configuration.dispatch_rows_per_turn == 256
    assert configuration.dispatch_value_columns == 24
    assert configuration.window_count == 61
    assert configuration.window_stride_days == 8
    assert configuration.window_days == 7


def test_bank_rejects_changes_to_frozen_geometry() -> None:
    with pytest.raises(ValueError, match='pi_daily_signal_count must remain 2'):
        BankConfiguration(pi_daily_signal_count=4)
    with pytest.raises(ValueError, match='window_count must remain 61'):
        BankConfiguration(window_count=60)
    with pytest.raises(ValueError, match='window_stride_days must remain 8'):
        BankConfiguration(window_stride_days=7)


def test_window_as_of_is_pinned_to_1600_utc_and_stride_is_eight_days() -> None:
    configuration = BankConfiguration()

    first = _window_as_of(configuration=configuration, index=0)
    second = _window_as_of(configuration=configuration, index=1)
    last = _window_as_of(configuration=configuration, index=60)

    assert first == datetime(2026, 8, 30, 16, 0, tzinfo=UTC)
    assert first - second == timedelta(days=8)
    assert last == datetime(2025, 5, 7, 16, 0, tzinfo=UTC)


def test_window_as_of_rejects_indices_outside_frozen_bank() -> None:
    configuration = BankConfiguration()

    with pytest.raises(ValueError, match='window index is outside the configured bank'):
        _window_as_of(configuration=configuration, index=-1)
    with pytest.raises(ValueError, match='window index is outside the configured bank'):
        _window_as_of(configuration=configuration, index=61)


def test_pi_window_dates_cover_eight_calendar_partitions_without_overlap() -> None:
    configuration = BankConfiguration()
    seen: set[date] = set()

    for index in range(_WINDOW_COUNT):
        as_of = _window_as_of(configuration=configuration, index=index)
        dates = _inclusive_dates(as_of.date() - timedelta(days=7), as_of.date())
        assert len(dates) == 8
        assert not seen.intersection(dates)
        seen.update(dates)

    assert len(seen) == 488


def test_expected_final_bank_file_geometry_is_1343_files() -> None:
    pi_daily_files = _WINDOW_COUNT * 8
    dispatch_shift_files = _WINDOW_COUNT * 14

    assert pi_daily_files == 488
    assert dispatch_shift_files == 854
    assert 1 + pi_daily_files + dispatch_shift_files == 1343


def test_alarm_signal_mapping_reuses_fixed_pool_without_declaring_alarm_ceiling() -> None:
    assert _alarm_signal_ordinal(1) == 1
    assert _alarm_signal_ordinal(1000) == 1000
    assert _alarm_signal_ordinal(1001) == 1
    assert _alarm_signal_ordinal(2000) == 1000
    assert _alarm_signal_ordinal(8001) == 1


def test_alarm_signal_mapping_rejects_invalid_ordinals_and_pool() -> None:
    with pytest.raises(ValueError, match='alarm_ordinal must be greater than zero'):
        _alarm_signal_ordinal(0)
    with pytest.raises(ValueError, match='pool_size must be greater than zero'):
        _alarm_signal_ordinal(1, pool_size=0)


def test_partition_helpers_match_current_pi_and_dispatch_routes() -> None:
    value = date(2026, 8, 30)

    assert _day_partition(value) == {'year': '2026', 'month': '08', 'day': '30'}
    assert _shift_partition(value, '002') == {
        'year': '2026',
        'month': '08',
        'day': '30',
        'turn': '002',
    }
    assert _shift_id(value, '002') == 260830002


def test_date_and_shift_seeds_are_deterministic_and_physically_distinct() -> None:
    first_date = date(2026, 8, 30)
    second_date = date(2026, 8, 22)

    assert _seed_for_date(7007, first_date) == _seed_for_date(7007, first_date)
    assert _seed_for_date(7007, first_date) != _seed_for_date(7007, second_date)
    assert _seed_for_shift(7007, first_date, '001') != _seed_for_shift(7007, first_date, '002')


def test_path_classification_matches_final_bank_sources() -> None:
    assert _classify_path(
        Path('pi/not_pii/interpolated/daily/year=2026/month=08/day=30/data.parquet')
    ) == ('pi.interpolated', 'daily')
    assert _classify_path(Path('pi/not_pii/interpolated/latest/data.parquet')) == (
        'pi.interpolated',
        'latest',
    )
    assert _classify_path(
        Path('dispatch/std_shift_state/shift/year=2026/month=08/day=30/turn=002/data.parquet')
    ) == ('dispatch.std_shift_state', 'shift')


def test_cli_only_exposes_output_directory_and_not_harness_controls(tmp_path: Path) -> None:
    args = _parse_args(['--output-dir', str(tmp_path / 'bank')])

    assert args.output_dir == tmp_path / 'bank'
    assert not hasattr(args, 'alarm_count_ladder')
    assert not hasattr(args, 'phase_b')
    assert not hasattr(args, 'drop_caches')
    assert not hasattr(args, 'window_count')


def test_productive_bank_generator_has_no_comments_and_mirror_only_adds_comments() -> None:
    production_source = _PRODUCTION.read_text(encoding='utf-8')

    assert not any(
        item.type == token.COMMENT
        for item in tokenize.tokenize(io.BytesIO(production_source.encode()).readline)
    )
    assert _python_tokens(_COMMENTED) == _python_tokens(_PRODUCTION)


def _python_tokens(path: Path) -> list[tuple[int, str]]:
    ignored = {
        token.COMMENT,
        token.ENCODING,
        token.ENDMARKER,
        token.INDENT,
        token.DEDENT,
        token.NEWLINE,
        tokenize.NL,
    }
    return [
        (item.type, item.string)
        for item in tokenize.tokenize(io.BytesIO(path.read_bytes()).readline)
        if item.type not in ignored
    ]
