"""H3 実コーパス適合率（合成ベースライン + 手元過去論文コーパス）.

定義（製品「100%」主張ではない）:

- **gold_item_recall**: 合成 ``image_reuse`` / ``image_partial`` の required gold 再現率
- **citation_suppression**: 出典あり fixture で IMAGE_REUSE Warning が 0 かつ cited 記録あり
- **distractor_fp_pairs**: 無関係コーパスとの交差ヒット数（低いほど良い）
- **known_corpus_h3**: private_benchmark + 関連コーパスで H3 gold が scored/matched か

出力: ``outputs/metrics/h3_corpus_report.json``（git 外想定）。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pre_peer_checker.eval.gold_eval import (
    REPO_ROOT,
    load_manifest,
    resolve_case_focus_inputs,
    run_and_evaluate,
)
from pre_peer_checker.pipeline.orchestrator import run_verification
from pre_peer_checker.warnings import WarningTag

SYN_REUSE_MS = REPO_ROOT / "fixtures" / "synthetic" / "image_reuse" / "manuscript"
SYN_REUSE_CITED = (
    REPO_ROOT / "fixtures" / "synthetic" / "image_reuse" / "manuscript_cited"
)
SYN_REUSE_CORPUS = REPO_ROOT / "fixtures" / "synthetic" / "image_reuse" / "corpus"
SYN_PARTIAL_MS = REPO_ROOT / "fixtures" / "synthetic" / "image_partial" / "manuscript"
SYN_PARTIAL_CORPUS = REPO_ROOT / "fixtures" / "synthetic" / "image_partial" / "corpus"
CHECK_REF = REPO_ROOT / "check_reference"
PRIVATE_BM = REPO_ROOT / "input" / "private_benchmark"


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _h3_warnings(result) -> list:
    return [
        w
        for w in result.warnings
        if w.tag == WarningTag.IMAGE_REUSE
        and w.metadata.get("corpus_match")
    ]


def evaluate_synthetic_baseline(*, out_dir: Path) -> dict[str, Any]:
    """CI-equivalent synthetic H3 metrics (no real PDFs)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    reuse = run_and_evaluate(
        "image_reuse",
        input_paths=[SYN_REUSE_MS],
        corpus_paths=[SYN_REUSE_CORPUS],
        corpus_present=True,
        warnings_out=out_dir / "image_reuse_warnings.json",
    )
    partial = run_and_evaluate(
        "image_partial",
        input_paths=[SYN_PARTIAL_MS],
        corpus_paths=[SYN_PARTIAL_CORPUS],
        corpus_present=True,
        warnings_out=out_dir / "image_partial_warnings.json",
    )

    cited_run = run_verification([SYN_REUSE_CITED], corpus=[SYN_REUSE_CORPUS])
    h3_warns = _h3_warnings(cited_run)
    arts = cited_run.artifacts.get("corpus_scan") or {}
    cited_n = len(arts.get("cited_matches") or []) + len(
        arts.get("cited_partial_matches") or []
    )
    pairs = arts.get("cross_match_pairs") or []

    return {
        "image_reuse": {
            "required_recall": reuse.get("required_recall"),
            "precision": reuse.get("precision"),
            "recall": reuse.get("recall"),
            "misses": reuse.get("misses"),
        },
        "image_partial": {
            "required_recall": partial.get("required_recall"),
            "precision": partial.get("precision"),
            "recall": partial.get("recall"),
            "misses": partial.get("misses"),
        },
        "citation_suppression": {
            "ok": len(h3_warns) == 0 and cited_n > 0,
            "image_reuse_warnings": len(h3_warns),
            "cited_artifact_pairs": cited_n,
            "cross_match_pairs": len(pairs),
        },
    }


def _private_manifest() -> dict[str, Any]:
    try:
        return load_manifest("private_benchmark")
    except FileNotFoundError:
        return {}


def _private_benchmark_inputs() -> list[Path] | None:
    """Focus slice + optional ``h3_extra_inputs`` (e.g. Supp figure PDFs) from the manifest."""
    paths = resolve_case_focus_inputs("private_benchmark")
    if not paths:
        return [PRIVATE_BM] if PRIVATE_BM.exists() else None
    if paths == [PRIVATE_BM]:
        return paths
    for rel in _private_manifest().get("h3_extra_inputs") or []:
        p = PRIVATE_BM / rel
        if p.is_file():
            paths.append(p)
    return paths


