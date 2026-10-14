"""Key ceilings use synthetic credentials and the isolated database only."""
import pytest

from messaging_service.store import APIError


def test_legacy_ceiling_cannot_inherit_new_operation_permissions():
    from messaging_service.policy import effective_permissions
    assert effective_permissions(['read', 'send', 'groups-manage'], None) == ['read', 'send']
    assert effective_permissions(['read', 'send'], ['read']) == ['read']


def test_existing_key_is_not_elevated_by_principal_change(seeded):
    s, old, _ = seeded
    s.set_access('agent-a', permissions=['read', 'groups-read', 'groups-manage'])
    assert s.capabilities(old)['permissions'] == ['read']
    new = s.issue_key('agent-a', ['telegram-demo', 'whatsapp-demo-1'],
                      key_permissions=['read', 'groups-read'])
    assert s.capabilities(new)['permissions'] == ['groups-read', 'read']
    assert s.capabilities(old)['permissions'] == ['read']


def test_key_ceiling_change_is_explicit_and_versioned(seeded):
    s, old, _ = seeded
    credential = s.capabilities(old)['credential_id']
    result = s.set_key_permissions(credential, ['read'])
    assert result['permissions'] == ['read'] and result['permission_version'] == 2
    with pytest.raises(APIError, match='forbidden'):
        s.get_attachment(old, 'demo-attachment')
    with pytest.raises(ValueError, match='invalid_scope'):
        s.set_key_permissions(credential, ['arbitrary-admin'])
    with s.pool.connection() as c:
        assert c.execute('SELECT permission_version FROM api_keys WHERE id=%s',
                         (credential,)).fetchone()['permission_version'] == 2


def test_new_key_cannot_grant_permissions_outside_principal(seeded):
    s, _, _ = seeded
    with pytest.raises(ValueError, match='invalid_key_permissions'):
        s.issue_key('agent-a', ['telegram-demo', 'whatsapp-demo-1'],
                    key_permissions=['groups-manage'])
