"""Gold miss → pipeline layer diagnosis (parser / linking / match).

Used by gold_eval to decide where to invest next when required items miss.
Does not claim population accuracy — benchmark-relative only.
"""

from __future__ import annotations

from typing import Any

# Layers aligned with ROADMAP / DETECTION §5 bottlenecks.
LAYER_PARSER = "parser"
LAYER_LINKING = "linking"
LAYER_MATCH = "match"
LAYER_SKIPPED = "skipped"
LAYER_UNKNOWN = "unknown"

# pattern_id → which coverage checks feed this detector, and primary compare layer.
_PATTERN_SPEC: dict[str, dict[str, Any]] = {
    "P-DATA-SWAP-CROSS-CONDITION": {
        "primary": LAYER_MATCH,
        "parser_checks": ["tables", "plot_digitize", "figure_pdf", "scripts"],
        "linking_checks": ["cross_table"],
        "need_any_ran": ["cross_table", "plot_digitize", "figure_pdf", "scripts"],
    },
    "P-FILENAME-CONTENT-MISMATCH": {
        "primary": LAYER_MATCH,
        "parser_checks": ["plot_digitize", "tables", "scripts"],
        "linking_checks": [],
        "need_any_ran": ["plot_digitize", "tables", "scripts"],
    },
    "P-N-MISMATCH-LEGEND-VS-DATA": {
        "primary": LAYER_MATCH,
        "parser_checks": ["word_legend", "tables"],
        "linking_checks": ["legend_n_match", "n_matrix"],
        "need_any_ran": ["legend_n_match", "n_matrix"],
    },
    "P-EXCLUSION-UNDECLARED": {
        "primary": LAYER_MATCH,
        "parser_checks": ["word_legend", "tables"],
        "linking_checks": ["legend_n_match", "n_matrix"],
        "need_any_ran": ["legend_n_match", "n_matrix"],
    },
    "P-N-INCONSISTENT-ACROSS-IDENTICAL-PLOTS": {
        "primary": LAYER_MATCH,
        "parser_checks": ["word_legend", "plot_digitize", "figure_pdf"],
        "linking_checks": ["legend_n_match", "n_matrix"],
        "need_any_ran": ["plot_digitize", "figure_pdf", "legend_n_match"],
    },
    "P-IMAGE-REUSE-UNCITED": {
        "primary": LAYER_MATCH,
        "parser_checks": ["images", "figure_pdf"],
        "linking_checks": ["corpus_h3"],
        "need_any_ran": ["images", "figure_pdf", "corpus_h3"],
    },
    "P-SHARED-CONTROL-UNDISCLOSED": {
        "primary": LAYER_MATCH,
        "parser_checks": ["tables", "plot_digitize"],
        "linking_checks": ["cross_table"],
        "need_any_ran": ["cross_table", "tables", "plot_digitize"],
    },
    "P-STATS-RECALC-MISMATCH": {
        "primary": LAYER_MATCH,
        "parser_checks": ["tables", "word_legend"],
        "linking_checks": ["legend_n_match"],
        "need_any_ran": ["tables"],
    },
    "P-SOURCE-DUPLICATE-VALUES": {
        "primary": LAYER_MATCH,
        "parser_checks": ["tables"],
        "linking_checks": ["cross_table"],
        "need_any_ran": ["tables"],
    },
    "P-SOURCE-RATIO-ARTIFACT": {
        "primary": LAYER_MATCH,
        "parser_checks": ["tables"],
        "linking_checks": ["cross_table"],
        "need_any_ran": ["tables"],
    },
    "P-DERIVED-VALUE-PRECISION": {
        "primary": LAYER_MATCH,
        "parser_checks": ["tables"],
        "linking_checks": [],
        "need_any_ran": ["tables"],
    },
    "P-STAT-METHOD-INCONSISTENT": {
        "primary": LAYER_MATCH,
        "parser_checks": ["tables", "word_legend", "scripts"],
        "linking_checks": [],
        "need_any_ran": ["tables", "scripts", "word_legend"],
    },
}


def _checks_by_id(coverage: dict[str, Any] | None) -> dict[str, dict[str, str]]:
    if not coverage:
        return {}
    out: dict[str, dict[str, str]] = {}
    for c in coverage.get("checks") or []:
        if isinstance(c, dict) and c.get("id"):
            out[str(c["id"])] = {
                "status": str(c.get("status") or ""),
                "detail": str(c.get("detail") or ""),
                "name": str(c.get("name") or ""),
            }
    return out


def json_ish_blob(obj: Any) -> str:
    if isinstance(obj, dict):
        return " ".join(json_ish_blob(v) for v in obj.values())
    if isinstance(obj, list):
        return " ".join(json_ish_blob(v) for v in obj)
    return str(obj)


def _n_matrix_unlinked_ratio(artifacts: dict[str, Any] | None) -> float | None:
    if not artifacts:
        return None
    rows = artifacts.get("n_matrix") or []
    if not rows:
        return None
    unlinked = 0
    total = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        total += 1
        blob = json_ish_blob(row)
        if "未紐付け" in blob or "unlinked" in blob.lower() or "データ未投入" in blob:
            unlinked += 1
    if total == 0:
        return None
    return unlinked / total


