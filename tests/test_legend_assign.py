"""LLM assignment of the stated n the rules left unread (stubbed model)."""

from __future__ import annotations

import json

from pre_peer_checker.llm.legend_assign import (
    assign_unread_ns,
    build_assign_prompt,
    legend_panel_letters,
    parse_assign_response,
    stated_n_mentions,
    unread_mentions,
)
from pre_peer_checker.llm.legend_extract import extract_check_items_from_chunk
from pre_peer_checker.llm.legend_schema import LegendFigureJSON, LegendPanelJSON
from pre_peer_checker.parsers.figure_chunks import FigureChunk


def _vals(text: str) -> list[tuple[int, int | None]]:
    return [(m.n, m.n_max) for m in stated_n_mentions(text)]


def test_mentions_cover_lists_ranges_and_worded_counts():
    assert _vals("N = 15 (day 3), 93 (day 6), and 48 (day 15) aggregates.") == [
        (15, None), (93, None), (48, None)
    ]
    assert _vals("cells (n = 28–32 per group)") == [(28, 32)]
    assert _vals("nuclei, n = 171 to 221; junctions, n = 934 to 1194") == [(171, 221), (934, 1194)]
    assert _vals("n (BW) = 6; scale bar 100 µm") == [(6, None)]
    # worded counts only when the legend has no ``n =``
    assert _vals("Data from three independent experiments.") == [(3, None)]
    assert _vals("n = 5. Data from three independent experiments.") == [(5, None)]


def test_prompt_tags_each_mention_in_place():
    text = "(C) WT, n = 8; KO, n = 6."
    prompt = build_assign_prompt("Figure 1", text, stated_n_mentions(text))
    assert "WT, n = 8 [#1]; KO, n = 6 [#2]." in prompt
    assert "Tags: #1 = 8, #2 = 6" in prompt


def test_answers_align_by_echoed_n_when_ids_drift():
    text = "MDD, n = 6; control, n = 4."
    ms = stated_n_mentions(text)
    raw = json.dumps({"tags": [
        {"id": 1, "n": 99, "panels": ["M"], "skip": True},
        {"id": 2, "n": 6, "panels": ["A", "B"], "group": "MDD"},
        {"id": 3, "n": 4, "panels": "A, B", "group": "control"},
    ]})
    got = [(m.n, panels, g) for m, panels, g in parse_assign_response(raw, ms)]
    assert got == [(6, ["A", "B"], "MDD"), (4, ["A", "B"], "control")]


def test_skipped_tags_and_free_text_around_json():
    text = "n = 12 and 13 (total 25)."
    ms = stated_n_mentions(text)
    raw = "```json\n" + json.dumps({"tags": [
        {"id": 1, "n": 12, "panels": ["B"], "group": "WT", "skip": False},
        {"id": 2, "n": 13, "panels": ["B"], "group": "KO", "skip": True},
    ]}) + "\n```"
    assert [(m.n, p) for m, p, _g in parse_assign_response(raw, ms)] == [(12, ["B"])]


def test_only_unread_values_are_added():
    legend = "Figure 2. (A) WT, n = 8. (B-C) Quantification. For all panels n = 5 mice."
    rules = LegendFigureJSON(figure="Figure 2", panels=[LegendPanelJSON(panel="A", n=8, groups=["WT"])])
    assert [m.n for m in unread_mentions(legend, rules)] == [5]
    raw = json.dumps({"tags": [
        {"id": 1, "n": 8, "panels": ["A", "B"], "group": "WT"},
        {"id": 2, "n": 5, "panels": ["B", "C"], "group": ""},
    ]})
    out = assign_unread_ns("Figure 2", legend, rules, lambda _p: raw)
    rows = sorted((p.panel, p.n, tuple(p.groups)) for p in out.panels)
    # n = 8 stays as the rules read it; only the unread n = 5 comes from the model
    assert rows == [("A", 8, ("WT",)), ("B", 5, ()), ("C", 5, ())]
    assert out.extractor == "rules+llm-assign"


