"""引用主張 ↔ 提供 PDF チャンクのスコープ検索と矛盾ゲート.

- Soft: citation_evidence_reviews（情報カード）
- Hard: P-REF-CLAIM-CONTRADICTION（年・数値・極性の決定論矛盾のみ）
"""

from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

from pre_peer_checker.llm.claim_cite_schema import CitationClaim
from pre_peer_checker.parsers.cited_paper_ingest import load_entry_payload
from pre_peer_checker.warnings import WarningItem, WarningTag

_TOKEN_RE = re.compile(r"[a-z0-9]+", re.I)
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_POLARITY_UP = re.compile(
    r"\b(increas\w*|upregulat\w*|elevat\w*|higher|greater)\b", re.I
)
_POLARITY_DOWN = re.compile(
    r"\b(decreas\w*|downregulat\w*|reduc\w*|lower|fewer)\b", re.I
)


def _tokenize(text: str) -> Counter[str]:
    return Counter(t.lower() for t in _TOKEN_RE.findall(text or "") if len(t) > 2)


def _bm25_score(query: Counter[str], doc: Counter[str], *, avgdl: float, N: int, df: dict[str, int]) -> float:
    if not query or not doc:
        return 0.0
    k1, b = 1.2, 0.75
    dl = sum(doc.values()) or 1
    score = 0.0
    for term, qf in query.items():
        if term not in doc:
            continue
        n_q = df.get(term, 0) or 1
        idf = math.log(1 + (N - n_q + 0.5) / (n_q + 0.5))
        tf = doc[term]
        score += idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / max(avgdl, 1.0))) * qf
    return float(score)


def _chunks_for_keys(
    cite_keys: list[str],
    key_to_pdf: dict[str, Path],
) -> list[dict[str, Any]]:
    """Collect chunks only from PDFs linked to the claim's cite keys."""
    paths: list[Path] = []
    seen: set[str] = set()
    for k in cite_keys:
        p = key_to_pdf.get(k)
        if p is None:
            continue
        rp = str(Path(p).resolve())
        if rp in seen:
            continue
        seen.add(rp)
        paths.append(Path(p))
    out: list[dict[str, Any]] = []
    for p in paths:
        pl = load_entry_payload(p)
        if not pl:
            continue
        for ch in pl.get("chunks") or []:
            out.append(
                {
                    "text": ch.get("text") or "",
                    "i": ch.get("i"),
                    "pdf_path": pl["path"],
                    "meta": pl.get("meta") or {},
                }
            )
    return out


