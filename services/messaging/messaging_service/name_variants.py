"""Bounded transliteration spelling candidates, never identity links."""
import re

LETTERS=dict(zip('абвгдеёжзийклмнопрстуфхцчшщъыьэюя',
    ['a','b','v','g','d','e','yo','zh','z','i','y','k','l','m','n','o','p','r','s',
     't','u','f','kh','ts','ch','sh','shch','','y','','e','yu','ya']))


def patterns(query):
    latin=''.join(LETTERS.get(c,c) for c in query.lower())
    variants={latin}
    # Common terminal spellings: Юля / Юлия / Yulia / Yuliya. This is a
    # search expansion, not proof that the four observations are one person.
    for match in list(re.finditer(r'(?:iya|ia|ya)(?=\b|$)',latin))[:2]:
        for spelling in ('iya','ia','ya'):
            variants.add(latin[:match.start()]+spelling+latin[match.end():])
    return ['%'+v.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
        for v in sorted(variants)[:8]]
