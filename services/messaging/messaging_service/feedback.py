"""Private, bounded, exact-credential-owned feedback; never external messaging."""
from __future__ import annotations
import hashlib
import json
import re
from psycopg.types.json import Jsonb
from .policy import OPERATION_PERMISSIONS, scope_filter, targets


def _validate_refs(c, p, refs):
    from .store import APIError
    if refs is None:
        refs = {}
    if not isinstance(refs, dict) or set(refs) - {'message_ids', 'chat_ids', 'subscription_ids'}:
        raise APIError('invalid_feedback')
    normalized = {}
    for kind, values in refs.items():
        if (not isinstance(values, list) or len(values) > 32 or
                any(not isinstance(v, str) or not 1 <= len(v) <= 256 or
                    any(ord(ch) < 32 or ord(ch) == 127 for ch in v) for v in values)):
            raise APIError('invalid_feedback')
        normalized[kind] = sorted(set(values))
        if not values:
            continue
        if kind == 'subscription_ids':
            # Refresh the same policy/version/source fences as a normal scoped
            # subscription read; access_changed is otherwise lazily persisted.
            from .subscriptions import _owned
            found = []
            for subscription_id in normalized[kind]:
                row = _owned(c, p, subscription_id)
                if row['state'] == 'access_changed':
                    raise APIError('not_found', 404)
                found.append({'id': str(row['id'])})
        else:
            table, alias, chat_column = ('messages', 'm', 'chat') if kind == 'message_ids' else ('chats', 'c', 'id')
            where, args = scope_filter(p, alias, chat_column)
            found = c.execute('SELECT ' + alias + '.id FROM ' + table + ' ' + alias +
                ' WHERE ' + where + ' AND ' + alias + '.id=ANY(%s)', (*args, values)).fetchall()
        if {row['id'] for row in found} != set(values):
            raise APIError('not_found', 404)
    if len(json.dumps(normalized).encode()) > 8192:
        raise APIError('invalid_feedback')
    return normalized


def submit_feedback(store, c, p, request_id, category, description, *, operation=None, refs=None):
    from .store import APIError
    if 'read' not in p['permissions']:
        raise APIError('forbidden', 403)
    if (not isinstance(request_id, str) or not re.fullmatch(r'[a-zA-Z0-9_.:-]{8,128}', request_id) or
            category not in ('bug', 'feature', 'quality', 'other') or
            not isinstance(description, str) or not 1 <= len(description) <= 4000 or not description.strip() or
            any(ord(ch) < 32 and ch not in '\n\t' or ord(ch) == 127 for ch in description) or
            operation is not None and (not isinstance(operation, str) or operation not in OPERATION_PERMISSIONS)):
        raise APIError('invalid_feedback')
    refs = _validate_refs(c, p, refs)
    value = dict(category=category, description=description, operation=operation, refs=refs)
    request_hash = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('feedback:' + str(p['key_id']),))
    existing = c.execute('SELECT id,request_hash FROM agent_feedback WHERE key_id=%s AND request_id=%s',
        (p['key_id'], request_id)).fetchone()
    if existing:
        if existing['request_hash'] != request_hash:
            raise APIError('idempotency_conflict', 409)
        row = existing
    else:
        if c.execute('SELECT count(*) AS n FROM agent_feedback WHERE key_id=%s',
                (p['key_id'],)).fetchone()['n'] >= 1000:
            raise APIError('feedback_limit', 429)
        row = c.execute('''INSERT INTO agent_feedback(principal,key_id,request_id,request_hash,
            category,description,operation,refs) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id''',
            (p['id'], p['key_id'], request_id, request_hash, category, description, operation, Jsonb(refs))).fetchone()
    targets(p, [str(row['id'])])
    return {'feedback_id': str(row['id']), 'status': 'recorded', 'external_delivery': False}


def list_feedback(store, c, p, *, limit=50):
    from .store import bounded, serial
    bounded(limit)
    rows = c.execute('''SELECT id,category,description,operation,created_at FROM agent_feedback
        WHERE principal=%s AND key_id=%s ORDER BY created_at DESC,id DESC LIMIT %s''',
        (p['id'], p['key_id'], limit + 1)).fetchall()
    # Source references are validated on submission, then omitted from listing so
    # a later policy change cannot leak formerly authorized source identifiers.
    targets(p, [str(row['id']) for row in rows[:limit]])
    return serial({'items': rows[:limit], 'has_more': len(rows) > limit, 'external_delivery': False})
