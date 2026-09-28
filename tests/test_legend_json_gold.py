"""読む JSON（Legend 抽出）合成ゴールド CI — Phase 6A.

GPU 不要。rules 経路で Fig2A / Fig4K / Fig5E 型などの必須スパンを再現する。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from pre_peer_checker.llm.legend_extract import extract_legend_json_hybrid

ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "fixtures" / "gold" / "legend_json" / "legend_json_synthetic_matrix.json"


def _load_matrix() -> dict:
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def _norm(s: str) -> str:
    t = (s or "").strip().lower()
    t = t.replace("−", "-").replace("–", "-")
    t = re.sub(r"\s+", " ", t)
    return t


def _group_tokens(s: str) -> set[str]:
    t = _norm(s)
    t = t.replace("(", " ").replace(")", " ").replace(",", " ")
    t = t.replace("+/-", " ").replace("±", " ")
    return {x for x in re.split(r"[^\w]+", t) if x}


def _group_match(pred: str, gold: str, aliases: list[str] | None) -> bool:
    cands = [_norm(gold), *(_norm(a) for a in (aliases or []))]
    p = _norm(pred)
    if not gold and not pred:
        return True
    if p in cands:
        return True
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


def _pred_rows(figure: str, legend_text: str) -> list[dict]:
    parsed = extract_legend_json_hybrid(legend_text, figure_hint=figure)
    rows: list[dict] = []
    for p in parsed.panels:
        groups = list(p.groups or [])
        if not groups:
            rows.append({"panel": p.panel.upper(), "n": p.n, "group": ""})
        else:
            for g in groups:
                rows.append({"panel": p.panel.upper(), "n": p.n, "group": str(g)})
    return rows


def _pred_rows_from_chunk(entry: dict) -> list[dict]:
    from pre_peer_checker.llm.legend_extract import extract_check_items_from_chunk
    from pre_peer_checker.parsers.figure_chunks import FigureChunk

    chunk_meta = entry.get("chunk") or {}
    chunk = FigureChunk(
        figure_id=str(entry["figure"]),
        figure_num=str(entry["figure"]).split()[-1],
        legend=str(entry["legend_text"]),
        results=list(chunk_meta.get("results") or []),
        panel_labels_from_figure=list(chunk_meta.get("panel_labels_from_figure") or []),
    )
    must = list(entry.get("prompt_must_contain") or [])
    if must:
        body = chunk.prompt_body()
        for needle in must:
            assert needle in body, f"{entry['id']}: missing {needle!r} in prompt"

    parsed = extract_check_items_from_chunk(chunk, prefer_llm=False, legend_only_prompt=False)
    rows: list[dict] = []
    for p in parsed.panels:
        groups = list(p.groups or [])
        if not groups:
            rows.append({"panel": p.panel.upper(), "n": p.n, "group": ""})
        else:
            for g in groups:
                rows.append({"panel": p.panel.upper(), "n": p.n, "group": str(g)})
    return rows


def _hit(expect: dict, preds: list[dict]) -> bool:
    panel = str(expect.get("panel") or "").upper()
    n = expect.get("n")
    group = str(expect.get("group") or "")
    aliases = list(expect.get("aliases_group") or [])
    for pr in preds:
        if pr["panel"] != panel or pr["n"] != n:
            continue
        if _group_match(pr["group"], group, aliases):
            return True
    return False


def _forbidden(forbid: dict, preds: list[dict]) -> bool:
    """True if a forbidden (panel, n) appears (group ignored)."""
    panel = str(forbid.get("panel") or "").upper()
    n = forbid.get("n")
    return any(pr["panel"] == panel and pr["n"] == n for pr in preds)


@pytest.mark.parametrize(
    "entry",
    [e for e in _load_matrix()["entries"] if e.get("ci_required")],
    ids=lambda e: e["id"],
)
def test_legend_json_synthetic_ci_required(entry: dict):
    if entry.get("chunk"):
        preds = _pred_rows_from_chunk(entry)
    else:
        preds = _pred_rows(str(entry["figure"]), str(entry["legend_text"]))
    missing = [ex for ex in entry.get("expect") or [] if not _hit(ex, preds)]
    assert not missing, f"{entry['id']}: missing {missing}; preds={preds}"
    bad = [fb for fb in entry.get("forbid") or [] if _forbidden(fb, preds)]
    assert not bad, f"{entry['id']}: forbid hit {bad}; preds={preds}"


def test_matrix_all_entries_have_ids_and_expect():
    for e in _load_matrix()["entries"]:
        assert e.get("id")
        assert e.get("figure")
        assert e.get("legend_text")
        assert e.get("expect")
        assert isinstance(e.get("ci_required"), bool)


def test_typo_entry_hybrid_drops_llm_stolen_n():
    """Rules L/M survive; hybrid must drop 7B-like K=140 and 32B-like P=140 invents."""
    entry = next(e for e in _load_matrix()["entries"] if e["id"] == "LJ-F1-typo-kl")

    def fake_7b(_prompt: str) -> str:
        return json.dumps(
            {
                "figure": "Figure 1",
                "panels": [
                    {"panel": "K", "n": 140, "groups": ["control"]},
                    {"panel": "L", "n": 88, "groups": ["control"]},
                    {"panel": "L", "n": 140, "groups": []},
                    {"panel": "M", "n": 88, "groups": []},
                ],
                "tests": [],
                "p_values": [],
                "citation": {"mentioned": False},
            }
        )

    def fake_32b(_prompt: str) -> str:
        return json.dumps(
            {
                "figure": "Figure 1",
                "panels": [
                    {"panel": "P", "n": 140, "groups": ["k"]},
                    {"panel": "P", "n": 88, "groups": ["l"]},
                    {"panel": "L", "n": 140, "groups": []},
                    {"panel": "M", "n": 88, "groups": []},
                ],
                "tests": [],
                "p_values": [],
                "citation": {"mentioned": False},
            }
        )

    for fake in (fake_7b, fake_32b):
        parsed = extract_legend_json_hybrid(
            str(entry["legend_text"]),
            figure_hint=str(entry["figure"]),
            llm_generate=fake,
            prefer_llm=True,
        )
        preds = []
        for p in parsed.panels:
            groups = list(p.groups or [])
            if not groups:
                preds.append({"panel": p.panel.upper(), "n": p.n, "group": ""})
            else:
                for g in groups:
                    preds.append({"panel": p.panel.upper(), "n": p.n, "group": str(g)})
        missing = [ex for ex in entry["expect"] if not _hit(ex, preds)]
        bad = [fb for fb in entry.get("forbid") or [] if _forbidden(fb, preds)]
        assert not missing, preds
        assert not bad, preds
