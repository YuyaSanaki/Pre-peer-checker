#!/usr/bin/env python3
"""Leave-one-rule-out audit of the Legend parser rules (rules_only, no LLM).

Each rule is switched off by patching one source snippet, the legend task is scored
on dev and holdout, and the difference (with rule minus without) is written to
``fixtures/gold/rule_ledger.json`` as ``kind: parser_rule`` entries. ``decision``
stays human-owned; ``measured`` / ``suggestion`` are machine-written.

Holdout scores are only aggregated here (no per-case errors are printed), so the
audit does not burn holdout cases.

    python scripts/dev_rule_audit.py            # print + update the ledger
    python scripts/dev_rule_audit.py --dry-run  # print only
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
LS = ROOT / "src/pre_peer_checker/parsers/legend_struct.py"
MT = ROOT / "src/pre_peer_checker/parsers/manuscript_text.py"

ABLATIONS: dict[str, tuple[str, list[tuple[Path, str, str]]]] = {
    "upper_openers": ("Uppercase panel openers (A, ... / (A) ...) split a legend into sections.",
                      [(LS, "upper = _upper_letter_openers(text)", "upper = []")]),
    "single_multi_opener": ("A single opener listing several panels counts as a section split.",
                            [(LS, "if len(kept) < 2 and not (kept and len(kept[0][1]) >= 2):", "if len(kept) < 2:")]),
    "block_opener_reset": ("A multi-panel opener resets the expected next letter.",
                           [(LS, "last = chr(ord(panels[0]) - 1) if len(panels) > 1 else panels[-1]", "last = panels[-1]")]),
    "opener_colon_nospace": ("Openers may be followed by a colon without a space.",
                             [(LS, r'r"\s*(?:,\s+|:\s*)(?=\S)"', r'r"\s*[,:]\s+(?=\S)"')]),
    "capital_N_replicate": ("Capital N next to a lowercase n in one sentence is the replicate level.",
                            [(LS, 'mentions = [m for m in mentions if not (m.group(0)[0] == "N" and sentence(m.start())[0] in lower)]', "mentions = mentions")]),
    "sex_breakdown": ("n = 4 males, 2 females is a breakdown, not separate groups.",
                      [(LS, "if not _SEX_UNIT_RE.match(chunk, m.end()) and ", "if ")]),
    "total_of_groups": ("A closing n equal to the sum of the others is a total.",
                        [(LS, "if m is mentions[-1] and not m.group(2)", "if False and not m.group(2)")]),
    "shared_n_no_group": ("A shared n stated after several groups is not split per group.",
                          [(LS, "if _SHARED_N_RE.search(chunk[m.end(4) : m.end()]):", "if False:")]),
    "figure_level_n": ("An n after the closing statistics sentence covers the figure.",
                       [(LS, "fig_at = stats.start() - last_start if stats else len(t)", "fig_at = len(t)")]),
    "figure_level_data_filter": ("A figure-level n covers only quantified (data) sections.",
                                 [(LS, "if _is_data_section(t[s", "if True or _is_data_section(t[s")]),
    "qualified_n": ("n (BW and KW/BW) = 12: n of the named measurements.",
                    [(LS, "for m in _N_QUALIFIED_RE.finditer(chunk):", "for m in []:")]),
    "measure_panels": ("A qualified n targets the panels showing that measure.",
                       [(LS, "targets = _measure_panels(chunk[body:], section_panels, kind[5:]) or targets", "targets = targets")]),
    "per_animal_times": ("n = 10 units per animal x 4-6 reads the animal count.",
                         [(LS, "times = list(_N_PER_UNIT_TIMES_RE.finditer(chunk))", "times = []")]),
    "trailing_panel_ref": ("A panel reference right after the n binds it.",
                           [(LS, "if after and not chunk[m.end() : after[0][0]].strip():", "if False:")]),
    "either_stopword": ("either / neither are not group labels.",
                        [(LS, "|both|either|neither)$", "|both)$")]),
    "respectively_pair": ("n = 128 and 151, respectively splits into two groups.",
                          [(LS, "if not m.group(2) and pair and re.search", "if False and pair and re.search")]),
    "worded_reference_skip": ("'one representative' / 'all these' are not worded sample sizes.", [
        (LS, 'lo == 1 and re.search(r"\\brepresentative\\b"', 'lo == -1 and re.search(r"\\brepresentative\\b"'),
        (LS, 'r"\\b(?:all|these|those|both)\\s+$"', 'r"(?!x)x"'),
    ]),
    "worded_pair": ("'three WT and four KO mice' gives two groups.",
                    [(LS, "pairs = [] if n_sentences else list(_WORDED_PAIR_RE.finditer(chunk))", "pairs = []")]),
    "worded_own_list_narrow": ("A worded count followed by its own panel list is left to that list.",
                               [(LS, 'if kind == "worded"\n', 'if False\n')]),
    "worded_section_skip": ("Worded counts are read only in sections without n =.",
                            [(LS, "if n_sentences or any(p.start() <= m.start() < p.end() for p in pairs):", "if any(p.start() <= m.start() < p.end() for p in pairs):")]),
    "lower_bound_n": ("n >= 2 / n > 10 is read as an open range (not compared, counted for recall).",
                      [(LS, "for m in _N_LOWER_RE.finditer(chunk):", "for m in []:")]),
    "n_for_panel_scope": ("n = 4 for d-g covers every panel of the stated range.",
                          [(LS, "for m in _N_FOR_PANELS_RE.finditer(text):", "for m in []:")]),
    "legend_unfinished_cont": ("An unfinished legend continues into the next block.",
                               [(MT, "if follows or panel_cont or unfinished or resumed:", "if follows or panel_cont or resumed:")]),
    "legend_fig_continued": ("A smaller block on the resume page continues the legend.",
                             [(MT, "resumed = b.page == resume_page and _smaller(b)", "resumed = False")]),
    "legend_tail_reroute": ("A legend tail found later is re-attached to its legend.",
                            [(MT, "earlier = _resumed_legend(b, legends)", "earlier = None")]),
}


def _load(name: str, path: Path, patches: list[tuple[str, str]]) -> None:
    src = path.read_text(encoding="utf-8")
    for old, new in patches:
        if src.count(old) != 1:
            raise LookupError(f"{name}: snippet not unique/found: {old!r}")
        src = src.replace(old, new)
    spec = importlib.util.spec_from_loader(name, loader=None, origin=str(path))
    mod = importlib.util.module_from_spec(spec)
    mod.__file__ = str(path)
    sys.modules[name] = mod
    exec(compile(src, str(path), "exec"), mod.__dict__)


def _score(patches: list[tuple[Path, str, str]]) -> dict[str, dict[str, tuple[int, int, int, int]]]:
    """split -> case -> (hit, items, pred_hit, pred)."""
    _load("pre_peer_checker.parsers.manuscript_text", MT, [(o, n) for p, o, n in patches if p == MT])
    _load("pre_peer_checker.parsers.legend_struct", LS, [(o, n) for p, o, n in patches if p == LS])
    from pre_peer_checker.eval import generalization as g

    out: dict[str, dict[str, tuple[int, int, int, int]]] = {"dev": {}, "holdout": {}}
    for case in g.cases_with_gold(g.discover_cases(split="all"), "legend"):
        sc = g.score_legend_case(case, g.legend_predictions(case, g.legend_config("rules_only")))
        out[case.split][case.case_id] = (sc["n_hit"], sc["n_items"], sc["n_pred_hit"], sc["n_pred"])
    return out


def _agg(cases: dict[str, tuple[int, int, int, int]]) -> tuple[float | None, float | None]:
    hit = sum(v[0] for v in cases.values())
    items = sum(v[1] for v in cases.values())
    ph = sum(v[2] for v in cases.values())
    pred = sum(v[3] for v in cases.values())
    return (hit / items if items else None, ph / pred if pred else None)


def main() -> int:
    from pre_peer_checker.eval.generalization import LEDGER_PATH, _git_rev as git_rev, _suggest

    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rule", action="append", default=[])
    args = ap.parse_args()

    base = _score([])
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = {}
    for rule, (summary, patches) in ABLATIONS.items():
        if args.rule and rule not in args.rule:
            continue
        try:
            abl = _score(patches)
        except LookupError as exc:
            print(f"{rule:26} skipped: {exc}")
            continue
        measured = {}
        for split in ("dev", "holdout"):
            r1, p1 = _agg(base[split])
            r0, p0 = _agg(abl[split])
            changed = sorted(c for c in base[split] if base[split][c] != abl[split].get(c))
            measured[split] = {
                "contribution_recall": None if r1 is None or r0 is None else round(r1 - r0, 4),
                "contribution_precision": None if p1 is None or p0 is None else round(p1 - p0, 4),
                "n_cases_changed": len(changed),
                "measured_at": now,
                "git_rev": git_rev(),
            }
            if split == "dev":
                measured[split]["cases_changed"] = changed
        rows[rule] = (summary, measured)
        d, h = measured["dev"], measured["holdout"]
        print(
            f"{rule:26} dev dR={d['contribution_recall']:+.4f} dP={d['contribution_precision']:+.4f} "
            f"({d['n_cases_changed']} cases)  holdout dR={h['contribution_recall']:+.4f} "
            f"dP={h['contribution_precision']:+.4f} ({h['n_cases_changed']} cases)"
        )
    if args.dry_run:
        return 0

    ledger = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    by_id = {r["id"]: r for r in ledger["rules"]}
    for rule, (summary, measured) in rows.items():
        rid = f"parser:{rule}"
        entry = by_id.get(rid)
        if entry is None:
            entry = {
                "id": rid,
                "task": "legend",
                "kind": "parser_rule",
                "summary": summary,
                "baseline_config": "rules_only",
                "ablation_config": f"rules_only-minus-{rule}",
                "origin_cases": measured["dev"].get("cases_changed", []),
                "origin_difficulty": [],
                "decision": "keep",
                "suggestion": "no_data",
                "measured": {},
            }
            ledger["rules"].append(entry)
            by_id[rid] = entry
        entry["measured"] = measured
        h = measured["holdout"]
        entry["suggestion"] = _suggest([h["contribution_recall"], h["contribution_precision"]])
    ledger["updated_at"] = now
    LEDGER_PATH.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"updated {LEDGER_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
