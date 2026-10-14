from messaging_service.search_query import shape_messages


def test_truncated_full_response_keeps_explicit_full_body_entity_reference():
    evidence = {'state': 'observed', 'entities': [{'type': 'bold', 'offset': 2, 'length': 4}]}
    rows = [{'body': '😀 test', 'formatting': evidence}]
    shape_messages(rows, body_chars=1)
    assert rows[0]['body_truncated'] is True
    assert rows[0]['formatting']['entities'] == evidence['entities']
    assert rows[0]['formatting']['offsets_reference'] == 'full_provider_body_utf16'
    assert 'offsets_reference' not in evidence
