"""Panel n comparison matrix for HTML report."""

from __future__ import annotations

from pathlib import Path

from pre_peer_checker.data.group_vectors import GroupVector
from pre_peer_checker.engine.n_matrix import (
    attach_fig_pdf_counts,
    build_n_matrix,
    n_matrix_to_artifact,
)
from pre_peer_checker.parsers.legend_struct import PanelN
from pre_peer_checker.report.html_report import (
    annotate_legend_html,
    find_panel_n_spans,
    render_html_report,
)
from pre_peer_checker.warnings import WarningItem, WarningTag


def test_build_n_matrix_flags_mismatch(tmp_path: Path, sided_profile):
    data = tmp_path / "Fig1" / "plotDump"
    data.mkdir(parents=True)
    raw = data / "WT.xlsx"
    graph = data / "graphAlphaExp.xlsx"
    raw.write_bytes(b"")
    graph.write_bytes(b"")

    vecs = [
        GroupVector(source=raw, group_key="0", values=(1.0,) * 8, n=8),
        GroupVector(source=graph, group_key="0", values=(1.0,) * 8, n=8),
    ]
    # mark plot table via name graph*
    pn = PanelN(panel="B", n=9, figure="Figure 1", context="n=9 (B)")
    rows = build_n_matrix(
        [pn],
        vecs,
        legend_json_by_panel={
            ("Figure 1", "B"): {
                "extractor": "hybrid-llm",
                "evidence_span": "n=9 (B)",
            }
        },
        manuscript_by_figure={"Figure 1": str(tmp_path / "manuscript" / "ms.docx")},
        case_roots=[tmp_path],
    )
    assert len(rows) == 1
    r = rows[0]
    assert r.manuscript.n == 9
    assert r.manuscript.file_display is not None
    assert "ms.docx" in (r.manuscript.file_display or "")
    assert r.plot.n == 8
    assert r.plot.file_display and "graphAlphaExp.xlsx" in r.plot.file_display
    assert r.data.file_display and "WT.xlsx" in r.data.file_display
    assert r.mismatch is True
    assert r.extractor == "hybrid-llm"
    art = n_matrix_to_artifact(rows)
    assert art[0]["plot"]["n"] == 8
    assert art[0]["plot"]["file_display"]


def test_build_n_matrix_flat_layout_soft_link(tmp_path: Path):
    """Fig1 フォルダ無しでもファイル名ヒントで紐付く。"""
    flat = tmp_path / "data"
    flat.mkdir()
    raw = flat / "fig2_panelE_wt.csv"
    graph = flat / "graph_fig2E.csv"
    raw.write_text("label,value\n0,1.0\n0,2.0\n0,3.0\n")
    graph.write_text("label,value\n0,1.0\n0,2.0\n0,3.0\n")
    from pre_peer_checker.data.group_vectors import extract_group_vectors

    vecs = extract_group_vectors(raw) + extract_group_vectors(graph)
    pn = PanelN(panel="E", n=3, figure="Figure 2", context="n=3 (E)", group="wt")
    rows = build_n_matrix(
        [pn],
        vecs,
        case_roots=[tmp_path],
        script_artifacts=[
            {
                "path": str(flat / "fig2_stats.R"),
                "reads": [{"path": "fig2_panelE_wt.csv"}],
            }
        ],
    )
    assert len(rows) == 1
    r = rows[0]
    assert r.data.n == 3
    assert r.data.file and "fig2_panelE_wt.csv" in r.data.file
    assert r.plot.n == 3
    assert r.plot.file and "graph_fig2E" in r.plot.file
    assert r.stats.file and "fig2_stats.R" in r.stats.file


