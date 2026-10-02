"""ゴールド Warning との突合（適合率・再現率の集計）。

製品は任意論文セット向け。本モジュールは benchmark ケース用の評価器であり、
照合エンジン本体をケース固有ロジックに閉じないこと。
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pre_peer_checker.eval.layers import diagnose_item_layer, summarize_layers


def _fixtures_root() -> Path:
    """Resolve fixtures/ whether running from src tree or installed package."""
    here = Path(__file__).resolve()
    for parent in [here.parents[3], here.parents[2], Path.cwd()]:
        cand = parent / "fixtures"
        if cand.is_dir():
            return cand
    raise FileNotFoundError("fixtures/ directory not found")


FIXTURES = _fixtures_root()
REPO_ROOT = FIXTURES.parent


@dataclass
class MatchResult:
    item_id: str
    severity: str
    matched: bool
    matched_tags: list[str]
    matched_pattern_ids: list[str]
    note: str = ""


def load_gold(case_id: str) -> dict[str, Any]:
    path = FIXTURES / "gold" / case_id / "gold_warnings.json"
    if not path.is_file():
        # fall back to example for schema-only environments
        alt = FIXTURES / "gold" / case_id / "gold_warnings.example.json"
        if alt.is_file() and case_id != "demo":
            raise FileNotFoundError(
                f"Filled gold not found: {path}. Copy from gold_warnings.example.json locally."
            )
        if alt.is_file():
            return json.loads(alt.read_text(encoding="utf-8"))
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def load_manifest(case_id: str) -> dict[str, Any]:
    path = FIXTURES / "gold" / case_id / "case_manifest.json"
    if not path.is_file():
        raise FileNotFoundError(
            f"case_manifest.json not found for {case_id}: {path}. "
            "Copy from case_manifest.example.json if this is a private benchmark."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def load_warnings(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "warnings" in data:
        return list(data["warnings"])
    if isinstance(data, list):
        return data
    raise ValueError(f"Unrecognized warnings JSON shape: {path}")


def _tags_of(warning: dict[str, Any]) -> set[str]:
    tag = warning.get("tag") or warning.get("warning_tag") or ""
    if isinstance(tag, list):
        return set(tag)
    return {tag} if tag else set()


def _pattern_ids_of(warning: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    meta = warning.get("metadata") or {}
    if isinstance(meta, dict):
        for key in ("pattern_id", "also_pattern"):
            pid = meta.get(key)
            if pid:
                out.add(str(pid))
    if warning.get("pattern_id"):
        out.add(str(warning["pattern_id"]))
    return out


def _text_blob(warning: dict[str, Any]) -> str:
    parts = [
        str(warning.get("title", "")),
        str(warning.get("location", "")),
        str(warning.get("reason", "")),
        " ".join(str(s) for s in warning.get("sources", [])),
    ]
    meta = warning.get("metadata") or {}
    if isinstance(meta, dict):
        for k in ("panel", "panel_a", "panel_b"):
            if meta.get(k):
                parts.append(f"panel {meta[k]}")
    return " ".join(parts).lower()


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _panel_mentioned(blob: str, panel: str) -> bool:
    """Avoid single-letter false positives: require panel context."""
    p = panel.strip().upper()
    if not p:
        return False
    patterns = [
        rf"\bpanel\s*{re.escape(p)}\b",
        rf"\({re.escape(p)}\)",
        rf"\bpanels?\s+[a-z0-9/]*{re.escape(p)}",
        rf"\b{re.escape(p)}\s*n\s*=",
        rf"\bn\s*=\s*\d+\s*\({re.escape(p)}\)",
    ]
    return any(re.search(pat, blob, flags=re.I) for pat in patterns)


def _figure_mentioned(blob: str, figure: str) -> bool:
    n = _norm(figure)
    b = _norm(blob)
    if n and n in b:
        return True
    # Fig.1 / Figure 1 style
    m = re.search(r"(?:fig(?:ure)?\.?\s*)(s?\d+[a-z]?)", figure, re.I)
    if not m:
        return False
    token = m.group(1).lower()
    return bool(re.search(rf"(?:fig(?:ure)?\.?\s*){re.escape(token)}\b", blob, re.I)) or (
        _norm(f"fig{token}") in b
    )


def _artifact_mentioned(blob: str, artifact: str) -> bool:
    return _norm(artifact) in _norm(blob) if artifact else False


def _evidence_mentioned(blob: str, evidence_paths: list[str]) -> bool:
    for ep in evidence_paths:
        name = Path(ep).name
        if name and _norm(name) in _norm(blob):
            return True
        # folder hints like plot_residue / Fig1C
        for part in Path(ep).parts:
            if len(part) >= 4 and _norm(part) in _norm(blob):
                return True
    return False


def warning_supports_item(item: dict[str, Any], warning: dict[str, Any]) -> bool:
    """Return True if a single warning is evidence for a gold item."""
    expected_tags = set(item.get("expected_tags", []))
    tags = _tags_of(warning)
    if expected_tags and not expected_tags.intersection(tags):
        return False

    expected_pid = item.get("pattern_id")
    pids = _pattern_ids_of(warning)
    blob = _text_blob(warning)
    panels = item.get("panels") or []
    evidence = item.get("evidence_paths") or []

    panel_ok = False
    group_hits = 0
    fig_hits = 0
    if panels:
        for p in panels:
            fig = p.get("figure")
            group = p.get("group")
            artifact = p.get("artifact")
            if fig and _figure_mentioned(blob, str(fig)):
                fig_hits += 1
            if group and _panel_mentioned(blob, str(group)):
                group_hits += 1
            if artifact and _artifact_mentioned(blob, str(artifact)):
                panel_ok = True
        if group_hits >= 1 or fig_hits >= 1:
            panel_ok = True
    else:
        panel_ok = True

    evidence_ok = _evidence_mentioned(blob, evidence) if evidence else False

    # Primary: correct pattern_id (also_pattern counts).
    # When gold lists panels/evidence, require a panel/figure/artifact or evidence
    # path hit — otherwise unrelated same-pattern warnings (e.g. B/C for H2b) credit.
    if expected_pid and expected_pid in pids:
        if not panels and not evidence:
            return True
        return bool(panel_ok or evidence_ok)

    # If gold declares a pattern_id, do not fall back to fuzzy panel letters
    if expected_pid:
        return False

    # Fallback for gold items without pattern_id: tag + strong panel/evidence
    if group_hits >= 1 or evidence_ok:
        return True
    if panel_ok and not panels:
        return True
    return False


def match_item(item: dict[str, Any], warnings: list[dict[str, Any]]) -> MatchResult:
    candidates = [w for w in warnings if warning_supports_item(item, w)]
    if candidates:
        matched_tags = sorted({t for w in candidates for t in _tags_of(w)})
        matched_pids = sorted({p for w in candidates for p in _pattern_ids_of(w)})
        return MatchResult(
            item_id=item["id"],
            severity=item.get("severity", "required"),
            matched=True,
            matched_tags=matched_tags,
            matched_pattern_ids=matched_pids,
        )

    return MatchResult(
        item_id=item["id"],
        severity=item.get("severity", "required"),
        matched=False,
        matched_tags=[],
        matched_pattern_ids=[],
        note="no warning matched expected pattern_id/tags/panels",
    )


def _warning_matches_any_item(warning: dict[str, Any], items: list[dict[str, Any]]) -> bool:
    return any(warning_supports_item(item, warning) for item in items)


def precision_recall_from_results(
    gold_items: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    results: list[MatchResult],
    *,
    corpus_present: bool = False,
) -> dict[str, Any]:
    """Gold-relative precision / recall (not population prevalence).

    - Recall: fraction of *required* gold items matched (skipped corpus items count as hit).
    - Precision: fraction of emitted warnings that support ≥1 gold item.
      Extra warnings are expected on real manuscripts; do not chase 100%.
    """
    active_items = []
    for item in gold_items:
        sev = item.get("severity", "required")
        if sev == "required_when_corpus_present" and not corpus_present:
            continue
        if sev in {"required", "required_when_corpus_present", "desirable"}:
            active_items.append(item)

    required = [r for r in results if r.severity in {"required", "required_when_corpus_present"}]
    req_hit = sum(1 for r in required if r.matched)
    recall = None if not required else req_hit / len(required)

    tp = 0
    fp = 0
    for w in warnings:
        if _warning_matches_any_item(w, active_items):
            tp += 1
        else:
            fp += 1
    precision = None if (tp + fp) == 0 else tp / (tp + fp)

    fn_ids = [r.item_id for r in required if not r.matched]
    return {
        "recall": recall,
        "precision": precision,
        "true_positive_warnings": tp,
        "false_positive_warnings": fp,
        "false_negative_item_ids": fn_ids,
        "n_gold_items_scored": len(active_items),
        "definition": (
            "recall=matched_required_gold_items/required_gold_items; "
            "precision=warnings_supporting_any_gold_item/all_emitted_warnings. "
            "Not a claim of population accuracy; benchmark-case relative only."
        ),
    }


def _extract_eval_context(warnings_path: Path, warnings: list[dict[str, Any]]) -> dict[str, Any]:
    """Pull run_coverage / slim artifacts from warnings JSON envelope if present."""
    raw = json.loads(warnings_path.read_text(encoding="utf-8"))
    coverage = None
    artifacts: dict[str, Any] = {}
    if isinstance(raw, dict):
        coverage = raw.get("run_coverage")
        art = raw.get("artifacts")
        if isinstance(art, dict):
            artifacts = art
            if coverage is None:
                coverage = art.get("run_coverage")
        # allow top-level coverage only
    return {"coverage": coverage if isinstance(coverage, dict) else None, "artifacts": artifacts}


def evaluate(
    case_id: str,
    warnings_path: Path,
    *,
    corpus_present: bool = False,
) -> dict[str, Any]:
    gold = load_gold(case_id)
    warnings = load_warnings(warnings_path)
    ctx = _extract_eval_context(warnings_path, warnings)
    # candidate cards list tables to check; they are not findings and score neither way
    info_cards = [w for w in warnings if (w.get("metadata") or {}).get("info_card")]
    warnings = [w for w in warnings if not (w.get("metadata") or {}).get("info_card")]
    coverage = ctx["coverage"]
    artifacts = ctx["artifacts"]
    results: list[MatchResult] = []
    layer_rows: list[dict[str, Any]] = []

    for item in gold["items"]:
        sev = item.get("severity", "required")
        if sev == "required_when_corpus_present" and not corpus_present:
            results.append(
                MatchResult(
                    item_id=item["id"],
                    severity=sev,
                    matched=True,
                    matched_tags=[],
                    matched_pattern_ids=[],
                    note="skipped (past-paper corpus not provided)",
                )
            )
            diag = diagnose_item_layer(
                item, matched=False, skipped=True, coverage=coverage, artifacts=artifacts
            )
            layer_rows.append(
                {
                    "id": item["id"],
                    "pattern_id": item.get("pattern_id"),
                    "severity": sev,
                    "matched": True,
                    "layer": diag["layer"],
                    "fail_layer": diag["fail_layer"],
                    "layer_note": diag["note"],
                }
            )
            continue
        mr = match_item(item, warnings)
        results.append(mr)
        diag = diagnose_item_layer(
            item,
            matched=mr.matched,
            skipped=False,
            coverage=coverage,
            artifacts=artifacts,
        )
        layer_rows.append(
            {
                "id": item["id"],
                "pattern_id": item.get("pattern_id"),
                "severity": sev,
                "matched": mr.matched,
                "layer": diag["layer"],
                "fail_layer": diag["fail_layer"],
                "layer_note": diag["note"],
            }
        )

    required = [r for r in results if r.severity in {"required", "required_when_corpus_present"}]
    desirable = [r for r in results if r.severity == "desirable"]
    req_hit = sum(1 for r in required if r.matched)
    des_hit = sum(1 for r in desirable if r.matched)
    pr = precision_recall_from_results(
        gold["items"], warnings, results, corpus_present=corpus_present
    )
    layers = summarize_layers(layer_rows)
    by_layer = {row["id"]: row for row in layer_rows}

    return {
        "case_id": case_id,
        "warnings_path": str(warnings_path),
        "n_warnings_emitted": len(warnings),
        "n_info_cards": len(info_cards),
        "required_recall": None if not required else req_hit / len(required),
        "required_hit": req_hit,
        "required_total": len(required),
        "desirable_hit": des_hit,
        "desirable_total": len(desirable),
        "precision": pr["precision"],
        "recall": pr["recall"],
        "true_positive_warnings": pr["true_positive_warnings"],
        "false_positive_warnings": pr["false_positive_warnings"],
        "false_negative_item_ids": pr["false_negative_item_ids"],
        "corpus_present": corpus_present,
        "layer_diagnosis_available": coverage is not None or bool(artifacts),
        "layers": layers,
        "items": [
            {
                "id": r.item_id,
                "severity": r.severity,
                "matched": r.matched,
                "matched_tags": r.matched_tags,
                "matched_pattern_ids": r.matched_pattern_ids,
                "note": r.note,
                "layer": by_layer[r.item_id]["layer"],
                "fail_layer": by_layer[r.item_id]["fail_layer"],
                "layer_note": by_layer[r.item_id]["layer_note"],
            }
            for r in results
        ],
        "metrics_definition": pr["definition"],
        "note": (
            "Metrics are relative to this benchmark gold only — not a claim of 100% "
            "detection on all life-science manuscripts. Do not overfit to one case. "
            "check_reference/ informs gold design; it is not runtime input. "
            "layers.* diagnoses parser vs linking vs match for next engineering focus."
        ),
    }


def resolve_case_input_roots(case_id: str) -> list[Path]:
    """Resolve local input roots from case_manifest (filled)."""
    manifest = load_manifest(case_id)
    rel = manifest.get("root_relative")
    if not rel:
        raise ValueError(f"manifest for {case_id} lacks root_relative")
    root = REPO_ROOT / rel
    if not root.exists():
        # synthetic demo may live under fixtures/synthetic
        alt = FIXTURES / "synthetic" / case_id
        if alt.exists():
            return [alt]
        raise FileNotFoundError(f"case root not found: {root}")
    # Prefer a focused Phase-1 slice when full tree is huge: manuscript + key quant
    # Still allow full root; caller may pass overrides.
    return [root]


def resolve_case_focus_inputs(case_id: str) -> list[Path] | None:
    """Focused input slice from case_manifest ``focus_inputs`` (paths under root_relative).

    Returns the slice when every entry exists, else ``[root]``; ``None`` when the
    manifest or its root is missing.
    """
    try:
        manifest = load_manifest(case_id)
    except FileNotFoundError:
        return None
    rel = manifest.get("root_relative")
    if not rel:
        return None
    root = REPO_ROOT / rel
    if not root.exists():
        return None
    focus = [root / p for p in manifest.get("focus_inputs") or []]
    if focus and all(p.exists() for p in focus):
        return focus
    return [root]


def resolve_case_corpus_roots(case_id: str) -> list[Path]:
    """Optional corpus roots from case_manifest.corpus_relative."""
    try:
        manifest = load_manifest(case_id)
    except FileNotFoundError:
        return []
    rel = manifest.get("corpus_relative")
    if not rel:
        return []
    root = REPO_ROOT / rel
    if root.exists():
        return [root]
    alt = FIXTURES / "synthetic" / case_id / "corpus"
    return [alt] if alt.exists() else []


def run_and_evaluate(
    case_id: str,
    *,
    input_paths: list[Path] | None = None,
    corpus_paths: list[Path] | None = None,
    cited_papers_paths: list[Path] | None = None,
    corpus_present: bool = False,
    warnings_out: Path | None = None,
    legend_llm: bool = False,
    legend_llm_prefer: str = "auto",
    legend_llm_model: str | None = None,
    legend_llm_profile: str | None = None,
) -> dict[str, Any]:
    """Run verification then gold_eval. Used by regression tests and CLI."""
    from pre_peer_checker.pipeline.orchestrator import run_verification

    roots = input_paths or resolve_case_input_roots(case_id)
    corpus = corpus_paths if corpus_paths is not None else resolve_case_corpus_roots(case_id)
    if corpus:
        corpus_present = True
    cited = cited_papers_paths
    if cited is None:
        # Convention: fixtures/synthetic/<case>/cited_pdfs
        guess = REPO_ROOT / "fixtures" / "synthetic" / case_id / "cited_pdfs"
        cited = [guess] if guess.is_dir() else []
    result = run_verification(
        [str(p) for p in roots],
        corpus=[str(p) for p in corpus] if corpus else None,
        cited_papers=[str(p) for p in cited] if cited else None,
        legend_llm=legend_llm,
        legend_llm_prefer=legend_llm_prefer,
        legend_llm_model=legend_llm_model,
        legend_llm_profile=legend_llm_profile,
    )
    payload = {
        "case_id": case_id,
        "inputs": [str(p) for p in roots],
        "corpus": [str(p) for p in corpus],
        "cited_papers": [str(p) for p in cited],
        "warnings": [w.to_dict() for w in result.warnings],
        "run_coverage": result.artifacts.get("run_coverage"),
        "artifacts": {
            # slim slice for layer diagnosis (avoid huge blobs in CI artifacts)
            "run_coverage": result.artifacts.get("run_coverage"),
            "n_matrix": result.artifacts.get("n_matrix"),
            "legend_llm_status": result.artifacts.get("legend_llm_status"),
            "group_vectors_n": len(result.artifacts.get("group_vectors") or []),
            "legend_panel_ns_n": len(result.artifacts.get("legend_panel_ns") or []),
            "digitized_plots_n": len(result.artifacts.get("digitized_plots") or []),
            "figure_panel_plots_n": len(result.artifacts.get("figure_panel_plots") or []),
            "r_sources_n": len(result.artifacts.get("r") or []),
            "rhistory_n": sum(
                1
                for a in (result.artifacts.get("r") or [])
                if isinstance(a, dict) and a.get("source_kind") == "rhistory"
            ),
            "r_resolved_reads_n": sum(
                1
                for a in (result.artifacts.get("r") or [])
                if isinstance(a, dict)
                for rd in (a.get("reads") or [])
                if isinstance(rd, dict) and rd.get("resolved_path")
            ),
        },
    }
    if warnings_out is None:
        warnings_out = Path("outputs") / f"{case_id}_warnings.json"
    warnings_out.parent.mkdir(parents=True, exist_ok=True)
    warnings_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return evaluate(case_id, warnings_out, corpus_present=corpus_present)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate warnings.json against gold fixtures")
    parser.add_argument("--case", default="private_benchmark")
    parser.add_argument(
        "--warnings",
        type=Path,
        default=None,
        help="Existing warnings JSON. Omit when using --run.",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="Run verification on case_manifest root (or --input) then evaluate",
    )
    parser.add_argument(
        "--input",
        action="append",
        type=Path,
        default=None,
        help="Override input path(s) for --run (repeatable)",
    )
    parser.add_argument(
        "--corpus",
        action="append",
        type=Path,
        default=None,
        help="Past-paper image corpus path(s) for --run (repeatable; enables H3)",
    )
    parser.add_argument(
        "--cited-papers",
        action="append",
        type=Path,
        default=None,
        help="Cited-paper PDF path(s) or dirs for --run (literature meta + claim checks)",
    )
    parser.add_argument(
        "--corpus-present",
        action="store_true",
        help="過去論文コーパスを投入した場合に H3 等を必須扱いする",
    )
    parser.add_argument("-o", "--output", type=Path, default=None)
    parser.add_argument(
        "--layers",
        action="store_true",
        help="Print a short parser/linking/match layer summary to stderr",
    )
    parser.add_argument(
        "--legend-llm",
        action="store_true",
        help="Enable Legend LLM extraction during --run (MLX / transformers)",
    )
    parser.add_argument(
        "--legend-llm-prefer",
        default="auto",
        choices=["auto", "mlx", "cuda", "transformers", "none"],
        help="LLM backend preference (default: auto)",
    )
    parser.add_argument(
        "--legend-llm-model",
        default=None,
        help="Override model id for Legend LLM",
    )
    parser.add_argument(
        "--llm-profile",
        default=None,
        help="Text LLM profile id from llm/model_registry.yaml",
    )
    args = parser.parse_args(argv)

    if args.run:
        report = run_and_evaluate(
            args.case,
            input_paths=args.input,
            corpus_paths=args.corpus,
            cited_papers_paths=args.cited_papers,
            corpus_present=args.corpus_present or bool(args.corpus),
            warnings_out=Path("outputs") / f"{args.case}_warnings.json",
            legend_llm=bool(args.legend_llm),
            legend_llm_prefer=args.legend_llm_prefer,
            legend_llm_model=args.legend_llm_model,
            legend_llm_profile=args.llm_profile,
        )
    else:
        if not args.warnings:
            parser.error("--warnings is required unless --run is set")
        report = evaluate(args.case, args.warnings, corpus_present=args.corpus_present)

    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")

    if args.layers:
        _print_layer_summary(report)

    if report["required_total"] and report["required_hit"] < report["required_total"]:
        return 2
    return 0


def _print_layer_summary(report: dict[str, Any]) -> None:
    import sys

    layers = report.get("layers") or {}
    by = layers.get("by_layer") or {}
    print("\n=== layer summary ===", file=sys.stderr)
    print(
        f"case={report.get('case_id')} "
        f"required_recall={report.get('required_recall')} "
        f"precision={report.get('precision')} "
        f"diagnosis={'on' if report.get('layer_diagnosis_available') else 'off'}",
        file=sys.stderr,
    )
    for name in ("parser", "linking", "match", "skipped", "unknown"):
        row = by.get(name) or {}
        print(
            f"  {name}: hits={row.get('hits', 0)} misses={row.get('misses', 0)} "
            f"miss_ids={row.get('miss_ids') or []}",
            file=sys.stderr,
        )
    focus = layers.get("recommended_focus")
    if focus:
        print(
            f"recommended_focus={focus} "
            f"(required misses={layers.get('recommended_focus_required_misses')})",
            file=sys.stderr,
        )
    else:
        print("recommended_focus=(none — no required misses)", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
