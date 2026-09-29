"""Phase 6B: data fingerprints + entity link (incl. incomplete data folder)."""

from __future__ import annotations

from pathlib import Path

from pre_peer_checker.data.fingerprint import (
    exact_match,
    fingerprint_from_values,
    fingerprint_from_vector,
    stats_close,
)
from pre_peer_checker.data.group_vectors import GroupVector, extract_group_vectors
from pre_peer_checker.engine.entity_link import (
    LinkStatus,
    LinkTier,
    link_plot_for_panel,
    link_raw_for_panel,
)
from pre_peer_checker.engine.n_and_names import match_legend_n_to_vectors
from pre_peer_checker.engine.n_matrix import build_n_matrix
from pre_peer_checker.parsers.legend_struct import PanelN
from pre_peer_checker.pipeline.run_coverage import build_run_coverage
from pre_peer_checker.io_bundle import FileKind, InputBundle


def test_fingerprint_exact_and_stats():
    a = fingerprint_from_values((1.0, 3.0, 2.0), group_key="wt")
    b = fingerprint_from_values((3.0, 1.0, 2.0), group_key="renamed")
    c = fingerprint_from_values((1.0, 3.0, 2.001), group_key="near")
    assert exact_match(a, b)
    assert a.value_hash == b.value_hash
    assert not exact_match(a, c)
    # same n/mean/sd within eps after rounding noise on one point → not exact, but close-ish means
    d = fingerprint_from_values((1.0, 2.0, 3.0), group_key="x")
    e = fingerprint_from_values((1.0, 2.0, 3.0), group_key="y")
    assert stats_close(d, e)


def test_tier1_links_renamed_raw_to_plot(tmp_path: Path):
    """Same numeric content under different filenames → Tier1 fingerprint link."""
    data = tmp_path / "data" / "Fig1"
    data.mkdir(parents=True)
    raw = data / "experiment_A.csv"
    plot = data / "graph_panel.csv"
    body = "label,value\n0,1.5\n0,2.5\n0,3.5\n0,4.5\n"
    raw.write_text(body)
    plot.write_text(body)
    vecs = extract_group_vectors(raw) + extract_group_vectors(plot)
    pn = PanelN(panel="E", n=4, figure="Figure 1", context="n=4 (E)", group="0")

    plot_link = link_plot_for_panel(pn, vecs)
    assert plot_link.status == LinkStatus.LINKED
    raw_link = link_raw_for_panel(pn, vecs, plot_anchor=plot_link.vector)
    assert raw_link.status == LinkStatus.LINKED
    assert raw_link.tier == LinkTier.TIER1
    assert raw_link.vector is not None
    assert raw_link.vector.source.name == "experiment_A.csv"
    assert "指紋" in raw_link.reason


def test_data_missing_only_when_no_tables_at_all():
    pn = PanelN(panel="B", n=53, figure="Figure 1", context="n=53 (B)")
    assert link_raw_for_panel(pn, []).status == LinkStatus.DATA_MISSING
    assert match_legend_n_to_vectors([pn], []) == []


def test_other_figure_folder_is_unlinked_not_missing(tmp_path: Path):
    """Tables exist under Fig1/; Fig2 panel fails content link → unlinked, not data_missing."""
    fig1 = tmp_path / "Fig1" / "plotDump"
    fig1.mkdir(parents=True)
    only = fig1 / "WT.csv"
    only.write_text("label,value\n0,1\n0,2\n0,3\n")
    vecs = extract_group_vectors(only)
    pn = PanelN(panel="A", n=12, figure="Figure 2", context="n=12 (A)")

    raw = link_raw_for_panel(pn, vecs, table_paths=[only])
    assert raw.status == LinkStatus.UNLINKED
    assert "データ未投入" not in raw.reason

    rows = build_n_matrix([pn], vecs, case_roots=[tmp_path], table_paths=[only])
    assert rows[0].data_link_status == "unlinked"
    assert rows[0].input_gap is False


def test_coverage_reports_input_gaps():
    bundle = InputBundle()
    bundle.files[FileKind.DOCX] = []
    arts = {
        "n_matrix": [
            {
                "figure": "Figure 2",
                "panel": "A",
                "input_gap": True,
                "data_link_status": "data_missing",
                "data": {"link_status": "data_missing", "detail": "データ未投入"},
                "mismatch": False,
            },
            {
                "figure": "Figure 1",
                "panel": "E",
                "input_gap": False,
                "data_link_status": "linked",
                "data": {"link_status": "linked", "n": 11},
                "mismatch": True,
            },
        ]
    }
    cov = build_run_coverage(bundle, arts, corpus_provided=False)
    by_id = {c["id"]: c for c in cov["checks"]}
    assert by_id["input_gaps"]["status"] == "ran"
    assert "ファイル忘れ" in by_id["input_gaps"]["detail"]
    assert "データ未投入 1" in by_id["n_matrix"]["detail"]


