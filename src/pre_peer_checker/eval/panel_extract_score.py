"""Score Legend panel extraction rows against panel_extract_gold (figure, panel, group, n)."""

from __future__ import annotations

import re
from typing import Any


def _norm(s: str) -> str:
    t = (s or "").strip().lower()
    t = t.replace("−", "-").replace("–", "-")
    t = re.sub(r"\s+", " ", t)
    return t


def _group_tokens(s: str) -> set[str]:
    t = _norm(s)
    t = t.replace("(", " ").replace(")", " ").replace(",", " ").replace("+/-", " ")
    t = t.replace("+/-", " ").replace("±", " ")
    parts = [x for x in re.split(r"[^\w]+", t) if x]
    # keep genotype-style tokens; drop pure empties
    return set(parts)


def _group_match(pred: str, gold: str, aliases: list[str] | None) -> bool:
    cands = [_norm(gold), *(_norm(a) for a in (aliases or []))]
    p = _norm(pred)
    if not gold and not pred:
        return True
    if p in cands:
        return True
    # soft contain either way for "control (early L3)" vs "control, early L3"
    if any(c and (c in p or p in c) for c in cands if c):
        return True
    pt = _group_tokens(pred)
    if not pt:
        return False
    for c in cands:
        ct = _group_tokens(c)
        if ct and (ct <= pt or pt <= ct):
            return True
    return False


def _pred_matches_item(pr: dict[str, Any], it: dict[str, Any]) -> bool:
    return (
        pr["figure"] == str(it.get("figure") or "")
        and pr["panel"] == str(it.get("panel") or "").upper()
        and pr["n"] == it.get("n")
        and _group_match(pr["group"], str(it.get("group") or ""), list(it.get("aliases_group") or []))
    )


def score_precision(gold: dict[str, Any], preds: list[dict[str, Any]]) -> dict[str, Any]:
    """Share of distinct predicted (figure, panel, group, n) rows that hit a gold item.

    Only figures present in gold are scored, so supplementary / out-of-scope
    figures do not count as false positives. Gold must list every legend n in
    those figures for this to be meaningful.
    """
    items = gold.get("items") or []
    figures = {str(it.get("figure") or "") for it in items}
    uniq: dict[tuple[str, str, str, Any], dict[str, Any]] = {}
    for pr in preds:
        if pr.get("n") is None or pr["figure"] not in figures:
            continue
        uniq.setdefault((pr["figure"], pr["panel"], _norm(pr["group"]), pr["n"]), pr)
    fps: list[dict[str, Any]] = []
    tp = 0
    for pr in uniq.values():
        if any(_pred_matches_item(pr, it) for it in items):
            tp += 1
        else:
            fps.append({k: pr[k] for k in ("figure", "panel", "group", "n")})
    total = len(uniq)
    return {
        "n_pred": total,
        "n_pred_hit": tp,
        "precision": (tp / total) if total else None,
        "false_positives": fps,
    }


def score_one(gold: dict[str, Any], preds: list[dict[str, Any]]) -> dict[str, Any]:
    items = gold.get("items") or []
    hit = 0
    details: list[dict[str, Any]] = []
    forbid_fp = 0
    for it in items:
        fig = str(it.get("figure") or "")
        panel = str(it.get("panel") or "").upper()
        group = str(it.get("group") or "")
        n_gold = it.get("n")
        aliases = list(it.get("aliases_group") or [])
        matched = False
        matched_row = None
        for pr in preds:
            if pr["figure"] != fig or pr["panel"] != panel:
                continue
            if pr["n"] != n_gold:
                continue
            if _group_match(pr["group"], group, aliases):
                matched = True
                matched_row = pr
                break
        if matched:
            hit += 1
        # forbid wrong panel letters (e.g. Fig5 A when gold is E)
        for bad in it.get("forbid_panels") or []:
            for pr in preds:
                if (
                    pr["figure"] == fig
                    and pr["panel"] == str(bad).upper()
                    and pr["n"] == n_gold
                ):
                    forbid_fp += 1
        details.append(
            {
                "id": it.get("id"),
                "matched": matched,
                "gold": {"figure": fig, "panel": panel, "group": group, "n": n_gold},
                "pred": matched_row,
                "difficulty": it.get("difficulty"),
            }
        )
    total = len(items)
    recall = (hit / total) if total else None
    prec = score_precision(gold, preds)
    p = prec["precision"]
    f1 = (2 * p * recall / (p + recall)) if p and recall else None
    return {
        "n_items": total,
        "n_hit": hit,
        "recall": recall,
        "precision": p,
        "f1": f1,
        "n_pred": prec["n_pred"],
        "n_pred_hit": prec["n_pred_hit"],
        "forbid_panel_fp": forbid_fp,
        "false_positives": prec["false_positives"],
        "items": details,
    }


def format_score(label: str, sc: dict[str, Any]) -> str:
    def f(x: float | None) -> str:
        return "n/a" if x is None else f"{x:.3f}"

    return (
        f"{label}: recall={f(sc['recall'])} ({sc['n_hit']}/{sc['n_items']}) "
        f"precision={f(sc['precision'])} ({sc['n_pred_hit']}/{sc['n_pred']}) "
        f"f1={f(sc['f1'])} forbid_fp={sc['forbid_panel_fp']}"
    )
