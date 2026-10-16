"""Caption matches are current scoped evidence, never extracted file content."""
import pytest

from messaging_service.presentation import render
from messaging_service.store import APIError
from test_file_index import add_file, drain, ids, search, text, worker_for


def caption(s, message, body):
    with s.pool.connection() as c:
        c.execute('UPDATE messages SET body=%s,revision=revision+1 WHERE id=%s', (body, message))


@pytest.mark.parametrize('mode,query', [
    ('all', 'договорами'), ('phrase', 'договоры готовы'),
    ('web', '"договоры готовы" OR impossibleword'), ('exact', 'Договоры готовы'),
])
def test_caption_only_match_preserves_source_and_extraction_state(seeded, mode, query):
    s, a, b = seeded
    body = 'Вступление. ' * 100 + 'Договоры готовы: объект OTR02.'
    caption(s, 'demo-tg-1', body)
    add_file(s, 'caption-photo', 'demo-tg-1', b'opaque video bytes', 'image1.mp4', 'video/mp4')
    drain(worker_for(s))
    result = search(s, b, query, query_mode=mode, search_language='russian')
    assert ids(result) == ['caption-photo']
    row = result['items'][0]
    assert row['matched_content'] == 'message_context'
    assert row['match_sources'] == ['message_context']
    assert 'договор' in row['excerpt'].lower()
    assert row['chunk_start'] is None and row['chunk_end'] is None
    assert row['message_context'] == {'message_id': 'demo-tg-1', 'revision': 2, 'field': 'body'}
    assert row['download_status'] == 'ready' and row['extraction_status'] == 'unsupported'
    assert row['enrichment_status'] == 'not_configured' and row['text_chars'] == 0
    assert text(s, b, 'caption-photo')['text'] == ''
    compact = render('search_files', result, 'compact')['items'][0]
    assert compact['matched_content'] == 'message_context'
    assert compact['message_context'] == row['message_context']
    assert compact['match_sources'] == ['message_context']
    assert 'договор' in compact['snippet'].lower()


def test_queued_file_can_be_found_by_caption_without_triggering_extraction(seeded):
    s, a, b = seeded
    caption(s, 'demo-tg-1', 'Скан паспорта')
    add_file(s, 'pending-caption', 'demo-tg-1', b'raw', 'image1.txt')
    result = search(s, b, 'паспорт')
    assert ids(result) == ['pending-caption']
    assert result['items'][0]['extraction_status'] == 'queued'
    assert result['items'][0]['matched_content'] == 'message_context'


def test_mixed_match_has_both_sources_but_file_snippet_is_not_caption(seeded):
    s, a, b = seeded
    caption(s, 'demo-tg-1', 'Contract mentioned in the caption')
    add_file(s, 'mixed', 'demo-tg-1', b'Contract inside the actual file', 'note.txt')
    drain(worker_for(s))
    row = search(s, b, 'contract')['items'][0]
    assert row['matched_content'] == 'extracted'
    assert set(row['match_sources']) == {'extracted', 'message_context'}
    assert 'actual file' in row['excerpt'] and row['message_context']['revision'] == 2


def test_same_bytes_hidden_caption_never_leaks_to_visible_occurrence(seeded):
    s, a, b = seeded
    caption(s, 'demo-private-1', 'hiddenneedle private contract')
    payload = b'shared content with no caption term'
    add_file(s, 'hidden-caption', 'demo-private-1', payload)
    add_file(s, 'visible-copy', 'demo-tg-1', payload)
    drain(worker_for(s))
    assert not search(s, b, 'hiddenneedle')['items']
    assert not search(s, a, 'hiddenneedle')['items']
    assert not search(s, b, 'hiddenneedle', chats=['private-chat'])['items']


