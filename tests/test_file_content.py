"""Retained document pagination, bounded passage indexes and source coordinates."""
from copy import deepcopy
from pathlib import Path
import re
import shutil

import pytest
from psycopg.types.json import Jsonb

from messaging_service.file_extract import MAX_TEXT_CHARS, Rejected, _office, valid_metadata
from messaging_service.file_index import get_file_segments
from messaging_service.store import APIError
from test_file_index import add_file, drain, extract, ids, office, search, text, worker_for


def workbook(*, external=False):
    return office({
        'xl/workbook.xml': '<workbook xmlns:r="urn:relationships"><sheets>'
            '<sheet name="Бюджет 2026" sheetId="9" r:id="r2"/>'
            '<sheet name="Actual Sales" sheetId="1" r:id="r1"/></sheets></workbook>',
        'xl/_rels/workbook.xml.rels': '<Relationships>'
            '<Relationship Id="r2" Type="urn:rel/worksheet" Target="worksheets/sheet2.xml"/>'
            '<Relationship Id="r1" Type="urn:rel/worksheet" Target="' +
            ('https://invalid.example/private" TargetMode="External' if external else '/xl/worksheets/sheet1.xml') +
            '"/></Relationships>',
        'xl/worksheets/sheet2.xml': '<worksheet><sheetData><row r="7">'
            '<c r="B7" t="inlineStr"><is><t>Расходы</t></is></c>'
            '<c r="D7"><v>250</v></c><c><v>12</v></c>'
            '</row></sheetData></worksheet>',
        'xl/worksheets/sheet1.xml': '<worksheet><sheetData><row r="12">'
            '<c r="C12" t="inlineStr"><is><t>April revenue</t></is></c>'
            '</row></sheetData></worksheet>'})


def test_workbook_uses_real_display_names_order_and_cell_coordinates(tmp_path):
    result = extract(tmp_path, workbook(), 'budget.xlsx')
    assert result.state == 'ready' and valid_metadata(result.metadata)
    assert '[Бюджет 2026]' in result.text and '[Actual Sales]' in result.text
    assert 'xl/worksheets/' not in result.text
    sheets = result.metadata['sheet_spans']
    assert [s['sheet_name'] for s in sheets] == ['Бюджет 2026', 'Actual Sales']
    assert [s['part'] for s in sheets] == ['xl/worksheets/sheet2.xml', 'xl/worksheets/sheet1.xml']
    cells = result.metadata['cell_spans']
    assert [s['cell'] for s in cells] == ['B7', 'D7', 'E7', 'C12']
    assert [s['row'] for s in cells] == [7, 7, 7, 12]
    assert [result.text[s['start']:s['end']] for s in cells] == ['Расходы', '250', '12', 'April revenue']
    assert cells[2]['coordinates_source'] == 'position' and cells[0]['coordinates_source'] == 'explicit'
    assert result.metadata['sheets_total'] == result.metadata['sheets_processed'] == 2
    bad = deepcopy(result.metadata); bad['cell_spans'][0]['row'] = 0
    assert not valid_metadata(bad)
    with pytest.raises(Rejected, match='invalid_document'):
        _office(workbook(external=True), 'xlsx')


def test_missing_workbook_names_and_provenance_cap_are_explicit(monkeypatch):
    import messaging_service.file_extract as module
    monkeypatch.setattr(module, 'MAX_CELL_SPANS', 1)
    result = _office(office({'xl/worksheets/sheet1.xml': '<worksheet><row>'
        '<c><v>one</v></c><c><v>two</v></c></row></worksheet>'}), 'xlsx')
    assert result.metadata['sheet_spans'][0]['sheet_name'] is None
    assert result.metadata['provenance_truncated'] and not result.truncated
    assert len(result.metadata['cell_spans']) == 1 and 'one\ttwo' in result.text


def test_extracted_text_cap_is_configurable_and_not_claimed_full(tmp_path):
    from messaging_service.file_extract import extract_original
    from test_file_index import original
    content = ('abc ' * 500 + 'tail').encode()
    path, digest = original(tmp_path, content)
    result = extract_original(path, digest=digest, bytes=len(content), filename='x.txt', mime='text/plain', text_char_limit=1000)
    assert len(result.text) == 1000 and result.truncated
    assert result.metadata['text_char_limit'] == 1000
    assert extract_original(path, digest=digest, bytes=len(content), filename='x.txt', mime='text/plain', text_char_limit=MAX_TEXT_CHARS+1).error_code == 'invalid_text_limit'


