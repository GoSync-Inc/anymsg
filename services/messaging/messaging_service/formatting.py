"""Bounded deterministic message formatting; no network or provider operations.

Offsets use UTF-16 code units. Provider text is a separate artifact because
WhatsApp native markup differs from the visible text shown during approval.
"""
from dataclasses import dataclass, field
from html.parser import HTMLParser
import re
import unicodedata
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt

from .store import APIError

RENDERER_VERSION = 'anymsg-renderer-1'
MAX_SOURCE_UNITS = 16000
MAX_WIRE_UNITS = 4000
MAX_TOKENS = 1024
MAX_ENTITIES = 128
MAX_DEPTH = 32
STYLE_TYPES = frozenset(('bold', 'italic', 'strikethrough', 'underline', 'spoiler'))
ENTITY_TYPES = STYLE_TYPES | {'code', 'pre', 'blockquote', 'text_link'}
LANGUAGE = re.compile(r'[A-Za-z0-9_+.-]{1,32}\Z')
HTML_TAG = re.compile(r'''</[A-Za-z][A-Za-z0-9-]*\s*>|<[A-Za-z][A-Za-z0-9-]*(?:\s+[A-Za-z][A-Za-z0-9_-]*\s*=\s*(?:"[^"]*"|'[^']*'))*\s*/?>''')
WA_STYLE = re.compile(r'(?<!\w)([*_~])(?=\S)([^\n]*?\S)\1(?!\w)')


def utf16_units(text):
    if not isinstance(text, str) or any(0xD800 <= ord(c) <= 0xDFFF for c in text):
        raise APIError('invalid_message_text')
    return len(text.encode('utf-16-le')) // 2


def _text(text, maximum):
    units = utf16_units(text)
    if not text.strip() or any(ord(c) < 32 and c not in '\n\r\t' for c in text):
        raise APIError('invalid_message_text')
    if units > maximum:
        raise APIError('message_too_long')
    return units


def _url(url):
    if not isinstance(url, str) or not 1 <= len(url) <= 2048:
        raise APIError('unsafe_message_markup')
    decoded = url
    for depth in range(4):
        if any(c.isspace() or ord(c) < 33 or 127 <= ord(c) <= 159 or
               unicodedata.category(c) in ('Cc', 'Cf', 'Cs') for c in decoded) or '\\' in decoded:
            raise APIError('unsafe_message_markup')
        newer = unquote(decoded)
        if newer == decoded:
            break
        if depth == 3:
            # Three decoding steps are enough for the supported dialect. The
            # final value must be checked, and more layers fail closed.
            raise APIError('unsafe_message_markup')
        decoded = newer
    try:
        parsed = urlsplit(url)
        port = parsed.port
        if (parsed.scheme.lower() not in ('http', 'https') or not parsed.hostname or
                parsed.username is not None or parsed.password is not None or
                port is not None and not 1 <= port <= 65535):
            raise ValueError
    except (ValueError, UnicodeError):
        raise APIError('unsafe_message_markup') from None
    return url


def validate_semantic_entities(text, entities):
    """Validate bounded preview spans, independent of native Telegram rules.

    WhatsApp previews describe source intent, including visible unsupported
    styles and nested code. These spans are not sent as provider entities.
    """
    utf16_units(text)
    boundaries = {0: 0}
    offset = 0
    for index, char in enumerate(text):
        offset += 2 if ord(char) > 0xFFFF else 1
        boundaries[offset] = index + 1
    if not isinstance(entities, list) or len(entities) > MAX_ENTITIES:
        raise APIError('invalid_message_entities')
    result = []
    for entity in entities:
        if (not isinstance(entity, dict) or not isinstance(entity.get('type'), str) or
                entity['type'] not in ENTITY_TYPES):
            raise APIError('invalid_message_entities')
        kind = entity['type']
        allowed = {'type', 'offset', 'length'} | ({'url'} if kind == 'text_link' else
                    {'language'} if kind == 'pre' else set())
        start, length = entity.get('offset'), entity.get('length')
        if (set(entity) - allowed or not {'type', 'offset', 'length'} <= entity.keys() or
                type(start) is not int or type(length) is not int or length <= 0 or
                start not in boundaries or start + length not in boundaries):
            raise APIError('invalid_message_entities')
        if kind == 'text_link':
            _url(entity.get('url'))
        if 'language' in entity and (not isinstance(entity['language'], str) or
                                     not LANGUAGE.fullmatch(entity['language'])):
            raise APIError('invalid_message_entities')
        result.append(dict(entity))
    result.sort(key=lambda e: (e['offset'], -e['length'], e['type']))
    return result


