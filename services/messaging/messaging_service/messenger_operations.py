"""Reviewed operation catalog, never an unrestricted provider RPC proxy.

Archive reads retain their existing passive contract. These operations run on the
existing session owner with an independent key grant and account opt-in.
"""
from copy import deepcopy
import math
import re

MAX_REGISTERED_FILE_BYTES = 32 * 1024**2
CATALOG_VERSION = 1


def _field(kind, *, required=False, default=None, **limits):
    return {'type': kind, 'required': required, 'default': default, **limits}


def _spec(permission, target='chat', *, mutation=False, full_account=False,
          platforms=('telegram', 'whatsapp'), fields=None, limitation=''):
    return dict(permission=permission, target=target, mutation=mutation,
        full_account=full_account, platforms=list(platforms), fields=fields or {},
        limitation=limitation)


_page = {'limit': _field('integer', default=50, minimum=1, maximum=100),
         'offset': _field('integer', default=0, minimum=0, maximum=100000)}
_user = {'user_id': _field('contact_id', required=True)}
_title = {'title': _field('string', required=True, maximum=128, nonempty=True)}
_duration = {'seconds': _field('integer', default=86400, minimum=1, maximum=2147483647)}
OPERATIONS = {
    'chat_info': _spec('read-live'),
    'list_participants': _spec('groups-read', fields=_page),
    'get_reactions': _spec('read-live', 'message'),
    'get_message_info': _spec('read-live', 'message'),
    'get_pinned_messages': _spec('read-live', fields=_page, platforms=('whatsapp',)),
    'list_topics': _spec('groups-read', fields=_page, platforms=('telegram',)),
    'get_profile': _spec('profile-read', 'account', full_account=True),
    'list_contacts': _spec('contacts-read', 'account', full_account=True, fields=_page),
    'get_contact': _spec('contacts-read', 'account', full_account=True, fields=_user),
    'list_blocked': _spec('contacts-read', 'account', full_account=True, fields=_page),
    'send_file': _spec('send', mutation=True, fields={
        'attachment_id': _field('attachment_id', required=True),
        'sha256': _field('sha256', required=True),
        'mode': _field('file_mode', default='document'),
        'caption': _field('string', default='', maximum=1024)},
        limitation='Registered accessible SHA-256 verified original only; 32 MiB upload bound.'),
    'forward_message': _spec('send', mutation=True, fields={
        'source_message_id': _field('message_id', required=True),
        'source_revision': _field('integer', required=True, minimum=1, maximum=2147483647)},
        limitation='Both source and destination must be authorized in the same account; provider restrictions apply.'),
    'send_poll': _spec('send', mutation=True, fields={
        'question': _field('string', required=True, maximum=255, nonempty=True),
        'options': _field('poll_options', required=True),
        'multiple': _field('boolean', default=False)}),
    'send_contact': _spec('send', mutation=True, full_account=True),  # Contains contact data.
    'send_location': _spec('send', mutation=True, fields={
        'latitude': _field('number', required=True, minimum=-90, maximum=90),
        'longitude': _field('number', required=True, minimum=-180, maximum=180),
        'description': _field('string', default='', maximum=255)}),
    'edit_message': _spec('message-edit', 'message', mutation=True, fields={
        'text': _field('string', required=True, maximum=4000, nonempty=True)},
        limitation='Own outgoing message at the exact archive revision; plain text only.'),
    'delete_message': _spec('message-delete', 'message', mutation=True,
        limitation='Own outgoing message only; global revoke must not fall back to local deletion.'),
    'send_reaction': _spec('message-react', 'message', mutation=True, fields={
        'emoji': _field('string', required=True, maximum=64)},
        limitation='Empty emoji removes the reaction; no paid or custom-emoji transactions.'),
    'vote_poll': _spec('message-react', 'message', mutation=True, platforms=('telegram',),
        fields={'option_indices': _field('poll_indices', required=True)}),
    'pin_message': _spec('message-pin', 'message', mutation=True, fields=_duration),
    'unpin_message': _spec('message-pin', 'message', mutation=True),
    'mark_read': _spec('read-state', 'message', mutation=True,
        limitation='Telegram read boundary is the target message; WhatsApp marks the whole chat read.'),
    'mark_unread': _spec('read-state', mutation=True),
    'archive_chat': _spec('chat-manage', mutation=True),
    'unarchive_chat': _spec('chat-manage', mutation=True),
    'mute_chat': _spec('chat-manage', mutation=True, fields=_duration),
    'unmute_chat': _spec('chat-manage', mutation=True),
    'pin_chat': _spec('chat-manage', mutation=True, platforms=('whatsapp',)),
    'unpin_chat': _spec('chat-manage', mutation=True, platforms=('whatsapp',)),
    'star_message': _spec('chat-manage', 'message', mutation=True, platforms=('whatsapp',)),
    'unstar_message': _spec('chat-manage', 'message', mutation=True, platforms=('whatsapp',)),
    'create_group': _spec('groups-manage', 'account', mutation=True, full_account=True,
        fields={**_title, 'user_ids': _field('contact_ids', required=True)}),
    'rename_group': _spec('groups-manage', mutation=True, fields=_title),
    'set_group_description': _spec('groups-manage', mutation=True,
        fields={'description': _field('string', required=True, maximum=255)}),
    'add_participant': _spec('groups-manage', mutation=True, fields=_user),
    'remove_participant': _spec('groups-manage', mutation=True, fields=_user),
    'promote_participant': _spec('groups-manage', mutation=True, fields=_user,
        limitation='Conservative administrator rights only; Telegram basic-group promotion is unsupported.'),
    'demote_participant': _spec('groups-manage', mutation=True, fields=_user),
    'leave_group': _spec('groups-manage', mutation=True),
    'get_invite_link': _spec('groups-manage', mutation=True,
        limitation='Sensitive invitation link; WhatsApp may create/export a link. Telegram reads existing invite only.'),
    'revoke_invite_link': _spec('groups-manage', mutation=True),
    'add_contact': _spec('contacts-manage', 'account', mutation=True, full_account=True,
        fields={
            'phone': _field('phone', required=True),
            'first_name': _field('string', required=True, maximum=64, nonempty=True),
            'last_name': _field('string', default='', maximum=64)}),
    'delete_contact': _spec('contacts-manage', 'account', mutation=True, full_account=True,
        fields=_user, limitation='WhatsApp address-book deletion requires a phone-number ID, not a LID.'),
    'block_contact': _spec('contacts-manage', 'account', mutation=True, full_account=True, fields=_user),
    'unblock_contact': _spec('contacts-manage', 'account', mutation=True, full_account=True, fields=_user),
    'update_profile': _spec('profile-manage', 'account', mutation=True, full_account=True),
    'get_labels': _spec('contacts-read', 'account', full_account=True,
        platforms=('whatsapp',), limitation='Business feature; availability depends on the account.'),
    'set_chat_labels': _spec('chat-manage', mutation=True, platforms=('whatsapp',),
        fields={'label_ids': _field('label_ids', required=True)},
        limitation='Business feature; availability depends on the account.'),
}


