import importlib.util
import io
import sys
import token
import tokenize
from datetime import date
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[1]
_PRODUCTION = _ROOT / 'performance' / 'f007_dataset_pilot.py'
_COMMENTED = _ROOT / 'performance' / 'commented' / 'f007_dataset_pilot.py'
_SPEC = importlib.util.spec_from_file_location('f007_dataset_pilot_under_test', _PRODUCTION)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)

PilotConfiguration = _MODULE.PilotConfiguration
_aggregate_fingerprint = _MODULE._aggregate_fingerprint
_classify_path = _MODULE._classify_path
_day_partition = _MODULE._day_partition
_deterministic_float = _MODULE._deterministic_float
_nearest_calibration = _MODULE._nearest_calibration
_signal_calibration_candidates = _MODULE._signal_calibration_candidates
_PI_DAILY_INTERVAL_SECONDS = _MODULE._PI_DAILY_INTERVAL_SECONDS
_PI_DAILY_ROWS = _MODULE._PI_DAILY_ROWS
_DISPATCH_ROWS_PER_TURN = _MODULE._DISPATCH_ROWS_PER_TURN
_parse_args = _MODULE._parse_args
_shift_id = _MODULE._shift_id
_shift_partition = _MODULE._shift_partition


def test_pilot_defaults_preserve_known_baseline_without_declaring_capacity_ceiling() -> None:
    configuration = PilotConfiguration()

    assert configuration.signal_count == 1000
    assert configuration.pi_daily_target_bytes == 200 * 1024
    assert configuration.dispatch_day_target_bytes == 150 * 1024
    assert configuration.dispatch_value_columns == 24
    assert configuration.seed == 7007


def test_temporal_geometry_freezes_full_day_at_ten_second_interpolation() -> None:
    assert _PI_DAILY_INTERVAL_SECONDS == 10
    assert _PI_DAILY_ROWS == 8640
    assert _DISPATCH_ROWS_PER_TURN == 256


def test_signal_calibration_candidates_cover_powers_of_two_and_exact_latest_pool() -> None:
    assert _signal_calibration_candidates(1000) == (
        1,
        2,
        4,
        8,
        16,
        32,
        64,
        128,
        256,
        512,
        1000,
    )
    assert _signal_calibration_candidates(1024)[-1] == 1024


@pytest.mark.parametrize('signal_count', [0, 999])
def test_pilot_rejects_signal_count_below_phase_a_baseline(signal_count: int) -> None:
    with pytest.raises(ValueError, match='signal_count must be at least 1000'):
        PilotConfiguration(signal_count=signal_count)


def test_partition_helpers_match_current_dataset_route_dimensions() -> None:
    value = date(2026, 8, 30)

    assert _day_partition(value) == {'year': '2026', 'month': '08', 'day': '30'}
    assert _shift_partition(value, '002') == {
        'year': '2026',
        'month': '08',
        'day': '30',
        'turn': '002',
    }
    assert _shift_id(value, '002') == 260830002


def test_aggregate_fingerprint_is_order_independent_and_path_sensitive() -> None:
    first = _aggregate_fingerprint((('b/data.parquet', 'bbb'), ('a/data.parquet', 'aaa')))
    second = _aggregate_fingerprint((('a/data.parquet', 'aaa'), ('b/data.parquet', 'bbb')))
    changed_path = _aggregate_fingerprint((('a/data.parquet', 'aaa'), ('c/data.parquet', 'bbb')))

    assert first == second
    assert first != changed_path


def test_calibration_selects_nearest_measured_candidate() -> None:
    assert _nearest_calibration(target_bytes=100, left=(8, 90), right=(16, 140)) == (8, 90)
    assert _nearest_calibration(target_bytes=100, left=(8, 50), right=(16, 120)) == (16, 120)
    assert _nearest_calibration(target_bytes=100, left=None, right=(1, 130)) == (1, 130)


def test_synthetic_values_are_deterministic_and_change_across_rows_and_columns() -> None:
    value = _deterministic_float(seed=7007, row_index=10, column_index=20)

    assert value == _deterministic_float(seed=7007, row_index=10, column_index=20)
    assert value != _deterministic_float(seed=7007, row_index=11, column_index=20)
    assert value != _deterministic_float(seed=7007, row_index=10, column_index=21)


def test_path_classification_matches_pi_and_selected_dispatch_source() -> None:
    assert _classify_path(
        Path('pi/not_pii/interpolated/daily/year=2026/month=08/day=30/data.parquet')
    ) == ('pi.interpolated', 'daily')
    assert _classify_path(
        Path('dispatch/std_shift_state/shift/year=2026/month=08/day=30/turn=001/data.parquet')
    ) == ('dispatch.std_shift_state', 'shift')


def test_cli_exposes_calibration_inputs_without_phase_b_controls(tmp_path: Path) -> None:
    args = _parse_args(['--output-dir', str(tmp_path / 'pilot')])

    assert args.signal_count == 1000
    assert args.pi_daily_target_bytes == 200 * 1024
    assert args.dispatch_day_target_bytes == 150 * 1024
    assert not hasattr(args, 'pi_daily_rows')
    assert not hasattr(args, 'pi_daily_interval_seconds')
    assert not hasattr(args, 'alarm_count_ladder')
    assert not hasattr(args, 'phase_b')


def test_productive_pilot_has_no_comments_and_commented_mirror_only_adds_comments() -> None:
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
