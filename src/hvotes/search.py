"""Case-insensitive, word-boundary keyword search over extracted text + metadata."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .extract import Extraction


@dataclass
class Hit:
    keyword: str
    page: int | None     # None for metadata/title matches
    location: str        # "document" | "title" | "metadata"
    snippet: str


def compile_patterns(keywords: list[str]) -> dict[str, re.Pattern]:
    """One regex per keyword. Spaces -> \\s+ so line wraps still match; word
    boundaries so 'plat' doesn't match 'platform' and 'CDBG' not 'CDBGs'."""
    pats: dict[str, re.Pattern] = {}
    for kw in keywords:
        parts = [re.escape(tok) for tok in kw.split()]
        body = r"\s+".join(parts)
        pats[kw] = re.compile(rf"\b{body}\b", re.IGNORECASE)
    return pats


def _snippet(text: str, start: int, end: int, width: int) -> str:
    half = max(0, (width - (end - start)) // 2)
    s = max(0, start - half)
    e = min(len(text), end + half)
    snip = text[s:e].replace("\n", " ")
    snip = re.sub(r"\s+", " ", snip).strip()
    if s > 0:
        snip = "… " + snip
    if e < len(text):
        snip = snip + " …"
    return snip


def _scan(text: str, page: int | None, location: str,
          patterns: dict[str, re.Pattern], width: int) -> list[Hit]:
    hits: list[Hit] = []
    for kw, pat in patterns.items():
        for m in pat.finditer(text):
            hits.append(Hit(keyword=kw, page=page, location=location,
                            snippet=_snippet(text, m.start(), m.end(), width)))
    return hits


def search_extraction(ex: Extraction, patterns: dict[str, re.Pattern],
                      width: int) -> list[Hit]:
    hits: list[Hit] = []
    for pt in ex.pages:
        if pt.text:
            hits.extend(_scan(pt.text, pt.page, "document", patterns, width))
    return hits


def search_metadata(title: str, keywords_field: str,
                    patterns: dict[str, re.Pattern], width: int) -> list[Hit]:
    hits: list[Hit] = []
    if title:
        hits.extend(_scan(title, None, "title", patterns, width))
    if keywords_field:
        hits.extend(_scan(keywords_field, None, "metadata", patterns, width))
    return hits
