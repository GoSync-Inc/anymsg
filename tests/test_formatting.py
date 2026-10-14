"""Synthetic drafts only; deterministic rendering never calls providers."""
import importlib.util
import pytest

from messaging_service.store import APIError


def test_formatting_module_is_available():
    assert importlib.util.find_spec('messaging_service.formatting') is not None


def render(text, *, platform='telegram', format='markdown', **kwargs):
    from messaging_service.formatting import render_message
    return render_message(platform, text, format=format, **kwargs)


def test_plain_passthrough_and_utf16():
    item = render('Привет 😀 **буквально**\n', format='plain')
    assert item['source_text'] == item['rendered_text'] == item['provider_text'] == 'Привет 😀 **буквально**\n'
    assert item['entities'] == []
    assert item['utf16_units'] == 24
    assert item['provider_payload'] == {'text': item['provider_text'], 'entities': [], 'link_preview': False}


def test_markdown_nested_styles_offsets_are_utf16():
    item = render('😀 **Привет *мир***')
    assert item['rendered_text'] == item['provider_text'] == '😀 Привет мир'
    assert item['entities'] == [
        {'type': 'bold', 'offset': 3, 'length': 10},
        {'type': 'italic', 'offset': 10, 'length': 3},
    ]


def test_markdown_strike_spoiler_code_and_literals():
    item = render('5*3 $20 **нет\n~~старое~~ ||секрет|| `a*b_~`')
    assert item['rendered_text'] == '5*3 $20 **нет\nстарое секрет a*b_~'
    assert [e['type'] for e in item['entities']] == ['strikethrough', 'spoiler', 'code']


def test_markdown_links_preserve_balanced_parentheses():
    item = render('[ссылка](https://example.test/a_(b))')
    assert item['rendered_text'] == 'ссылка'
    assert item['entities'] == [{'type': 'text_link', 'offset': 0, 'length': 6, 'url': 'https://example.test/a_(b)'}]
    assert item['links'] == [{'label': 'ссылка', 'url': 'https://example.test/a_(b)'}]


def test_markdown_blocks_have_deterministic_fallbacks():
    item = render('# Заголовок\n\n> Цитата\n> продолжение\n\n3. один\n4. два\n\n```py\nx = 5*3\n```\n')
    assert item['rendered_text'] == 'Заголовок\n\nЦитата\nпродолжение\n\n3. один\n4. два\n\nx = 5*3\n'
    assert item['warnings'] == ['heading_rendered_as_plain_text']
    assert [e['type'] for e in item['entities']] == ['blockquote', 'pre']
    assert item['entities'][-1]['language'] == 'py'


def test_markdown_raw_html_is_literal_text():
    item = render('<script>alert(1)</script> **text**')
    assert item['rendered_text'] == '<script>alert(1)</script> text'
    assert item['entities'] == [{'type': 'bold', 'offset': 26, 'length': 4}]


def test_html_documented_tags_and_entities():
    item = render('<p>😀 <b>bold <i>italics</i></b></p><p><u>under</u> <tg-spoiler>secret</tg-spoiler><br><a href="https://example.test/x">link</a></p>', format='html')
    assert item['rendered_text'] == '😀 bold italics\n\nunder secret\nlink'
    assert [e['type'] for e in item['entities']] == ['bold', 'italic', 'underline', 'spoiler', 'text_link']
    assert item['entities'][0]['offset'] == 3


def test_whatsapp_render_separates_wire_text():
    item = render('**Привет** _мир_ ~~старое~~', platform='whatsapp')
    assert item['rendered_text'] == 'Привет мир старое'
    assert item['provider_text'] == '*Привет* _мир_ ~старое~'
    assert item['provider_payload']['entities'] == []


def test_whatsapp_plain_warns_about_native_markup():
    item = render('**literal**', platform='whatsapp', format='plain')
    assert item['provider_text'] == '**literal**'
    assert 'whatsapp_native_markup_possible' in item['warnings']


def test_whatsapp_named_link_conversion_is_visible():
    item = render('[ссылка](https://example.test/path)', platform='whatsapp')
    assert item['rendered_text'] == item['provider_text'] == 'ссылка: https://example.test/path'
    assert item['warnings'] == ['whatsapp_named_link_expanded']