def validate_entities(text, entities):
    """Validate native Telegram spans without changing reviewed wire data."""
    result = validate_semantic_entities(text, entities)
    encoded = text.encode('utf-16-le')
    for entity in result:
        end = entity['offset'] + entity['length']
        span = encoded[entity['offset'] * 2:end * 2].decode('utf-16-le')
        if span[-1].isspace():
            # Telegram excludes trailing whitespace/newlines. Persisted
            # invalid payloads must be rejected instead of silently modified.
            raise APIError('invalid_message_entities')
    for i, first in enumerate(result):
        end = first['offset'] + first['length']
        for second in result[i + 1:]:
            if second['offset'] >= end:
                break
            second_end = second['offset'] + second['length']
            if (first == second or second_end > end or
                    first['type'] in ('code', 'pre') or second['type'] in ('code', 'pre') or
                    first['type'] not in STYLE_TYPES and second['type'] not in STYLE_TYPES):
                raise APIError('invalid_message_entities')
    return result


def validate_provider_payload(platform, payload):
    """Validate persisted wire data without re-rendering its original source."""
    if platform not in ('telegram', 'whatsapp') or not isinstance(payload, dict) or \
            set(payload) != {'text', 'entities', 'link_preview'} or type(payload['link_preview']) is not bool:
        raise APIError('invalid_message_format')
    _text(payload['text'], MAX_WIRE_UNITS)
    entities = validate_entities(payload['text'], payload['entities'])
    if platform == 'whatsapp' and entities:
        raise APIError('invalid_message_entities')
    return {'text': payload['text'], 'entities': entities, 'link_preview': payload['link_preview']}


@dataclass
class _Node:
    kind: str
    text: str = ''
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)


@dataclass
class _Piece:
    text: str = ''
    wire: str = ''
    entities: list = field(default_factory=list)
    links: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    raw_markers: list = field(default_factory=list)
    native_pairs: list = field(default_factory=list)


def _join(pieces, separator=''):
    out = _Piece()
    for index, piece in enumerate(pieces):
        if index:
            out.text += separator
            out.wire += separator
        shift = utf16_units(out.text)
        wire_shift = len(out.wire)
        out.entities.extend({**e, 'offset': e['offset'] + shift} for e in piece.entities)
        out.raw_markers.extend(wire_shift + position for position in piece.raw_markers)
        out.native_pairs.extend((wire_shift + start, wire_shift + end, width)
                                for start, end, width in piece.native_pairs)
        out.text += piece.text
        out.wire += piece.wire
        out.links.extend(piece.links)
        out.warnings.extend(piece.warnings)
    return out


def _spoiler(state, silent):
    start = state.pos
    if state.src[start:start + 2] != '||':
        return False
    end = start + 2
    while True:
        end = state.src.find('||', end)
        if end < 0 or end + 2 > state.posMax:
            return False
        escaped = 0
        before = end - 1
        while before >= start and state.src[before] == '\\':
            escaped += 1
            before -= 1
        if escaped % 2 == 0:
            break
        end += 2
    if end == start + 2:
        return False
    if not silent:
        depth = state.env.get('_spoiler_depth', 0)
        if depth >= MAX_DEPTH:
            raise APIError('formatting_too_complex')
        state.push('spoiler_open', 'span', 1)
        state.env['_spoiler_depth'] = depth + 1
        try:
            inner = []
            state.md.inline.parse(state.src[start + 2:end], state.md, state.env, inner)
            state.tokens.extend(inner)
        finally:
            state.env['_spoiler_depth'] = depth
        state.push('spoiler_close', 'span', -1)
    state.pos = end + 2
    return True