def test_attach_fig_pdf_counts_from_publication_pdf(tmp_path: Path):
    from pre_peer_checker.engine.panel_plot_identity import (
        warnings_from_figure_panel_identity,
    )
    from pre_peer_checker.parsers.pdf_panel_plots import build_synthetic_multipanel_pdf

    pdf = build_synthetic_multipanel_pdf(tmp_path / "Fig1.pdf")
    _warns, arts = warnings_from_figure_panel_identity([pdf], min_groups=1, min_score=0.9)
    assert arts and arts[0]["figure_id"] == "Figure 1"
    panel_i = next(p for p in arts[0]["panels"] if p["panel"] == "I")
    assert sum(panel_i["group_ns"]) == panel_i["n_markers"] == 16

    rows = [
        {"figure": "Figure 1", "panel": "I", "mismatch": False},
        {"figure": "Figure 1", "panel": "Z", "mismatch": False},
        {"figure": "Figure 2", "panel": "I", "mismatch": False},
    ]
    attach_fig_pdf_counts(rows, arts, roots=[tmp_path])
    assert rows[0]["fig_pdf"]["group_ns"] == panel_i["group_ns"]
    assert rows[0]["fig_pdf"]["file_display"] == "Fig1.pdf"
    assert rows[0]["fig_pdf_display"] == " / ".join(str(n) for n in panel_i["group_ns"])
    assert rows[0]["mismatch"] is False
    assert rows[1]["fig_pdf"] is None and rows[1]["fig_pdf_display"] == "—"
    assert rows[2]["fig_pdf"] is None


def test_html_report_includes_n_matrix():
    html = render_html_report(
        [
            WarningItem(
                tag=WarningTag.SAMPLE_SIZE,
                title="t",
                location="l",
                reason="r",
            )
        ],
        coverage={
            "checks": [],
            "notes": [],
            "legend_llm_status": {
                "status": "unavailable",
                "message": "LLM 未導入のため規則のみ",
            },
            "n_matrix": [
                {
                    "figure": "Figure 1",
                    "panel": "F",
                    "manuscript": {
                        "n": 12,
                        "file": "/case/manuscript/paper.docx",
                        "file_display": "manuscript/paper.docx",
                        "detail": "n=12 (F)",
                    },
                    "data": {
                        "n": 11,
                        "file": "/case/data/WT.xlsx",
                        "file_display": "data/WT.xlsx",
                        "detail": "指紋一致 · hash=abc · n=11",
                        "link_status": "linked",
                        "link_tier": "tier1",
                    },
                    "plot": {
                        "n": 11,
                        "file": "/case/data/graphAlphaExp.xlsx",
                        "file_display": "data/graphAlphaExp.xlsx",
                        "detail": "候補キー接地 · group=mutx-/-",
                        "link_status": "linked",
                        "link_tier": "tier3",
                    },
                    "stats": {
                        "n": 11,
                        "file": "/case/data/analyze.R",
                        "file_display": "data/analyze.R",
                        "detail": "reads table",
                    },
                    "extractor": "hybrid-llm",
                    "mismatch": True,
                    "fig_pdf": {
                        "group_ns": [9, 10],
                        "n_markers": 19,
                        "file": "/case/manuscript/Fig1.pdf",
                        "file_display": "manuscript/Fig1.pdf",
                        "page": 0,
                    },
                    "fig_pdf_display": "9 / 10",
                }
            ],
            "figure_chunks": [
                {
                    "figure_id": "Figure 1",
                    "legend": "Figure 1. n=12 (F).",
                    "results": ["Fig. 1"],
                    "methods": ["Welch"],
                    "methods_includes_shared": True,
                    "prompt_chars": 120,
                }
            ],
            "cited_image_matches": [
                {
                    "a": "/ms/panel.png",
                    "b": "/corpus/old.png",
                    "similarity": 0.9912,
                }
            ],
        },
    )
    assert "パネル n 対照表" in html
    assert "正の n" in html
    assert "有効行数" in html
    assert "Figure 1" in html
    assert "graphAlphaExp.xlsx" in html
    assert "analyze.R" in html
    assert "paper.docx" in html
    assert "参照ファイル名" in html
    assert "Legend LLM" in html
    assert "Figチャンク" not in html
    assert 'data-panel="F"' in html
    assert 'class="leg-hl"' in html
    assert "n=12 (F)" in html
    assert "legend-block" in html
    assert "対応表" in html
    assert 'class="link-badge tier1"' in html
    assert 'class="link-badge tier3"' in html
    assert "候補キー接地" in html
    assert "Fig PDF 点数（参考）" in html
    assert "9 / 10" in html
    assert "2 群 · 計 19 点" in html
    assert "manuscript/Fig1.pdf p0" in html
    assert 'class="extractor-badge hybrid-llm"' in html
    assert "hybrid-llm" in html
    assert "画像再利用（出典あり・情報）" in html
    assert "出典あり一致" in html
    assert "panel.png" in html
    assert "old.png" in html


