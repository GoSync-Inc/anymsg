from datetime import datetime,timezone
from types import SimpleNamespace

import pytest

from messaging_service.adapters import telegram_message,whatsapp_message


def test_telegram_without_network_and_with_original_metadata():
    message=SimpleNamespace(chat_id=-100123,id=19,sender_id=42,message='photo caption',
        date=datetime(2026,10,1,tzinfo=timezone.utc),edit_date=None,out=True,
        file=SimpleNamespace(name=None,ext='.jpg',mime_type='image/jpeg',size=32*1024**2),
        document=None,photo=SimpleNamespace(id=123456))
    e=telegram_message('tg-owner',message)
    assert e['chat']=='-100123' and e['direction']=='outgoing'
    assert e['media'][0]['provider_id']=='123456' and e['media'][0]['bytes']==32*1024**2


def test_telegram_webpage_document_metadata_does_not_use_preview_photo():
    from telethon import types,utils
    now=datetime(2026,10,13,tzinfo=timezone.utc)
    document=types.Document(id=200,access_hash=0,file_reference=b'ref',date=now,
        mime_type='application/pdf',size=1234,dc_id=1,
        attributes=[types.DocumentAttributeFilename(file_name='document.pdf')])
    photo=types.Photo(id=300,access_hash=0,file_reference=b'ref',date=now,
        sizes=[types.PhotoSize(type='x',w=2,h=2,size=4)],dc_id=1)
    webpage=types.WebPage(id=20,url='https://example.invalid',display_url='example.invalid',
        hash=0,document=document,photo=photo)
    message=types.Message(id=10,peer_id=types.PeerUser(42),from_id=types.PeerUser(42),
        date=now,message='preview',media=types.MessageMediaWebPage(webpage=webpage))
    assert message.file.size==4  # Pinned Telethon selects the preview by default.
    descriptor=telegram_message('tg-owner',message)['media'][0]
    assert descriptor=={'provider_id':'200','filename':'document.pdf','mime':'application/pdf','bytes':1234}
    assert utils._get_file_info(message.document or message.photo).size==descriptor['bytes']


def test_whatsapp_accounts_mapping_and_delete_time():
    event={'messageId':'true_group_ABC','chatId':'123@g.us','senderId':'456@c.us',
           'body':'caption','fromMe':True,'timestamp':1791000000,'hasMedia':True,
           'filename':'video.mp4','mimetype':'video/mp4','filesize':256*1024**2}
    first=whatsapp_message('wa-1',event); second=whatsapp_message('wa-2',event)
    assert first['account']!=second['account']
    assert first['sender']=='456@c.us' and first['chat']=='123@g.us'
    assert first['media'][0]['bytes']==256*1024**2
    with pytest.raises(ValueError,match='missing_change_time'): whatsapp_message('wa-1',event,change='edit')
    deleted=whatsapp_message('wa-1',event,change='delete',observed_at=1791000030)
    assert deleted['deleted'] and deleted['body']=='' and deleted['media']==[]


def test_owner_attention_is_provider_metadata_not_text_guessing():
    message=SimpleNamespace(chat_id=-123,id=19,sender_id=42,message='@Someone',
        date=datetime(2026,10,1,tzinfo=timezone.utc),edit_date=None,out=False,
        file=None,document=None,photo=None,mentioned=True)
    assert telegram_message('tg-owner',message)['metadata']['mentions_owner'] is True
    del message.mentioned
    assert telegram_message('tg-owner',message)['metadata']['mentions_owner'] is None
    wa={'messageId':'false_123@g.us_A','chatId':'123@g.us','senderId':'456@c.us',
        'timestamp':1791000000,'body':'@Owner','mentionsOwner':True,'replyToOwner':True}
    md=whatsapp_message('wa-owner',wa)['metadata']
    assert md['mentions_owner'] is True and md['reply_to_owner'] is True
    wa.pop('mentionsOwner');wa.pop('replyToOwner')
    assert whatsapp_message('wa-owner',wa)['metadata']['mentions_owner'] is None


def test_telegram_cross_peer_reply_does_not_resolve_in_local_id_namespace():
    from telethon.types import PeerChat
    message=SimpleNamespace(chat_id=-123,id=19,sender_id=42,message='Reply',
        date=datetime(2026,10,1,tzinfo=timezone.utc),edit_date=None,out=False,
        file=None,document=None,photo=None,
        reply_to=SimpleNamespace(reply_to_msg_id=10,reply_to_peer_id=PeerChat(456)))
    assert telegram_message('tg-owner',message)['metadata']['reply_to_provider_id'] is None
    message.reply_to.reply_to_peer_id=PeerChat(123)
    assert telegram_message('tg-owner',message)['metadata']['reply_to_provider_id']=='10'