def retrieve_passages(
    claim: CitationClaim,
    key_to_pdf: dict[str, Path],
    *,
    top_k: int = 3,
) -> list[dict[str, Any]]:
    corpus = _chunks_for_keys(claim.cite_keys, key_to_pdf)
    if not corpus:
        return []
    q = _tokenize(claim.span)
    docs = [_tokenize(c["text"]) for c in corpus]
    N = len(docs)
    avgdl = sum(sum(d.values()) for d in docs) / max(N, 1)
    df: dict[str, int] = {}
    for d in docs:
        for t in d:
            df[t] = df.get(t, 0) + 1
    scored: list[tuple[float, dict[str, Any]]] = []
    for c, d in zip(corpus, docs):
        s = _bm25_score(q, d, avgdl=avgdl, N=N, df=df)
        scored.append((s, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    out = []
    for s, c in scored[:top_k]:
        if s <= 0:
            continue
        out.append(
            {
                "score": round(s, 4),
                "text": (c.get("text") or "")[:600],
                "pdf_path": c.get("pdf_path"),
                "chunk_i": c.get("i"),
            }
        )
    return out


def build_key_to_pdf(link_report: dict[str, Any]) -> dict[str, Path]:
    mapping: dict[str, Path] = {}
    for m in link_report.get("matches") or []:
        key = str(m.get("key") or "")
        path = m.get("pdf_path")
        if key and path:
            mapping[key] = Path(path)
    return mapping


def _numeric_contradiction(claim_vals: list[str], passage: str) -> str | None:
    """Flag if claim asserts a %/number that passage asserts a clearly different one nearby."""
    p_nums = set(_NUM_RE.findall(passage))
    for raw in claim_vals:
        m = _NUM_RE.search(raw)
        if not m:
            continue
        v = m.group(0)
        # only high-signal units
        if "%" not in raw and "fold" not in raw.lower():
            continue
        if v in p_nums:
            continue
        # passage has another percentage?
        pcts = re.findall(r"(\d+(?:\.\d+)?)\s*%", passage)
        if pcts and v not in pcts:
            return f"数値主張 {raw} が根拠文の {', '.join(x + '%' for x in pcts)} と不一致"
    return None


def _polarity_contradiction(claim: CitationClaim, passage: str) -> str | None:
    types = {f.type for f in claim.asserted_facts}
    has_up = "polarity_up" in types
    has_down = "polarity_down" in types
    if has_up and has_down:
        return None
    p_up = bool(_POLARITY_UP.search(passage))
    p_down = bool(_POLARITY_DOWN.search(passage))
    if has_up and p_down and not p_up:
        return "本文は増加を主張するが根拠文は減少表現"
    if has_down and p_up and not p_down:
        return "本文は減少を主張するが根拠文は増加表現"
    return None


def _year_contradiction(claim: CitationClaim, passage: str, pdf_year: int | None) -> str | None:
    claim_years = [int(f.value) for f in claim.asserted_facts if f.type == "year"]
    if not claim_years:
        return None
    # Only when claim states a year as a finding about the cited work's result year
    # and PDF meta year differs — weak; require passage also has a year conflict
    p_years = {int(y) for y in re.findall(r"\b((?:19|20)\d{2})\b", passage)}
    for y in claim_years:
        if pdf_year and y != int(pdf_year) and pdf_year in p_years:
            return f"主張の年 {y} が PDF メタ年 {pdf_year} と矛盾"
    return None


def review_claims_against_pdfs(
    claims: list[CitationClaim],
    link_report: dict[str, Any],
    *,
    top_k: int = 3,
) -> tuple[list[dict[str, Any]], list[WarningItem]]:
    """Return (evidence_reviews, hard_warnings)."""
    key_to_pdf = build_key_to_pdf(link_report)
    reviews: list[dict[str, Any]] = []
    warnings: list[WarningItem] = []

    for claim in claims:
        passages = retrieve_passages(claim, key_to_pdf, top_k=top_k)
        linked = [k for k in claim.cite_keys if k in key_to_pdf]
        review = {
            "span": claim.span,
            "cite_keys": list(claim.cite_keys),
            "linked_keys": linked,
            "asserted_facts": [f.to_dict() for f in claim.asserted_facts],
            "passages": passages,
            "status": (
                "no_pdf"
                if not linked
                else ("no_passage" if not passages else "reviewed")
            ),
        }
        reviews.append(review)

        if not passages:
            continue
        top = passages[0]
        text = top.get("text") or ""
        pdf_meta = {}
        for m in link_report.get("matches") or []:
            if m.get("key") in claim.cite_keys:
                pdf_meta = m.get("pdf_meta") or {}
                break
        pdf_year = pdf_meta.get("year")
        reasons: list[str] = []
        num_vals = [f.value for f in claim.asserted_facts if f.type == "numeric"]
        r = _numeric_contradiction(num_vals, text)
        if r:
            reasons.append(r)
        r = _polarity_contradiction(claim, text)
        if r:
            reasons.append(r)
        r = _year_contradiction(claim, text, int(pdf_year) if pdf_year else None)
        if r:
            reasons.append(r)
        if not reasons:
            continue
        warnings.append(
            WarningItem(
                tag=WarningTag.REF_INCONSISTENCY,
                title="引用主張と参考文献 PDF の内容が矛盾",
                location=claim.span[:80],
                reason="; ".join(reasons),
                sources=[str(top.get("pdf_path") or "")],
                metadata={
                    "pattern_id": "P-REF-CLAIM-CONTRADICTION",
                    "cite_keys": list(claim.cite_keys),
                    "reasons": reasons,
                    "passage": text[:400],
                    "score": top.get("score"),
                },
            )
        )
    return reviews, warnings