def test_html_report_includes_figure_preview_hotspots():
    tiny_png = (
        "data:image/jpeg;base64,"
        "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkS"
        "Ew8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJ"
        "CQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy"
        "MjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAA"
        "AAAAAAAAAAj/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFQEBAQAAAAAAAAAAAAAA"
        "AAAAAAD/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAGfAP/EABQQ"
        "AQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAQUCf//EABQRAQAAAAAAAAAAAAAAAAAA"
        "AAD/2gAIAQMBAT8Bf//EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQIBAT8Bf//E"
        "ABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEABj8Cf//EABQQAQAAAAAAAAAAAAAA"
        "AAAAAAD/2gAIAQEAAT8hf//Z"
    )
    html = render_html_report(
        [],
        coverage={
            "checks": [],
            "n_matrix": [
                {
                    "figure": "Figure 1",
                    "panel": "B",
                    "manuscript": {"n": 53, "file": None, "detail": "n=53 (B)"},
                    "data": {"n": None, "file": None, "detail": ""},
                    "plot": {"n": None, "file": None, "detail": ""},
                    "stats": {"n": None, "file": None, "detail": ""},
                    "extractor": "rules",
                    "mismatch": False,
                }
            ],
            "figure_chunks": [
                {"figure_id": "Figure 1", "legend": "n=53 (B).", "results": [], "methods": []}
            ],
            "figure_previews": [
                {
                    "figure_id": "Figure 1",
                    "source": "/tmp/Fig.pdf",
                    "source_name": "Fig.pdf",
                    "page": 0,
                    "image_data_uri": tiny_png,
                    "panels": [
                        {
                            "panel": "B",
                            "left_pct": 10,
                            "top_pct": 20,
                            "width_pct": 15,
                            "height_pct": 18,
                        }
                    ],
                    "n_panels": 1,
                }
            ],
        },
    )
    assert "panel-hotspot" in html
    assert "fig-preview" in html
    assert "Fig → 対応表 → Legend" in html
    assert 'data-panel="B"' in html


def test_panel_boxes_percent_covers_labels():
    from pre_peer_checker.report.figure_previews import panel_boxes_percent

    labels = {
        "A": (12.0, 50.0, 40.0),
        "B": (12.0, 200.0, 40.0),
        "C": (12.0, 50.0, 300.0),
    }
    boxes = panel_boxes_percent(labels, 400.0, 600.0)
    assert set(boxes) == {"A", "B", "C"}
    assert boxes["A"]["width_pct"] > 0
    assert boxes["B"]["left_pct"] > boxes["A"]["left_pct"]


def test_find_panel_n_spans_prefers_sample_size():
    legend = (
        "Figure 1. (A-C) eyes (B). (D) n=53 (B), and 56 (C). "
        "(Q) n=17 (N) and 12 (O). (R) n=17 (N) and 12 (O)."
    )
    assert find_panel_n_spans(legend, "B", 53) == [
        (legend.index("n=53 (B)"), legend.index("n=53 (B)") + len("n=53 (B)"))
    ]
    o_spans = find_panel_n_spans(legend, "O", 12)
    assert len(o_spans) == 2
    for start, end in o_spans:
        assert legend[start:end] in {"12 (O)", "n=12 (O)"} or legend[start:end].endswith("(O)")