def test_n_matrix_tier1_fingerprint_in_detail(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir()
    raw = data / "fig1_wt.csv"
    graph = data / "graph_fig1.csv"
    body = "label,value\nwt,10\nwt,11\nwt,12\n"
    raw.write_text(body)
    graph.write_text(body)
    vecs = extract_group_vectors(raw) + extract_group_vectors(graph)
    pn = PanelN(panel="E", n=3, figure="Figure 1", context="n=3 (E)", group="wt")
    rows = build_n_matrix([pn], vecs, case_roots=[tmp_path])
    assert rows[0].data.link_tier in {"tier1", "tier2", "soft"}
    assert rows[0].data.n == 3
    assert rows[0].plot.n == 3


def test_soft_rejects_fig_path_alone_when_n_mismatches(tmp_path: Path):
    """Panel B n=53 must not soft-link to unrelated n=4 under Fig1/."""
    fig1 = tmp_path / "Fig1" / "plotDump"
    fig1.mkdir(parents=True)
    graph = fig1 / "graphBetaExpS.csv"
    graph.write_text("label,value\n1,1.0\n1,2.0\n1,3.0\n1,4.0\n")
    vecs = extract_group_vectors(graph)
    pn = PanelN(panel="B", n=53, figure="Figure 1", context="n=53 (B)")
    plot = link_plot_for_panel(pn, vecs, table_paths=[graph])
    assert plot.status == LinkStatus.UNLINKED
    assert plot.tier == LinkTier.NONE


def test_soft_rejects_ambiguous_n_only_across_files(tmp_path: Path):
    """Two Fig2 files with same n and no group/panel → soft refuses."""
    fig = tmp_path / "Fig2" / "exp"
    fig.mkdir(parents=True)
    a = fig / "alpha.csv"
    b = fig / "beta.csv"
    body = "label,value\nx,1\nx,2\nx,3\nx,4\n"
    a.write_text(body)
    b.write_text(body)
    vecs = extract_group_vectors(a) + extract_group_vectors(b)
    # Panel Z has no group keys
    pn = PanelN(panel="Z", n=4, figure="Figure 2", context="n=4 (Z)")
    raw = link_raw_for_panel(pn, vecs, table_paths=[a, b])
    assert raw.status == LinkStatus.UNLINKED


def test_soft_keeps_group_match_despite_multi_file_tie(tmp_path: Path):
    """Group-matched soft may keep best when two condition files both have the group."""
    flat = tmp_path / "data"
    flat.mkdir()
    a = flat / "quant_a.csv"
    b = flat / "quant_b.csv"
    a.write_text("group,value\nctrl,1\nctrl,1.1\nctrl,0.9\nctrl,1.2\nctrl,1.05\n")
    b.write_text("group,value\nctrl,9\nctrl,9.1\nctrl,9.2\nctrl,9.3\nctrl,9.4\n")
    vecs = extract_group_vectors(a) + extract_group_vectors(b)
    pn = PanelN(panel="A1", n=6, figure="Figure 1", context="n=6 (A1)", group="ctrl")
    raw = link_raw_for_panel(pn, vecs, table_paths=[a, b])
    # quant_* counts as plot table — use plot link
    from pre_peer_checker.engine.entity_link import link_plot_for_panel

    plot = link_plot_for_panel(pn, vecs, table_paths=[a, b])
    assert plot.status == LinkStatus.LINKED
    assert plot.tier == LinkTier.SOFT
    assert plot.vector is not None
    assert plot.vector.n == 5  # legend 6 vs data 5


def test_tier1_unique_n_ignores_plotdump_under_other_fig(tmp_path: Path):
    """PlotDump under Fig5 must not satisfy Figure 1 path_claims via alias token."""
    fig1 = tmp_path / "Fig1" / "x"
    fig5 = tmp_path / "Fig5" / "PlotDump"
    fig1.mkdir(parents=True)
    fig5.mkdir(parents=True)
    (fig1 / "a.csv").write_text("label,value\n1,1\n")
    rows = "\n".join(f"0,{i}" for i in range(17))
    (fig5 / "GradualRatio.csv").write_text("label,value\n" + rows + "\n")
    vecs = extract_group_vectors(fig1 / "a.csv") + extract_group_vectors(
        fig5 / "GradualRatio.csv"
    )
    pn = PanelN(panel="N", n=17, figure="Figure 1", context="n=17 (N)")
    raw = link_raw_for_panel(
        pn, vecs, table_paths=[fig1 / "a.csv", fig5 / "GradualRatio.csv"]
    )
    assert raw.vector is None or "Fig5" not in str(raw.vector.source)
    assert raw.vector is None or raw.vector.source.name != "GradualRatio.csv"


def test_script_supported_prefers_resolved_read(tmp_path: Path):
    fig = tmp_path / "Fig2" / "exp"
    fig.mkdir(parents=True)
    target = fig / "good.csv"
    other = fig / "other.csv"
    body = "label,value\n0,1\n0,2\n0,3\n"
    target.write_text(body)
    other.write_text(body)
    vecs = extract_group_vectors(target) + extract_group_vectors(other)
    pn = PanelN(panel="E", n=3, figure="Figure 2", context="n=3 (E)", group="0")
    arts = [
        {
            "path": str(fig / "from_Rhistory.R"),
            "source_kind": "rscript",
            "reads": [{"path": "good.csv", "resolved_path": str(target)}],
        }
    ]
    raw = link_raw_for_panel(
        pn, vecs, table_paths=[target, other], script_artifacts=arts
    )
    assert raw.status == LinkStatus.LINKED
    assert raw.vector is not None
    assert raw.vector.source.name == "good.csv"


def test_tier3_bridges_genotype_notation(tmp_path: Path):
    """Legend 'mutx mutant' ↔ table 'mutx-/-' → Tier3 candidate-key link."""
    data = tmp_path / "Fig1" / "data"
    data.mkdir(parents=True)
    mut = data / "mut.csv"
    wt = data / "wt.csv"
    mut.write_text("group,value\n" + "".join(f"mutx-/-,{i + 1}.0\n" for i in range(5)))
    wt.write_text("group,value\n" + "".join(f"wt,{i + 10}.0\n" for i in range(5)))
    vecs = extract_group_vectors(mut) + extract_group_vectors(wt)
    pn = PanelN(
        panel="A",
        n=5,
        figure="Figure 1",
        context="n=5 (A)",
        group="mutx mutant",
    )
    raw = link_raw_for_panel(pn, vecs, table_paths=[mut, wt])
    assert raw.status == LinkStatus.LINKED
    assert raw.tier == LinkTier.TIER3
    assert raw.vector is not None
    assert raw.vector.group_key == "mutx-/-"
    assert "候補キー" in raw.reason


def test_key_normalize_proposes_alias():
    from pre_peer_checker.engine.key_normalize import propose_key_candidates

    hits = propose_key_candidates(["mutx mutant"], ["mutx-/-", "wt"])
    assert any(h.source == "mutx mutant" and "mutx-/-" in h.aliases for h in hits)


def test_tier2_legend_mean_disambiguates_same_n(tmp_path: Path):
    """Two groups share n=5; Legend mean picks the matching table (no group synonym)."""
    data = tmp_path / "Fig1" / "data"
    data.mkdir(parents=True)
    low = data / "low.csv"
    high = data / "high.csv"
    low.write_text("group,value\n" + "".join(f"low,{0.8 + i * 0.1}\n" for i in range(5)))
    high.write_text("group,value\n" + "".join(f"high,{4.8 + i * 0.1}\n" for i in range(5)))
    vecs = extract_group_vectors(low) + extract_group_vectors(high)
    pn = PanelN(
        panel="B",
        n=5,
        figure="Figure 1",
        context="low 1.0±0.1 (n=5); high 5.0±0.2 (n=5).",
        group="",  # force mean disambiguation (not n+group)
    )
    raw = link_raw_for_panel(pn, vecs, table_paths=[low, high])
    assert raw.status == LinkStatus.LINKED
    assert raw.tier == LinkTier.TIER2
    assert raw.vector is not None
    assert raw.vector.source.name == "low.csv"
    assert "Legend数値" in raw.reason


def test_llm_alias_hook_adopts_via_tier3(tmp_path: Path):
    """Injected LLM alias map bridges a novel legend label to table group."""
    data = tmp_path / "Fig2" / "data"
    data.mkdir(parents=True)
    tab = data / "g.csv"
    tab.write_text("group,value\n" + "".join(f"alpha,{i}\n" for i in range(4)))
    vecs = extract_group_vectors(tab)
    pn = PanelN(
        panel="C",
        n=4,
        figure="Figure 2",
        context="n=4 (C)",
        group="protocol-X",
    )
    # Without alias → no group match
    bare = link_raw_for_panel(pn, vecs, table_paths=[tab])
    assert bare.tier != LinkTier.TIER3 or bare.vector is None or bare.vector.group_key != "alpha"

    linked = link_raw_for_panel(
        pn,
        vecs,
        table_paths=[tab],
        key_alias_map={"protocol-X": {"alpha"}},
    )
    assert linked.status == LinkStatus.LINKED
    assert linked.vector is not None
    assert linked.vector.group_key == "alpha"

def test_fig_folder_is_not_plot_table_by_itself(tmp_path: Path):
    from pre_peer_checker.engine.n_and_names import is_plot_quant_table

    raw = tmp_path / "data" / "Fig1" / "WT.xlsx"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"")
    assert is_plot_quant_table(raw) is False
    graph = raw.parent / "graph.xlsx"
    graph.write_bytes(b"")
    assert is_plot_quant_table(graph) is True


