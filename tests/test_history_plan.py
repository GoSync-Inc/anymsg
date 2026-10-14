"""Setup policy preview: fixed calendar bounds, no provider access or fake coverage."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest


ANCHOR = datetime(2026, 10, 14, 0, 0, tzinfo=timezone.utc)


def compile(policy, anchor=ANCHOR):
    from messaging_service.history_plan import compile_plan
    return compile_plan(policy, anchor=anchor)


def test_new_only_produces_no_history_work():
    plan = compile({'mode': 'new_only'})
    assert plan['activation_at'] == ANCHOR.isoformat()
    assert plan['target_since'] == ANCHOR.isoformat()
    assert plan['waves'] == []
    assert plan['full_history_verified'] is False


def test_year_has_recent_first_disjoint_bands():
    plan = compile({'mode': 'window', 'months': 12})
    assert plan['target_since'] == '2025-10-14T00:00:00+00:00'
    waves = plan['waves']
    assert [w['goal_since'] for w in waves] == [
        '2026-10-07T00:00:00+00:00', '2026-09-14T00:00:00+00:00',
        '2026-07-14T00:00:00+00:00', '2025-10-14T00:00:00+00:00']
    assert waves[0]['to_time'] == ANCHOR.isoformat()
    assert all(w['from_time'] == w['goal_since'] for w in waves)
    assert all(waves[i]['to_time'] == waves[i-1]['from_time'] for i in range(1, 4))
    assert all(w['coverage'] == 'not_started' for w in waves)


@pytest.mark.parametrize('months,count', [(1, 2), (3, 3), (6, 4), (12, 4), (18, 5), (24, 5), (60, 6), (120, 7)])
def test_window_stops_at_chosen_depth(months, count):
    plan = compile({'mode': 'window', 'months': months})
    assert len(plan['waves']) == count
    assert plan['waves'][-1]['from_time'] == plan['target_since']
    assert all(datetime.fromisoformat(w['from_time']) < datetime.fromisoformat(w['to_time'])
               for w in plan['waves'])


@pytest.mark.parametrize('anchor,months,floor', [
    ('2024-03-31T12:34:56Z', 1, '2024-02-29T12:34:56+00:00'),
    ('2025-03-31T12:34:56Z', 1, '2025-02-28T12:34:56+00:00'),
    ('2024-02-29T00:00:00Z', 12, '2023-02-28T00:00:00+00:00'),
    ('2026-01-31T00:00:00Z', 3, '2025-10-31T00:00:00+00:00'),
])
def test_months_are_calendar_months(anchor, months, floor):
    plan = compile({'mode': 'window', 'months': months}, anchor=datetime.fromisoformat(anchor))
    assert plan['target_since'] == floor


def test_same_saved_anchor_is_identical_after_restart():
    policy = {'mode': 'window', 'months': 3}
    first = compile(policy)
    restored = datetime.fromisoformat(json.loads(json.dumps(first))['activation_at'])
    assert compile(policy, restored) == first
    assert policy == {'mode': 'window', 'months': 3}


def test_timezone_offsets_are_normalized_before_bounds():
    anchor = datetime.fromisoformat('2026-10-14T08:00:00+08:00')
    assert compile({'mode': 'window', 'months': 12}, anchor) == compile({'mode': 'window', 'months': 12})


@pytest.mark.parametrize('days,expected', [(2, 1), (7, 1), (10, 2), (30, 2), (50, 3), (150, 4), (500, 5)])
def test_explicit_date_inserts_exact_final_band(days, expected):
    since = (ANCHOR-timedelta(days=days)).isoformat()
    plan = compile({'mode': 'since', 'since': since})
    assert len(plan['waves']) == expected
    assert plan['waves'][-1]['from_time'] == since
    assert plan['target_since'] == since


def test_all_available_has_open_final_band_without_full_history_claim():
    plan = compile({'mode': 'all_available'})
    assert plan['target_since'] is None
    assert plan['waves'][-1]['from_time'] is None
    assert plan['waves'][-1]['goal_since'] is None
    assert len(plan['waves']) == 8
    assert plan['full_history_verified'] is False
    assert plan['completion_basis'] == 'provider_observations_required'


def test_expanding_preserves_shared_bands():
    quarter = compile({'mode': 'window', 'months': 3})
    year = compile({'mode': 'window', 'months': 12})
    assert quarter['waves'] == year['waves'][:3]


def test_no_fetch_interval_overlaps_or_omits_the_requested_range():
    for months in (1, 3, 6, 12, 20, 60, 100):
        plan = compile({'mode': 'window', 'months': months})
        bands = [(datetime.fromisoformat(w['from_time']), datetime.fromisoformat(w['to_time']))
                 for w in plan['waves']]
        assert bands[0][1] == ANCHOR
        assert bands[-1][0] == datetime.fromisoformat(plan['target_since'])
        for i in range(len(bands)-1):
            assert bands[i][0] == bands[i+1][1]


@pytest.mark.parametrize('policy', [
    None, [], 'all', {}, {'mode': 'forever'}, {'mode': True},
    {'mode': 'window'}, {'mode': 'window', 'months': True},
    {'mode': 'window', 'months': 0}, {'mode': 'window', 'months': -1},
    {'mode': 'window', 'months': 1201}, {'mode': 'window', 'months': '12'},
    {'mode': 'window', 'months': 1.5}, {'mode': 'window', 'months': 12, 'rate': 100},
    {'mode': 'new_only', 'months': 3}, {'mode': 'all_available', 'since': '2020-01-01'},
    {'mode': 'since'}, {'mode': 'since', 'since': None},
    {'mode': 'since', 'since': '2026-01-01'},
    {'mode': 'since', 'since': '2026-01-01T00:00:00'},
    {'mode': 'since', 'since': '2027-01-01T00:00:00Z'},
    {'mode': 'since', 'since': ANCHOR.isoformat()},
])
def test_invalid_policies_fail_before_any_collection(policy):
    with pytest.raises(ValueError, match='invalid_history_policy'):
        compile(policy)


@pytest.mark.parametrize('anchor', [None, '2026-10-14T00:00:00Z', datetime(2026, 10, 14)])
def test_anchor_must_be_explicit_aware_datetime(anchor):
    with pytest.raises(ValueError, match='invalid_history_anchor'):
        compile({'mode': 'window', 'months': 3}, anchor)


def test_underflow_is_a_safe_validation_error():
    with pytest.raises(ValueError, match='invalid_history_policy'):
        compile({'mode': 'window', 'months': 1200}, datetime(50, 1, 1, tzinfo=timezone.utc))


def test_recent_policy_works_even_if_larger_standard_waves_would_underflow():
    plan = compile({'mode': 'window', 'months': 1}, datetime(10, 4, 1, tzinfo=timezone.utc))
    assert len(plan['waves']) == 2
    assert plan['target_since'] == '0010-03-01T00:00:00+00:00'


def cli(*args):
    return subprocess.run([sys.executable, '-m', 'messaging_service.history_plan', *args],
                          cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)


def test_cli_is_preview_only_and_needs_no_secrets_or_database():
    result = cli('--months', '12', '--anchor', '2026-10-14T00:00:00Z')
    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan['preview_only'] is True
    assert len(plan['waves']) == 4
    assert plan['full_history_verified'] is False


@pytest.mark.parametrize('args', [
    ('--new-only',),
    ('--months', '0', '--anchor', '2026-10-14T00:00:00Z'),
    ('--new-only', '--months', '3', '--anchor', '2026-10-14T00:00:00Z'),
    ('--since', 'not-a-date', '--anchor', '2026-10-14T00:00:00Z'),
])
def test_cli_rejects_invalid_or_implicit_choices(args):
    result = cli(*args)
    assert result.returncode == 2
    assert result.stdout == ''