def test_whatsapp_spoiler_requires_explicit_loss_acceptance():
    import pytest
    from messaging_service.store import APIError
    with pytest.raises(APIError, match='lossy_formatting_requires_confirmation'):
        render('||secret||', platform='whatsapp')
    item = render('||secret||', platform='whatsapp', allow_lossy=True)
    assert item['rendered_text'] == item['provider_text'] == 'secret'
    assert item['warnings'] == ['whatsapp_spoiler_visible']


def test_whatsapp_link_preview_warning_is_honest():
    item = render('hello', platform='whatsapp', link_preview=True)
    assert item['provider_payload']['link_preview'] is True
    assert 'whatsapp_link_preview_not_guaranteed' in item['warnings']


@pytest.mark.parametrize('url', [
    'javascript:alert(1)', 'file:///etc/passwd', 'data:text/plain,hello',
    '//example.test/x', 'https://user:pass@example.test/x',
    'https://example.test/%0afoo', 'https://example.test/%250afoo',
    'https://example.test/\u200bpath', 'https://example.test:99999/path',
])
def test_rich_links_reject_unsafe_destinations(url):
    with pytest.raises(APIError, match='unsafe_message_markup'):
        render(f'<a href="{url}">link</a>', format='html')


@pytest.mark.parametrize('source', [
    '<script>secret</script>', '<img src="https://example.test/image">',
    '<b style="color:red">x</b>', '<b onclick="evil()">x</b>',
    '<a href="https://example.test" href="https://other.test">x</a>',
    '<!-- hidden --><b>x</b>', '<!DOCTYPE html><b>x</b>',
    '<b><i>x</b></i>', '<b>x', '<b', '<b title="x>broken',
])
def test_restricted_html_rejects_unsupported_or_malformed_markup(source):
    with pytest.raises(APIError, match='unsafe_message_markup'):
        render(source, format='html')


def test_markdown_unsafe_links_are_rejected_not_silently_reinterpreted():
    with pytest.raises(APIError, match='unsafe_message_markup'):
        render('[label](javascript:alert)')
    with pytest.raises(APIError, match='unsafe_message_markup'):
        render('![image](https://example.test/x)')


@pytest.mark.parametrize('source', [
    '<b>' * 34 + 'x' + '</b>' * 34,
    '<b>x</b>' * 200,
])
def test_html_complexity_is_bounded(source):
    with pytest.raises(APIError, match='formatting_too_complex'):
        render(source, format='html')


def test_markdown_depth_attack_rejected():
    with pytest.raises(APIError, match='formatting_too_complex'):
        render('> ' * 40 + 'x')


def test_markdown_token_attack_rejected():
    with pytest.raises(APIError, match='formatting_too_complex'):
        render('a *b* ' * 300)


@pytest.mark.parametrize('source', ['', '   ', 'hello\x00', '\ud800', '\udfff'])
def test_invalid_text_is_rejected(source):
    with pytest.raises(APIError, match='invalid_message_text'):
        render(source)


def test_input_and_wire_sizes_use_utf16_and_never_split():
    with pytest.raises(APIError, match='message_too_long'):
        render('😀' * 8001)
    with pytest.raises(APIError, match='message_too_long'):
        render('😀' * 2001, format='plain')
    assert render('😀' * 2000, format='plain')['utf16_units'] == 4000
    with pytest.raises(APIError, match='message_too_long'):
        render('**' + 'a' * 3999 + '**', platform='whatsapp')


@pytest.mark.parametrize('entities', [
    [{'type': 'bold', 'offset': 1, 'length': 1}],
    [{'type': 'bold', 'offset': 0, 'length': 1}],
    [{'type': 'bold', 'offset': -1, 'length': 1}],
    [{'type': 'bold', 'offset': 2, 'length': 0}],
    [{'type': 'bold', 'offset': True, 'length': 2}],
    [{'type': 'mention', 'offset': 0, 'length': 2}],
    [{'type': 'bold', 'offset': 0, 'length': 2, 'url': 'https://example.test'}],
])
def test_telegram_entities_reject_invalid_utf16_spans(entities):
    from messaging_service.formatting import validate_entities
    with pytest.raises(APIError, match='invalid_message_entities'):
        validate_entities('😀x', entities)


@pytest.mark.parametrize('source', [
    '<b><code>x</code></b>', '<a href="https://example.test"><a href="https://other.test">x</a></a>',
    '<blockquote><blockquote>x</blockquote></blockquote>',
])
def test_telegram_invalid_nesting_rejected(source):
    with pytest.raises(APIError, match='invalid_message_entities'):
        render(source, format='html')