def diagnose_item_layer(
    item: dict[str, Any],
    *,
    matched: bool,
    skipped: bool = False,
    coverage: dict[str, Any] | None = None,
    artifacts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return layer attribution for one gold item."""
    pid = str(item.get("pattern_id") or "")
    gold_hint = item.get("primary_layer") or item.get("fail_layer_hint")
    spec = _PATTERN_SPEC.get(
        pid,
        {
            "primary": LAYER_MATCH,
            "parser_checks": [],
            "linking_checks": [],
            "need_any_ran": [],
        },
    )
    primary = str(gold_hint or spec["primary"])

    if skipped:
        return {
            "layer": LAYER_SKIPPED,
            "fail_layer": None,
            "note": "skipped (corpus or severity gate)",
        }

    if matched:
        return {
            "layer": primary,
            "fail_layer": None,
            "note": f"matched (credited to {primary})",
        }

    checks = _checks_by_id(coverage)
    if not checks and not artifacts:
        return {
            "layer": LAYER_UNKNOWN,
            "fail_layer": LAYER_UNKNOWN,
            "note": (
                "unmatched; no run_coverage/artifacts in warnings JSON — "
                "re-run with --run to enable layer diagnosis"
            ),
        }

    parser_ids = list(spec.get("parser_checks") or [])
    parser_statuses = [checks[c]["status"] for c in parser_ids if c in checks]
    if parser_ids and parser_statuses and all(s == "skipped" for s in parser_statuses):
        detail = "; ".join(f"{c}:{checks[c]['detail']}" for c in parser_ids if c in checks)
        return {
            "layer": LAYER_PARSER,
            "fail_layer": LAYER_PARSER,
            "note": f"prerequisite inputs missing/skipped ({detail})",
        }

    if pid in {
        "P-N-MISMATCH-LEGEND-VS-DATA",
        "P-N-INCONSISTENT-ACROSS-IDENTICAL-PLOTS",
        "P-EXCLUSION-UNDECLARED",
    }:
        wl = checks.get("word_legend")
        if wl and (
            "抽出パネル n=0" in wl.get("detail", "")
            or "処理できた Word 0" in wl.get("detail", "")
        ):
            return {
                "layer": LAYER_PARSER,
                "fail_layer": LAYER_PARSER,
                "note": f"legend/panel extract empty ({wl.get('detail')})",
            }

    linking_ids = list(spec.get("linking_checks") or [])
    for lid in linking_ids:
        lc = checks.get(lid)
        if not lc:
            continue
        if lc["status"] == "skipped":
            ran_parser = any(checks.get(c, {}).get("status") == "ran" for c in parser_ids)
            if ran_parser or not parser_ids:
                return {
                    "layer": LAYER_LINKING,
                    "fail_layer": LAYER_LINKING,
                    "note": f"link step skipped: {lid} — {lc.get('detail')}",
                }

    ratio = _n_matrix_unlinked_ratio(artifacts)
    if ratio is not None and ratio >= 0.5 and pid.startswith("P-N-"):
        return {
            "layer": LAYER_LINKING,
            "fail_layer": LAYER_LINKING,
            "note": f"n_matrix mostly unlinked ({ratio:.0%})",
        }

    need_any = list(spec.get("need_any_ran") or [])
    if need_any and any(checks.get(c, {}).get("status") == "ran" for c in need_any):
        return {
            "layer": LAYER_MATCH,
            "fail_layer": LAYER_MATCH,
            "note": "prerequisites ran but pattern_id not emitted/matched",
        }

    if gold_hint:
        return {
            "layer": str(gold_hint),
            "fail_layer": str(gold_hint),
            "note": "unmatched; gold fail_layer_hint used (coverage inconclusive)",
        }

    return {
        "layer": LAYER_UNKNOWN,
        "fail_layer": LAYER_UNKNOWN,
        "note": "unmatched; could not classify layer from coverage",
    }


def summarize_layers(item_layer_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate hit/miss by fail_layer (misses) and credit layer (hits)."""
    by: dict[str, dict[str, Any]] = {
        LAYER_PARSER: {"hits": 0, "misses": 0, "miss_ids": []},
        LAYER_LINKING: {"hits": 0, "misses": 0, "miss_ids": []},
        LAYER_MATCH: {"hits": 0, "misses": 0, "miss_ids": []},
        LAYER_SKIPPED: {"hits": 0, "misses": 0, "miss_ids": []},
        LAYER_UNKNOWN: {"hits": 0, "misses": 0, "miss_ids": []},
    }
    required_miss_layers: list[str] = []
    for row in item_layer_rows:
        sev = row.get("severity", "required")
        if row.get("matched"):
            layer = row.get("layer") or LAYER_UNKNOWN
            by.setdefault(layer, {"hits": 0, "misses": 0, "miss_ids": []})
            by[layer]["hits"] += 1
            continue
        if sev == "required_when_corpus_present" and row.get("layer") == LAYER_SKIPPED:
            by[LAYER_SKIPPED]["hits"] += 1
            continue
        fail = row.get("fail_layer") or LAYER_UNKNOWN
        by.setdefault(fail, {"hits": 0, "misses": 0, "miss_ids": []})
        by[fail]["misses"] += 1
        by[fail]["miss_ids"].append(row.get("id"))
        if sev in {"required", "required_when_corpus_present"}:
            required_miss_layers.append(fail)

    focus = None
    focus_n = -1
    for layer in (LAYER_PARSER, LAYER_LINKING, LAYER_MATCH, LAYER_UNKNOWN):
        n = sum(1 for x in required_miss_layers if x == layer)
        if n > focus_n:
            focus_n = n
            focus = layer if n > 0 else None

    return {
        "definition": (
            "parser=extract/parse inputs; linking=entity link panel↔table↔plot; "
            "match=deterministic compare fired inputs but gold pattern missed. "
            "Hits credited to pattern primary layer; misses to diagnosed fail_layer."
        ),
        "by_layer": by,
        "recommended_focus": focus,
        "recommended_focus_required_misses": focus_n if focus_n > 0 else 0,
    }