def _markdown(source):
    parser = MarkdownIt('commonmark', {'html': False, 'maxNesting': MAX_DEPTH + 1})
    parser.enable('strikethrough')
    parser.inline.add_terminator_char('|')
    parser.inline.ruler.before('emphasis', 'spoiler', _spoiler)
    # Parse unsafe destinations as links so they are rejected explicitly below,
    # rather than silently becoming a different literal message.
    parser.validateLink = lambda value: True
    tokens = parser.parse(source)
    count = sum(1 + len(token.children or []) for token in tokens)
    if count > MAX_TOKENS:
        raise APIError('formatting_too_complex')
    mapping = {'strong': 'bold', 'em': 'italic', 's': 'strikethrough', 'spoiler': 'spoiler',
               'link': 'text_link', 'paragraph': 'paragraph', 'heading': 'heading',
               'blockquote': 'blockquote', 'bullet_list': 'list', 'ordered_list': 'list',
               'list_item': 'item'}
    root = _Node('root')
    stack = [root]

    def consume(sequence):
        for token in sequence:
            if token.level > MAX_DEPTH or len(stack) > MAX_DEPTH:
                raise APIError('formatting_too_complex')
            if token.type == 'inline':
                consume(token.children or [])
            elif token.nesting == 1:
                kind = mapping.get(token.type.removesuffix('_open'))
                if kind is None:
                    raise APIError('unsupported_message_formatting')
                attrs = {}
                if kind == 'text_link':
                    if token.attrGet('title') is not None:
                        raise APIError('unsafe_message_markup')
                    attrs['url'] = _url(token.attrGet('href'))
                if token.type == 'ordered_list_open':
                    attrs['start'] = token.attrGet('start') or 1
                node = _Node(kind, attrs=attrs)
                stack[-1].children.append(node)
                stack.append(node)
            elif token.nesting == -1:
                stack.pop()
            elif token.type == 'text':
                stack[-1].children.append(_Node('text', token.content))
            elif token.type in ('softbreak', 'hardbreak'):
                stack[-1].children.append(_Node('text', '\n'))
            elif token.type == 'code_inline':
                stack[-1].children.append(_Node('code', children=[_Node('text', token.content)]))
            elif token.type in ('fence', 'code_block'):
                attrs = {}
                if token.info:
                    language = token.info.strip()
                    if not LANGUAGE.fullmatch(language):
                        raise APIError('unsupported_message_formatting')
                    attrs['language'] = language
                stack[-1].children.append(_Node('pre', attrs=attrs, children=[_Node('text', token.content)]))
            elif token.type == 'image':
                raise APIError('unsafe_message_markup')
            else:
                raise APIError('unsupported_message_formatting')
    consume(tokens)
    return root


