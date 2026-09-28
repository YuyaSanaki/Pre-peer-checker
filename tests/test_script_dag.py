"""Python AST / R tree-sitter DAG 本線の回帰テスト."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pre_peer_checker.engine.script_dag import warnings_from_python_dag, warnings_from_r_dag
from pre_peer_checker.parsers.python_ast import (
    analyze_python_dag_file,
    build_python_dag,
    notebook_to_source,
)
from pre_peer_checker.parsers.r_treesitter import build_r_dag, treesitter_available
from pre_peer_checker.pipeline.orchestrator import run_verification
from pre_peer_checker.warnings import WarningTag


def test_python_dag_read_plot_save():
    code = """
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt
df_alphaexp = pd.read_csv("alphaexp.csv")
ax = sns.boxplot(data=df_alphaexp, x="group", y="size")
plt.savefig("RplotBetaExp.pdf")
"""
    dag = build_python_dag(code)
    assert dag.backend == "python-ast"
    assert len(dag.reads) == 1
    assert dag.reads[0].path == "alphaexp.csv"
    assert dag.reads[0].assigned_to == "df_alphaexp"
    assert len(dag.plots) == 1
    assert dag.plots[0].data_expr == "df_alphaexp"
    assert len(dag.saves) == 1
    assert dag.saves[0].path == "RplotBetaExp.pdf"
    assert any(e[2] == "read" for e in dag.edges)
    warns = warnings_from_python_dag("plot.py", dag)
    assert any(w.tag == WarningTag.DATA_SWAP for w in warns)


def test_python_notebook_cells(tmp_path: Path):
    nb = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {},
        "cells": [
            {"cell_type": "markdown", "metadata": {}, "id": "md1", "source": ["# title"]},
            {
                "cell_type": "code",
                "metadata": {},
                "id": "c1",
                "outputs": [],
                "source": ["import pandas as pd\n", 'df = pd.read_csv("a.csv")\n'],
            },
            {
                "cell_type": "code",
                "metadata": {},
                "id": "c2",
                "outputs": [],
                "source": ['sns.boxplot(data=df, x="g", y="y")\n'],
            },
        ],
    }
    path = tmp_path / "analysis.ipynb"
    path.write_text(json.dumps(nb), encoding="utf-8")
    src = notebook_to_source(path)
    assert "read_csv" in src
    dag = analyze_python_dag_file(path)
    assert len(dag.reads) == 1
    assert len(dag.plots) == 1


def test_r_dag_pipe_ggplot_ggsave():
    code = """
df_alphaexp <- read.csv("alphaexp.csv")
df_betaexp <- read.csv("betaexp.csv") %>% dplyr::filter(ok)
p <- ggplot(df_alphaexp, aes(x=group, y=size)) + geom_boxplot()
ggsave("RplotBetaExp.pdf", p)
df_betaexp |> ggplot(aes(x=g, y=v)) + geom_point()
"""
    dag = build_r_dag(code)
    funcs = {b.function for b in dag.bindings}
    assert "read.csv" in funcs
    assert "ggplot" in funcs
    assert "ggsave" in funcs
    assert len(dag.reads) >= 2
    assert dag.var_sources.get("df_alphaexp") == "alphaexp.csv"
    # assigned plot uses alphaexp data
    assigned = [p for p in dag.plots if p.assigned_to == "p"]
    assert assigned
    assert assigned[0].data_expr == "df_alphaexp"
    assert "geom_boxplot" in assigned[0].layers
    # pipe ggplot
    piped = [p for p in dag.plots if p.piped_from == "df_betaexp" or p.data_expr == "df_betaexp"]
    assert piped
    assert dag.saves
    assert dag.saves[0].path == "RplotBetaExp.pdf"
    warns = warnings_from_r_dag("fig.R", dag)
    assert any("ラベル不一致" in w.title for w in warns)


def test_r_treesitter_backend_when_available():
    if not treesitter_available():
        pytest.skip("tree-sitter R grammar not installed")
    dag = build_r_dag("ggplot(df, aes(x=a)) + geom_point()\n", require_treesitter=True)
    assert dag.backend.startswith("tree-sitter")


def test_pipeline_script_dag_warnings(tmp_path: Path):
    r_path = tmp_path / "plot.R"
    r_path.write_text(
        """
data_marcom <- read.csv("graph.csv")
ggplot(data_marcom, aes(x=group, y=value)) + geom_boxplot()
ggplot(data_marcom, aes(x=group, y=value)) + geom_point()
""",
        encoding="utf-8",
    )
    result = run_verification([tmp_path])
    tags = {w.tag for w in result.warnings}
    assert WarningTag.DATA_SWAP in tags
    assert result.artifacts.get("r")
    assert result.artifacts["r"][0].get("backend")
