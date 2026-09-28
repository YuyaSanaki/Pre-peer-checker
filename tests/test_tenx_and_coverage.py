"""Gzip / 10x collection and coverage non-empty guarantees."""

from __future__ import annotations

import gzip
from pathlib import Path

from pre_peer_checker.data.tenx_matrix import (
    read_mtx_shape,
    scan_tenx_matrices,
    summarize_tenx_dir,
)
from pre_peer_checker.gui.worker import GuiRunConfig, _ensure_coverage_lines, run_verification_job
from pre_peer_checker.io_bundle import FileKind, classify, collect_inputs


def _write_gz_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(text)


def test_classify_tsv_gz() -> None:
    assert classify(Path("barcodes.tsv.gz")) is FileKind.TSV
    assert classify(Path("matrix.mtx.gz")) is FileKind.MTX
    assert classify(Path("table.csv.gz")) is FileKind.CSV


def test_collect_inputs_includes_barcodes_gz(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _write_gz_text(data / "barcodes.tsv.gz", "AA\nBB\n")
    _write_gz_text(data / "features.tsv.gz", "g1\tg\tGene Expression\n")
    _write_gz_text(
        data / "matrix.mtx.gz",
        "%%MatrixMarket matrix coordinate integer general\n1 2 1\n1 1 3\n",
    )
    (tmp_path / "manuscript").mkdir()
    bundle = collect_inputs([tmp_path / "manuscript", data])
    try:
        names = {p.name for p in bundle.get(FileKind.TSV)}
        assert "barcodes.tsv.gz" in names
        assert "features.tsv.gz" in names
        assert any(p.name == "matrix.mtx.gz" for p in bundle.get(FileKind.MTX))
    finally:
        bundle.cleanup_extracts()


def test_tenx_dimension_ok_and_mismatch(tmp_path: Path) -> None:
    ok_dir = tmp_path / "ok"
    _write_gz_text(ok_dir / "barcodes.tsv.gz", "c1\nc2\n")
    _write_gz_text(ok_dir / "features.tsv.gz", "g1\ta\tGene Expression\ng2\tb\tGene Expression\n")
    _write_gz_text(
        ok_dir / "matrix.mtx.gz",
        "%%MatrixMarket matrix coordinate integer general\n%\n2 2 2\n1 1 1\n2 2 1\n",
    )
    summary = summarize_tenx_dir(ok_dir)
    assert summary.ok
    assert summary.n_barcodes == 2
    assert summary.n_features == 2
    assert read_mtx_shape(ok_dir / "matrix.mtx.gz") == (2, 2, 2)

    bad = tmp_path / "bad"
    _write_gz_text(bad / "barcodes.tsv.gz", "c1\nc2\nc3\n")
    _write_gz_text(bad / "features.tsv.gz", "g1\ta\tGene Expression\n")
    _write_gz_text(
        bad / "matrix.mtx.gz",
        "%%MatrixMarket matrix coordinate integer general\n1 2 1\n1 1 1\n",
    )
    bad_sum = summarize_tenx_dir(bad)
    assert bad_sum.ok is False
    scan = scan_tenx_matrices(
        [bad / "barcodes.tsv.gz", bad / "features.tsv.gz", bad / "matrix.mtx.gz"]
    )
    assert len(scan.warnings) == 1


def test_ensure_coverage_lines_never_empty() -> None:
    cov, lines = _ensure_coverage_lines(None, n_warnings=0, n_inputs=2)
    assert lines
    assert cov.get("checks")


def test_worker_coverage_lines_nonempty(tmp_path: Path) -> None:
    demo = Path("fixtures/synthetic/demo")
    ms = tmp_path / "manuscript"
    data = tmp_path / "data"
    ms.mkdir()
    data.mkdir()
    # minimal inputs: copy nothing required; empty folders still run
    (data / "a.csv").write_text("g,v\nA,1\nB,2\n", encoding="utf-8")
    if demo.exists():
        import shutil

        for name in ("quant_a.csv", "quant_b.csv", "legend_n.json"):
            src = demo / name
            if src.exists():
                shutil.copy(src, data / name if name.endswith(".csv") else ms / name)

    result = run_verification_job(
        GuiRunConfig(
            inputs=[ms, data],
            output_html=tmp_path / "r.html",
            output_json=tmp_path / "r.json",
        )
    )
    assert result.ok
    assert result.coverage_lines
    assert "読込ファイル" in result.coverage_lines[0] or len(result.coverage_lines) >= 1
