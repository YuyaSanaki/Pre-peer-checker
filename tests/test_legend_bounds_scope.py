"""Legend n written as a lower bound or with an explicit panel scope."""

from __future__ import annotations

from pre_peer_checker.parsers.legend_struct import OPEN_N_MAX, parse_panel_ns

NATURE = (
    "Fig. 3 | Characterization. a, Identification of markers. "
    "b, PCA of bulk RNA-seq data of isolated intermediates, n ≥ 2. "
    "c, Experimental designs. d, Morphological changes under several conditions, n = 4. "
    "e, Flow cytometry profiles of intermediates, n > 10."
)


def test_lower_bound_is_an_open_range():
    rows = {(p.panel, p.n, p.n_max) for p in parse_panel_ns("Extended Data Figure 3", NATURE)}
    assert ("B", 2, OPEN_N_MAX) in rows
    assert ("E", 11, OPEN_N_MAX) in rows
    assert ("D", 4, None) in rows
    pns = [p for p in parse_panel_ns("Extended Data Figure 3", NATURE) if p.panel == "B"]
    assert pns and all(p.n_open for p in pns)


def test_n_for_panel_range_covers_every_panel():
    text = (
        "Fig. 4 | Direct derivation. a, FDL representation of libraries. "
        "d, Phase-contrast image. Scale bar, 100 μm. e, Immunostaining for several makers. "
        "Scale bar, 100 μm. f, g, Phase-contrast and immunostaining of ST (f) and EVT (g) cells. "
        "Scale bar, 100 μm. n = 4 for d–g. h, Spearman correlation of transcriptomes."
    )
    got = {(p.panel, p.n) for p in parse_panel_ns("Figure 4", text) if p.n_max is None}
    assert {("D", 4), ("E", 4), ("F", 4), ("G", 4)} <= got
