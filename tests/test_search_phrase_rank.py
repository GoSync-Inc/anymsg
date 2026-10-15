"""Relevance: an exact phrase ranks above a morphology-only match of the same words."""
from messaging_service.search_query import compile_query
from test_search_v2 import add


def ids(result):
    return [r['id'] for r in result['items']]


def seed(s):
    add(s,'phrase-exact','Подписали брачный договор у нотариуса в среду.',day=5)
    add(s,'phrase-inflected','Обсудили условия брачного договора вчера вечером.',day=6)
    # Russian stemming merges договор/договориться; several forms score higher alone.
    add(s,'morph-only','Договоримся: брачные дела, договорились: брачный вопрос.',day=7)
    add(s,'morph-tie','Нужно договориться, брачные дела.',day=8)


def test_exact_phrase_outranks_morphology_only_matches(seeded):
    s,a,b=seeded
    seed(s)
    result=s.search(b,'брачный договор',order='relevance')
    assert ids(result)==['phrase-exact','phrase-inflected','morph-only','morph-tie']
    assert [r['match_type'] for r in result['items']][2:]==['morphology_russian']*2
    # Case of the typed query does not matter for the literal tier.
    assert ids(s.search(b,'Брачный ДОГОВОР',order='relevance'))[:2]==['phrase-exact','phrase-inflected']
    # Phrase mode only returns adjacent words; the exact form still comes first.
    assert ids(s.search(b,'брачный договор',query_mode='phrase',order='relevance'))==['phrase-exact','phrase-inflected']
    # Chronological orders and the matched set are unchanged.
    assert ids(s.search(b,'брачный договор',order='desc'))==['morph-tie','morph-only','phrase-inflected','phrase-exact']


def test_tiered_scores_stay_bounded_and_paginate(seeded):
    s,a,b=seeded
    seed(s)
    add(s,'phrase-dense',' '.join(['брачный договор']*400),day=4)
    pages=[];cursor='';scores=[]
    while True:
        result=s.search(b,'брачный договор',order='relevance',limit=1,after=cursor)
        pages+=ids(result);scores+=[r['score'] for r in result['items']]
        cursor=result['next_cursor']
        if not cursor:break
    assert pages==['phrase-dense','phrase-exact','phrase-inflected','morph-only','morph-tie']
    assert all(type(x) is int and 0<=x<=1000000 for x in scores) and scores==sorted(scores,reverse=True)


def test_rank_without_text_keeps_the_legacy_expression():
    plan=compile_query('брачный договор','all','russian')
    assert plan.rank('f')=="(round((greatest(ts_rank_cd(f.document_russian,q.russian,32))*1000000)::numeric))::bigint"
    assert 'LIKE' in plan.rank('m','m.body') and 'q.phrase_russian' in plan.rank('m','m.body')
    assert 'LIKE' not in compile_query('брачный договор','web','russian').rank('m','m.body')
    assert compile_query('брачный договор','exact').rank('m','m.body')=='0::bigint'