def ingest_pdfs_to_library(
    pdfs: list[Path],
    *,
    library_root: Path | None = None,
) -> list[dict[str, Any]]:
    from pre_peer_checker.imaging.past_paper_ingest import ingest_past_paper_pdf

    entries: list[dict[str, Any]] = []
    for pdf in pdfs:
        if not pdf.is_file():
            continue
        try:
            result = ingest_past_paper_pdf(pdf, library_root=library_root)
            if not result.get("ok"):
                entries.append(
                    {"error": result.get("error"), "source": str(pdf), "ok": False}
                )
                continue
            summary = dict(result.get("entry") or {})
            summary["ok"] = True
            summary["id"] = summary.get("id") or (result.get("meta") or {}).get("id")
            summary["source_path"] = str(pdf)
            entries.append(summary)
        except Exception as exc:  # noqa: BLE001
            entries.append({"error": str(exc), "source": str(pdf), "ok": False})
    return entries


def _related_substring() -> str | None:
    """Filename substring of the check_reference PDF related to the private benchmark."""
    value = _private_manifest().get("related_reference_substring")
    return str(value) if value else None


def select_check_reference_pdfs(
    *,
    related_substring: str | None = None,
    max_distractors: int = 3,
) -> tuple[Path | None, list[Path]]:
    """Pick related PubPeer PDF (if any) + unrelated distractors."""
    if not CHECK_REF.is_dir():
        return None, []
    related_substring = related_substring or _related_substring()
    pdfs = sorted(CHECK_REF.glob("PubPeer*.pdf"))
    related = None
    distractors: list[Path] = []
    for p in pdfs:
        if related_substring and related_substring.lower() in p.name.lower():
            related = p
        elif len(distractors) < max_distractors:
            distractors.append(p)
    return related, distractors


def collect_h3_query_images(
    manuscript_root: Path,
    *,
    max_files: int = 40,
) -> list[Path]:
    """Prefer publication Fig/Supp embeds, then confocal exemplars (not every Rplot)."""
    from pre_peer_checker.parsers.pdf_images import export_embedded_images

    root = manuscript_root
    if (root / "Manuscript").is_dir():
        root = root / "Manuscript"
    found: list[Path] = []
    # Export embeds from top-level Fig*.pdf / *Supp*.pdf into a stable cache dir
    fig_pdfs = sorted(root.glob("Fig*.pdf")) + sorted(root.glob("*Supp*.pdf"))
    # Dedupe paths
    seen_pdf: set[str] = set()
    uniq_pdfs: list[Path] = []
    for p in fig_pdfs:
        key = str(p.resolve())
        if key in seen_pdf:
            continue
        seen_pdf.add(key)
        uniq_pdfs.append(p)
    cache = REPO_ROOT / "cache" / "h3_query_embeds"
    for pdf in uniq_pdfs:
        dest = cache / pdf.stem
        dest.mkdir(parents=True, exist_ok=True)
        try:
            # Reuse existing exports when present
            existing = sorted(dest.glob("*.png"))
            if len(existing) >= 3:
                embeds = existing
            else:
                embeds = export_embedded_images(
                    pdf, dest, min_side=80, max_images=40
                )
        except Exception:  # noqa: BLE001
            embeds = []
        for e in embeds:
            found.append(e)
            if len(found) >= max_files:
                return found[:max_files]
    # Confocal / raster exemplars under data/
    data = root / "data"
    if data.is_dir():
        for ext in ("*.jpg", "*.jpeg", "*.png", "*.tif", "*.tiff"):
            for p in sorted(data.rglob(ext)):
                if "rplot" in p.name.lower():
                    continue
                found.append(p)
                if len(found) >= max_files:
                    return _dedupe_paths(found, max_files)
    if len(found) < 5:
        for ext in ("*.png", "*.jpg", "*.tif"):
            for p in sorted(root.rglob(ext)):
                found.append(p)
                if len(found) >= max_files:
                    break
    return _dedupe_paths(found, max_files)


def _dedupe_paths(paths: list[Path], max_files: int) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for p in paths:
        key = str(p.resolve())
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
        if len(out) >= max_files:
            break
    return out


