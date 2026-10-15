"""Readable private chat titles when the provider title is only its chat ID."""
from datetime import datetime, timedelta, timezone

from messaging_service.archive import Archive
from messaging_service.presentation import render
from test_archive import event

LID = '48000000000001@lid'


def peer_chat(seeded):
    s,_,_=seeded
    ar=Archive(s);ar.register('title-wa','whatsapp','Synthetic titles',discover=True)
    key=s.issue_key('title-agent',['title-wa'])
    base=datetime.now(timezone.utc)-timedelta(hours=3)
    def add(n,direction,name):
        at=(base+timedelta(minutes=n)).isoformat()
        prefix='true_' if direction=='outgoing' else 'false_'
        return ar.ingest(event(account='title-wa',platform='whatsapp',chat=LID,chat_title=LID,
            provider_id=prefix+LID+'_SYNTHETIC'+str(n),sender='owner@c.us' if direction=='outgoing' else LID,
            body='Synthetic message '+str(n),sent_at=at,updated_at=at,direction=direction,
            sender_profile={'display_name':name,'username':''},metadata={'chat_type':'private'}))['message']
    add(1,'incoming','Synthetic Peer')
    add(2,'outgoing','Synthetic Owner')
    return s,key


def test_private_chat_titled_by_its_id_shows_latest_incoming_sender_name(seeded):
    s,key=peer_chat(seeded)
    row=s.list_chats(key,include_coverage=False)['items'][0]
    assert row['title']=='Synthetic Peer' and row['title_basis']=='latest_incoming_sender_name'
    assert row['provider_chat_id']==LID
    assert render('list_chats',s.list_chats(key,include_coverage=False),'compact')['items'][0]['title']=='Synthetic Peer'
    assert [r['id'] for r in s.list_chats(key,query='peer',include_coverage=False)['items']]==[row['id']]
    assert s.list_chats(key,query='owner',include_coverage=False)['items']==[]
    messages=s.get_messages(key,row['id'])['items']
    assert {m['chat_title'] for m in messages}=={'Synthetic Peer'}


def test_named_chat_keeps_provider_title(seeded):
    s,key=peer_chat(seeded)
    with s.pool.connection() as c:c.execute("UPDATE chats SET title='Synthetic provider title' WHERE account='title-wa'")
    row=s.list_chats(key,include_coverage=False)['items'][0]
    assert row['title']=='Synthetic provider title' and 'title_basis' not in row


def test_self_chat_titled_by_its_lid_is_labelled_saved_messages(seeded):
    s,_,_=seeded
    ar=Archive(s);ar.register('title-self','whatsapp','Synthetic self',discover=True)
    key=s.issue_key('title-self-agent',['title-self'])
    own='48000000000002@lid'
    at=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
    # The owner's own LID chat: only its messages carry the self-chat marker,
    # and an own-device copy must not rename it after the owner.
    for n,direction in ((1,'outgoing'),(2,'incoming')):
        ar.ingest(event(account='title-self',platform='whatsapp',chat=own,chat_title=own,
            provider_id=('true_' if direction=='outgoing' else 'false_')+own+'_SELF'+str(n),sender=own,
            body='Synthetic note '+str(n),sent_at=at,updated_at=at,direction=direction,
            sender_profile={'display_name':'Synthetic Owner','username':''},
            metadata={'chat_type':'private','is_self_chat':True}))
    row=s.list_chats(key,include_coverage=False)['items'][0]
    assert row['title']=='Saved messages' and row['title_basis']=='self_chat'
    assert {m['chat_title'] for m in s.get_messages(key,row['id'])['items']}=={'Saved messages'}
