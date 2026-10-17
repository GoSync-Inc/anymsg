import pytest

from messaging_service.messenger_operations import operation_manifest, operation_spec, validate_operation


def test_catalog_keeps_archive_read_separate_from_side_effects():
    assert operation_spec('mark_read')['permission'] == 'read-state'
    assert operation_spec('mark_read')['mutation'] is True
    assert operation_spec('rename_group')['permission'] == 'groups-manage'
    assert operation_spec('list_contacts')['full_account'] is True
    assert operation_spec('chat_info')['full_account'] is False
    assert operation_spec('update_profile')['permission'] == 'profile-manage'


def test_forward_uses_archive_evidence_not_caller_provider_ids():
    assert validate_operation('telegram', 'forward_message', {
        'source_message_id': 'msg_' + 'a' * 32, 'source_revision': 2,
    }) == {'source_message_id': 'msg_' + 'a' * 32, 'source_revision': 2}
    with pytest.raises(ValueError, match='invalid_operation_parameters'):
        validate_operation('telegram', 'forward_message', {
            'source_provider_chat': '123', 'source_provider_message': '456',
        })


def test_defaults_and_strict_bounded_inputs():
    assert validate_operation('whatsapp', 'list_participants', {}) == {'limit': 50, 'offset': 0}
    for params in [{'limit': True}, {'limit': 101}, {'offset': -1}, {'unexpected': 1}]:
        with pytest.raises(ValueError, match='invalid_operation_parameters'):
            validate_operation('whatsapp', 'list_participants', params)
    with pytest.raises(ValueError, match='invalid_operation_parameters'):
        validate_operation('telegram', 'send_poll', {
            'question': 'Synthetic?', 'options': ['one', 'one'], 'multiple': False,
        })


def test_registered_file_requires_digest_and_has_no_path_or_url():
    value = validate_operation('telegram', 'send_file', {
        'attachment_id': 'att_' + 'a' * 32, 'sha256': 'b' * 64,
    })
    assert value['mode'] == 'document' and value['caption'] == ''
    for extra in ['path', 'url', 'registered_file_path', 'trusted_blob_directory']:
        with pytest.raises(ValueError, match='invalid_operation_parameters'):
            validate_operation('telegram', 'send_file', {**value, extra: '/private/file'})


def test_provider_unsupported_functions_fail_before_any_dispatch():
    rows = {r['operation']: r for r in operation_manifest('whatsapp')}
    assert rows['delete_message']['supported'] is False
    assert 'fallback' in rows['delete_message']['limitation']
    with pytest.raises(ValueError, match='unsupported_operation'):
        validate_operation('whatsapp', 'delete_message', {})
    with pytest.raises(ValueError, match='unsupported_operation'):
        validate_operation('telegram', '__raw_api__', {})


def test_contact_ids_are_platform_specific_and_no_coercion():
    assert validate_operation('telegram', 'get_contact', {'user_id': '123'}) == {'user_id': '123'}
    assert validate_operation('whatsapp', 'get_contact', {'user_id': '123@lid'}) == {'user_id': '123@lid'}
    for platform, value in [('telegram', '123@c.us'), ('whatsapp', '123'), ('telegram', 123)]:
        with pytest.raises(ValueError, match='invalid_operation_parameters'):
            validate_operation(platform, 'get_contact', {'user_id': value})


def test_location_and_profile_validate_without_raw_payload():
    value = validate_operation('telegram', 'send_location', {'latitude': -8.6, 'longitude': 115.2})
    assert value['description'] == ''
    with pytest.raises(ValueError, match='invalid_operation_parameters'):
        validate_operation('telegram', 'send_location', {'latitude': float('nan'), 'longitude': 1})
    for platform, params in [('telegram', {'status': 'hello'}), ('whatsapp', {'bio': 'hello'})]:
        with pytest.raises(ValueError, match='invalid_operation_parameters'):
            validate_operation(platform, 'update_profile', params)


def test_account_lifecycle_auth_payments_and_calls_are_not_agent_operations():
    for name in ['delete_account', 'export_session', 'sign_out', 'pay', 'start_call']:
        with pytest.raises(ValueError, match='unsupported_operation'):
            operation_spec(name)


def test_platform_specific_pin_and_label_removal():
    assert validate_operation('telegram','pin_message',{})=={}
    assert validate_operation('whatsapp','pin_message',{})=={'seconds':86400}
    assert validate_operation('whatsapp','set_chat_labels',{'label_ids':[]})=={'label_ids':[]}
    assert operation_spec('send_contact')['full_account']
    assert validate_operation('whatsapp','add_contact',{'phone':'+123456789','first_name':'Synthetic'})['last_name']==''


def test_parameter_formats_described():
    from messaging_service.messenger_operations import OPERATIONS, _fields, parameter_format
    plain = {'string', 'integer', 'number', 'boolean'}
    for platform, provider in [('telegram', None), ('whatsapp', None), ('whatsapp', 'whatsmeow')]:
        for row in operation_manifest(platform, provider=provider):
            reviewed = _fields(platform, row['operation'], OPERATIONS[row['operation']])
            for name, descriptor in row['parameters_schema'].items():
                # Metadata is added; reviewed keys, types and limits stay as they were.
                assert {k: v for k, v in descriptor.items() if k != 'format'} == reviewed[name]
                if descriptor['type'] in plain: assert 'format' not in descriptor
                else: assert descriptor['format'] == parameter_format(platform, descriptor['type']) != None
    rows = {r['operation']: r for r in operation_manifest('telegram')}
    assert 'decimal' in rows['create_group']['parameters_schema']['user_ids']['format']
    assert 'person_' in rows['get_contact']['parameters_schema']['user_id']['format']
    whatsapp = {r['operation']: r for r in operation_manifest('whatsapp')}
    assert '@c.us' in whatsapp['get_contact']['parameters_schema']['user_id']['format']
    assert 'format' not in OPERATIONS['create_group']['fields']['user_ids']