def evaluate_with_corpus_roots(
    manuscript_paths: list[Path],
    corpus_roots: list[Path],
    *,
    label: str,
    prefer_dino: bool = True,
    enable_partial: bool = False,
    max_query: int = 30,
    max_corpus: int = 40,
) -> dict[str, Any]:
    """Lightweight H3 scan (no full orchestrator / Excel)."""
    from pre_peer_checker.imaging.corpus_scan import scan_against_corpus

    # Resolve a manuscript root for query collection
    ms_root = PRIVATE_BM if PRIVATE_BM.exists() else manuscript_paths[0]
    queries = collect_h3_query_images(ms_root, max_files=max_query)
    # Also accept explicit image paths from manuscript_paths
    for p in manuscript_paths:
        if p.is_file() and p.suffix.lower() in {
            ".png",
            ".jpg",
            ".jpeg",
            ".tif",
            ".tiff",
            ".pdf",
            ".lif",
            ".czi",
        }:
            if p not in queries:
                queries.append(p)

    result = scan_against_corpus(
        queries,
        corpus_roots,
        legend_has_citation=False,
        max_query=max_query,
        max_corpus=max_corpus,
        prefer_dino=prefer_dino,
        prefer_lightglue=prefer_dino,
        enable_partial=enable_partial,
    )
    arts = result.artifacts
    pairs = list(arts.get("cross_match_pairs") or [])
    return {
        "label": label,
        "corpus_present": bool(result.corpus_present),
        "corpus_files": len(arts.get("corpus_files") or []),
        "query_files": len(queries),
        "query_previews": arts.get("query_previews"),
        "cross_match_pairs": len(pairs),
        "full_pairs": sum(1 for p in pairs if p.get("kind") == "full"),
        "partial_pairs": sum(1 for p in pairs if p.get("kind") == "partial"),
        "h3_warnings": len(result.warnings),
        "scan_method": arts.get("scan_method"),
        "note": arts.get("note"),
        "pair_samples": pairs[:12],
        "enable_partial": enable_partial,
        "prefer_dino": prefer_dino,
    }


def evaluate_private_benchmark_h3(
    corpus_roots: list[Path],
    *,
    out_dir: Path,
    heavy: bool = False,
) -> dict[str, Any]:
    """Score private_benchmark gold with corpus — optional (heavy full pipeline)."""
    if not heavy:
        return {
            "skipped": True,
            "reason": "full private_benchmark gold_eval skipped (use --heavy); "
            "pair-level related_scan is the primary real-corpus metric",
        }
    inputs = _private_benchmark_inputs()
    if not inputs:
        return {"skipped": True, "reason": "private_benchmark inputs missing"}
    out_dir.mkdir(parents=True, exist_ok=True)
    report = run_and_evaluate(
        "private_benchmark",
        input_paths=inputs,
        corpus_paths=corpus_roots,
        corpus_present=True,
        warnings_out=out_dir / "private_benchmark_h3_warnings.json",
    )
    items = report.get("items") or []
    h3 = next((i for i in items if i.get("id") == "H3"), None)
    return {
        "required_recall": report.get("required_recall"),
        "precision": report.get("precision"),
        "h3_item": h3,
        "corpus_present_scored": True,
    }