def test_same_n_group_prefers_matching_fig_folder(tmp_path: Path):
    """Similar experiments in Fig1 and Fig2: Figure 1 stays in Fig1/."""
    fig1 = tmp_path / "data" / "Fig1"
    fig2 = tmp_path / "data" / "Fig2"
    fig1.mkdir(parents=True)
    fig2.mkdir(parents=True)
    body = "group,value\nwt,1\nwt,2\nwt,3\nwt,4\n"
    a = fig1 / "wt.csv"
    b = fig2 / "wt.csv"
    a.write_text(body)
    b.write_text(body)
    vecs = extract_group_vectors(a) + extract_group_vectors(b)
    pn = PanelN(panel="A", n=4, figure="Figure 1", context="n=4 (A)", group="wt")
    raw = link_raw_for_panel(pn, vecs, table_paths=[a, b])
    assert raw.status == LinkStatus.LINKED
    assert raw.vector is not None
    assert "Fig1" in raw.vector.source.parts
    assert "Fig2" not in raw.vector.source.parts


def test_misplaced_file_in_other_fig_folder_still_links(tmp_path: Path):
    """Figure 1 data sitting only under Fig2/ still links via unique n+group."""
    fig2 = tmp_path / "data" / "Fig2"
    fig2.mkdir(parents=True)
    tab = fig2 / "wt.csv"
    tab.write_text("group,value\nwt,1.0\nwt,2.0\nwt,3.0\nwt,4.0\nwt,5.0\n")
    vecs = extract_group_vectors(tab)
    pn = PanelN(panel="B", n=5, figure="Figure 1", context="n=5 (B)", group="wt")
    raw = link_raw_for_panel(pn, vecs, table_paths=[tab])
    assert raw.status == LinkStatus.LINKED
    assert raw.vector is not None
    assert raw.vector.source.name == "wt.csv"
    assert "別実験フォルダ" in raw.reason


