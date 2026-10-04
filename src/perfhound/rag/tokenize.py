"""Tokenizer for code AND prose.

Identifiers are split the way developers read them, and the whole
identifier is kept too, so both `satask` and `get_all_known_facts` match:

    "sympy.assumptions.satask.get_all_relevant_facts"
      -> sympy assumptions satask get all relevant facts get_all_relevant_facts ...
    "HttpConnection$KeyVal.inputStream(InputStream)"
      -> http connection httpconnection key val keyval input stream inputstream ...

Tokens that appear in almost every piece of code (def, self, return, import,
the, ...) are dropped: they carry no signal about WHICH code is involved.
"""

from __future__ import annotations

import re

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")

STOPWORDS = frozenset("""
a an and are as at be by for from has have in is it its of on or that the this to was were will with
not no yes if else elif then than so do does did done can could should would may might must also
def class return import self cls none true false pass lambda yield async await raise try except finally
while for in with assert global nonlocal del print args kwargs public private protected static final void
int float str bool new null this super extends implements package interface throws throw catch
x y z i j k n m v e f g h p q r s t u w l o b c d tmp val var obj value values item items key keys
""".split())


def split_identifier(word: str) -> list[str]:
    parts = []
    for chunk in word.split("_"):
        parts.extend(_CAMEL.findall(chunk))
    return [p.lower() for p in parts if p]


def tokenize(text: str) -> list[str]:
    out: list[str] = []
    for word in _WORD.findall(text):
        lower = word.lower()
        pieces = split_identifier(word)
        if len(pieces) > 1 and lower not in STOPWORDS and len(lower) > 1:
            out.append(lower)                      # keep the whole identifier as well
        out.extend(p for p in pieces if p not in STOPWORDS and len(p) > 1 and not p.isdigit())
    return out