def test_fulltext_tail_unique_vectors_and_exact_vs_passage_semantics(seeded):
    s, a, b = seeded
    # A monolithic vector for these unique lexemes exceeds PostgreSQL's 1MiB limit.
    content = 'firstneedle ' + ' '.join('term%06d' % i for i in range(85000)) + ' lastneedle договоры'
    assert 800000 < len(content) < MAX_TEXT_CHARS
    add_file(s, 'long-unique', 'demo-tg-1', content.encode())
    worker = worker_for(s)
    assert drain(worker)[0]['status'] == 'ready'
    with worker.connection() as c:
        assert c.execute('SHOW statement_timeout').fetchone()['statement_timeout'] == '5s'
    with s.authorized(b, 'search') as (c, _):
        assert c.execute('SHOW statement_timeout').fetchone()['statement_timeout'] == '5s'
    result = search(s, b, 'lastneedle', order='relevance')
    assert ids(result) == ['long-unique'] and result['search_scope'] == 'passage'
    assert result['items'][0]['chunk_start'] > 100000
    assert 'lastneedle' in result['items'][0]['excerpt'].lower()
    assert ids(search(s, b, 'договор', search_language='russian')) == ['long-unique']
    assert ids(search(s, b, 'firstneedle lastneedle')) == []
    assert ids(search(s, b, 'lastneedle договоры', query_mode='exact')) == ['long-unique']
    page = text(s, b, 'long-unique', offset=100100, limit=8000)
    assert page['text'] == content[100100:108100] and page['next_offset'] == 108100
    assert page['content_coverage']['complete'] and page['content_chars'] == len(content)
    tail = text(s, b, 'long-unique', offset=len(content)-20)
    assert tail['next_offset'] is None and tail['text'] == content[-20:]
    with s.pool.connection() as c:
        rows = c.execute("SELECT start_offset,end_offset,pg_column_size(document) AS vector_bytes FROM attachment_text_chunks WHERE attachment='long-unique' ORDER BY chunk_index").fetchall()
    assert len(rows) > 20 and all(r['vector_bytes'] < 1024**2 for r in rows)
    assert rows[0]['start_offset'] == 0 and rows[-1]['end_offset'] == len(content)


def test_phrase_overlap_and_chunk_invalidation_are_transactional(seeded):
    s, a, b = seeded
    content = ('pad ' * 7998) + 'boundaryphrase nextword ' + ('end ' * 200)
    add_file(s, 'overlap-file', 'demo-tg-1', content.encode())
    drain(worker_for(s))
    assert ids(search(s, b, 'boundaryphrase nextword', query_mode='phrase')) == ['overlap-file']
    with s.pool.connection() as c:
        c.execute("UPDATE messages SET revision=revision+1 WHERE id='demo-tg-1'")
        chunks = c.execute("SELECT source_revision FROM attachment_text_chunks WHERE attachment='overlap-file'").fetchall()
        assert len(chunks) == 2 and all(row['source_revision'] == 2 for row in chunks)
    assert ids(search(s, b, 'boundaryphrase')) == ['overlap-file']
    retained = text(s, b, 'overlap-file')
    assert retained['text'] == content[:4000] and retained['revision'] == 2
    # PostgreSQL treats ASCII dotted filenames as a single lexeme. The explicit
    # filename filter remains available regardless of extraction state.
    assert ids(search(s, b, filename='invoice')) == ['overlap-file']


def test_sheet_spans_paginate_and_never_expose_other_occurrence(seeded):
    s, a, b = seeded
    add_file(s, 'sheet-public', 'demo-tg-1', workbook(), 'budget.xlsx')
    add_file(s, 'sheet-private', 'demo-wa-1', workbook(), 'private.xlsx')
    drain(worker_for(s))
    result = text(s, b, 'sheet-public')
    cells = [span for span in result['source_spans'] if span['kind'] == 'cell']
    assert cells[0]['sheet_name'] == 'Бюджет 2026' and cells[0]['cell'] == 'B7'
    assert result['source_spans_truncated'] is False
    start = cells[-1]['start']
    page = text(s, b, 'sheet-public', offset=start, limit=5)
    assert page['text'] == 'April' and all(span['end'] > start for span in page['source_spans'])
    with pytest.raises(APIError, match='not_found'):
        text(s, b, 'sheet-private')


def segments(s, token, attachment_id, **kwargs):
    with s.authorized(token, 'get_file_segments') as (c, p):
        return get_file_segments(s, c, p, attachment_id, **kwargs)