def test_n_only_does_not_jump_other_fig_folder(tmp_path: Path):
    """n-only (no group) in Fig2 must not satisfy Figure 1 — similar-n trap."""
    fig2 = tmp_path / "data" / "Fig2"
    fig2.mkdir(parents=True)
    tab = fig2 / "counts.csv"
    tab.write_text("group,value\nx,1\nx,2\nx,3\nx,4\nx,5\n")
    vecs = extract_group_vectors(tab)
    pn = PanelN(panel="Z", n=5, figure="Figure 1", context="n=5 (Z)")
    raw = link_raw_for_panel(pn, vecs, table_paths=[tab])
    assert raw.status == LinkStatus.UNLINKED


def test_fingerprint_links_unique_raw_in_other_fig_folder(tmp_path: Path):
    fig1 = tmp_path / "data" / "Fig1"
    fig2 = tmp_path / "data" / "Fig2"
    fig1.mkdir(parents=True)
    fig2.mkdir(parents=True)
    body = "group,value\nwt,10\nwt,11\nwt,12\n"
    plot = fig1 / "graph_wt.csv"
    raw = fig2 / "experiment.csv"
    plot.write_text(body)
    raw.write_text(body)
    vecs = extract_group_vectors(plot) + extract_group_vectors(raw)
    pn = PanelN(panel="E", n=3, figure="Figure 1", context="n=3 (E)", group="wt")
    plot_link = link_plot_for_panel(pn, vecs, table_paths=[plot, raw])
    assert plot_link.status == LinkStatus.LINKED
    raw_link = link_raw_for_panel(
        pn, vecs, plot_anchor=plot_link.vector, table_paths=[plot, raw]
    )
    assert raw_link.status == LinkStatus.LINKED
    assert raw_link.tier == LinkTier.TIER1
    assert raw_link.vector is not None
    assert raw_link.vector.source.name == "experiment.csv"
    assert "別実験フォルダ" in raw_link.reason


def test_experiment_unit_dir_prefers_fig_folder(tmp_path: Path):
    from pre_peer_checker.engine.n_and_names import experiment_unit_dir, figure_num_from_dir_name

    nested = tmp_path / "data" / "Fig1" / "plotDump" / "WT.xlsx"
    nested.parent.mkdir(parents=True)
    nested.write_bytes(b"")
    assert experiment_unit_dir(nested).name == "Fig1"
    assert figure_num_from_dir_name("FigS2") == "S2"
    loose = tmp_path / "data" / "orphan.csv"
    loose.write_text("a,1\n")
    assert experiment_unit_dir(loose).name == "data"
