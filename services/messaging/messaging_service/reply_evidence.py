"""Exact reply proofs frozen at ingestion/watch creation; no later archive lookup."""
import re


BASIS = 'exact_native_reply_to_archived_outgoing'


def _target(c, account, chat, provider_id):
    # The existing (account, chat, provider_id) unique index bounds this lookup.
    return c.execute('''SELECT id,revision,provider_id,sender FROM messages
        WHERE account=%s AND chat=%s AND provider_id=%s AND direction='outgoing'
        AND NOT deleted''', (account, chat, provider_id)).fetchone()


def capture(c, envelope, chat):
    metadata = envelope.get('metadata') or {}
    reference = metadata.get('reply_to_provider_id')
    if (envelope['direction'] != 'incoming' or envelope['deleted'] or
            metadata.get('reply_to_owner') is not None or not reference):
        return None
    target = _target(c, envelope['account'], chat, reference)
    if target is None:
        return None
    return dict(basis=BASIS, target_message=target['id'], target_revision=target['revision'],
        target_provider_id=target['provider_id'])


def validate(value):
    if (not isinstance(value, dict) or set(value) !=
            {'basis', 'target_message', 'target_revision', 'target_provider_id'} or
            value.get('basis') != BASIS or type(value.get('target_revision')) is not int or
            not 1 <= value['target_revision'] <= 2**53 - 1):
        raise ValueError('invalid_event_metadata')
    for key in ('target_message', 'target_provider_id'):
        item = value[key]
        if (not isinstance(item, str) or not 1 <= len(item) <= 256 or
                any(ord(ch) < 32 or ord(ch) == 127 for ch in item)):
            raise ValueError('invalid_event_metadata')


def confirmed_native_reference(c, anchor):
    """Freeze a WA stanza only when the exact outgoing archive proves its parts.

    The native serialization permits underscores in chat, stanza and participant.
    Remove only exact known components; splitting at arbitrary separators would
    invent identities. Missing/mismatched native sender evidence stays unknown.
    """
    if anchor['platform'] != 'whatsapp':
        return {}
    target = _target(c, anchor['account'], anchor['chat'], anchor['provider_message_id'])
    if target is None:
        return {}
    prefix = 'true_' + anchor['provider_chat'] + '_'
    provider_id = target['provider_id']
    if not provider_id.startswith(prefix):
        return {}
    reference = provider_id[len(prefix):]
    if anchor['provider_chat'].endswith('@g.us'):
        suffix = '_' + target['sender']
        if (re.fullmatch(r'[a-zA-Z0-9_.:-]{1,140}@(c\.us|lid)', target['sender']) and
                reference.endswith(suffix)):
            reference = reference[:-len(suffix)]
    # A remaining @ can be an unproven participant suffix, not a known stanza.
    if not re.fullmatch(r'[a-zA-Z0-9_.:-]{1,256}', reference):
        return {}
    return dict(platform='whatsapp', provider_chat=anchor['provider_chat'], native_reply_id=reference,
        target_message=target['id'], target_revision=target['revision'],
        native_reply_basis='exact_outgoing_archive_components')
