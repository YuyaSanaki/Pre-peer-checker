"""Rhistory collection + path resolution onto local tables."""

from __future__ import annotations

from pathlib import Path

from pre_peer_checker.engine.script_resolve import (
    enrich_r_dag_with_local_paths,
    r_dag_to_artifact_dict,
)
from pre_peer_checker.io_bundle import FileKind, classify, collect_inputs, is_office_temp_name
from pre_peer_checker.parsers.r_treesitter import analyze_r_dag_file, build_r_dag


def test_classify_rhistory_and_rmd():
    assert classify(Path(".Rhistory")) is FileKind.R_SCRIPT
    assert classify(Path("analysis.Rmd")) is FileKind.R_SCRIPT
    assert classify(Path("plot.R")) is FileKind.R_SCRIPT
    assert not is_office_temp_name(".Rhistory")


def test_collect_inputs_picks_up_rhistory(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir()
    hist = data / ".Rhistory"
    hist.write_text(
        'library(readxl)\ngraph <- read_excel("graphBetaExpS.xlsx")\n'
        "ggplot(graph, aes(x=label, y=value)) + geom_point()\n",
        encoding="utf-8",
    )
    (data / "graphBetaExpS.xlsx").write_bytes(b"PK\x03\x04")  # stub; not parsed here
    bundle = collect_inputs([data])
    try:
        r_files = bundle.get(FileKind.R_SCRIPT)
        assert any(p.name == ".Rhistory" for p in r_files)
    finally:
        bundle.cleanup_extracts()


def test_rhistory_dag_resolves_google_drive_basename(tmp_path: Path):
    data = tmp_path / "plotDump"
    data.mkdir()
    local = data / "graphBetaExpS.xlsx"
    # minimal real xlsx via openpyxl/pandas
    import pandas as pd

    pd.DataFrame(
        {"ID": [1, 2], "value": [1.0, 2.0], "genotype": ["a", "a"], "label": [1, 1]}
    ).to_excel(local, index=False)

    hist = data / ".Rhistory"
    hist.write_text(
        'graphBetaExpS <- read_excel("~/Google Drive/mutx paper/Fig1/plotDump/graphBetaExpS.xlsx")\n'
        "ggplot(graphBetaExpS, aes(x=label, y=value)) + geom_point()\n",
        encoding="utf-8",
    )
    dag = analyze_r_dag_file(hist)
    assert dag.reads
    assert any("graphBetaExpS.xlsx" in (r.path or "") for r in dag.reads)
    resolved = enrich_r_dag_with_local_paths(
        dag, script_path=hist, table_paths=[local]
    )
    assert "graphbetaexps.xlsx" in resolved
    assert Path(resolved["graphbetaexps.xlsx"]).name == "graphBetaExpS.xlsx"
    art = r_dag_to_artifact_dict(dag, script_path=hist, source_kind="rhistory")
    assert art["source_kind"] == "rhistory"
    assert art["reads"][0].get("resolved_path")


def test_build_r_dag_from_multiline_history_snippet():
    code = (
        'graph <- read_excel("~/Google Drive/mutx paper/statictics/Fig1/plotDump/graph.xlsx",\n'
        'col_types = c("numeric", "numeric", "text", "text"))\n'
        "ggplot(graph, aes(x=label, y=value)) + geom_point()\n"
    )
    dag = build_r_dag(code)
    assert dag.reads
    assert dag.reads[0].path and path_endswith(dag.reads[0].path, "graph.xlsx")
    assert dag.plots


def path_endswith(p: str, name: str) -> bool:
    return p.replace("\\", "/").endswith(name)
