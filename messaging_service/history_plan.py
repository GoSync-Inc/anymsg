"""History setup preview. Pure date planning; never contacts a provider or opens a session.

The durable wave runner is a separate delivery step. Do not pass this preview
as a collector configuration or infer coverage from a compiled request.
"""
from calendar import monthrange
from datetime import datetime, timedelta, timezone


def _anchor(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError('invalid_history_anchor')
    try:
        if value.utcoffset() is None:
            raise ValueError
        return value.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise ValueError('invalid_history_anchor') from None


def _months_before(anchor, count):
    year, month = divmod(anchor.year*12+anchor.month-1-count, 12)
    month += 1
    if year < 1:
        raise ValueError('invalid_history_policy')
    return anchor.replace(year=year, month=month,
                          day=min(anchor.day, monthrange(year, month)[1]))


def _policy(value, anchor):
    if not isinstance(value, dict) or not isinstance(value.get('mode'), str):
        raise ValueError('invalid_history_policy')
    mode = value['mode']
    fields = {'new_only': {'mode'}, 'all_available': {'mode'},
              'window': {'mode', 'months'}, 'since': {'mode', 'since'}}
    if mode not in fields or set(value) != fields[mode]:
        raise ValueError('invalid_history_policy')
    if mode == 'window':
        count = value['months']
        if type(count) is not int or not 1 <= count <= 1200:
            raise ValueError('invalid_history_policy')
        return dict(value), _months_before(anchor, count)
    if mode == 'since':
        since = value['since']
        if not isinstance(since, str) or len(since) > 64:
            raise ValueError('invalid_history_policy')
        try:
            floor = _anchor(datetime.fromisoformat(since))
        except (ValueError, OverflowError):
            raise ValueError('invalid_history_policy') from None
        if floor >= anchor:
            raise ValueError('invalid_history_policy')
        return {'mode': mode, 'since': floor.isoformat()}, floor
    return dict(value), anchor if mode == 'new_only' else None


def compile_plan(policy, *, anchor):
    """Compile fixed UTC calendar goals into recent-first, disjoint [from,to) bands.

    The caller must save and restore the explicit anchor, not substitute now()
    on a restart. An open lower bound means all provider-accessible history,
    never verified full history. No archive progress is inferred here.
    """
    anchor = _anchor(anchor)
    policy, target = _policy(policy, anchor)
    waves = []
    upper = anchor
    if policy['mode'] != 'new_only':
        # Keep first stages stable when the target is extended. Calendar depths
        # beyond a year are spaced out; custom targets supply the final stage.
        for months in (0, 1, 3, 12, 24, 60, 120, None):
            try:
                floor = (anchor-timedelta(days=7) if months == 0 else
                         _months_before(anchor, months) if months is not None else None)
            except (ValueError, OverflowError):
                floor = None
            if target is not None and (floor is None or floor <= target):
                floor = target
            if floor is not None and floor >= upper:
                continue
            waves.append({'index': len(waves)+1,
                          'goal_since': floor.isoformat() if floor is not None else None,
                          'from_time': floor.isoformat() if floor is not None else None,
                          'to_time': upper.isoformat(), 'coverage': 'not_started'})
            if floor is None or floor == target:
                break
            upper = floor
    return {'schema': 1, 'preview_only': True, 'policy': policy,
            'activation_at': anchor.isoformat(),
            'target_since': target.isoformat() if target is not None else None,
            'range_semantics': 'inclusive_from_exclusive_to', 'waves': waves,
            'full_history_verified': False,
            'completion_basis': 'provider_observations_required'}


def main():
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--anchor', required=True,
                        help='Fixed activation instant with timezone; save it for resumptions.')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--new-only', action='store_true')
    mode.add_argument('--months', type=int)
    mode.add_argument('--since')
    mode.add_argument('--all-available', action='store_true')
    args = parser.parse_args()
    if args.new_only:
        policy = {'mode': 'new_only'}
    elif args.months is not None:
        policy = {'mode': 'window', 'months': args.months}
    elif args.since is not None:
        policy = {'mode': 'since', 'since': args.since}
    else:
        policy = {'mode': 'all_available'}
    try:
        anchor = datetime.fromisoformat(args.anchor)
    except (ValueError, OverflowError):
        parser.error('invalid_history_anchor')
    try:
        plan = compile_plan(policy, anchor=anchor)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(plan, indent=2))


if __name__ == '__main__':
    main()
