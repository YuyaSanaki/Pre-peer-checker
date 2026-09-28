"""引用主張の規則抽出スキーマ（LLM フォールバック用の形も定義）。

規則本線: in-text cite 近傍文から claim span を取る。
LLM は空のときだけ呼び出し側で補充可能。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from pre_peer_checker.parsers.references import InTextCite

CLAIM_CITATION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["claims"],
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["span", "cite_keys"],
                "properties": {
                    "span": {"type": "string"},
                    "cite_keys": {"type": "array", "items": {"type": "string"}},
                    "asserted_facts": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string"},
                                "value": {"type": "string"},
                            },
                        },
                    },
                    "paraphrase": {"type": "string"},
                },
            },
        }
    },
}

_NUM_FACT_RE = re.compile(
    r"(?P<val>\d+(?:\.\d+)?)\s*(?P<unit>%|fold|×|x)?",
    re.I,
)
_POLARITY_UP = re.compile(
    r"\b(increas\w*|upregulat\w*|elevat\w*|higher|greater|上昇|増加)\b",
    re.I,
)
_POLARITY_DOWN = re.compile(
    r"\b(decreas\w*|downregulat\w*|reduc\w*|lower|fewer|低下|減少)\b",
    re.I,
)
_YEAR_FACT_RE = re.compile(r"\b((?:19|20)\d{2})\b")


@dataclass
class AssertedFact:
    type: str  # numeric | polarity_up | polarity_down | year
    value: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CitationClaim:
    span: str
    cite_keys: list[str]
    paragraph: str
    asserted_facts: list[AssertedFact] = field(default_factory=list)
    paraphrase: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "span": self.span,
            "cite_keys": list(self.cite_keys),
            "paragraph": self.paragraph,
            "asserted_facts": [f.to_dict() for f in self.asserted_facts],
            "paraphrase": self.paraphrase,
        }


def _facts_from_text(text: str) -> list[AssertedFact]:
    facts: list[AssertedFact] = []
    for m in _NUM_FACT_RE.finditer(text):
        val = m.group("val")
        unit = (m.group("unit") or "").strip()
        # skip bare citation numbers in brackets context — caller passes claim window
        facts.append(AssertedFact(type="numeric", value=f"{val}{unit}"))
    if _POLARITY_UP.search(text):
        facts.append(AssertedFact(type="polarity_up", value="increase"))
    if _POLARITY_DOWN.search(text):
        facts.append(AssertedFact(type="polarity_down", value="decrease"))
    for y in _YEAR_FACT_RE.findall(text):
        facts.append(AssertedFact(type="year", value=y))
    return facts


def claims_from_in_text(cites: list[InTextCite]) -> list[CitationClaim]:
    """Build claims from in-text citation windows (sentence around cite)."""
    out: list[CitationClaim] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for c in cites:
        para = c.paragraph
        # sentence window: split on . ; and take the one containing span
        parts = re.split(r"(?<=[.;])\s+", para)
        span = c.span
        window = para
        for part in parts:
            if c.span in part:
                window = part.strip()
                break
        key = (window, tuple(c.keys))
        if key in seen:
            continue
        seen.add(key)
        # Strip the citation marker itself from fact mining to reduce noise
        fact_src = window.replace(c.span, " ")
        out.append(
            CitationClaim(
                span=window if window else span,
                cite_keys=list(c.keys),
                paragraph=para,
                asserted_facts=_facts_from_text(fact_src),
            )
        )
    return out
