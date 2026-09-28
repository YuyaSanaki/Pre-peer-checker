"""Messy / headerless Excel recovery for biology measurement workbooks."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from pre_peer_checker.data.fingerprint import values_exact_match
from pre_peer_checker.data.group_vectors import extract_group_vectors
from pre_peer_checker.data.stats_recalc import load_table, recover_ratio_measurement_table
from pre_peer_checker.engine.entity_link import LinkStatus, LinkTier, link_raw_for_panel
from pre_peer_checker.parsers.legend_struct import PanelN


def _write_clone_ratio_xlsx(path: Path, ratios: list[float]) -> None:
    """Minimal Sheet1 layout mimicking imaging clone-ratio workbooks."""
    rows: list[list[object | None]] = [
        [None] * 5 + ["x20 lens"],
        [None] * 5 + ["width (nm)", "height (nm)", "pixel area", None, "pixel are"],
        [None] * 5 + [1, 1, 1, None, 1],
        [None] * 14,
        [None] * 6 + ["sd", 1.0, None, "sd", 1.0, None, 1.0],
        [None] * 6 + ["mean", 1.0, None, "mean", 1.0, None, 1.0],
        [None, "note", None, None, None, None, "whole disc", "whole disc", None, "clone", "clone"],
        [
            "disc ID",
            "result #",
            "pixels",
            None,
            None,
            "dic ID",
            "pixel count",
            "area (um^2)",
            None,
            "pixel count",
            "area (um^2)",
            None,
            "ratio (%)",
        ],
    ]
    for i, r in enumerate(ratios, start=1):
        rows.append(
            [i, 1, 100.0, None, None, i, 100.0, 100.0, None, 10.0, 10.0, None, r]
        )
    # First sheet empty (like WT.xlsx "graph"), data on Sheet1
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame([[None]]).to_excel(writer, sheet_name="graph", header=False, index=False)
        pd.DataFrame(rows).to_excel(writer, sheet_name="Sheet1", header=False, index=False)


def test_recover_ratio_skips_empty_first_sheet(tmp_path: Path):
    ratios = [8.82701, 10.9366, 5.34533, 6.93916, 7.0785, 4.96133]
    path = tmp_path / "Cont_geneY.xlsx"
    _write_clone_ratio_xlsx(path, ratios)
    df = load_table(path)
    assert "value" in df.columns
    assert len(df) == 6
    vecs = extract_group_vectors(path)
    assert len(vecs) == 1
    assert vecs[0].n == 6
    assert vecs[0].group_key == "cont"  # filename Cont_* → cont
    assert abs(vecs[0].values[0] - min(ratios)) < 1e-6


def test_recover_drops_sentinel_100(tmp_path: Path):
    raw = pd.DataFrame(
        [
            [None, None, "ratio (%)"],
            [1, 1, 3.5],
            [2, 2, 4.5],
            [3, 3, 5.5],
            [4, 4, 100.0],
        ]
    )
    tidy = recover_ratio_measurement_table(raw)
    assert tidy is not None
    assert len(tidy) == 3
    assert 100.0 not in set(tidy["value"])


def test_tier1_links_recovered_raw_to_graph(tmp_path: Path):
    data = tmp_path / "Fig1" / "plotDump"
    data.mkdir(parents=True)
    ratios = [3.26805, 3.97456, 3.74643, 6.99055, 2.83745, 3.87153]
    raw = data / "ContBetaExp.xlsx"
    _write_clone_ratio_xlsx(raw, ratios)
    graph = data / "graphBetaExpS.xlsx"
    pd.DataFrame(
        {
            "ID": range(1, 7),
            "value": ratios,
            "genotype": ["Cont"] * 6,
            "label": [1] * 6,
        }
    ).to_excel(graph, index=False)

    raw_vecs = extract_group_vectors(raw)
    plot_vecs = extract_group_vectors(graph)
    assert values_exact_match(raw_vecs[0].values, plot_vecs[0].values)

    pn = PanelN(panel="N", n=6, figure="Figure 1", context="n=6 (N)", group="cont")
    plot = plot_vecs[0]
    link = link_raw_for_panel(
        pn,
        raw_vecs + plot_vecs,
        plot_anchor=plot,
        table_paths=[raw, graph],
    )
    assert link.status == LinkStatus.LINKED
    assert link.tier == LinkTier.TIER1
    assert link.vector is not None
    assert link.vector.source.name == "ContBetaExp.xlsx"
