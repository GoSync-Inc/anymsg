"""Capabilities report per-account usable operations exactly as list_messenger_operations."""
from messaging_service.envelope import stable_id
from test_messenger_commands import operator


def available(s,key,account):
    return [x['operation'] for x in s.list_messenger_operations(key,account)['items'] if x['available']]


def test_capabilities_available_operations_match_operation_listing(operator):
    s,_,key,_=operator
    row=s.capabilities(key)['messenger_operations']['supported_by_account']['command-fixture']
    assert row['available_operations']==available(s,key,'command-fixture')
    assert row['count']==len(row['operations'])>len(row['available_operations'])
    # Supported and key-permitted, but not opted in for this account.
    assert 'rename_group' in row['operations'] and 'rename_group' not in row['available_operations']
    assert {'edit_message','list_contacts','send_file'}<=set(row['available_operations'])
    s.set_messenger_operations('command-fixture',['rename_group'])
    row=s.capabilities(key)['messenger_operations']['supported_by_account']['command-fixture']
    assert row['available_operations']==['rename_group']==available(s,key,'command-fixture')


def test_chat_scoped_key_excludes_full_account_operations(operator):
    s,_,_,_=operator
    chat=stable_id('chat','command-fixture','100')
    key=s.issue_key('command-chat-agent',['command-fixture'],chats=[chat],
                    permissions=['read','download','send','message-edit','contacts-read'])
    row=s.capabilities(key)['messenger_operations']['supported_by_account']['command-fixture']
    assert row['available_operations']==available(s,key,'command-fixture')
    assert 'edit_message' in row['available_operations'] and 'list_contacts' not in row['available_operations']
    assert 'list_contacts' in row['operations']