def test_panel_letters_the_legend_references():
    assert legend_panel_letters("(A) Images. (B–D) Quantified area. n = 5.") >= {"A", "B", "C", "D"}
    assert legend_panel_letters("Overview. d IHC images. f,g Effects of size. in (e).") >= {"D", "E", "F", "G"}
    assert not legend_panel_letters("Serum levels in WT and KO mice. n = 7 for all groups.") & set("ABCDEF")


def test_letters_absent_from_the_legend_are_dropped():
    legend = "Figure 5. Serum levels in WT and KO mice. n = 7 for all groups."
    rules = LegendFigureJSON(figure="Figure 5")
    raw = json.dumps({"tags": [{"id": 1, "n": 7, "panels": ["A", "B"], "group": ""}]})
    assert assign_unread_ns("Figure 5", legend, rules, lambda _p: raw).panels == []


def test_model_not_called_when_rules_read_everything():
    legend = "Figure 1. (A) n = 4."
    rules = LegendFigureJSON(figure="Figure 1", panels=[LegendPanelJSON(panel="A", n=4)])

    def boom(_p: str) -> str:
        raise AssertionError("LLM must not be called")

    assert assign_unread_ns("Figure 1", legend, rules, boom) is rules


def test_auto_chunk_path_uses_assign_generate(monkeypatch):
    from pre_peer_checker.llm import legend_extract

    monkeypatch.setattr(
        legend_extract, "_rules_from_chunk", lambda ch: LegendFigureJSON(figure=ch.figure_id)
    )
    chunk = FigureChunk(
        figure_id="Figure 3",
        figure_num="3",
        legend="Figure 3. (A) Images of the retina. (B) Quantified area; n = 7 eyes per group.",
    )
    calls: list[str] = []

    def gen(prompt: str) -> str:
        calls.append(prompt)
        return json.dumps({"tags": [{"id": 1, "n": 7, "panels": ["B"], "group": ""}]})

    out = extract_check_items_from_chunk(
        chunk, llm_generate=None, prefer_llm=True, only_if_unread=True, assign_generate=gen
    )
    assert len(calls) == 1 and "[#1]" in calls[0]
    assert [(p.panel, p.n) for p in out.panels if p.n == 7] == [("B", 7)]


def test_vote_keeps_only_agreed_panels_and_verify_applies_corrections():
    from pre_peer_checker.llm.legend_assign import (
        NMention,
        apply_verdicts,
        assign_unread_ns,
        vote,
    )

    m1, m2, m3 = NMention(0, 1, 8), NMention(5, 6, 9), NMention(9, 10, 4)
    a = [(m1, ["B", "C"], "WT"), (m2, ["D"], "KO"), (m3, ["E"], "")]
    b = [(m1, ["C"], "WT"), (m2, ["F"], "KO")]
    assert vote(a, b) == [(m1, ["C"], "WT")]

    raw = json.dumps({"tags": [{"id": 1, "ok": True}, {"id": 2, "ok": False, "panels": ["F"]},
                               {"id": 3, "ok": False, "panels": []}]})
    assert apply_verdicts(raw, a) == [(m1, ["B", "C"], "WT"), (m2, ["F"], "KO")]

    text = "Figure 2. (B) Growth of WT larvae, n = 8 [#1]. (C) Size of KO larvae (n = 9)."
    rules = LegendFigureJSON(figure="Figure 2", panels=[])

    def gen(prompt: str) -> str:
        # the two readings disagree on the second tag
        second = ["C"] if "Work tag by tag" in prompt else ["B"]
        return json.dumps({"tags": [
            {"id": 1, "n": 8, "panels": ["B"], "group": "WT", "skip": False},
            {"id": 2, "n": 9, "panels": second, "group": "KO", "skip": False},
        ]})

    out = assign_unread_ns("Figure 2", text, rules, gen, strategy="vote")
    assert {(p.panel, p.n) for p in out.panels} == {("B", 8)}
