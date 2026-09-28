"""Python / Prism / KaleidaGraph analysis provenance."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from pre_peer_checker.engine.script_resolve import (
    enrich_python_dag_with_local_paths,
    python_dag_to_artifact_dict,
)
from pre_peer_checker.io_bundle import FileKind, classify, collect_inputs
from pre_peer_checker.parsers.kaleida import kaleida_to_artifact, parse_kaleida_file
from pre_peer_checker.parsers.prism_pzfx import parse_pzfx
from pre_peer_checker.parsers.python_ast import analyze_python_dag_file
from pre_peer_checker.data.fingerprint import values_exact_match
from pre_peer_checker.data.group_vectors import extract_group_vectors
from pre_peer_checker.engine.entity_link import LinkStatus, LinkTier, link_raw_for_panel
from pre_peer_checker.parsers.legend_struct import PanelN


def _minimal_pzfx(path: Path, *, title: str, y_title: str, values: list[float]) -> None:
    rows = "\n".join(f"<d>{v}</d>" for v in values)
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<GraphPadPrismFile PrismXMLVersion="5.00">
  <Table ID="Table0" XFormat="none" YFormat="replicates">
    <Title>{title}</Title>
    <YColumn Width="80" Subcolumns="1">
      <Title>{y_title}</Title>
      <Subcolumn>
        {rows}
      </Subcolumn>
    </YColumn>
  </Table>
</GraphPadPrismFile>
"""
    path.write_text(xml, encoding="utf-8")


def test_classify_prism_and_kaleida():
    assert classify(Path("fig.pzfx")) is FileKind.PRISM
    assert classify(Path("old.pzf")) is FileKind.PRISM
    assert classify(Path("plot.qpc")) is FileKind.KALEIDA
    assert classify(Path("data.qpd")) is FileKind.KALEIDA


def test_parse_pzfx_y_columns(tmp_path: Path):
    pz = tmp_path / "clone.pzfx"
    vals = [8.827, 10.936, 5.345, 6.939]
    _minimal_pzfx(pz, title="AlphaExp cont", y_title="cont", values=vals)
    pf = parse_pzfx(pz)
    assert len(pf.tables) == 1
    assert pf.tables[0].title == "AlphaExp cont"
    y = pf.tables[0].y_vectors()
    assert len(y) == 1
    assert y[0][0] == "cont"
    assert list(y[0][1]) == vals
    art = pf.to_dict()
    assert art["source_kind"] == "prism"
    assert art["reads"]


def test_prism_fingerprint_links_to_csv(tmp_path: Path):
    vals = [1.5, 2.5, 3.5, 4.5]
    pz = tmp_path / "Fig1" / "plotDump"
    pz.mkdir(parents=True)
    prism = pz / "quant.pzfx"
    _minimal_pzfx(prism, title="BetaExp", y_title="1", values=vals)
    csv = pz / "ContBetaExp.csv"
    csv.write_text("label,value\n" + "".join(f"1,{v}\n" for v in vals))

    pf = parse_pzfx(prism)
    from pre_peer_checker.data.group_vectors import GroupVector

    pvecs = [
        GroupVector(source=prism, group_key=g, values=tuple(sorted(v)), n=len(v))
        for t in pf.tables
        for g, v in t.y_vectors()
    ]
    cvecs = extract_group_vectors(csv)
    assert values_exact_match(pvecs[0].values, cvecs[0].values)
    pn = PanelN(panel="N", n=4, figure="Figure 1", context="n=4 (N)", group="1")
    link = link_raw_for_panel(pn, pvecs + cvecs, plot_anchor=pvecs[0], table_paths=[csv, prism])
    assert link.status == LinkStatus.LINKED
    assert link.tier == LinkTier.TIER1


def test_python_dag_resolves_local_csv(tmp_path: Path):
    data = tmp_path / "analysis"
    data.mkdir()
    csv = data / "quant.csv"
    csv.write_text("label,value\na,1\na,2\na,3\n")
    script = data / "plot_fig.py"
    script.write_text(
        "import pandas as pd\n"
        "import matplotlib.pyplot as plt\n"
        "df = pd.read_csv('/remote/drive/quant.csv')\n"
        "plt.scatter(df['label'], df['value'])\n"
        "plt.savefig('out.png')\n",
        encoding="utf-8",
    )
    dag = analyze_python_dag_file(script)
    assert dag.reads and dag.reads[0].path and "quant.csv" in dag.reads[0].path
    enrich_python_dag_with_local_paths(dag, script_path=script, table_paths=[csv])
    assert getattr(dag.reads[0], "resolved_path", None)
    art = python_dag_to_artifact_dict(dag, script_path=script, source_kind="python")
    assert art["source_kind"] == "python"
    assert art["reads"][0]["resolved_path"]


def test_kaleida_string_scrape_resolves_xlsx(tmp_path: Path):
    data = tmp_path / "kg"
    data.mkdir()
    xlsx = data / "GradualRatio.xlsx"
    pd.DataFrame({"value": [1.0, 2.0]}).to_excel(xlsx, index=False)
    # Fake binary with embedded filename
    qpd = data / "session.qpd"
    qpd.write_bytes(b"\x00\x01KALEIDA\x00" + b"C:\\Users\\x\\GradualRatio.xlsx" + b"\x00\xff")
    ref = parse_kaleida_file(qpd)
    assert any("GradualRatio.xlsx" in n for n in ref.referenced_names)
    art = kaleida_to_artifact(ref, table_paths=[xlsx])
    assert art["source_kind"] == "kaleida"
    assert any(r.get("resolved_path") for r in art["reads"])


def test_collect_inputs_includes_prism(tmp_path: Path):
    d = tmp_path / "data"
    d.mkdir()
    pz = d / "t.pzfx"
    _minimal_pzfx(pz, title="T", y_title="Y", values=[1.0, 2.0, 3.0])
    bundle = collect_inputs([d])
    try:
        assert bundle.get(FileKind.PRISM)
    finally:
        bundle.cleanup_extracts()