def test_caption_changes_deletes_and_attachment_deactivation_invalidate_matches(seeded):
    s, a, b = seeded
    caption(s, 'demo-tg-1', 'oldcaptionneedle')
    add_file(s, 'changing', 'demo-tg-1', b'retainedfileneedle file detail')
    drain(worker_for(s))
    assert ids(search(s, b, 'oldcaptionneedle')) == ['changing']
    caption(s, 'demo-tg-1', 'newcaptionneedle')
    assert not search(s, b, 'oldcaptionneedle')['items']
    row = search(s, b, 'newcaptionneedle')['items'][0]
    assert row['message_context']['revision'] == 3 and row['extraction_status'] == 'ready'
    page = text(s, b, 'changing')
    assert page['text'] == 'retainedfileneedle file detail' and page['provenance']['revision'] == 3
    assert ids(search(s, b, 'retainedfileneedle')) == ['changing']
    with s.pool.connection() as c:
        c.execute("UPDATE attachments SET active=false WHERE id='changing'")
    assert not search(s, b, 'newcaptionneedle')['items']
    assert not search(s, b, 'retainedfileneedle')['items']
    with s.pool.connection() as c:
        c.execute("UPDATE attachments SET active=true WHERE id='changing'")
        c.execute("UPDATE messages SET deleted=true WHERE id='demo-tg-1'")
    assert not search(s, b, 'newcaptionneedle')['items']
    assert not search(s, b, 'retainedfileneedle')['items']


def test_caption_filters_and_paging_keep_occurrences_and_current_acl(seeded):
    s, a, b = seeded
    with s.pool.connection() as c:
        c.execute("DELETE FROM attachments WHERE id='demo-file-1'")
    for mid in ('demo-tg-1', 'demo-tg-2', 'demo-wa-1'):
        caption(s, mid, 'fileneedle')
    add_file(s, 'caption-first', 'demo-tg-1', b'first', 'first.txt')
    add_file(s, 'caption-second', 'demo-tg-2', b'second', 'second.txt')
    add_file(s, 'caption-wa', 'demo-wa-1', b'wa', 'wa.txt')
    result = search(s, b, 'fileneedle', order='relevance', limit=1)
    rest = search(s, b, 'fileneedle', order='relevance', after=result['next_cursor'])
    assert set(ids(result) + ids(rest)) == {'caption-first', 'caption-second'}
    assert ids(search(s, a, 'fileneedle', accounts=['whatsapp-demo-1'])) == ['caption-wa']
    assert ids(search(s, b, 'fileneedle', filename='first')) == ['caption-first']
    assert not search(s, b, 'fileneedle', accounts=[])['items']
    assert not search(s, b, 'fileneedle', senders=['absent'])['items']
    with pytest.raises(APIError, match='invalid_cursor'):
        search(s, b, 'differentneedle', order='relevance', after=result['next_cursor'])
    s.set_access('agent-b', chats=[])
    assert not search(s, b, 'fileneedle')['items']


def test_caption_anchor_is_rechecked_when_message_changes(seeded):
    s, a, b = seeded
    with s.pool.connection() as c:
        c.execute("DELETE FROM attachments WHERE id='demo-file-1'")
    for mid in ('demo-tg-1', 'demo-tg-2'):
        caption(s, mid, 'anchorneedle')
    add_file(s, 'anchor-first', 'demo-tg-1', b'first')
    add_file(s, 'anchor-second', 'demo-tg-2', b'second')
    result = search(s, b, 'anchorneedle', order='relevance', limit=1)
    caption(s, result['items'][0]['message_id'], 'removed')
    with pytest.raises(APIError, match='not_found'):
        search(s, b, 'anchorneedle', order='relevance', after=result['next_cursor'])


def test_caption_match_requires_original_presence(seeded):
    s, a, b = seeded
    caption(s, 'demo-tg-1', 'missingoriginalneedle')
    digest = add_file(s, 'absent-original', 'demo-tg-1', b'original')
    (s.files / digest).unlink()
    assert not search(s, b, 'missingoriginalneedle')['items']