def test_annotate_legend_html_marks_panels():
    legend = "n=53 (B), and 56 (C). n=17 (N) and 12 (O)."
    rows = [
        {"panel": "B", "manuscript": {"n": 53}},
        {"panel": "O", "manuscript": {"n": 12}},
    ]
    html = annotate_legend_html(legend, rows)
    assert 'data-panel="B"' in html
    assert 'data-panel="O"' in html
    assert "<mark class=\"leg-hl\"" in html
    assert "&lt;" not in html  # plain text escaped only when needed


def _vec(src: Path, key: str, n: int) -> GroupVector:
    return GroupVector(source=src, group_key=key, values=tuple(float(i) for i in range(n)), n=n)


def test_n_profile_links_the_statement_file(tmp_path: Path):
    from pre_peer_checker.engine.entity_link import link_by_n_profile

    fig = tmp_path / "Fig3"
    fig.mkdir()
    a, b = fig / "manipulation.xlsx", fig / "other.xlsx"
    ctx = "clone size. n= 10 (J), 12 (K), 12 (L), and 10 (M). ***p<0.001 by Dunn"
    pns = [PanelN(panel=p, n=n, figure="Figure 3", context=ctx) for p, n in zip("JKLM", (10, 12, 12, 9))]
    vecs = [_vec(a, "g1", 10), _vec(a, "g2", 12), _vec(a, "g3", 12), _vec(a, "g4", 10),
            _vec(b, "x", 10), _vec(b, "y", 15)]
    links = link_by_n_profile(pns, vecs)
    assert {k[1]: (l.vector.source.name, l.vector.n) for k, l in links.items()} == {
        "J": ("manipulation.xlsx", 10), "K": ("manipulation.xlsx", 12),
        "L": ("manipulation.xlsx", 12), "M": ("manipulation.xlsx", 10),
    }
    # the file has fewer groups than the statement: the odd row has nothing to compare with
    assert link_by_n_profile(pns, vecs[:3] + vecs[4:]) == {}


def test_link_confidence_gates_mismatch(tmp_path: Path, monkeypatch):
    from pre_peer_checker.engine.entity_link import (
        LINK_CONFIDENCE_ENV,
        EntityLink,
        LinkStatus,
        LinkTier,
        link_confidence,
        min_mismatch_confidence,
    )
    from pre_peer_checker.engine.n_matrix import NCell

    vec = GroupVector(source=tmp_path / "a.xlsx", group_key="0", values=(1.0,) * 8, n=8)
    assert link_confidence(EntityLink(LinkStatus.LINKED, LinkTier.TIER1, vec, score=100)) == 1.0
    assert link_confidence(EntityLink(LinkStatus.LINKED, LinkTier.TIER3, vec, score=40)) == 0.4
    assert link_confidence(EntityLink(LinkStatus.LINKED, LinkTier.SOFT, vec, score=60)) == 0.0
    assert link_confidence(EntityLink(LinkStatus.UNLINKED, LinkTier.NONE, None)) == 0.0

    assert NCell(n=8, confidence=0.4).counts_for_mismatch(9, 0.4)
    assert not NCell(n=8, confidence=0.3).counts_for_mismatch(9, 0.4)
    assert not NCell(n=8, confidence=1.0, n_lower_bound=True).counts_for_mismatch(9, 0.4)
    assert NCell(n=10, confidence=1.0, n_lower_bound=True).counts_for_mismatch(9, 0.4)

    monkeypatch.setenv(LINK_CONFIDENCE_ENV, "0.7")
    assert min_mismatch_confidence() == 0.7