class _HTML(HTMLParser):
    TAGS = {'b': 'bold', 'strong': 'bold', 'i': 'italic', 'em': 'italic',
            's': 'strikethrough', 'strike': 'strikethrough', 'del': 'strikethrough',
            'u': 'underline', 'tg-spoiler': 'spoiler', 'code': 'code', 'pre': 'pre',
            'blockquote': 'blockquote', 'p': 'paragraph', 'a': 'text_link', 'br': 'break'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node('root')
        self.stack = [(None, self.root)]
        self.count = 0

    def _bump(self):
        self.count += 1
        if self.count > MAX_TOKENS or len(self.stack) > MAX_DEPTH:
            raise APIError('formatting_too_complex')

    def handle_starttag(self, tag, attrs):
        self._bump()
        kind = self.TAGS.get(tag)
        if kind is None or len(dict(attrs)) != len(attrs):
            raise APIError('unsafe_message_markup')
        attrs = dict(attrs)
        allowed = {'href'} if kind == 'text_link' else {'language'} if kind in ('code', 'pre') else set()
        if set(attrs) - allowed:
            raise APIError('unsafe_message_markup')
        normalized = {}
        if kind == 'text_link':
            normalized['url'] = _url(attrs.get('href'))
        if 'language' in attrs:
            if not isinstance(attrs['language'], str) or not LANGUAGE.fullmatch(attrs['language']):
                raise APIError('unsafe_message_markup')
            if kind == 'code' and self.stack[-1][1].kind != 'pre':
                raise APIError('unsafe_message_markup')
            normalized['language'] = attrs['language']
        if kind == 'break':
            self.stack[-1][1].children.append(_Node('text', '\n'))
            return
        node = _Node(kind, attrs=normalized)
        self.stack[-1][1].children.append(node)
        self.stack.append((tag, node))

    def handle_startendtag(self, tag, attrs):
        if tag != 'br':
            raise APIError('unsafe_message_markup')
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        self._bump()
        if len(self.stack) == 1 or self.stack[-1][0] != tag:
            raise APIError('unsafe_message_markup')
        self.stack.pop()

    def handle_data(self, data):
        self._bump()
        self.stack[-1][1].children.append(_Node('text', data))

    def handle_comment(self, data):
        raise APIError('unsafe_message_markup')

    def handle_decl(self, decl):
        raise APIError('unsafe_message_markup')

    def handle_pi(self, data):
        raise APIError('unsafe_message_markup')

    def unknown_decl(self, data):
        raise APIError('unsafe_message_markup')


def _html(source):
    # HTMLParser is deliberately forgiving; a message dialect is not. Do not
    # silently lose an unfinished tag or attribute during its close() call.
    position = 0
    while True:
        position = source.find('<', position)
        if position < 0:
            break
        token = HTML_TAG.match(source, position)
        if token is None:
            raise APIError('unsafe_message_markup')
        position = token.end()
    parser = _HTML()
    parser.feed(source)
    parser.close()
    if len(parser.stack) != 1:
        raise APIError('unsafe_message_markup')
    return parser.root


def _render(node, platform, allow_lossy, literal=False):
    if node.kind == 'text':
        if platform == 'whatsapp' and not literal and ('`' in node.text or WA_STYLE.search(node.text)):
            # A literal backtick beside a generated code span can alter the
            # delimiter run. WhatsApp offers no reliable escaping contract.
            raise APIError('unsupported_message_formatting')
        return _Piece(node.text, node.text, raw_markers=[index for index, char in enumerate(node.text)
                      if not literal and char in '*_~'])
    if node.kind == 'pre' and len(node.children) == 1 and node.children[0].kind == 'code':
        child = node.children[0]
        node = _Node('pre', attrs=node.attrs | child.attrs, children=child.children)
    if node.kind == 'list':
        parts = []
        for index, child in enumerate(node.children):
            marker = f"{int(node.attrs['start']) + index}. " if 'start' in node.attrs else '- '
            parts.append(_join([_Piece(marker, marker), _render(child, platform, allow_lossy)]))
        return _join(parts, '\n')
    separator = '\n\n' if node.kind in ('root', 'item', 'blockquote') else ''
    # HTML permits inline text around a block. Consecutive inline children keep
    # adjacency while documented blocks have a stable two-newline separator.
    if node.kind in ('root', 'blockquote'):
        groups = []
        inline = []
        for child in node.children:
            if child.kind in ('paragraph', 'heading', 'blockquote', 'list', 'pre'):
                if inline:
                    groups.append(_join(inline)); inline = []
                groups.append(_render(child, platform, allow_lossy))
            else:
                inline.append(_render(child, platform, allow_lossy))
        if inline:
            groups.append(_join(inline))
        piece = _join(groups, separator)
    else:
        piece = _join([_render(child, platform, allow_lossy, literal or node.kind in ('code', 'pre'))
                       for child in node.children], separator)
    if node.kind == 'heading':
        piece.warnings.append('heading_rendered_as_plain_text')
    if node.kind not in ENTITY_TYPES or not piece.text:
        return piece
    span_text = piece.text.rstrip() if platform == 'telegram' else piece.text
    entity = {'type': node.kind, 'offset': 0, 'length': utf16_units(span_text)}
    if node.kind == 'text_link':
        url = node.attrs['url']
        piece.links.append({'label': piece.text, 'url': url})
        entity['url'] = url
        if platform == 'whatsapp':
            position = len(piece.wire) + 2
            piece.raw_markers.extend(position + i for i, char in enumerate(url) if char in '*_~`')
            piece.text += ': ' + url
            piece.wire += ': ' + url
            piece.warnings.append('whatsapp_named_link_expanded')
    if node.kind == 'pre' and 'language' in node.attrs:
        entity['language'] = node.attrs['language']
    if platform == 'whatsapp':
        delimiters = {'bold': '*', 'italic': '_', 'strikethrough': '~', 'code': '`', 'pre': '```'}
        if node.kind in delimiters:
            delimiter = delimiters[node.kind]
            if any(e['type'] == 'blockquote' for e in piece.entities):
                raise APIError('unsupported_message_formatting')
            if node.kind in ('code', 'pre'):
                if '`' in piece.wire or piece.entities:
                    raise APIError('unsupported_message_formatting')
            elif any(char in piece.wire for char in '*_~`') or '\n' in piece.wire:
                raise APIError('unsupported_message_formatting')
            if not piece.wire.strip() or piece.wire[0].isspace() or piece.wire[-1].isspace() and node.kind != 'pre':
                raise APIError('unsupported_message_formatting')
            length = len(piece.wire)
            width = len(delimiter)
            piece.native_pairs = [(start + width, end + width, size)
                                  for start, end, size in piece.native_pairs]
            piece.native_pairs.append((0, width + length, width))
            piece.wire = delimiter + piece.wire + delimiter
            piece.raw_markers = [position + len(delimiter) for position in piece.raw_markers]
            if node.kind == 'pre' and 'language' in node.attrs:
                piece.warnings.append('whatsapp_code_language_omitted')
        elif node.kind == 'underline':
            piece.warnings.append('whatsapp_underline_omitted')
        elif node.kind == 'spoiler':
            if not allow_lossy:
                raise APIError('lossy_formatting_requires_confirmation')
            piece.warnings.append('whatsapp_spoiler_visible')
        elif node.kind == 'blockquote':
            if any(e['type'] == 'blockquote' for e in piece.entities):
                raise APIError('unsupported_message_formatting')
            piece.raw_markers = [position + 2 * (piece.wire[:position].count('\n') + 1)
                                 for position in piece.raw_markers]
            piece.native_pairs = [(start + 2 * (piece.wire[:start].count('\n') + 1),
                                   end + 2 * (piece.wire[:end].count('\n') + 1), width)
                                  for start, end, width in piece.native_pairs]
            piece.wire = '\n'.join('> ' + line for line in piece.wire.split('\n'))
    if entity['length']:
        piece.entities.insert(0, entity)
    return piece


def render_message(platform, text, format='plain', link_preview=False, allow_lossy=False):
    """Return a reviewable artifact. This function neither approves nor sends it."""
    if platform not in ('telegram', 'whatsapp') or format not in ('plain', 'markdown', 'html') or \
            type(link_preview) is not bool or type(allow_lossy) is not bool:
        raise APIError('invalid_message_format')
    _text(text, MAX_SOURCE_UNITS)
    if format == 'plain':
        piece = _Piece(text, text, warnings=['whatsapp_native_markup_possible'] if platform == 'whatsapp' else [])
    else:
        root = _markdown(text) if format == 'markdown' else _html(text)
        piece = _render(root, platform, allow_lossy)
        if format == 'markdown':
            tail = len(text) - len(text.rstrip('\n'))
            present = len(piece.text) - len(piece.text.rstrip('\n'))
            if tail > present:
                piece.text += '\n' * (tail - present)
                piece.wire += '\n' * (tail - present)
        if len(piece.entities) > MAX_ENTITIES:
            raise APIError('formatting_too_complex')
    if platform == 'whatsapp' and link_preview:
        piece.warnings.append('whatsapp_link_preview_not_guaranteed')
    if platform == 'whatsapp' and format != 'plain':
        raw_markers = set(piece.raw_markers)
        if any(match.start() in raw_markers or match.end() - 1 in raw_markers
               for match in WA_STYLE.finditer(piece.wire)):
            raise APIError('unsupported_message_formatting')
        for start, end, width in piece.native_pairs:
            before = piece.wire[start - 1] if start else ''
            after = piece.wire[end + width] if end + width < len(piece.wire) else ''
            if (before and (before.isalnum() or before in '*_~`') or
                    after and (after.isalnum() or after in '*_~`')):
                raise APIError('unsupported_message_formatting')
        if re.search(r'(^|\n)(?:> |[-*] |[0-9]+\. )', piece.wire):
            piece.warnings.append('whatsapp_native_block_markup_possible')
    units = _text(piece.wire, MAX_WIRE_UNITS)
    if platform == 'telegram':
        entities = validate_entities(piece.text, piece.entities)
    else:
        # These describe source intent in the visible preview. The WA wire
        # contains native markup and never receives Telegram entity offsets.
        entities = validate_semantic_entities(piece.text, piece.entities)
    payload = validate_provider_payload(platform, {
        'text': piece.wire, 'entities': entities if platform == 'telegram' else [],
        'link_preview': link_preview,
    })
    return {'format': format, 'source_text': text, 'rendered_text': piece.text,
            'provider_text': piece.wire, 'entities': entities, 'provider_payload': payload,
            'warnings': list(dict.fromkeys(piece.warnings)), 'links': piece.links,
            'utf16_units': units, 'renderer_version': RENDERER_VERSION}
