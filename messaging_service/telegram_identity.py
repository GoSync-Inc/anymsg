"""Bounded repair of short-send receipts using authenticated self and the outbox.

No network, new session or outgoing action. Original revisions remain immutable.
"""
from .envelope import validate


def repair_sent_senders(archive,account,lease,verified_self):
    from telethon import types
    if not isinstance(verified_self,types.User) or not verified_self.is_self:return 0
    with archive.store.pool.connection() as c:
        archive.fence(c,account,lease)
        sources=c.execute('''SELECT mr.snapshot FROM messages m
            JOIN chats ch ON ch.id=m.chat AND ch.account=m.account
            JOIN message_revisions mr ON mr.message=m.id AND mr.revision=m.revision
            JOIN accounts a ON a.id=m.account AND a.platform='telegram'
            WHERE m.account=%s AND m.sender='' AND m.direction='outgoing' AND NOT m.deleted
                AND ch.chat_type='private' AND EXISTS(SELECT 1 FROM outgoing_messages o
                    WHERE o.account=m.account AND o.chat=m.chat AND o.provider_chat=ch.provider_chat
                        AND o.provider_message_id=m.provider_id AND o.status='sent' AND o.body=m.body
                        AND o.reply_provider_id IS NOT DISTINCT FROM mr.snapshot->'metadata'->>'reply_to_provider_id')
            ORDER BY m.sent_at,m.id LIMIT 100''',(account,)).fetchall()
    repaired=0
    profile={'display_name':' '.join(x for x in (verified_self.first_name,verified_self.last_name) if isinstance(x,str)),
        'username':verified_self.username or ''}
    for row in sources:
        source=row['snapshot']
        if not source.get('provider_chat','').isdigit():continue
        envelope={k:source[k] for k in ('account','platform','chat_title','provider_id','body','sent_at',
            'updated_at','deleted','direction','media')}
        envelope.update(chat=source['provider_chat'],sender=str(verified_self.id),sender_profile=profile,
            origin='reconcile',metadata={**source.get('metadata',{}),'chat_type':'private',
                'sender_identity_basis':'authenticated_self'})
        outcome=archive.ingest(validate(envelope),lease=lease)
        repaired+=outcome['outcome']=='edited'
    return repaired
