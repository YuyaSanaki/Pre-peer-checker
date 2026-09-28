"""GUI worker tests (no display required)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from docx import Document

from pre_peer_checker.gui.worker import GuiRunConfig, run_verification_job


def test_gui_worker_runs_on_synthetic(tmp_path: Path):
    ms = tmp_path / "ms"
    ms.mkdir()
    pd.DataFrame({"group": ["ctrl"] * 5 + ["mut"] * 5, "value": list(range(10))}).to_csv(
        ms / "quant_a.csv", index=False
    )
    pd.DataFrame(
        {"group": ["ctrl"] * 5 + ["kd"] * 5, "value": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]}
    ).to_csv(ms / "quant_b.csv", index=False)
    # make mut/kd identical for D1-like swap signal
    pd.DataFrame(
        {
            "group": ["ctrl"] * 5 + ["kd"] * 5,
            "value": [10, 11, 12, 13, 14, 5, 6, 7, 8, 9],
        }
    ).to_csv(ms / "quant_b.csv", index=False)
    doc = Document()
    doc.add_paragraph("Figure 1. n=6 (A1).")
    doc.save(ms / "main.docx")

    out = tmp_path / "report.html"
    result = run_verification_job(
        GuiRunConfig(inputs=[ms], output_html=out, output_json=out.with_suffix(".json"))
    )
    assert result.ok, result.error
    assert result.report_path and result.report_path.exists()
    assert result.json_path and result.json_path.exists()
    assert result.n_warnings >= 1


def test_gui_worker_missing_input(tmp_path: Path):
    result = run_verification_job(
        GuiRunConfig(inputs=[tmp_path / "nope"], output_html=tmp_path / "r.html")
    )
    assert not result.ok
    assert result.error
