"""References 節・本文 in-text cite の抽出（規則本線）。

番号付き（[1] / 1.）と author–year の両系統を正規化する。
LLM フォールバックは呼び出し側の任意拡張に任せ、本モジュールは決定論のみ。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

_REF_HEAD_RE = re.compile(
    r"^(?:references?|bibliography|文献|参考文献)\s*$",
    re.I,
)
_SECTION_STOP_RE = re.compile(
    r"^(?:acknowledg?e?ments?|author\s+contributions?|funding|"
    r"competing\s+interests?|data\s+availability|"
    r"supplementa(?:l|ry)(?:\s+(?:information|materials?|figures?|tables?))?|"
    r"(?:star\W*|online\s+)?methods|materials?\s+and\s+methods|"
    r"key\s+resources\s+table|figure\s+legends?|figures?|tables?|"
    r"appendix|謝辞|著者貢献)\s*$",
    re.I,
)

# Figure legends / tables placed after the bibliography without their own heading.
_CAPTION_HEAD_RE = re.compile(
    r"^(?:(?:Extended\s+Data\s+|Supplementa(?:ry|l)\s+)?(?:Figure|Fig\.?)|Table)\s*S?\d+\s*[.:|]",
    re.I,
)

_NUM_ENTRY_RE = re.compile(
    r"^(?:\[(?P<b>\d+)\]|(?P<a>\d+)\.)\s*(?P<body>.+)$",
)
_DOI_RE = re.compile(r"\b(?:doi:\s*)?(10\.\d{4,9}/[^\s\]）,;]+)", re.I)
_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
_AUTHOR_YEAR_CITE_RE = re.compile(
    r"\((?P<authors>[A-Z][A-Za-z\-]+(?:\s+et\s+al\.?)?(?:\s*(?:&|and)\s+[A-Z][A-Za-z\-]+)?)"
    r"(?:,\s*|\s+)(?P<year>19\d{2}|20[0-4]\d)[a-z]?\)",
)
_BRACKET_CITE_RE = re.compile(r"\[(?P<nums>\d+(?:\s*[,;\-–]\s*\d+)*)\]")
_AUTHOR_YEAR_ENTRY_RE = re.compile(
    r"^(?P<authors>[A-Z][A-Za-z\-]+(?:,\s*[A-Z]\.?)*(?:\s+et\s+al\.?)?)"
    r".*?\((?P<year>(?:19|20)\d{2})[a-z]?\)\s*(?P<body>.+)$",
)


@dataclass
class ReferenceEntry:
    key: str
    raw: str
    authors: str | None = None
    year: int | None = None
    title: str | None = None
    journal: str | None = None
    doi: str | None = None
    style: str = "numbered"  # numbered | author_year

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class InTextCite:
    keys: list[str]
    span: str
    paragraph: str
    style: str  # numbered | author_year
    authors: str | None = None
    year: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReferenceBundle:
    entries: list[ReferenceEntry] = field(default_factory=list)
    in_text: list[InTextCite] = field(default_factory=list)
    references_section_found: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "entries": [e.to_dict() for e in self.entries],
            "in_text": [c.to_dict() for c in self.in_text],
            "references_section_found": self.references_section_found,
            "n_entries": len(self.entries),
            "n_in_text": len(self.in_text),
        }


def _normalize_doi(raw: str | None) -> str | None:
    if not raw:
        return None
    d = raw.strip().rstrip(".,;)")
    d = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", d, flags=re.I)
    return d or None


def _guess_title(body: str) -> str | None:
    """Heuristic: first sentence-like chunk after authors/year."""
    t = body.strip()
    t = _DOI_RE.sub("", t)
    t = re.sub(r"\s+", " ", t).strip(" .;")
    if len(t) < 8:
        return None
    # Prefer quoted title
    m = re.search(r"[\"“](.+?)[\"”]", t)
    if m and len(m.group(1)) >= 8:
        return m.group(1).strip()
    # Take up to first period before journal-ish token
    parts = re.split(r"\.\s+", t, maxsplit=1)
    cand = parts[0].strip()
    if 8 <= len(cand) <= 220:
        return cand
    return t[:180] if t else None


def _parse_entry_body(key: str, body: str, *, style: str) -> ReferenceEntry:
    doi_m = _DOI_RE.search(body)
    doi = _normalize_doi(doi_m.group(1) if doi_m else None)
    years = [int(y) for y in _YEAR_RE.findall(body)]
    year = years[0] if years else None
    authors = None
    am = re.match(
        r"^([A-Z][A-Za-z\-]+(?:,\s*[A-Z]\.?)*(?:\s+(?:et\s+al\.?|&|and)\s+[A-Z][A-Za-z\-]+)*)",
        body.strip(),
    )
    if am:
        authors = am.group(1).strip().rstrip(",")
    title = _guess_title(body)
    return ReferenceEntry(
        key=key,
        raw=body.strip(),
        authors=authors,
        year=year,
        title=title,
        doi=doi,
        style=style,
    )


def extract_references_section(paragraphs: list[str]) -> list[str]:
    """Return paragraph texts belonging to the References section."""
    started = False
    out: list[str] = []
    for p in paragraphs:
        t = (p or "").strip()
        if not t:
            continue
        if not started:
            if _REF_HEAD_RE.match(t):
                started = True
            continue
        if _SECTION_STOP_RE.match(t) or _CAPTION_HEAD_RE.match(t):
            break
        out.append(t)
    return out


def parse_reference_entries(ref_paragraphs: list[str]) -> list[ReferenceEntry]:
    """Parse numbered or author–year bibliography lines."""
    entries: list[ReferenceEntry] = []
    buf_key: str | None = None
    buf_parts: list[str] = []
    buf_style = "numbered"

    def _flush() -> None:
        nonlocal buf_key, buf_parts, buf_style
        if buf_key is None or not buf_parts:
            buf_key, buf_parts = None, []
            return
        body = " ".join(buf_parts).strip()
        entries.append(_parse_entry_body(buf_key, body, style=buf_style))
        buf_key, buf_parts = None, []

    for para in ref_paragraphs:
        t = para.strip()
        if not t:
            continue
        m = _NUM_ENTRY_RE.match(t)
        if m:
            _flush()
            buf_key = m.group("b") or m.group("a")
            buf_style = "numbered"
            buf_parts = [m.group("body").strip()]
            continue
        ay = _AUTHOR_YEAR_ENTRY_RE.match(t)
        if ay and buf_key is None:
            _flush()
            authors = ay.group("authors").split(",")[0].strip()
            year = ay.group("year")
            buf_key = f"{authors.lower()}_{year}"
            buf_style = "author_year"
            buf_parts = [t]
            continue
        if buf_key is not None:
            buf_parts.append(t)
        elif ay:
            authors = ay.group("authors").split(",")[0].strip()
            year = ay.group("year")
            entries.append(
                _parse_entry_body(
                    f"{authors.lower()}_{year}",
                    t,
                    style="author_year",
                )
            )
    _flush()
    return entries


def _expand_num_list(blob: str) -> list[str]:
    keys: list[str] = []
    for part in re.split(r"[,;]", blob):
        part = part.strip()
        if not part:
            continue
        if re.match(r"^\d+$", part):
            keys.append(str(int(part)))
            continue
        m = re.match(r"^(\d+)\s*[\-–]\s*(\d+)$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if 0 < b - a <= 20:
                keys.extend(str(i) for i in range(a, b + 1))
            else:
                keys.append(str(a))
                keys.append(str(b))
    return keys


def extract_in_text_cites(paragraphs: list[str]) -> list[InTextCite]:
    """Extract in-text citations from body paragraphs (skip References section).

    Text after the bibliography (e.g. STAR★Methods) is still body text.
    """
    body: list[str] = []
    in_refs = False
    for p in paragraphs:
        t = (p or "").strip()
        if not t:
            continue
        if _REF_HEAD_RE.match(t):
            in_refs = True
            continue
        if in_refs:
            if _SECTION_STOP_RE.match(t) or _CAPTION_HEAD_RE.match(t):
                in_refs = False
            else:
                continue
        body.append(t)

    cites: list[InTextCite] = []
    seen: set[tuple[str, str]] = set()
    for para in body:
        for m in _BRACKET_CITE_RE.finditer(para):
            keys = _expand_num_list(m.group("nums"))
            if not keys:
                continue
            span = m.group(0)
            sig = (span, para[:80])
            if sig in seen:
                continue
            seen.add(sig)
            cites.append(
                InTextCite(keys=keys, span=span, paragraph=para, style="numbered")
            )
        for m in _AUTHOR_YEAR_CITE_RE.finditer(para):
            authors = m.group("authors").strip()
            year = int(m.group("year"))
            first = re.split(r"\s+et\s+al|\s+&|\s+and|,", authors, flags=re.I)[0].strip()
            key = f"{first.lower()}_{year}"
            span = m.group(0)
            sig = (span, para[:80])
            if sig in seen:
                continue
            seen.add(sig)
            cites.append(
                InTextCite(
                    keys=[key],
                    span=span,
                    paragraph=para,
                    style="author_year",
                    authors=authors,
                    year=year,
                )
            )
    return cites


def parse_references_from_paragraphs(paragraphs: list[str]) -> ReferenceBundle:
    ref_paras = extract_references_section(paragraphs)
    entries = parse_reference_entries(ref_paras) if ref_paras else []
    in_text = extract_in_text_cites(paragraphs)
    return ReferenceBundle(
        entries=entries,
        in_text=in_text,
        references_section_found=bool(ref_paras),
    )


def paragraphs_from_docx(path) -> list[str]:
    from docx import Document

    doc = Document(str(path))
    return [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]


def parse_references_from_docx(path) -> ReferenceBundle:
    return parse_references_from_paragraphs(paragraphs_from_docx(path))


def normalize_title(title: str | None) -> str:
    if not title:
        return ""
    t = title.lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t