def test_html_pre_code_language_is_single_native_entity():
    item = render('<pre><code language="python">x = 5*3\n</code></pre>', format='html')
    assert item['entities'] == [{'type': 'pre', 'offset': 0, 'length': 7, 'language': 'python'}]


@pytest.mark.parametrize('source', ['**a_b**', '**a *b***', '`a`b`', '<code>a`b</code>'])
def test_whatsapp_ambiguous_native_markup_rejected(source):
    with pytest.raises(APIError, match='unsupported_message_formatting'):
        render(source, platform='whatsapp', format='html' if source.startswith('<') else 'markdown')


def test_whatsapp_code_language_and_underline_fallback_disclosed():
    item = render('<u>under</u> <pre language="python">a*b_~\n</pre>', platform='whatsapp', format='html')
    assert item['provider_text'] == 'under \n\n```a*b_~\n```'
    assert item['warnings'] == ['whatsapp_underline_omitted', 'whatsapp_code_language_omitted']


def test_provider_payload_revalidates_fresh_copy_and_strict_keys():
    from messaging_service.formatting import validate_provider_payload
    original = {'text': '😀 x', 'entities': [{'type': 'bold', 'offset': 3, 'length': 1}], 'link_preview': False}
    copy = validate_provider_payload('telegram', original)
    copy['entities'][0]['length'] = 3
    assert original['entities'][0]['length'] == 1
    for bad in [{**original, 'parse_mode': 'html'}, {**original, 'link_preview': 1}]:
        with pytest.raises(APIError, match='invalid_message_format'):
            validate_provider_payload('telegram', bad)
    with pytest.raises(APIError, match='invalid_message_entities'):
        validate_provider_payload('whatsapp', original)


def test_markdown_trailing_newlines_are_preserved():
    assert render('**hi**\n\n')['provider_text'] == 'hi\n\n'


def test_whatsapp_quote_uses_native_wire_marker():
    item = render('> first\n> second', platform='whatsapp')
    assert item['rendered_text'] == 'first\nsecond'
    assert item['provider_text'] == '> first\n> second'


@pytest.mark.parametrize('source', ['<b>*hidden*</b>', '<u>_native_</u>', '<p>*unintended*</p>'])
def test_whatsapp_html_literal_delimiters_cannot_add_undisclosed_styles(source):
    with pytest.raises(APIError, match='unsupported_message_formatting'):
        render(source, platform='whatsapp', format='html')


@pytest.mark.parametrize('entities', [
    [{'type': [], 'offset': 0, 'length': 1}],
    [{'type': {}, 'offset': 0, 'length': 1}],
    [{'type': 'bold', 'offset': 0, 'length': 2}, {'type': 'italic', 'offset': 1, 'length': 2}],
])
def test_payload_validation_cannot_raise_unhandled_type_errors(entities):
    from messaging_service.formatting import validate_provider_payload
    with pytest.raises(APIError, match='invalid_message_entities'):
        validate_provider_payload('telegram', {'text': 'abc', 'entities': entities, 'link_preview': False})


def test_code_language_must_apply_to_pre_not_inline():
    with pytest.raises(APIError, match='unsafe_message_markup'):
        render('<code language="python">x</code>', format='html')


def test_html_entities_decode_without_fetching():
    item = render('<b>&lt;hello&gt; &amp; &#x1F600;</b>', format='html')
    assert item['rendered_text'] == '<hello> & 😀'
    assert item['entities'] == [{'type': 'bold', 'offset': 0, 'length': 12}]


def test_telegram_crossing_entities_rejected():
    from messaging_service.formatting import validate_entities
    with pytest.raises(APIError, match='invalid_message_entities'):
        validate_entities('abcdef', [
            {'type': 'bold', 'offset': 0, 'length': 4},
            {'type': 'italic', 'offset': 2, 'length': 4},
        ])


def test_whatsapp_code_preserves_special_punctuation():
    item = render('`5*3 _x_ ~old~`', platform='whatsapp')
    assert item['rendered_text'] == '5*3 _x_ ~old~'
    assert item['provider_text'] == '`5*3 _x_ ~old~`'


def test_whatsapp_delimiters_split_across_tags_are_rejected():
    with pytest.raises(APIError, match='unsupported_message_formatting'):
        render('<u>*</u>hidden<u>*</u>', platform='whatsapp', format='html')


