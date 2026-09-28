"""パーサーと統計のスモークテスト."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from pre_peer_checker.data.stats_recalc import analyze_table_file, total_n
from pre_peer_checker.parsers.python_ast import analyze_python_script, trace_python_script
from pre_peer_checker.parsers.r_treesitter import analyze_r_script
from pre_peer_checker.pipeline.orchestrator import run_verification
from pre_peer_checker.report.html_report import render_html_report
from pre_peer_checker.warnings import WarningItem, WarningTag


FIXTURES = Path(__file__).parent / "fixtures"


def test_python_ast_plot_trace():
    code = """
import seaborn as sns
import pandas as pd
df = pd.read_csv("data.csv")
sns.boxplot(data=df, x="group", y="size")
"""
    plots = trace_python_script(code)
    assert len(plots) == 1
    assert plots[0].func == "sns.boxplot"
    analysis = analyze_python_script(code)
    assert len(analysis["reads"]) == 1


def test_r_treesitter_ggplot():
    code = """
data <- read.csv("betaexp.csv")
ggplot(data, aes(x=group, y=size)) + geom_boxplot()
"""
    bindings = analyze_r_script(code)
    funcs = {b.function for b in bindings}
    assert "read.csv" in funcs
    assert "ggplot" in funcs


def test_stats_recalc_and_n(tmp_path: Path):
    csv_path = tmp_path / "data.csv"
    pd.DataFrame(
        {
            "group": ["A"] * 5 + ["B"] * 6,
            "size": list(range(5)) + list(range(6)),
        }
    ).to_csv(csv_path, index=False)
    result = analyze_table_file(csv_path)
    assert total_n(result) == 11
    assert len(result.groups) == 2


def test_pipeline_sample_size_warning(tmp_path: Path):
    # n=11 のデータ
    data = tmp_path / "graph.csv"
    pd.DataFrame({"group": ["WT"] * 11, "value": range(11)}).to_csv(data, index=False)

    # Legend に n=12 と書く docx は python-docx で作成
    from docx import Document

    docx_path = tmp_path / "main.docx"
    doc = Document()
    doc.add_paragraph("Figure 1. Quantification of clone size. n = 12 biological replicates.")
    doc.save(docx_path)

    # 同一データ引数の二重 ggplot
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
    assert WarningTag.SAMPLE_SIZE in tags
    assert WarningTag.DATA_SWAP in tags

    html = render_html_report(result.warnings, file_summary="test")
    assert "Warning" in html
    out = result.write_report(tmp_path / "report.html")
    assert out.exists()