def test_enriched_tail_document_type_segments_and_current_acl(seeded):
    s, a, b = seeded
    add_file(s, 'voice-public', 'demo-tg-1', b'audio-one', 'voice.ogg', 'audio/ogg')
    add_file(s, 'voice-private', 'demo-wa-1', b'audio-two', 'voice.ogg', 'audio/ogg')
    drain(worker_for(s))
    long_text = ' '.join('voice%06d' % i for i in range(80000)) + ' tailtranscription'
    assert 900000 < len(long_text) < MAX_TEXT_CHARS
    marks = [{'start': i, 'end': i+0.5, 'text': 'segment '+str(i)} for i in range(103)]
    with s.pool.connection() as c:
        # Test fixture writes two near-cap derivatives; real background workers
        # receive this write budget, while subsequent API reads remain at 5s.
        c.execute("SET LOCAL statement_timeout='30s'")
        c.execute("UPDATE attachment_enrichment SET state='ready',kind='audio',title='call',text_content=%s,metadata=%s WHERE attachment IN ('voice-public','voice-private')", (long_text, Jsonb({'segments':marks,'document_type':'meeting-notes'})))
    with s.authorized(b, 'search') as (c, _):
        assert c.execute('SHOW statement_timeout').fetchone()['statement_timeout'] == '5s'
    result = search(s, b, 'tailtranscription', order='relevance')
    assert ids(result) == ['voice-public'] and result['items'][0]['matched_content'] == 'enriched'
    assert result['items'][0]['document_type'] == 'meeting-notes'
    assert ids(search(s, b, 'meeting-notes', query_mode='exact')) == ['voice-public']
    page = segments(s, b, 'voice-public')
    assert len(page['segments']) == 100 and page['next_offset'] == 100 and page['total_segments'] == 103
    assert page['segments'][99] == {'index': 99, **marks[99]} and page['timestamp_unit'] == 'seconds'
    assert segments(s, b, 'voice-public', offset=100)['next_offset'] is None
    assert text(s, b, 'voice-public', content='enriched', offset=len(long_text)-17)['text'] == long_text[-17:]
    with pytest.raises(APIError, match='not_found'):
        segments(s, b, 'voice-private')
    with pytest.raises(APIError):
        segments(s, b, 'voice-public', limit=101)
    with s.pool.connection() as c:
        c.execute("UPDATE messages SET revision=revision+1 WHERE id='demo-tg-1'")
        chunks = c.execute("SELECT source_revision FROM attachment_enrichment_chunks WHERE attachment='voice-public'").fetchall()
        assert len(chunks) > 20 and all(row['source_revision'] == 2 for row in chunks)
    retained = segments(s, b, 'voice-public')
    assert retained['total_segments'] == 103 and retained['segments'][0] == {'index': 0, **marks[0]}
    assert ids(search(s, b, 'tailtranscription')) == ['voice-public']


def test_segment_response_byte_budget_and_absent_timestamps(seeded):
    s, a, b = seeded
    add_file(s, 'segments-budget', 'demo-tg-1', b'audio-data', 'v.ogg', 'audio/ogg')
    drain(worker_for(s))
    assert not segments(s, b, 'segments-budget')['has_timestamps']
    marks = [{'start': i, 'end': i+1, 'text': 'x' * 10000} for i in range(8)]
    with s.pool.connection() as c:
        c.execute("UPDATE attachment_enrichment SET state='ready',kind='audio',metadata=%s WHERE attachment='segments-budget'", (Jsonb({'segments':marks}),))
    result = segments(s, b, 'segments-budget')
    assert len(result['segments']) == 6 and result['next_offset'] == 6
    assert len(segments(s, b, 'segments-budget', offset=6)['segments']) == 2


def test_cache_does_not_reuse_smaller_text_cap_and_bounds_full_claim(seeded):
    s, a, b = seeded
    content = (b'word ' * 22000) + b'finalneedle'
    add_file(s, 'small-cap', 'demo-tg-1', content)
    drain(worker_for(s, text_char_limit=100000))
    first = text(s, b, 'small-cap')
    assert first['truncated'] and first['extraction_coverage']['text_char_limit'] == 100000
    assert not first['content_coverage']['complete']
    add_file(s, 'large-cap', 'demo-tg-1', content)
    drain(worker_for(s))
    assert ids(search(s, b, 'finalneedle')) == ['large-cap']
    assert text(s, b, 'large-cap')['content_chars'] == len(content)


def test_pdf_ocr_preserves_synthetic_table_rows(tmp_path):
    if not all(shutil.which(tool, path='/usr/local/bin:/usr/bin:/bin')
               for tool in ('pdftotext','pdftoppm','tesseract')):
        pytest.skip('native indexer image required')
    data = (Path(__file__).parent/'fixtures'/'synthetic-table-scan.pdf').read_bytes()
    result = extract(tmp_path, data, 'synthetic-scan.pdf', 'application/pdf')
    assert result.state == 'ready' and not result.truncated
    assert result.extractor == 'poppler-tesseract-eng-rus-v3'
    assert result.metadata['pages_processed'] == result.metadata['ocr_pages'] == 1
    for pattern in [r'Alpha\s+2\s+1250[.,]00', r'Beta\s+1\s+375[.,]00', r'TOTAL\s+3\s+1625[.,]00']:
        assert re.search(pattern, result.text, re.IGNORECASE), result.text
    page = result.metadata['page_spans'][0]
    assert page['page'] == 1 and page['method'] == 'ocr'
    assert result.text[page['start']:page['end']] == result.text
