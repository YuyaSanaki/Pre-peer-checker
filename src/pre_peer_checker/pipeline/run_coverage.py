"""Summarize which checks ran vs skipped for a verification run."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pre_peer_checker.io_bundle import FileKind, InputBundle


def _count(bundle: InputBundle) -> dict[str, int]:
    return {k.value: len(bundle.get(k)) for k in FileKind if bundle.get(k)}


def _samples(bundle: InputBundle, *, limit: int = 6) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for k in FileKind:
        items = bundle.get(k)
        if not items:
            continue
        out[k.value] = [p.name for p in items[:limit]]
    return out


def build_run_coverage(
    bundle: InputBundle,
    artifacts: dict[str, Any],
    *,
    corpus_provided: bool,
    cited_papers_provided: bool = False,
) -> dict[str, Any]:
    """Build a human-readable coverage dict from bundle + artifacts."""
    n_docx = len(bundle.get(FileKind.DOCX))
    n_tables = len(bundle.get(FileKind.CSV)) + len(bundle.get(FileKind.EXCEL))
    n_tsv = len(bundle.get(FileKind.TSV))
    n_mtx = len(bundle.get(FileKind.MTX))
    n_pdf = len(bundle.get(FileKind.PDF))
    n_img = len(bundle.get(FileKind.IMAGE))
    n_py = len(bundle.get(FileKind.PYTHON)) + len(bundle.get(FileKind.NOTEBOOK))
    n_r = len(bundle.get(FileKind.R_SCRIPT))
    n_prism = len(bundle.get(FileKind.PRISM))
    n_kaleida = len(bundle.get(FileKind.KALEIDA))
    n_micro = len(bundle.get(FileKind.LIF)) + len(bundle.get(FileKind.CZI))

    docx_used = [d.get("path", "") for d in (artifacts.get("docx") or []) if isinstance(d, dict)]
    panel_ns = artifacts.get("legend_panel_ns") or []
    group_vectors = artifacts.get("group_vectors") or []
    table_errors = artifacts.get("table_load_errors") or []
    dig_plots = artifacts.get("digitized_plots") or []
    fig_panels = artifacts.get("figure_panel_plots") or []
    extracted = artifacts.get("extracted_zips") or list(getattr(bundle, "extracted_from", []) or [])
    tenx = artifacts.get("tenx_matrices") or {}

    checks: list[dict[str, str]] = []

    def add(cid: str, name: str, status: str, detail: str) -> None:
        checks.append({"id": cid, "name": name, "status": status, "detail": detail})

    source = artifacts.get("manuscript_source") or {}
    if isinstance(source, dict) and source.get("kind") == "pdf":
        names = ", ".join(Path(p).name for p in source.get("paths") or [])
        detail = f"Word なし · PDF 原稿 {names} の本文を読み取り · 抽出パネル n={len(panel_ns)}"
        n_ocr = sum(len(x.get("pages") or []) for x in source.get("ocr_pages") or [])
        n_image_only = sum(len(x.get("pages") or []) for x in source.get("image_only_pages") or [])
        if n_ocr:
            detail += f" · OCR {n_ocr} ページ"
        if n_image_only:
            detail += f" · 画像のみで読めないページ {n_image_only}（Tesseract 未導入のため OCR せず。./install.sh の再実行で導入）"
        add("word_legend", "原稿 Legend / パネル n 抽出（PDF）", "ran", detail)
    elif n_docx:
        n_docx_ok = len(docx_used)
        detail = f"対象 Word {n_docx_ok} 件 · 抽出パネル n={len(panel_ns)}"
        if n_docx_ok == 0:
            detail = (
                f"処理できた Word 0 件（検出 {n_docx}）"
                f" · 抽出パネル n={len(panel_ns)}"
            )
        add(
            "word_legend",
            "Word Legend / パネル n 抽出",
            "ran",
            detail,
        )
    else:
        add(
            "word_legend",
            "Word Legend / パネル n 抽出",
            "skipped",
            "docx なし（本文を読める原稿 PDF もなし）",
        )

    if n_tables:
        detail = f"表ファイル {n_tables} 件 · 群ベクトル {len(group_vectors)} 件"
        if table_errors:
            detail += f" · 読込失敗 {len(table_errors)} 件"
        add("tables", "表（CSV/Excel）読込・群ベクトル", "ran", detail)
    else:
        add("tables", "表（CSV/Excel）読込・群ベクトル", "skipped", "csv/xlsx なし")

    if panel_ns and group_vectors:
        add("legend_n_match", "Legend n ↔ 表の突合", "ran", "パネル n と群サイズを照合")
    elif not panel_ns:
        add("legend_n_match", "Legend n ↔ 表の突合", "skipped", "Legend からパネル n を抽出できず")
    else:
        add("legend_n_match", "Legend n ↔ 表の突合", "skipped", "比較可能な群ベクトルなし")

    if len(group_vectors) >= 2:
        add(
            "cross_table",
            "表間の同一群ベクトル（取り違え疑い）",
            "ran",
            f"群ベクトル {len(group_vectors)} 件を相互比較",
        )
    else:
        add(
            "cross_table",
            "表間の同一群ベクトル（取り違え疑い）",
            "skipped",
            "比較に必要な群ベクトルが不足（2 未満）",
        )

    if n_py or n_r or n_prism or n_kaleida:
        r_arts = artifacts.get("r") or []
        n_hist = sum(
            1
            for a in r_arts
            if isinstance(a, dict) and a.get("source_kind") == "rhistory"
        )
        n_rscript = n_r - n_hist if n_r >= n_hist else n_r
        detail = f"Python/ipynb {n_py} · R/Rmd {n_rscript}"
        if n_hist:
            detail += f" · Rhistory {n_hist}"
        if n_prism:
            detail += f" · Prism {n_prism}"
        if n_kaleida:
            detail += f" · Kaleida {n_kaleida}"
        add(
            "scripts",
            "解析スクリプト DAG（Python/R/Prism/Kaleida）",
            "ran",
            detail,
        )
    else:
        add(
            "scripts",
            "解析スクリプト DAG（Python/R/Prism/Kaleida）",
            "skipped",
            ".py/.R/.Rmd/.Rhistory/.pzfx/.qpd なし",
        )

    if dig_plots or any(
        "rplot" in p.name.lower() for p in bundle.get(FileKind.PDF)
    ):
        add("plot_digitize", "Rplot 等のプロット数値化", "ran", f"digitized={len(dig_plots)}")
    else:
        add("plot_digitize", "Rplot 等のプロット数値化", "skipped", "Rplot*.pdf 等なし")

    if n_pdf:
        add(
            "figure_pdf",
            "Figure PDF / 埋め込み画像スキャン",
            "ran",
            f"PDF {n_pdf} 件 · パネル解析 {len(fig_panels)}",
        )
    else:
        add("figure_pdf", "Figure PDF / 埋め込み画像スキャン", "skipped", "pdf なし")

    if n_img or n_micro:
        add(
            "images",
            "画像・顕微鏡ファイルの重複スキャン",
            "ran",
            f"画像 {n_img} · LIF/CZI {n_micro}",
        )
    else:
        add("images", "画像・顕微鏡ファイルの重複スキャン", "skipped", "画像/LIF/CZI なし")

    n_tenx = int(tenx.get("n_matrices") or 0)
    if n_tenx or n_tsv or n_mtx:
        if n_tenx:
            add(
                "tenx",
                "10x 行列（barcodes / features / mtx）",
                "ran",
                (
                    f"{n_tenx} 組 · barcodes 合計 {tenx.get('total_barcodes', 0)} · "
                    f"整合 OK {tenx.get('n_ok', 0)}/{n_tenx} "
                    f"（tsv={n_tsv}, mtx={n_mtx}）"
                ),
            )
        else:
            add(
                "tenx",
                "10x 行列（barcodes / features / mtx）",
                "skipped",
                f"tsv/mtx はあるが 10x 3点セット未検出（tsv={n_tsv}, mtx={n_mtx}）",
            )
    else:
        add(
            "tenx",
            "10x 行列（barcodes / features / mtx）",
            "skipped",
            "barcodes.tsv.gz / matrix.mtx.gz なし",
        )

    if corpus_provided:
        add("corpus_h3", "過去論文コーパス照合（H3）", "ran", "コーパス指定あり")
    else:
        add("corpus_h3", "過去論文コーパス照合（H3）", "skipped", "コーパス未指定")

    llm_status = artifacts.get("legend_llm_status")
    if isinstance(llm_status, dict) and llm_status.get("status"):
        st = str(llm_status.get("status"))
        msg = str(llm_status.get("message") or "")
        if st == "active":
            add("legend_llm", "Legend LLM 補助（Figチャンク抽出）", "ran", msg)
        elif st == "unavailable":
            add("legend_llm", "Legend LLM 補助（Figチャンク抽出）", "skipped", msg)
        else:
            add("legend_llm", "Legend LLM 補助（Figチャンク抽出）", "skipped", msg)
    else:
        add(
            "legend_llm",
            "Legend LLM 補助（Figチャンク抽出）",
            "skipped",
            "オフ（規則抽出のみ）",
        )

    n_matrix = artifacts.get("n_matrix") or []
    if n_matrix:
        n_mis = sum(1 for r in n_matrix if isinstance(r, dict) and r.get("mismatch"))
        n_gap = sum(
            1
            for r in n_matrix
            if isinstance(r, dict)
            and (
                r.get("input_gap")
                or r.get("data_link_status") == "data_missing"
                or (isinstance(r.get("data"), dict) and r["data"].get("link_status") == "data_missing")
            )
        )
        n_unlinked = sum(
            1
            for r in n_matrix
            if isinstance(r, dict)
            and (
                r.get("data_link_status") == "unlinked"
                or (isinstance(r.get("data"), dict) and r["data"].get("link_status") == "unlinked")
            )
        )
        detail = f"{len(n_matrix)} 行 · 不一致候補 {n_mis} 行"
        if n_gap:
            detail += f" · データ未投入 {n_gap} 行"
        if n_unlinked:
            detail += f" · 未紐付け {n_unlinked} 行"
        detail += "（HTML レポート参照）"
        add("n_matrix", "パネル n 対照表", "ran", detail)
        if n_gap:
            add(
                "input_gaps",
                "データフォルダ未投入の検知",
                "ran",
                (
                    f"Legend パネルに対応する実験表が無い行が {n_gap} 件。"
                    "ファイル忘れの可能性（検出失敗ではない）"
                ),
            )
        else:
            add(
                "input_gaps",
                "データフォルダ未投入の検知",
                "ran",
                "データ未投入と判定した行なし",
            )
    else:
        add("n_matrix", "パネル n 対照表", "skipped", "抽出パネルなし")
        add(
            "input_gaps",
            "データフォルダ未投入の検知",
            "skipped",
            "抽出パネルなし",
        )

    preview_sources = artifacts.get("figure_preview_sources") or []
    if preview_sources:
        add(
            "figure_preview",
            "出版 Figure プレビュー（HTML）",
            "ran",
            f"{len(preview_sources)} PDF → レポートに Fig / 対応表 / Legend を連動表示",
        )
    else:
        add(
            "figure_preview",
            "出版 Figure プレビュー（HTML）",
            "skipped",
            "Fig*.pdf 系の出版図が見つからず",
        )

    # H3: cited corpus matches (hard Warning suppressed, still report as info)
    corpus = artifacts.get("corpus_scan") if isinstance(artifacts.get("corpus_scan"), dict) else {}
    cited_matches = list(corpus.get("cited_matches") or [])
    cited_partial = list(corpus.get("cited_partial_matches") or [])
    cited_all = cited_matches + cited_partial
    if corpus_provided and cited_all:
        add(
            "image_reuse_cited",
            "画像再利用（出典あり・情報）",
            "ran",
            f"コーパス一致 {len(cited_all)} 件（出典ありのため重大 Warning は抑制・レポートに情報表示）",
        )
    elif corpus_provided:
        add(
            "image_reuse_cited",
            "画像再利用（出典あり・情報）",
            "ran",
            "出典ありのコーパス一致なし",
        )
    else:
        add(
            "image_reuse_cited",
            "画像再利用（出典あり・情報）",
            "skipped",
            "コーパス未指定",
        )

    # Bibliography + cited-paper evidence
    ref_bundle = artifacts.get("reference_bundle") if isinstance(artifacts.get("reference_bundle"), dict) else {}
    n_ref = int(ref_bundle.get("n_entries") or len(ref_bundle.get("entries") or []))
    n_cite = int(ref_bundle.get("n_in_text") or len(ref_bundle.get("in_text") or []))
    orphans = list(artifacts.get("reference_orphans") or [])
    if n_ref or n_cite:
        detail = f"References {n_ref} 件 · 本文 cite {n_cite} 件"
        if orphans:
            detail += f" · 未引用エントリ {len(orphans)}（情報）"
        add("ref_biblio", "参考文献メタ（原稿内）", "ran", detail)
    else:
        add(
            "ref_biblio",
            "参考文献メタ（原稿内）",
            "skipped",
            "References／本文 cite を検出せず",
        )

    reviews = list(artifacts.get("citation_evidence_reviews") or [])
    link = artifacts.get("ref_pdf_link") if isinstance(artifacts.get("ref_pdf_link"), dict) else {}
    if cited_papers_provided:
        n_match = len(link.get("matches") or [])
        n_unmatched = len(link.get("unmatched_bib") or [])
        add(
            "ref_cited_pdfs",
            "引用先 PDF メタ／根拠",
            "ran",
            f"紐付け {n_match} · 未紐付け bib {n_unmatched} · 根拠レビュー {len(reviews)}",
        )
    else:
        add(
            "ref_cited_pdfs",
            "引用先 PDF メタ／根拠",
            "skipped",
            "引用先 PDF 未指定（--cited-papers / WebUI）",
        )

    return {
        "file_counts": _count(bundle),
        "sample_files": _samples(bundle),
        "docx_used_for_legend": docx_used,
        "extracted_zips": list(extracted),
        "legend_llm_status": llm_status if isinstance(llm_status, dict) else None,
        "n_matrix": n_matrix if isinstance(n_matrix, list) else [],
        "figure_chunks": artifacts.get("figure_chunks") or [],
        "figure_preview_sources": list(preview_sources) if isinstance(preview_sources, list) else [],
        "cited_image_matches": cited_all,
        "citation_evidence_reviews": reviews,
        "reference_orphans": orphans,
        "legend_citation_mentioned": bool(artifacts.get("legend_citation_mentioned")),
        "checks": checks,
        "notes": [
            "Warning 0 件は「問題なし確定」ではなく、上記チェックで閾値超えが無かったことを意味します。",
            "表がマニフェスト中心・Legend に n=パネルが無い場合、取り違え系チェックはスキップされやすいです。",
            "10x の barcodes.tsv.gz 等は次元整合を軽量確認します（全行列の数値解析はしません）。",
            "Legend LLM は pip install -e \".[mlx]\"（Mac）または \".[llm-cuda]\" が必要です。未導入時は ON でも規則のみになります。",
            "n 対照表の「—」は対応ファイルを機械的に紐付けできなかった欄です（未検出ではなく未対応の場合があります）。",
            "パネル n 対照表では Fig → 対応表 → Legend の順で、ホバー連動ハイライトが使えます。",
            "出典ありの画像コーパス一致は重大 Warning を出さず、レポートの情報カードに残します。",
            "引用主張の根拠レビューは情報カードです。数値・極性などの決定論矛盾のみ Warning になります。",
        ],
    }


def coverage_lines(coverage: dict[str, Any]) -> list[str]:
    """Plain-text lines for WebUI display."""
    lines: list[str] = []
    counts = coverage.get("file_counts") or {}
    if counts:
        bits = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        lines.append(f"読込ファイル: {bits}")
    zips = coverage.get("extracted_zips") or []
    if zips:
        lines.append(f"展開した ZIP: {len(zips)} 件")
    docx = coverage.get("docx_used_for_legend") or []
    if docx:
        names = ", ".join(Path(p).name for p in docx[:4])
        more = f" ほか{len(docx) - 4}件" if len(docx) > 4 else ""
        label = "PDF" if all(str(p).lower().endswith(".pdf") for p in docx) else "Word"
        lines.append(f"Legend 用 {label}: {names}{more}")
    for c in coverage.get("checks") or []:
        mark = "✓" if c.get("status") == "ran" else "–"
        lines.append(f"{mark} {c.get('name')}: {c.get('detail')}")
    for note in coverage.get("notes") or []:
        lines.append(f"注: {note}")
    return lines