def operation_spec(name):
    if not isinstance(name, str) or name not in OPERATIONS:
        raise ValueError('unsupported_operation')
    return deepcopy(OPERATIONS[name])


def _supported(platform, name, spec):
    return platform in spec['platforms'] and not (platform == 'whatsapp' and name == 'delete_message')


def _fields(platform, name, spec):
    if platform == 'telegram' and name == 'pin_message':
        return {}  # Telegram pins have no expiry parameter.
    if name == 'send_contact':
        return ({'contact_ids': _field('contact_ids', required=True)} if platform == 'whatsapp' else
            {'phone': _field('phone', required=True),
             'first_name': _field('string', required=True, maximum=64, nonempty=True),
             'last_name': _field('string', default='', maximum=64)})
    if name == 'update_profile':
        names = ('name', 'status') if platform == 'whatsapp' else ('first_name', 'last_name', 'bio')
        return {n: _field('string', maximum=64 if n in ('name', 'first_name', 'last_name') else 139,
                         nonempty=n in ('name', 'first_name')) for n in names}
    return spec['fields']


def operation_manifest(platform):
    if platform not in ('telegram', 'whatsapp'):
        raise ValueError('unsupported_platform')
    rows = []
    for name, spec in sorted(OPERATIONS.items()):
        row = {k: deepcopy(v) for k, v in spec.items() if k not in ('fields', 'platforms')}
        row.update(operation=name, supported=_supported(platform, name, spec),
                   parameters_schema=_fields(platform, name, spec))
        if platform == 'whatsapp' and name == 'delete_message':
            row['limitation'] = 'Unsupported: upstream global revoke can silently fallback to local deletion.'
        elif platform not in spec['platforms']:
            row['limitation'] = 'No implemented adapter for this provider-specific operation.'
        rows.append(row)
    return rows


