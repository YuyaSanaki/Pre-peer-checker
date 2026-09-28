"""原稿 bib ↔ ユーザー提供 PDF メタ突合 — P-REF-PDF-META-MISMATCH.

未紐付けは coverage skipped（Warning にしない）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pre_peer_checker.engine.ref_biblio import titles_similar
from pre_peer_checker.parsers.cited_paper_ingest import load_entry_payload
from pre_peer_checker.parsers.references import ReferenceBundle, ReferenceEntry, normalize_title
from pre_peer_checker.warnings import WarningItem, WarningTag


def _norm_doi(d: str | None) -> str | None:
    if not d:
        return None
    x = d.strip().lower().rstrip(".,;)")
    x = x.removeprefix("https://doi.org/").removeprefix("http://doi.org/")
    x = x.removeprefix("doi:")
    return x or None


def match_bib_to_pdfs(
    bundle: ReferenceBundle,
    entry_dirs: list[Path],
) -> dict[str, Any]:
    """Return linking report: matches, ambiguous, unmatched_bib, unmatched_pdf."""
    payloads = []
    for ed in entry_dirs:
        pl = load_entry_payload(ed)
        if pl:
            payloads.append(pl)

    matches: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    used_pdf: set[str] = set()

    def _candidates(entry: ReferenceEntry) -> list[tuple[str, dict[str, Any]]]:
        doi = _norm_doi(entry.doi)
        out: list[tuple[str, dict[str, Any]]] = []
        for pl in payloads:
            meta = pl.get("meta") or {}
            pdf_doi = _norm_doi(meta.get("doi"))
            if doi and pdf_doi and doi == pdf_doi:
                out.append(("doi", pl))
                continue
            if (
                entry.year
                and meta.get("year")
                and int(entry.year) == int(meta["year"])
                and titles_similar(entry.title, meta.get("title"))
            ):
                out.append(("title_year", pl))
                continue
            # filename heuristic
            src = str(meta.get("source_name") or Path(pl["path"]).name).lower()
            nt = normalize_title(entry.title)
            if nt and len(nt) >= 12:
                tokens = [t for t in nt.split() if len(t) > 4][:4]
                if tokens and all(t in src.replace("_", " ") for t in tokens[:2]):
                    out.append(("filename", pl))
        return out

    for entry in bundle.entries:
        cands = _candidates(entry)
        # unique by path
        by_path: dict[str, tuple[str, dict[str, Any]]] = {}
        for how, pl in cands:
            by_path[pl["path"]] = (how, pl)
        uniq = list(by_path.values())
        if not uniq:
            continue
        if len(uniq) > 1:
            ambiguous.append(
                {
                    "key": entry.key,
                    "candidates": [pl["path"] for _, pl in uniq],
                }
            )
            continue
        how, pl = uniq[0]
        used_pdf.add(pl["path"])
        matches.append(
            {
                "key": entry.key,
                "how": how,
                "pdf_path": pl["path"],
                "bib": entry.to_dict(),
                "pdf_meta": pl.get("meta") or {},
            }
        )

    matched_keys = {m["key"] for m in matches} | {a["key"] for a in ambiguous}
    unmatched_bib = [
        e.to_dict() for e in bundle.entries if e.key not in matched_keys
    ]
    unmatched_pdf = [
        {"path": pl["path"], "meta": pl.get("meta")}
        for pl in payloads
        if pl["path"] not in used_pdf
    ]
    return {
        "matches": matches,
        "ambiguous": ambiguous,
        "unmatched_bib": unmatched_bib,
        "unmatched_pdf": unmatched_pdf,
        "n_pdf_entries": len(payloads),
    }


def warnings_from_ref_pdf_meta(
    link_report: dict[str, Any],
    *,
    max_warnings: int = 15,
) -> list[WarningItem]:
    warnings: list[WarningItem] = []
    for m in link_report.get("matches") or []:
        bib = m.get("bib") or {}
        pdf = m.get("pdf_meta") or {}
        reasons: list[str] = []
        b_doi, p_doi = _norm_doi(bib.get("doi")), _norm_doi(pdf.get("doi"))
        if b_doi and p_doi and b_doi != p_doi:
            reasons.append(f"DOI 不一致: bib={b_doi} pdf={p_doi}")
        b_year, p_year = bib.get("year"), pdf.get("year")
        if b_year and p_year and int(b_year) != int(p_year):
            reasons.append(f"年不一致: bib={b_year} pdf={p_year}")
        if (
            bib.get("title")
            and pdf.get("title")
            and not titles_similar(bib.get("title"), pdf.get("title"))
            and m.get("how") == "doi"
        ):
            # Only when DOI matched but titles diverge strongly
            reasons.append(
                f"タイトル不一致: bib={bib.get('title')!r} pdf={pdf.get('title')!r}"
            )
        if not reasons:
            continue
        warnings.append(
            WarningItem(
                tag=WarningTag.REF_INCONSISTENCY,
                title=f"参考文献↔PDFメタ不一致: {m.get('key')}",
                location=f"References[{m.get('key')}]",
                reason="; ".join(reasons),
                sources=[str(m.get("pdf_path") or "")],
                metadata={
                    "pattern_id": "P-REF-PDF-META-MISMATCH",
                    "key": m.get("key"),
                    "how": m.get("how"),
                    "reasons": reasons,
                    "bib": bib,
                    "pdf_meta": pdf,
                },
            )
        )
        if len(warnings) >= max_warnings:
            break
    return warnings
