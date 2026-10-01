"""Phase 1 deterministic engines."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from pre_peer_checker.data.group_vectors import (
    GroupVector,
    extract_group_vectors,
    find_cross_table_matches,
)
from pre_peer_checker.engine.n_and_names import match_legend_n_to_vectors
from pre_peer_checker.engine.plot_table_match import (
    best_table_for_plot,
    values_match_score,
    warnings_from_plot_table_mismatch,
)
from pre_peer_checker.parsers.legend_struct import parse_panel_ns
from pre_peer_checker.parsers.pdf_plot_digitize import DigitizedGroup, DigitizedPlot
from pre_peer_checker.pipeline.orchestrator import run_verification


def test_parse_panel_ns_figure1_style():
    text = (
        "(H and I) Quantification of clone volume and relative clone size, "
        "n=12 (E), 12 (F), and 12 (G). ***p<0.001 by Dunnet test. "
        "(Q) Quantification of larval geneY-/- clone size. n=17 (N) and 12 (O)."
    )
    pns = parse_panel_ns("Figure 1", text)
    by = {p.panel: p.n for p in pns}
    assert by["E"] == 12
    assert by["F"] == 12
    assert by["G"] == 12
    assert by["N"] == 17
    assert by["O"] == 12


def test_parse_panel_ns_dose_labels_are_groups():
    text = (
        "(E) Quantification of relative circulating HormoneX-HF levels under "
        "control or rich food (1x or 4x). n=5 (1x), 8 (4x). **p<0.005."
    )
    pns = parse_panel_ns("Figure 5", text)
    assert {(p.n, p.group) for p in pns if p.panel == "E"} == {(5, "1x"), (8, "4x")}


def test_reconcile_drops_section_k_keeps_hij():
    """LLM mislabels K with n=11 (H),20(I),9(J) → keep H/I/J from rules."""
    from pre_peer_checker.llm.legend_extract import (
        _merge_llm_over_rules,
        _rules_from_chunk,
    )
    from pre_peer_checker.llm.legend_schema import LegendFigureJSON, LegendPanelJSON
    from pre_peer_checker.parsers.figure_chunks import FigureChunk

    legend = (
        "Figure 4. (H-J) clones. (K) Quantification of clone size. "
        "n=11 (H), 20 (I), and 9 (J). ***p<0.001 by Dunnett test."
    )
    chunk = FigureChunk(figure_id="Figure 4", figure_num="4", legend=legend)
    rules = _rules_from_chunk(chunk)
    assert {p.panel for p in rules.panels} >= {"H", "I", "J"}

    bad_llm = LegendFigureJSON(
        figure="Figure 4",
        panels=[
            LegendPanelJSON(
                panel="K",
                n=11,
                evidence_span="n=11 (H), 20 (I), and 9 (J)",
                confidence=0.9,
            )
        ],
        extractor="llm",
    )
    merged = _merge_llm_over_rules(rules, bad_llm, llm_primary=True)
    panels = {p.panel: p.n for p in merged.panels}
    assert "K" not in panels
    assert panels.get("H") == 11
    assert panels.get("I") == 20
    assert panels.get("J") == 9
    assert merged.extractor == "llm+rules"


def test_merge_fills_omitted_panel_groups_from_rules():
    """If LLM skips panel J entirely, rule rows n=13 (WT), 10 (mutx+/-) are kept."""
    from pre_peer_checker.llm.legend_extract import _merge_llm_over_rules
    from pre_peer_checker.llm.legend_schema import LegendFigureJSON, LegendPanelJSON

    rules = LegendFigureJSON(
        figure="Figure 2",
        panels=[
            LegendPanelJSON(panel="J", n=13, groups=["WT"], evidence_span="n=13 (WT), 10 (mutx+/-)"),
            LegendPanelJSON(
                panel="J", n=10, groups=["mutx+/-"], evidence_span="n=13 (WT), 10 (mutx+/-)"
            ),
            LegendPanelJSON(panel="A", n=4, groups=["early L3"], evidence_span="n=4 (early L3)"),
        ],
        extractor="rules",
    )
    llm = LegendFigureJSON(
        figure="Figure 2",
        panels=[
            LegendPanelJSON(panel="A", n=4, groups=["early L3"], evidence_span="n=4 (early L3)", confidence=0.9),
        ],
        extractor="llm",
    )
    merged = _merge_llm_over_rules(rules, llm, llm_primary=True)
    j = {(p.groups[0] if p.groups else ""): p.n for p in merged.panels if p.panel == "J"}
    assert j.get("WT") == 13
    assert j.get("mutx+/-") == 10


def test_merge_keeps_rule_range_when_llm_reads_only_the_low_end():
    from pre_peer_checker.llm.legend_extract import _merge_llm_over_rules
    from pre_peer_checker.llm.legend_schema import LegendFigureJSON, LegendPanelJSON

    rules = LegendFigureJSON(
        figure="Figure 3",
        panels=[LegendPanelJSON(panel="B", n=7, n_max=9, evidence_span="n = 7–9 per group")],
        extractor="rules",
    )
    llm = LegendFigureJSON(
        figure="Figure 3",
        panels=[LegendPanelJSON(panel="B", n=7, evidence_span="n = 7–9 per group", confidence=0.9)],
        extractor="llm",
    )
    merged = _merge_llm_over_rules(rules, llm, llm_primary=True)
    assert [(p.panel, p.n, p.n_max) for p in merged.panels] == [("B", 7, 9)]


def test_parse_panel_ns_lowercase_groups_bind_to_quant_panel():
    """n=18 (a) and 10 (b) after quantification (M) must NOT become panels A/B."""
    text = (
        "(K-M) Eye discs bearing ubi-GFP-labeled geneY-/- clones in control (K), "
        "with forced HormoneX release (L), "
        "and quantification of geneY-/- clone volume(M). "
        "n=18 (a) and 10 (b). ***p<0.001 by Welch's t-test."
    )
    pns = parse_panel_ns("Figure 2", text)
    assert not any(p.panel == "A" for p in pns)
    assert not any(p.panel == "B" and not p.group for p in pns)
    ms = [(p.n, p.group) for p in pns if p.panel == "M"]
    assert (18, "a") in ms
    assert (10, "b") in ms


def test_parse_panel_ns_legend_typo_kl_maps_to_lm():
    """n=140 (k) and 88 (l) under (P) are panel-letter typos → L=140, M=88."""
    text = (
        "(P) Quantification of adult phenotype frequency, "
        "n=140 (k) and 88 (l). p<0.001 by Pearson's Chi-squared test."
    )
    pns = parse_panel_ns("Figure 1", text)
    by_panel = {p.panel: p for p in pns if not p.group}
    assert by_panel["L"].n == 140
    assert by_panel["M"].n == 88
    assert not any(p.panel == "K" and p.n == 140 for p in pns)
    assert not any(p.panel == "P" and p.group in {"k", "l"} for p in pns)


def test_parse_published_nature_n_equals_styles():
    """Nature-style N = dishes / day lists / shared f,g — empty group for units."""
    text = (
        "Fig. 1 | Title. d qRT-PCR. Mean ± SEM. N = 3 dishes from 3 experiments. "
        "g Percentage. N = 14 images from 3 experiments (day 10), 25 images "
        "from 3 experiments (day 13), and 42 images from 9 experiments (day 15). "
        "h Sarcomeres. N = 88 sarcomere fibers from 4 experiments. "
        "f,g Aggregates. N = 57 aggregates from 7 experiments. "
        "Source data are provided as a Source Data file."
    )
    pns = parse_panel_ns("Figure 1", text)
    assert any(p.panel == "D" and p.n == 3 and not p.group for p in pns)
    assert {(p.n, p.group) for p in pns if p.panel == "G"} >= {
        (14, "day 10"),
        (25, "day 13"),
        (42, "day 15"),
    }
    assert any(p.panel == "H" and p.n == 88 and not p.group for p in pns)
    assert any(p.panel == "F" and p.n == 57 and not p.group for p in pns)
    assert any(p.panel == "G" and p.n == 57 and not p.group for p in pns)


def test_parse_published_cell_postfix_n():
    """Cell-style (A) … (n = 5) and mice/patients / (RNA-seq, n = 3)."""
    text = (
        "Figure 1. Title. "
        "(A) Heatmap (n = 5). "
        "(D) Levels from male BALB/c mice (n = 5) and clinical patients (n = 6). "
        "(G and H) Features (RNA-seq, n = 3) and epigenetics (ATAC-seq, n = 3). "
        "(J) Confirmation in mouse (n = 3). "
        "Data are mean ± SEM."
    )
    pns = parse_panel_ns("Figure 1", text)
    assert any(p.panel == "A" and p.n == 5 and not p.group for p in pns)
    assert any(p.panel == "D" and p.n == 5 and p.group == "mice" for p in pns)
    assert any(p.panel == "D" and p.n == 6 and p.group == "patients" for p in pns)
    assert any(p.panel == "G" and p.n == 3 and p.group == "RNA-seq" for p in pns)
    assert any(p.panel == "H" and p.n == 3 and p.group == "ATAC-seq" for p in pns)
    assert any(p.panel == "J" and p.n == 3 and not p.group for p in pns)


def test_merge_rules_lock_clears_invented_group_on_panel_letter_n():
    """Rules n=10 (D) with empty group beat LLM group=wild-type on same panel+n."""
    from pre_peer_checker.llm.legend_extract import _merge_llm_over_rules
    from pre_peer_checker.llm.legend_schema import LegendFigureJSON, LegendPanelJSON

    rules = LegendFigureJSON(
        figure="Figure 2",
        panels=[
            LegendPanelJSON(panel="D", n=10, groups=[], evidence_span="n=10 (D, E, G and H)"),
            LegendPanelJSON(
                panel="F", n=11, groups=["4x"], evidence_span="n=9 (1x), 10 (2x), 7 (3x), 11 (4x)"
            ),
        ],
        extractor="rules",
    )
    llm = LegendFigureJSON(
        figure="Figure 2",
        panels=[
            LegendPanelJSON(panel="D", n=10, groups=["wild-type"], evidence_span="wild-type (D)"),
            LegendPanelJSON(panel="F", n=9, groups=["1x"], evidence_span="n=9 (1x)"),
        ],
        extractor="llm",
    )
    merged = _merge_llm_over_rules(rules, llm, llm_primary=True)
    d = [p for p in merged.panels if p.panel == "D" and p.n == 10]
    assert d and not d[0].groups
    assert any(p.panel == "F" and p.n == 11 and p.groups == ["4x"] for p in merged.panels)


def test_merge_drops_llm_n_stolen_from_typo_rules():
    """Rules L=140/M=88 beat LLM inventing K=140 or P=140/(k)."""
    from pre_peer_checker.llm.legend_extract import _merge_llm_over_rules
    from pre_peer_checker.llm.legend_schema import LegendFigureJSON, LegendPanelJSON

    rules = LegendFigureJSON(
        figure="Figure 1",
        panels=[
            LegendPanelJSON(panel="L", n=140, groups=[], evidence_span="n=140 (k)"),
            LegendPanelJSON(panel="M", n=88, groups=[], evidence_span="n=88 (l)"),
        ],
        extractor="rules",
    )
    # 7B-like: invent K/L with groups
    llm_7b = LegendFigureJSON(
        figure="Figure 1",
        panels=[
            LegendPanelJSON(panel="K", n=140, groups=["control"], evidence_span="n=140 (k)"),
            LegendPanelJSON(panel="L", n=88, groups=["control"], evidence_span="n=88 (l)"),
            LegendPanelJSON(panel="L", n=140, groups=[], evidence_span="n=140 (k)"),
            LegendPanelJSON(panel="M", n=88, groups=[], evidence_span="n=88 (l)"),
        ],
        extractor="llm",
    )
    m7 = _merge_llm_over_rules(rules, llm_7b, llm_primary=True)
    assert not any(p.panel == "K" and p.n == 140 for p in m7.panels)
    assert {(p.panel, p.n) for p in m7.panels if not p.groups} >= {("L", 140), ("M", 88)}

    # 32B-like: invent P with groups k/l
    llm_32 = LegendFigureJSON(
        figure="Figure 1",
        panels=[
            LegendPanelJSON(panel="P", n=140, groups=["k"], evidence_span="n=140 (k)"),
            LegendPanelJSON(panel="P", n=88, groups=["l"], evidence_span="n=88 (l)"),
            LegendPanelJSON(panel="L", n=140, groups=[], evidence_span="n=140 (k)"),
            LegendPanelJSON(panel="M", n=88, groups=[], evidence_span="n=88 (l)"),
        ],
        extractor="llm",
    )
    m32 = _merge_llm_over_rules(rules, llm_32, llm_primary=True)
    assert not any(p.panel == "P" and p.n in {140, 88} for p in m32.panels)
    assert {(p.panel, p.n) for p in m32.panels if not p.groups} >= {("L", 140), ("M", 88)}


def test_merge_keeps_rule_n_when_llm_has_other_n_same_panel():
    """LLM J n=14 (stolen from I) must not drop rules J n=3; stolen n is dropped."""
    from pre_peer_checker.llm.legend_extract import _merge_llm_over_rules
    from pre_peer_checker.llm.legend_schema import LegendFigureJSON, LegendPanelJSON

    rules = LegendFigureJSON(
        figure="Figure 1",
        panels=[
            LegendPanelJSON(panel="J", n=3, groups=[], evidence_span="(n = 3)"),
            LegendPanelJSON(panel="I", n=14, groups=[], evidence_span="(n = 14)"),
        ],
        extractor="rules",
    )
    llm = LegendFigureJSON(
        figure="Figure 1",
        panels=[
            LegendPanelJSON(panel="J", n=14, groups=[], evidence_span="(I and J) ... (n = 14)"),
        ],
        extractor="llm",
    )
    merged = _merge_llm_over_rules(rules, llm, llm_primary=True)
    j_ns = {p.n for p in merged.panels if p.panel == "J"}
    assert j_ns == {3}
    assert any(p.panel == "I" and p.n == 14 for p in merged.panels)


def test_merge_drops_llm_paraphrased_group_on_rule_owned_panel_n():
    from pre_peer_checker.llm.legend_extract import _merge_llm_over_rules
    from pre_peer_checker.llm.legend_schema import LegendFigureJSON, LegendPanelJSON

    rules = LegendFigureJSON(
        figure="Figure 1",
        panels=[LegendPanelJSON(panel="G", n=3, groups=["RNA-seq"], evidence_span="(RNA-seq, n = 3)")],
        extractor="rules",
    )
    llm = LegendFigureJSON(
        figure="Figure 1",
        panels=[
            LegendPanelJSON(panel="G", n=3, groups=["transcriptional features of atrophy"]),
            LegendPanelJSON(panel="G", n=3, groups=["RNA-seq"]),
        ],
        extractor="llm",
    )
    merged = _merge_llm_over_rules(rules, llm, llm_primary=True)
    assert {(p.panel, tuple(p.groups), p.n) for p in merged.panels} == {("G", ("RNA-seq",), 3)}


def test_cross_table_exact_match_synthetic(tmp_path: Path):
    a = tmp_path / "fig1c_alphaexp.csv"
    b = tmp_path / "fig1h_betaexp.csv"
    # identical mut vectors across distinct experiment tokens
    pd.DataFrame(
        {"group": ["ctrl"] * 5 + ["mut"] * 5, "value": [1, 1.1, 0.9, 1.2, 1.05, 2, 2.1, 1.9, 2.2, 2.05]}
    ).to_csv(a, index=False)
    pd.DataFrame(
        {"group": ["ctrl"] * 5 + ["kd"] * 5, "value": [3, 3.1, 2.9, 3.2, 2.8, 2, 2.1, 1.9, 2.2, 2.05]}
    ).to_csv(b, index=False)
    vecs = extract_group_vectors(a) + extract_group_vectors(b)
    matches = find_cross_table_matches(vecs)
    assert any(m.exact for m in matches)


def test_legend_n_mismatch_mapping(tmp_path: Path, sided_profile):
    # Fig1C-like path (figure token of the alphaexp side)
    xlsx = tmp_path / "data" / "Fig1C" / "graph.xlsx"
    xlsx.parent.mkdir(parents=True)
    pd.DataFrame(
        {
            "label": [0] * 8 + [1] * 8 + [2] * 8,
            "value": list(range(24)),
            "genotype": ["WT"] * 8 + ["cont"] * 8 + ["mutx"] * 8,
        }
    ).to_excel(xlsx, index=False)
    from pre_peer_checker.parsers.legend_struct import PanelN

    panel_ns = [
        PanelN(panel="C", n=9, figure="Figure 1", context="n=9 (C)"),
        PanelN(panel="D", n=9, figure="Figure 1", context="n=9 (D)"),
    ]
    vecs = extract_group_vectors(xlsx)
    mism = match_legend_n_to_vectors(panel_ns, vecs)
    assert any(m.panel_n.panel == "C" and m.data_n == 8 for m in mism)


def test_pipeline_synthetic_demo():
    root = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic" / "demo"
    # rename paths to include experiment tokens for distinctness
    # demo already has identical mut/kd vectors
    result = run_verification([root])
    tags = {w.tag.value for w in result.warnings}
    assert any("データ取り違え" in t or "コントロール" in t for t in tags)


def test_values_match_score_tolerant():
    a = (1.0, 2.0, 3.0, 4.0)
    b = (1.05, 1.98, 3.1, 4.02)
    assert values_match_score(a, b, abs_tol=0.2) == 1.0
    assert values_match_score(a, (9.0, 8.0, 7.0, 6.0), abs_tol=0.2) == 0.0


def test_plot_table_mismatch_warning_synthetic():
    plot = DigitizedPlot(
        path=Path("/case/plotDump/RplotBetaExp.pdf"),
        groups=[
            DigitizedGroup("1", (1.0, 2.0, 3.0, 4.0, 5.0)),
            DigitizedGroup("2", (10.0, 11.0, 12.0, 13.0, 14.0)),
        ],
    )
    alphaexp = Path("/case/plotDump/graphAlphaExp.xlsx")
    betaexps = Path("/case/plotDump/graphBetaExpS.xlsx")
    table_vectors = {
        alphaexp: [
            GroupVector(alphaexp, "1", (1.0, 2.0, 3.0, 4.0, 5.0), 5),
            GroupVector(alphaexp, "2", (10.0, 11.0, 12.0, 13.0, 14.0), 5),
        ],
        betaexps: [
            GroupVector(betaexps, "1", (0.5, 0.6, 0.7, 0.8, 0.9, 1.0), 6),
            GroupVector(betaexps, "2", (20.0, 21.0, 22.0, 23.0, 24.0), 5),
        ],
    }
    hits = best_table_for_plot(plot, table_vectors)
    warnings = warnings_from_plot_table_mismatch(plot, hits)
    assert warnings
    assert warnings[0].metadata["pattern_id"] == "P-FILENAME-CONTENT-MISMATCH"
    assert "graphAlphaExp" in warnings[0].location


def test_h2b_inconsistent_n_synthetic(sided_profile):
    from pre_peer_checker.engine.n_and_names import warnings_inconsistent_n_identical_plots
    from pre_peer_checker.parsers.legend_struct import PanelN
    from pre_peer_checker.parsers.pdf_plot_digitize import DigitizedGroup, DigitizedPlot

    shared = (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0)
    plots = [
        DigitizedPlot(
            Path("/x/RplotAlphaExp.pdf"),
            [DigitizedGroup("1", shared), DigitizedGroup("2", tuple(x + 10 for x in shared))],
        ),
        DigitizedPlot(
            Path("/x/RplotBetaExp.pdf"),
            [DigitizedGroup("1", shared), DigitizedGroup("2", tuple(x + 10 for x in shared))],
        ),
        # Decoy: larger n in an incidental BetaExp folder must not beat RplotBetaExp
        DigitizedPlot(
            Path("/x/mutxBetaExp/exp/Rplot.pdf"),
            [DigitizedGroup("1", tuple(float(i) for i in range(16)))],
        ),
    ]
    panel_ns = [
        PanelN(panel="C", n=9, figure="Figure 1", context="n=9 (C)"),
        PanelN(panel="G", n=14, figure="Figure 1", context="n=14 (G)"),
        PanelN(panel="D", n=9, figure="Figure 1", context="n=9 (D)"),
        PanelN(panel="H", n=9, figure="Figure 1", context="n=9 (H)"),
    ]
    warnings = warnings_inconsistent_n_identical_plots(panel_ns, [], plots)
    h2b = [
        w
        for w in warnings
        if w.metadata.get("pattern_id") == "P-N-INCONSISTENT-ACROSS-IDENTICAL-PLOTS"
        and {"C", "G"} <= {w.metadata["panel_a"], w.metadata["panel_b"]}
    ]
    assert h2b
    w = h2b[0]
    assert w.reason.index("①") < w.reason.index("②") < w.reason.index("③")
    assert "同一点列" in w.reason
    assert "Legend n" in w.reason
    assert "正の n" in w.reason
    assert w.metadata.get("reason_order") == [
        "identical_series",
        "legend_n_mismatch",
        "n_authority",
    ]
    # Panel letters sorted: C before G in title/location
    assert "C" in w.location and "G" in w.location
    assert w.location.index("C") < w.location.index("G")
    # D/H same legend n → no H2b for that pair
    assert not any(
        {"D", "H"} <= {w.metadata.get("panel_a"), w.metadata.get("panel_b")}
        for w in warnings
    )


def test_stats_prefer_value_column(tmp_path: Path):
    from pre_peer_checker.data.stats_recalc import analyze_table_file

    xlsx = tmp_path / "graph.xlsx"
    pd.DataFrame(
        {
            "ID": [1, 2, 3, 4],
            "value": [10.0, 12.0, 20.0, 22.0],
            "genotype": ["a", "a", "b", "b"],
            "label": [1, 1, 2, 2],
        }
    ).to_excel(xlsx, index=False)
    sr = analyze_table_file(xlsx)
    means = {g.group: g.mean for g in sr.groups}
    assert means["1"] == 11.0
    assert means["2"] == 21.0