def _valid(platform, descriptor, value):
    kind = descriptor['type']
    if kind == 'string':
        return (isinstance(value, str) and len(value.encode('utf-16-le')) // 2 <= descriptor['maximum']
            and not any(ord(c) < 32 and c not in '\n\r\t' or ord(c) == 127 for c in value)
            and (not descriptor.get('nonempty') or bool(value.strip())))
    if kind == 'integer':
        return type(value) is int and descriptor['minimum'] <= value <= descriptor['maximum']
    if kind == 'boolean':
        return type(value) is bool
    if kind == 'number':
        return type(value) in (int, float) and math.isfinite(value) and descriptor['minimum'] <= value <= descriptor['maximum']
    if kind == 'contact_id':
        if not isinstance(value, str): return False
        if platform == 'telegram':
            return bool(re.fullmatch(r'[1-9][0-9]{0,18}', value)) and int(value) < 2**63
        return bool(re.fullmatch(r'[0-9]{1,30}@(c\.us|lid)', value))
    if kind in ('attachment_id', 'message_id', 'sha256'):
        prefix = {'attachment_id': 'att_', 'message_id': 'msg_', 'sha256': ''}[kind]
        size = 64 if kind == 'sha256' else 32
        return isinstance(value, str) and bool(re.fullmatch(prefix + r'[a-f0-9]{' + str(size) + '}', value))
    if kind == 'phone':
        return isinstance(value, str) and bool(re.fullmatch(r'\+?[1-9][0-9]{4,19}', value))
    if kind == 'file_mode':
        modes = {'document', 'photo', 'video', 'audio', 'voice'} | ({'video_note'} if platform == 'telegram' else {'sticker'})
        return isinstance(value, str) and value in modes
    if kind in ('contact_ids', 'poll_options', 'poll_indices', 'label_ids'):
        if not isinstance(value, list) or len(value) > 20: return False
        if kind not in ('poll_indices', 'label_ids') and not value: return False
        if kind == 'poll_options':
            if not 2 <= len(value) <= 10: return False
            valid = all(_valid(platform, _field('string', maximum=100, nonempty=True), x) for x in value)
        elif kind == 'contact_ids':
            valid = all(_valid(platform, _field('contact_id'), x) for x in value)
        elif kind == 'poll_indices':
            valid = all(type(x) is int and 0 <= x < 10 for x in value)
        else:
            valid = all(isinstance(x, str) and bool(re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', x)) for x in value)
        return valid and len(set(value)) == len(value)
    return False


def validate_operation(platform, operation, parameters):
    spec = operation_spec(operation)
    if not _supported(platform, operation, spec): raise ValueError('unsupported_operation')
    if parameters is None: parameters = {}
    if not isinstance(parameters, dict): raise ValueError('invalid_operation_parameters')
    fields = _fields(platform, operation, spec)
    if set(parameters) - fields.keys(): raise ValueError('invalid_operation_parameters')
    output = {}
    try:
        for name, descriptor in fields.items():
            if name not in parameters:
                if descriptor['required']: raise ValueError('invalid_operation_parameters')
                if descriptor['default'] is None: continue
                value = deepcopy(descriptor['default'])
            else: value = parameters[name]
            if not _valid(platform, descriptor, value): raise ValueError('invalid_operation_parameters')
            output[name] = value
        if operation == 'update_profile' and not output: raise ValueError('invalid_operation_parameters')
    except (UnicodeError, TypeError, OverflowError):
        raise ValueError('invalid_operation_parameters') from None
    return output