def build_h3_report(
    *,
    out_dir: Path | None = None,
    ingest: bool = True,
    library_root: Path | None = None,
    heavy_gold: bool = False,
    reuse_library: bool = True,
) -> dict[str, Any]:
    """Full H3 fit-rate report: synthetic + optional real check_reference ingest."""
    out = out_dir or (REPO_ROOT / "outputs" / "metrics")
    out.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "schema_version": "1.0",
        "generated_at": _utc(),
        "definitions": {
            "gold_item_recall": "synthetic image_reuse / image_partial required_recall",
            "citation_suppression": "cited fixture: zero IMAGE_REUSE warnings + cited artifacts",
            "distractor_fp_pairs": "cross_match_pairs vs unrelated PubPeer figures (lower better)",
            "related_hit_pairs": "cross_match_pairs vs the benchmark-related PubPeer extract",
            "known_corpus_h3": "optional heavy private_benchmark H3 gold (--heavy)",
        },
        "synthetic": evaluate_synthetic_baseline(out_dir=out / "h3_synthetic"),
    }

    related_pdf, distractor_pdfs = select_check_reference_pdfs()
    report["check_reference"] = {
        "related_pdf": str(related_pdf) if related_pdf else None,
        "distractor_pdfs": [str(p) for p in distractor_pdfs],
    }

    ms = _private_benchmark_inputs()
    if not ms:
        report["real_corpus"] = {"skipped": True, "reason": "no private_benchmark"}
        (out / "h3_corpus_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return report

    real: dict[str, Any] = {"ingest": ingest, "heavy_gold": heavy_gold}
    lib = library_root or (REPO_ROOT / "cache" / "past_papers")
    from pre_peer_checker.imaging.past_paper_ingest import list_entries, resolve_corpus_roots

    if ingest and (related_pdf or distractor_pdfs):
        existing_names = {str(e.get("source_name") or "") for e in list_entries(lib)}
        to_ingest: list[Path] = []
        for pdf in ([related_pdf] if related_pdf else []) + list(distractor_pdfs):
            if reuse_library and pdf.name in existing_names:
                continue
            to_ingest.append(pdf)
        if to_ingest:
            ingested = ingest_pdfs_to_library(to_ingest, library_root=lib)
            real["ingested_now"] = [
                {
                    "id": e.get("id"),
                    "source_name": e.get("source_name") or e.get("source"),
                    "n_figures": e.get("n_figures"),
                    "error": e.get("error"),
                    "ok": e.get("ok"),
                }
                for e in ingested
            ]
        else:
            real["ingested_now"] = []
            real["reused_library"] = True

        entries = list_entries(lib)

        related_sub = (_related_substring() or "").lower()

        def _is_related(e: dict[str, Any]) -> bool:
            if not related_sub:
                return False
            blob = f"{e.get('source_name') or ''} {e.get('title') or ''}"
            return related_sub in blob.lower()

        related_entries = [e for e in entries if _is_related(e) and e.get("id")]
        distractor_entries = [
            e for e in entries if not _is_related(e) and e.get("id")
        ][:3]
        related_id = str(related_entries[0]["id"]) if related_entries else None
        distractor_ids = [str(e["id"]) for e in distractor_entries]
        real["library_entries"] = [
            {
                "id": e.get("id"),
                "source_name": e.get("source_name"),
                "n_figures": e.get("n_figures"),
            }
            for e in entries
        ]

        if distractor_ids:
            d_roots = resolve_corpus_roots(distractor_ids, library_root=lib)
            real["distractor"] = evaluate_with_corpus_roots(
                ms,
                d_roots,
                label="unrelated_pubpeer_distractors",
                prefer_dino=True,
                enable_partial=False,
            )
        if related_id:
            r_roots = resolve_corpus_roots([related_id], library_root=lib)
            real["related_scan"] = evaluate_with_corpus_roots(
                ms,
                r_roots,
                label="related_pubpeer",
                prefer_dino=True,
                enable_partial=False,
            )
            real["private_benchmark_gold"] = evaluate_private_benchmark_h3(
                r_roots, out_dir=out / "h3_private", heavy=heavy_gold
            )
        real["library_root"] = str(lib)
    else:
        real["skipped_ingest"] = True

    report["real_corpus"] = real
    report["summary"] = {
        "synthetic_reuse_required_recall": (report["synthetic"].get("image_reuse") or {}).get(
            "required_recall"
        ),
        "synthetic_partial_required_recall": (
            report["synthetic"].get("image_partial") or {}
        ).get("required_recall"),
        "citation_suppression_ok": (report["synthetic"].get("citation_suppression") or {}).get(
            "ok"
        ),
        "distractor_fp_pairs": (real.get("distractor") or {}).get("cross_match_pairs"),
        "related_hit_pairs": (real.get("related_scan") or {}).get("cross_match_pairs"),
        "h3_gold_status": (
            ((real.get("private_benchmark_gold") or {}).get("h3_item") or {}).get("status")
            if heavy_gold
            else "skipped_heavy"
        ),
        "note": (
            "related_hit_pairs uses Fig/Supp embedded exports as queries when available; "
            "raw confocal-only queries can under-detect vs PubPeer page embeds"
        ),
    }

    path = out / "h3_corpus_report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report["report_path"] = str(path)
    return report