def test_whatsapp_literal_math_beside_generated_bold_is_preserved():
    item = render('5*3 **bold**', platform='whatsapp')
    assert item['rendered_text'] == '5*3 bold'
    assert item['provider_text'] == '5*3 *bold*'


def test_telegram_entities_trim_trailing_whitespace_without_changing_body():
    item = render('<b>x </b><i>😀 y</i>', format='html')
    assert item['provider_text'] == 'x 😀 y'
    assert item['entities'] == [
        {'type': 'bold', 'offset': 0, 'length': 1},
        {'type': 'italic', 'offset': 2, 'length': 4},
    ]
    assert render('```py\nx\n```')['entities'] == [
        {'type': 'pre', 'offset': 0, 'length': 1, 'language': 'py'},
    ]
    assert render('<b> </b>x', format='html')['provider_text'] == ' x'
    assert render('<b> </b>x', format='html')['entities'] == []


def test_persisted_telegram_entities_cannot_include_trailing_whitespace():
    from messaging_service.formatting import validate_provider_payload
    with pytest.raises(APIError, match='invalid_message_entities'):
        validate_provider_payload('telegram', {
            'text': 'x ', 'entities': [{'type': 'bold', 'offset': 0, 'length': 2}],
            'link_preview': False,
        })


@pytest.mark.parametrize('source', ['a<b>b</b>', '<b>a</b>b', '<b>a</b><b>b</b>', '<b><blockquote>x</blockquote></b>'])
def test_whatsapp_ambiguous_generated_markup_boundaries_rejected(source):
    with pytest.raises(APIError, match='unsupported_message_formatting'):
        render(source, platform='whatsapp', format='html')


@pytest.mark.parametrize('source', ['<p>&gt; hidden quote</p>', '<p>- hidden list</p>', '<p>1. hidden list</p>'])
def test_whatsapp_literal_native_blocks_are_disclosed(source):
    item = render(source, platform='whatsapp', format='html')
    assert 'whatsapp_native_block_markup_possible' in item['warnings']


@pytest.mark.parametrize('source', [r'\> hidden quote', r'\- hidden list', r'1\. hidden list'])
def test_whatsapp_escaped_markdown_native_blocks_are_disclosed(source):
    item = render(source, platform='whatsapp')
    assert 'whatsapp_native_block_markup_possible' in item['warnings']


@pytest.mark.parametrize('platform', ['telegram', 'whatsapp'])
@pytest.mark.parametrize('source,visible,wire', [
    ('<blockquote>one <b>two</b> three</blockquote>', 'one two three', '> one *two* three'),
    ('<blockquote>one<br>two</blockquote>', 'one\ntwo', '> one\n> two'),
])
def test_html_blockquote_preserves_inline_adjacency_and_explicit_breaks(platform, source, visible, wire):
    item = render(source, platform=platform, format='html')
    assert item['rendered_text'] == visible
    assert item['provider_text'] == (visible if platform == 'telegram' else wire)


@pytest.mark.parametrize('path', [
    '%25250afoo', '%2525250afoo', '%2525E2%252580%25258Bfoo',
    '%252525E2%25252580%2525258Bfoo',
])
def test_rich_links_reject_controls_at_or_beyond_decode_budget(path):
    with pytest.raises(APIError, match='unsafe_message_markup'):
        render(f'<a href="https://example.test/{path}">x</a>', format='html')


def test_semantic_entities_allow_wa_trailing_whitespace_and_native_nested_code():
    from messaging_service import formatting
    assert hasattr(formatting, 'validate_semantic_entities')
    for text, entities in [
        ('x\n', [{'type': 'pre', 'offset': 0, 'length': 2}]),
        ('secret', [{'type': 'spoiler', 'offset': 0, 'length': 6}, {'type': 'code', 'offset': 0, 'length': 6}]),
        ('code', [{'type': 'blockquote', 'offset': 0, 'length': 4}, {'type': 'code', 'offset': 0, 'length': 4}]),
    ]:
        assert formatting.validate_semantic_entities(text, entities) == sorted(
            entities, key=lambda e: (e['offset'], -e['length'], e['type']))
    with pytest.raises(APIError, match='invalid_message_entities'):
        formatting.validate_semantic_entities('😀x', [{'type': 'bold', 'offset': 1, 'length': 1}])
