"""検証パイプライン・オーケストレーション（Phase 1 決定論的コア）。"""

from __future__ import annotations

import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from pre_peer_checker.data.group_vectors import (
    GroupVector,
    experiments_look_distinct,
    extract_group_vectors,
    find_cross_table_matches,
)
from pre_peer_checker.data.stats_recalc import analyze_table_file
from pre_peer_checker.engine.case_profile import get_case_profile
from pre_peer_checker.engine.n_and_names import (
    N_AUTHORITY_FOOTER,
    filename_content_warnings,
    is_plot_quant_table,
    match_legend_n_to_vectors,
    warnings_from_n_mismatches,
    warnings_inconsistent_n_identical_plots,
)
from pre_peer_checker.engine.n_matrix import (
    attach_fig_pdf_counts,
    build_n_matrix,
    n_matrix_to_artifact,
)
from pre_peer_checker.engine.panel_plot_identity import (
    is_publication_figure_pdf,
    warnings_from_figure_panel_identity,
)
from pre_peer_checker.engine.plot_table_match import (
    best_table_for_plot,
    warnings_from_cross_plot_identity,
    warnings_from_plot_table_mismatch,
)
from pre_peer_checker.engine.shared_control import (
    warnings_from_shared_controls,
)
from pre_peer_checker.data.source_data_blocks import parse_source_data_bundle
from pre_peer_checker.engine.source_data_checks import (
    warnings_from_source_data_panels,
    warnings_from_source_data_reuse,
    warnings_from_source_data_summaries,
)
from pre_peer_checker.engine.source_values import (
    warnings_from_source_duplicates,
    warnings_from_source_ratio_artifacts,
)
from pre_peer_checker.engine.numeric_crossref import warnings_from_numeric_crossref
from pre_peer_checker.engine.derived_precision import warnings_from_derived_precision
from pre_peer_checker.engine.antibody_host import warnings_from_antibody_hosts
from pre_peer_checker.engine.methods_claim import warnings_from_methods_claims
from pre_peer_checker.engine.scale_bar_legend import warnings_from_scale_bar_legend
from pre_peer_checker.engine.errorbar_sem_sd import warnings_from_errorbar_sem_sd
from pre_peer_checker.engine.multiplicity import warnings_from_multiplicity_gap
from pre_peer_checker.engine.survival_count import warnings_from_survival_counts
from pre_peer_checker.engine.scale_mag import warnings_from_scale_mag
from pre_peer_checker.engine.microscopy_meta import warnings_from_microscopy_meta
from pre_peer_checker.engine.count_n import warnings_from_count_n
from pre_peer_checker.engine.stat_method import warnings_from_stat_method
from pre_peer_checker.engine.config_annotation import warnings_from_config_annotation
from pre_peer_checker.engine.ref_label import warnings_from_ref_labels
from pre_peer_checker.engine.ref_biblio import orphan_entry_keys, warnings_from_ref_biblio
from pre_peer_checker.engine.ref_pdf_meta import match_bib_to_pdfs, warnings_from_ref_pdf_meta
from pre_peer_checker.engine.ref_claim import review_claims_against_pdfs
from pre_peer_checker.engine.exclusion_trace import warnings_from_exclusion_id_trace
from pre_peer_checker.llm.claim_cite_schema import claims_from_in_text
from pre_peer_checker.parsers.references import parse_references_from_paragraphs
from pre_peer_checker.parsers.manuscript_text import (
    manuscript_paragraphs,
    manuscript_source_artifact,
    select_manuscript_pdfs,
)
from pre_peer_checker.parsers.cited_paper_ingest import ensure_pdfs_ingested
from pre_peer_checker.parsers.figure_panel_labels import (
    _figure_num_from_pdf_name,
    collect_panel_labels_by_figure_detailed,
    is_publication_figure_raster,
    raster_regions_from_meta,
)
from pre_peer_checker.engine.stats_residue_match import (
    match_residue_to_tables,
    warnings_from_residue_stats,
    warnings_residue_plot_table_divergence,
)
from pre_peer_checker.imaging.blot_lane import scan_blot_lane_reuse
from pre_peer_checker.imaging.corpus_scan import collect_corpus_images, scan_against_corpus
from pre_peer_checker.imaging.duplicate_scan import scan_image_duplicates_auto
from pre_peer_checker.imaging.lightglue_match import inversion_note
from pre_peer_checker.imaging.panel_reuse import scan_internal_panel_reuse
from pre_peer_checker.imaging.microscopy_scan import scan_microscopy_duplicates
from pre_peer_checker.io_bundle import FileKind, InputBundle, collect_inputs, is_r_history_name
from pre_peer_checker.data.tenx_matrix import scan_tenx_matrices
from pre_peer_checker.llm.legend_extract import (
    extract_legends_with_backend,
    legend_json_to_panel_ns,
    legends_any_citation,
    legends_any_reuse_statement,
    legends_to_artifact,
    merge_panel_ns,
    normalize_legend_llm_mode,
    summarize_legend_llm_meta,
)
from pre_peer_checker.parsers.figure_chunks import legend_coverage
from pre_peer_checker.parsers.legend_struct import (
    all_panel_ns,
    extract_structured_legends,
    extract_structured_legends_from_paragraphs,
)
from pre_peer_checker.parsers.pdf_figures import extract_pdf
from pre_peer_checker.parsers.docx_images import export_docx_images
from pre_peer_checker.parsers.pdf_images import export_embedded_images
from pre_peer_checker.parsers.pdf_plot_digitize import digitize_plot_pdf, find_plot_pdfs
from pre_peer_checker.engine.script_dag import warnings_from_python_dag, warnings_from_r_dag
from pre_peer_checker.engine.script_resolve import (
    enrich_python_dag_with_local_paths,
    enrich_r_dag_with_local_paths,
    python_dag_to_artifact_dict,
    r_dag_to_artifact_dict,
)
from pre_peer_checker.parsers.python_ast import analyze_python_dag_file
from pre_peer_checker.parsers.r_residue import find_residue_files, parse_textclipping
from pre_peer_checker.parsers.r_treesitter import analyze_r_dag_file, analyze_r_file
from pre_peer_checker.parsers.prism_pzfx import is_prism_binary, parse_pzfx
from pre_peer_checker.parsers.kaleida import kaleida_to_artifact, parse_kaleida_file
from pre_peer_checker.parsers.yaml_config import extract_group_defs, parse_yaml_config
from pre_peer_checker.pipeline.progress import ProgressTracker, Stage
from pre_peer_checker.pipeline.run_coverage import build_run_coverage
from pre_peer_checker.report.figure_compare import attach_figure_compares
from pre_peer_checker.report.html_report import write_html_report
from pre_peer_checker.warnings import WarningItem, WarningTag


@dataclass
class VerificationResult:
    bundle: InputBundle
    warnings: list[WarningItem] = field(default_factory=list)
    artifacts: dict[str, object] = field(default_factory=dict)

    def write_report(self, out_path: Path | str) -> Path:
        names = ", ".join(p.name for p in self.bundle.all_paths()[:8])
        if len(self.bundle.all_paths()) > 8:
            names += ", ..."
        coverage = self.artifacts.get("run_coverage")
        return write_html_report(
            self.warnings,
            out_path,
            file_summary=names,
            coverage=coverage if isinstance(coverage, dict) else None,
        )


def _select_docx_for_legend(docx_files: list[Path]) -> list[Path]:
    """Pick manuscript Word files for legend extraction (avoid response / loose copies)."""
    if not docx_files:
        return []

    def _score(p: Path) -> tuple[int, int, str]:
        name = p.name.lower()
        score = 0
        if "response" in name or "responce" in name:
            score -= 100
        if "highlight" in name or "etoc" in name or "template" in name:
            score -= 50
        if "suppl" in name or "supplement" in name:
            score -= 5
        if " copy" in name or name.endswith(" copy.docx"):
            score -= 20
        if "final" in name:
            score += 30
        if "text" in name:
            score += 20
        # Prefer shorter, non-copy names when scores tie
        return (score, -len(name), name)

    ranked = sorted(docx_files, key=_score, reverse=True)
    best = _score(ranked[0])[0]
    if best < 0:
        return list(docx_files)
    # Keep top tier (same score band) so Suppl + main text finals both run when tied high
    top = [p for p in ranked if _score(p)[0] >= max(best - 10, 0)]
    return top or ranked[:1]


def _release_model_memory() -> None:
    """Return freed model weights to the OS/GPU (best effort)."""
    import gc
    import sys

    gc.collect()
    mx = sys.modules.get("mlx.core")
    if mx is not None:
        clear = getattr(mx, "clear_cache", None) or getattr(
            getattr(mx, "metal", None), "clear_cache", None
        )
        if callable(clear):
            try:
                clear()
            except Exception:  # noqa: BLE001
                pass
    torch = sys.modules.get("torch")
    if torch is not None:
        for dev in ("cuda", "xpu", "mps"):
            mod = getattr(torch, dev, None)
            empty = getattr(mod, "empty_cache", None)
            try:
                if callable(empty) and (dev == "mps" or mod.is_available()):
                    empty()
            except Exception:  # noqa: BLE001
                pass


def _publication_figure_files(bundle: InputBundle, roots: list[Path]) -> list[Path]:
    """Fig*.pdf plus standalone JPEG/PNG/TIFF named like publication figures."""
    seen: set[Path] = set()
    out: list[Path] = []

    def add(p: Path) -> None:
        try:
            rp = p.resolve()
        except OSError:
            return
        if rp in seen or not p.is_file():
            return
        seen.add(rp)
        out.append(p)

    for p in bundle.get(FileKind.PDF):
        if is_publication_figure_pdf(p):
            add(p)
    for p in bundle.get(FileKind.IMAGE):
        if is_publication_figure_raster(p):
            add(p)
    for root in roots:
        base = root if root.is_dir() else root.parent
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            if is_publication_figure_pdf(p) or is_publication_figure_raster(p):
                add(p)
    return out


def _letter_crop_boxes_by_source(meta: dict) -> dict[str, list]:
    from pre_peer_checker.imaging.panel_split import PanelBox

    out: dict[str, list] = {}
    for r in raster_regions_from_meta(meta):
        src = r.get("source")
        if not src:
            continue
        try:
            key = str(Path(src).resolve())
        except OSError:
            key = str(src)
        try:
            box = PanelBox(int(r["x0"]), int(r["y0"]), int(r["x1"]), int(r["y1"]))
        except (KeyError, TypeError, ValueError):
            continue
        if box.width > 20 and box.height > 20:
            out.setdefault(key, []).append(box)
    return out


def _plan_stages(
    *,
    legend_llm: bool | str,
    vlm_assist: bool,
    n_corpus_images: int,
    cited_papers: bool,
    bundle: InputBundle | None = None,
) -> list[Stage]:
    """進捗表示用のステージ一覧。見積り秒数は入力件数からの粗い目安（実測・履歴で補正される）。"""

    def n(*kinds: FileKind) -> int:
        return sum(len(bundle.get(k)) for k in kinds) if bundle is not None else 4

    n_pdf = n(FileKind.PDF)
    # Without Word, one PDF is read as the manuscript.
    n_docx = min(n(FileKind.DOCX), 2) or min(n_pdf, 1)
    n_fig_pdf = (
        sum(1 for p in bundle.get(FileKind.PDF) if is_publication_figure_pdf(p))
        if bundle is not None
        else 4
    )
    n_fig_raster = (
        sum(1 for p in bundle.get(FileKind.IMAGE) if is_publication_figure_raster(p))
        if bundle is not None
        else 0
    )
    n_fig = n_fig_pdf + n_fig_raster
    n_images = n(FileKind.IMAGE)
    n_micro = n(FileKind.LIF, FileKind.CZI)
    llm_mode = normalize_legend_llm_mode(legend_llm)
    llm_on = llm_mode == "on"
    # Legend LLM は Figure 数ぶん生成する（Figure 数は抽出するまで不明なので 1 原稿 8 Figure と仮定）。
    # auto は規則で読めなかった Figure だけなので 1 原稿 1 Figure 程度と仮定する。
    if llm_on:
        legend_est = 20.0 + 30.0 * 8 * n_docx
    elif llm_mode == "auto":
        legend_est = 1.0 + 2.0 * n_docx + 60.0 * n_docx
    else:
        legend_est = 1.0 + 2.0 * n_docx
    legend_est += 8.0 * n_fig_raster
    panels_est = 2.0 + 3.0 * n_fig_pdf
    if vlm_assist:
        panels_est += 30.0 + 25.0 * min(n_fig_pdf, 6) * 0.5

    stages = [
        Stage("collect", "入力ファイルの収集", 3.0),
        Stage(
            "scripts",
            "解析スクリプト・Prism・設定ファイルの読み取り",
            1.0 + 0.3 * n(FileKind.PYTHON, FileKind.NOTEBOOK, FileKind.R_SCRIPT, FileKind.PRISM),
        ),
        Stage(
            "legend_llm" if llm_on else "legend",
            {
                "on": "Figure Legend の読み取り（ローカル LLM）",
                "auto": "Figure Legend の読み取り（規則＋読めない Figure だけ LLM）",
            }.get(llm_mode, "Figure Legend の読み取り（規則ベース）"),
            legend_est,
        ),
        Stage("tables", "表データの読み込み・統計の再計算", 1.0 + 0.5 * n(FileKind.CSV, FileKind.EXCEL)),
        Stage("consistency", "本文・Legend とデータの整合チェック", 2.0 + 1.5 * n_fig),
        Stage(
            "references",
            "参考文献チェック（引用先 PDF 照合あり）" if cited_papers else "参考文献チェック",
            2.0 + (30.0 if cited_papers else 0.0),
        ),
        Stage("plots", "Legend の n と生データ・作図 PDF の突合", 1.0 + 0.5 * n_pdf),
        Stage(
            "n_matrix_llm" if llm_on else "n_matrix",
            "n 対照表の作成（群名の対応付け）",
            22.0 if llm_on else 2.0,
        ),
        Stage(
            "figure_panels_vlm" if vlm_assist else "figure_panels",
            "出版 Figure のパネル解析（VLM 補助あり）" if vlm_assist else "出版 Figure のパネル解析",
            panels_est,
        ),
        Stage("images_extract", "PDF から埋め込み画像を抽出", 1.0 + 1.0 * n_pdf),
        Stage("images_dup", "Figure 内画像の類似度スキャン", 5.0 + 1.0 * n_fig_pdf),
        Stage(
            "images_panel_reuse",
            "原稿内のパネル単位の画像使い回しスキャン",
            # LightGlue は最大 2500 組（~45 ms/組）で頭打ちになるのでほぼ一定
            min(190.0, 20.0 + 2.0 * (n_images + 8 * n_fig_pdf + 8 * n(FileKind.DOCX))),
        ),
        Stage(
            "images_micro",
            "顕微鏡・ラスタ画像の重複スキャン",
            2.0 + 0.5 * n_images + 1.0 * n_micro + (10.0 if n_images + n_micro >= 2 else 0.0),
        ),
        Stage(
            "images_meta",
            "ブロットのレーン再利用・スケールバー・顕微鏡メタデータの照合",
            2.0 + 0.2 * n_images + 0.5 * n_micro,
        ),
    ]
    if n_corpus_images:
        # 部分一致は原稿画像 × コーパス画像の全ペアを照合する（上限は scan_against_corpus の既定値）
        # パネル照合は LightGlue 最大 2000 組（~45 ms/組）+ パネル特徴抽出
        n_query = min(n_images + n_micro + 10 * n_fig_pdf, 40)
        stages.append(
            Stage(
                "corpus",
                "過去論文コーパスとの画像照合（H3）",
                140.0 + 3.0 * max(n_query, 1) * min(n_corpus_images, 80),
            )
        )
    stages.append(Stage("report", "カバレッジ集計・レポート出力", 3.0))
    return stages


def _plan_flags(
    *,
    legend_llm: bool | str,
    vlm_assist: bool,
    corpus: list[Path | str] | None,
    cited_papers: list[Path | str] | None,
) -> dict[str, bool | int | str]:
    return {
        "legend_llm": normalize_legend_llm_mode(legend_llm),
        "vlm_assist": bool(vlm_assist),
        "n_corpus_images": len(collect_corpus_images(list(corpus))) if corpus else 0,
        "cited_papers": bool(cited_papers),
    }


def initial_stages(
    *,
    legend_llm: bool | str,
    vlm_assist: bool,
    corpus: list[Path | str] | None = None,
    cited_papers: list[Path | str] | None = None,
) -> list[Stage]:
    """入力走査前の暫定ステージ一覧（照合前の準備中から全体の残り時間を出すため）。"""
    return _plan_stages(
        **_plan_flags(
            legend_llm=legend_llm,
            vlm_assist=vlm_assist,
            corpus=corpus,
            cited_papers=cited_papers,
        )
    )


def run_verification(
    paths: list[Path | str],
    *,
    corpus: list[Path | str] | None = None,
    cited_papers: list[Path | str] | None = None,
    legend_llm: bool | str = False,
    legend_llm_prefer: str = "auto",
    legend_llm_model: str | None = None,
    legend_llm_profile: str | None = None,
    vlm_profile: str | None = None,
    vlm_assist: bool = False,
    vlm_prefer: str = "auto",
    vlm_model: str | None = None,
    patterns_path: Path | str | None = None,
    progress: ProgressTracker | None = None,
) -> VerificationResult:
    """検証パイプライン（読む＝LLM/VLM、比べる＝決定論）。

    corpus: 過去論文画像コーパス（H3 外部照合）。未指定ならスキップ。
    cited_papers: 引用先 PDF／そのディレクトリ（文献メタ＋引用整合）。未指定ならスキップ。
    legend_llm: ``"on"``/True で全 Figure を MLX/CUDA LLM で読む（読む本線）。``"auto"`` は
        規則で n を読み切れなかった Figure だけ LLM に回す（全部読めればモデルを読み込まない）。
        ``"off"``/False は規則のみ。
    legend_llm_profile / vlm_profile: ``llm/model_registry.yaml`` のプロファイル ID。
    vlm_assist: True のときベクターパネル分割が空の出版 Fig に VLM パネル地図を補助。
    patterns_path: 照合カタログ JSON。未指定時はアクティブカタログ → fixtures。
    progress: 進捗トラッカー（WebUI の進捗バー用）。最後の "report" ステージは開始のみ行い、
        レポート書き出し後の ``finish()`` は呼び出し側の責任。
    """
    from pre_peer_checker.catalog.runtime import (
        enabled_pattern_ids,
        filter_warnings_by_catalog,
        load_runtime_catalog,
        resolve_patterns_path,
    )
    from pre_peer_checker.llm.registry import resolve_model

    legend_llm = normalize_legend_llm_mode(legend_llm)
    tracker = progress if progress is not None else ProgressTracker()
    plan_flags = _plan_flags(
        legend_llm=legend_llm,
        vlm_assist=vlm_assist,
        corpus=corpus,
        cited_papers=cited_papers,
    )
    tracker.set_stages(_plan_stages(**plan_flags))
    tracker.start("collect", "入力フォルダを走査中")

    catalog_path = resolve_patterns_path(patterns_path)
    catalog = load_runtime_catalog(catalog_path)

    bundle = collect_inputs(paths)
    tracker.set_stages(_plan_stages(**plan_flags, bundle=bundle))
    result = VerificationResult(bundle=bundle)
    result.artifacts["patterns_catalog"] = {
        "path": str(catalog_path),
        "n_patterns": len(catalog.get("patterns") or []),
        "n_enabled": len(enabled_pattern_ids(catalog)),
        "revision": (catalog.get("catalog_policy") or {}).get("catalog_revision"),
    }
    result.artifacts["llm_selection"] = {
        "llm": resolve_model(
            role="text",
            profile_id=legend_llm_profile,
            model_id=legend_llm_model,
            prefer=None
            if (legend_llm_prefer or "auto").lower() == "auto"
            else legend_llm_prefer,
        ).to_dict(),
        "vlm": resolve_model(role="vision", profile_id=vlm_profile).to_dict(),
    }
    if bundle.extracted_from:
        result.artifacts["extracted_zips"] = list(bundle.extracted_from)
    roots = [Path(p).resolve() for p in paths]
    corpus_roots = [Path(p).resolve() for p in (corpus or [])]
    cited_paper_roots = [Path(p).resolve() for p in (cited_papers or [])]

    # --- スクリプト DAG（Python ast / R tree-sitter / Rhistory） ---
    table_paths_early = bundle.get(FileKind.CSV) + bundle.get(FileKind.EXCEL)

    py_files = list(bundle.get(FileKind.PYTHON)) + list(bundle.get(FileKind.NOTEBOOK))
    r_files = list(bundle.get(FileKind.R_SCRIPT))
    n_script_files = len(py_files) + len(r_files)
    tracker.start("scripts")
    tracker.update(done=0, total=n_script_files)

    py_plots = []
    for i_py, p in enumerate(py_files):
        tracker.update(done=i_py, detail=p.name)
        source_kind = "notebook" if p.suffix.lower() == ".ipynb" else "python"
        try:
            dag = analyze_python_dag_file(p)
        except Exception as exc:  # noqa: BLE001
            result.artifacts.setdefault("script_parse_errors", []).append(
                {"path": str(p), "error": str(exc), "source_kind": source_kind}
            )
            continue
        enrich_python_dag_with_local_paths(
            dag, script_path=p, table_paths=table_paths_early
        )
        py_plots.append(
            python_dag_to_artifact_dict(dag, script_path=p, source_kind=source_kind)
        )
        result.warnings.extend(warnings_from_python_dag(p, dag))
    result.artifacts["python"] = py_plots

    r_bindings = []
    for i_r, p in enumerate(r_files):
        tracker.update(done=len(py_files) + i_r, detail=p.name)
        source_kind = "rhistory" if is_r_history_name(p.name) else (
            "rmd" if p.suffix.lower() == ".rmd" else "rscript"
        )
        try:
            dag = analyze_r_dag_file(p)
        except Exception as exc:  # noqa: BLE001
            result.artifacts.setdefault("script_parse_errors", []).append(
                {"path": str(p), "error": str(exc), "source_kind": source_kind}
            )
            try:
                bindings = analyze_r_file(p)
                r_bindings.append(
                    {
                        "path": str(p),
                        "bindings": [b.__dict__ for b in bindings],
                        "backend": "error",
                        "source_kind": source_kind,
                    }
                )
            except Exception:  # noqa: BLE001
                pass
            continue
        enrich_r_dag_with_local_paths(dag, script_path=p, table_paths=table_paths_early)
        r_bindings.append(
            r_dag_to_artifact_dict(dag, script_path=p, source_kind=source_kind)
        )
        if source_kind != "rhistory":
            result.warnings.extend(warnings_from_r_dag(p, dag))
    result.artifacts["r"] = r_bindings
    tracker.update(done=n_script_files, detail="Prism / KaleidaGraph / YAML")

    # --- Prism (.pzfx) / KaleidaGraph (.qpd/.qpc) ---
    prism_arts: list[dict] = []
    prism_vectors: list[GroupVector] = []
    for p in bundle.get(FileKind.PRISM):
        if is_prism_binary(p) or p.suffix.lower() == ".pzf":
            prism_arts.append(
                {
                    "path": str(p),
                    "source_kind": "prism",
                    "backend": "pzf-binary",
                    "note": "binary .pzf — Save As .pzfx for table extraction",
                    "reads": [],
                    "plots": [],
                    "saves": [],
                }
            )
            continue
        try:
            pf = parse_pzfx(p)
        except Exception as exc:  # noqa: BLE001
            result.artifacts.setdefault("script_parse_errors", []).append(
                {"path": str(p), "error": str(exc), "source_kind": "prism"}
            )
            continue
        art = pf.to_dict()
        prism_arts.append(art)
        for table in pf.tables:
            for group, values in table.y_vectors():
                if len(values) < 1:
                    continue
                prism_vectors.append(
                    GroupVector(
                        source=p,
                        group_key=f"{table.title}:{group}" if table.title else group,
                        values=tuple(sorted(values)),
                        n=len(values),
                    )
                )
    result.artifacts["prism"] = prism_arts
    result.artifacts["prism_vectors_n"] = len(prism_vectors)
    # Stash for merge into group vectors after table extract
    result.artifacts["_prism_group_vectors"] = prism_vectors

    kaleida_arts: list[dict] = []
    for p in bundle.get(FileKind.KALEIDA):
        try:
            ref = parse_kaleida_file(p)
            kaleida_arts.append(
                kaleida_to_artifact(ref, table_paths=table_paths_early)
            )
        except Exception as exc:  # noqa: BLE001
            result.artifacts.setdefault("script_parse_errors", []).append(
                {"path": str(p), "error": str(exc), "source_kind": "kaleida"}
            )
    result.artifacts["kaleida"] = kaleida_arts

    for p in bundle.get(FileKind.YAML):
        cfg = parse_yaml_config(p)
        result.artifacts.setdefault("yaml", []).append(
            {"path": str(p), "groups": extract_group_defs(cfg)}
        )

    # Prefer primary manuscript docx to reduce duplicate legends from versioned copies.
    # Without a Word file, read the manuscript text out of a PDF instead.
    docx_files = bundle.get(FileKind.DOCX)
    manuscripts = _select_docx_for_legend(docx_files)
    manuscript_kind = "docx" if manuscripts else "none"
    tracker.start(
        "legend_llm" if legend_llm == "on" else "legend",
        "Word 原稿から Figure Legend を抽出中"
        if manuscripts
        else "PDF から原稿本文を読み取り中",
    )
    if not manuscripts:
        manuscripts = select_manuscript_pdfs(
            bundle.get(FileKind.PDF), exclude_under=cited_paper_roots
        )
        if manuscripts:
            manuscript_kind = "pdf"
            tracker.update(detail="PDF 原稿から Figure Legend を抽出中")
    result.artifacts["manuscript_source"] = manuscript_source_artifact(
        manuscript_kind, manuscripts
    )

    fig_files = _publication_figure_files(bundle, roots)
    if not fig_files and manuscript_kind == "pdf":
        # Published-article PDF with figures embedded in the page layout.
        from pre_peer_checker.parsers.article_figures import export_article_figures

        tracker.update(detail="論文 PDF から Figure 領域を切り出し中")
        try:
            fig_files = export_article_figures(manuscripts[0])
        except Exception:  # noqa: BLE001
            fig_files = []
        result.artifacts["article_figures"] = {
            "source": str(manuscripts[0]),
            "figures": [p.name for p in fig_files],
        }
    if fig_files:
        tracker.update(
            detail=f"Figure のパネルラベルを読み取り中（{len(fig_files)} 件・PDF/JPEG/PNG）"
        )
    labels_by_figure, panel_label_meta = collect_panel_labels_by_figure_detailed(
        fig_files
    )
    result.artifacts["figure_panel_labels"] = labels_by_figure
    result.artifacts["figure_panel_labels_meta"] = {
        k: {
            "source": v.source,
            "needs_review": v.needs_review,
            "ocr_engine": v.ocr_engine,
            "pdf": v.pdf,
            "labels": v.labels,
            "dropped": v.dropped,
            "regions_n": len(v.regions),
        }
        for k, v in panel_label_meta.items()
    }
    from pre_peer_checker.parsers.raster_figure_panel_ocr import unload_raster_ocr_models

    unload_raster_ocr_models()
    if manuscripts:
        tracker.update(
            detail="PDF 原稿から Figure Legend を抽出中"
            if manuscript_kind == "pdf"
            else "Word 原稿から Figure Legend を抽出中"
        )

    panel_ns = []
    docx_arts = []
    coverage_arts = []
    fig_file_keys = [k for k in (_figure_num_from_pdf_name(Path(f)) for f in fig_files) if k]
    for p in manuscripts:
        try:
            paragraphs = manuscript_paragraphs(p)
            legends = extract_structured_legends_from_paragraphs(paragraphs)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if "Package not found" in msg:
                msg = (
                    "Word ファイルとして開けませんでした"
                    "（Office の一時ファイル、同期途中、または破損の可能性）。"
                    " Word で一度開いて再保存するか、コピー／~$ 一時ファイルを除いて再実行してください。"
                    f" 詳細: {exc}"
                )
            kind_label = "PDF" if p.suffix.lower() == ".pdf" else "Word"
            result.warnings.append(
                WarningItem(
                    tag=WarningTag.CONFIG_MISMATCH,
                    title=f"{kind_label} 読込失敗: {p.name}",
                    location=str(p),
                    reason=msg,
                    sources=[str(p)],
                )
            )
            continue
        pns = all_panel_ns(legends)
        panel_ns.extend(pns)
        docx_arts.append(
            {
                "path": str(p),
                "figures": [
                    {
                        "figure": leg.figure,
                        "panel_ns": [pn.__dict__ for pn in leg.panel_ns],
                        "tests": leg.tests,
                    }
                    for leg in legends
                ],
            }
        )
        coverage_arts.append(
            {"path": str(p), **legend_coverage(paragraphs, legends, fig_file_keys)}
        )
    result.artifacts["docx"] = docx_arts
    result.artifacts["legend_coverage"] = coverage_arts

    # --- Legend / Figチャンク JSON（読む＝LLM 本線；失敗時は規則フォールバック） ---
    legend_jsons = []
    legend_llm_meta: list[dict] = []
    figure_chunks_art: list[dict] = []
    n_legend_docs = len(manuscripts)
    legend_figs_before = 0
    # One text model for every manuscript and the n-matrix alias step; each
    # select_backend() call would otherwise load the weights again.
    shared_llm = None
    if legend_llm != "off":
        try:
            from pre_peer_checker.llm.backend import select_backend

            shared_llm = select_backend(
                legend_llm_prefer,
                model_id=legend_llm_model,
                profile_id=legend_llm_profile,
            )
        except Exception:  # noqa: BLE001
            shared_llm = None
    for i_doc, p in enumerate(manuscripts):
        doc_fig_total = [0]

        def _on_figure(
            done: int,
            total: int,
            label: str,
            *,
            _i=i_doc,
            _p=p,
            _offset=legend_figs_before,
            _seen=doc_fig_total,
        ) -> None:
            _seen[0] = total
            # 完了済み原稿は実際の Figure 数で積み、未着手の原稿だけ現原稿と同数と仮定する
            tracker.update(
                done=_offset + done,
                total=_offset + total * (n_legend_docs - _i),
            )
            if done >= total:
                return
            what = f"{label or 'Figure'} を読み取り中（{done + 1}/{total}）"
            if n_legend_docs > 1:
                what = f"{_p.name}: {what}"
            if legend_llm == "on" and _i == 0 and done == 0:
                what += " ※初回はモデル読込を含むため時間がかかります"
            tracker.update(detail=what)

        try:
            legs, meta = extract_legends_with_backend(
                p,
                mode=legend_llm,
                prefer=legend_llm_prefer,
                model_id=legend_llm_model,
                profile_id=legend_llm_profile,
                figure_pdfs=fig_files,
                panel_labels_by_figure=labels_by_figure,
                panel_label_meta=panel_label_meta,
                on_item=_on_figure,
                backend=shared_llm,
            )
            legend_jsons.extend(legs)
            figure_chunks_art.extend(meta.get("figure_chunks") or [])
            legend_llm_meta.append(
                {
                    "path": str(p),
                    **{k: v for k, v in meta.items() if k != "figure_chunks"},
                }
            )
        except Exception:  # noqa: BLE001
            continue
        finally:
            legend_figs_before += doc_fig_total[0]
    result.artifacts["legend_json"] = legends_to_artifact(legend_jsons)
    result.artifacts["legend_llm"] = legend_llm_meta
    result.artifacts["legend_llm_status"] = summarize_legend_llm_meta(legend_llm_meta)
    result.artifacts["figure_chunks"] = figure_chunks_art
    legend_cited = legends_any_citation(legend_jsons)
    result.artifacts["legend_citation_mentioned"] = legend_cited
    # Reference citations (stocks, methods) say nothing about image reuse
    legend_reuse_stated = legends_any_reuse_statement(legend_jsons)
    result.artifacts["legend_reuse_statement"] = legend_reuse_stated
    # When LLM produced panels, still merge with rules (rules lock on same key).
    # prefer_llm exclusive mode dropped: it discarded F/N legend n and broke H2b.
    llm_active = any(
        getattr(j, "extractor", "") in {"llm", "llm+rules"} for j in legend_jsons
    )
    panel_ns = merge_panel_ns(
        panel_ns,
        legend_json_to_panel_ns(legend_jsons),
        prefer_llm=False,
    )
    if llm_active:
        result.artifacts["legend_panel_ns_merge"] = "rules_lock"
    # a legend range (``n = 28–32``) is no single n to compare with counts
    exact_panel_ns = [pn for pn in panel_ns if pn.n_max is None]

    # --- 表: 群ベクトル + 統計 ---
    table_paths = bundle.get(FileKind.CSV) + bundle.get(FileKind.EXCEL)
    all_vectors = []
    table_stats = []
    table_load_errors: list[dict[str, str]] = []
    tracker.start("tables")
    tracker.update(done=0, total=len(table_paths))
    for i_tab, p in enumerate(table_paths):
        tracker.update(done=i_tab, detail=f"{p.name}（{i_tab + 1}/{len(table_paths)}）")
        try:
            all_vectors.extend(extract_group_vectors(p))
            table_stats.append(analyze_table_file(p))
        except Exception as exc:  # noqa: BLE001 — 顕微鏡 Detailed.csv 等は Quiet にスキップ
            table_load_errors.append({"path": str(p), "error": str(exc)})
    # Prism Y-columns participate in fingerprint linking
    prism_vecs = result.artifacts.pop("_prism_group_vectors", None) or []
    if isinstance(prism_vecs, list):
        all_vectors.extend(prism_vecs)
    result.artifacts["group_vectors"] = [
        {"source": str(v.source), "group": v.group_key, "n": v.n} for v in all_vectors
    ]
    result.artifacts["table_load_errors"] = table_load_errors
    result.artifacts["stats"] = [
        {
            "path": str(s.path),
            "groups": [g.__dict__ for g in s.groups],
            "pairwise": s.pairwise,
            "anova": s.anova,
            "hints": s.warnings_hints,
        }
        for s in table_stats
    ]

    # --- 10x Genomics matrices (barcodes.tsv.gz / features / matrix.mtx.gz) ---
    tenx_paths = (
        bundle.get(FileKind.TSV)
        + bundle.get(FileKind.MTX)
        + bundle.get(FileKind.CSV)
        + bundle.get(FileKind.TEXT)
    )
    try:
        tenx = scan_tenx_matrices(tenx_paths)
        result.warnings.extend(tenx.warnings)
        result.artifacts["tenx_matrices"] = tenx.artifacts
    except Exception as exc:  # noqa: BLE001
        result.artifacts["tenx_matrices"] = {
            "n_matrices": 0,
            "n_ok": 0,
            "total_barcodes": 0,
            "matrices": [],
            "error": str(exc),
        }

    # --- P-DATA-SWAP: 別実験系の完全一致（コントロール以外） ---
    tracker.start("consistency", "群データの一致・共有コントロールを確認中")
    plot_vectors = [v for v in all_vectors if is_plot_quant_table(v.source)]
    for m in find_cross_table_matches(plot_vectors, min_n=5, jaccard_threshold=1.0):
        if not m.exact:
            continue
        if m.a.source.name == m.b.source.name:
            continue
        distinct = experiments_look_distinct(m.a.source, m.b.source)
        if not distinct:
            continue  # shared-control path handles same-token paths
        # control-like exact across distinct experiments → shared_control engine
        gk = f"{m.a.group_key} {m.b.group_key}".lower()
        if any(t in gk for t in ("ctrl", "control", "wt", "wild", "vehicle")):
            continue
        result.warnings.append(
            WarningItem(
                tag=WarningTag.DATA_SWAP,
                title="別実験系ファイル間で群データが完全一致",
                location=f"{m.a.source.name}[{m.a.group_key}] ↔ {m.b.source.name}[{m.b.group_key}]",
                reason=(
                    f"n={m.a.n}/{m.b.n}, exact match。"
                    "別条件のはずの定量値が一致していないか確認してください。"
                ),
                sources=[str(m.a.source), str(m.b.source)],
                metadata={"pattern_id": "P-DATA-SWAP-CROSS-CONDITION", "exact": True},
            )
        )

    # --- P-SHARED-CONTROL: 完全一致 / 部分集合 / 端点欠落 ---
    from pre_peer_checker.engine.shared_control import shared_control_disclosure

    claim_texts: list[str] = []
    legend_pairs: list[tuple[str, str]] = []
    manuscript_paras: list[str] = []
    for p in manuscripts:
        try:
            for leg in extract_structured_legends(p):
                claim_texts.append(leg.text)
                legend_pairs.append((leg.figure, leg.text))
        except Exception:
            continue
        # Methods / Results 本文も主張スパーン用に取り込む（Figure Legend 以外）
        try:
            paras = manuscript_paragraphs(p)
            claim_texts.extend(paras)
            manuscript_paras.extend(paras)
        except Exception:
            continue
    for ch in figure_chunks_art:
        if isinstance(ch, dict):
            for key in ("text", "legend", "caption", "content"):
                t = ch.get(key)
                if isinstance(t, str) and t.strip():
                    claim_texts.append(t)
    for art in result.artifacts.get("legend_json") or []:
        if not isinstance(art, dict):
            continue
        for key in ("evidence_span", "context", "text"):
            t = art.get(key)
            if isinstance(t, str) and t.strip():
                claim_texts.append(t)
        cit = art.get("citation") or {}
        if isinstance(cit, dict):
            for t in cit.get("spans") or []:
                if isinstance(t, str):
                    claim_texts.append(t)
            rf = cit.get("reproduced_from")
            if isinstance(rf, str) and rf.strip():
                claim_texts.append(f"reproduced from {rf}")

    disclosure = shared_control_disclosure(claim_texts)
    result.artifacts["shared_control_claimed"] = disclosure == "explicit"
    result.artifacts["shared_control_disclosure"] = disclosure
    from pre_peer_checker.llm.legend_schema import detect_independence_claims

    indep = detect_independence_claims("\n".join(claim_texts))
    independence_claimed = bool(
        indep.mentioned and indep.kind == "independent"
    )
    result.artifacts["independence_claimed"] = independence_claimed
    result.artifacts["independence_spans"] = list(indep.spans or [])
    result.warnings.extend(
        warnings_from_shared_controls(
            plot_vectors,
            min_n=4,
            disclosure=disclosure,
            independence_claimed=independence_claimed,
        )
    )

    # --- P-SOURCE-DUPLICATE-VALUES / P-SOURCE-RATIO-ARTIFACT（同一表内指紋） ---
    result.warnings.extend(warnings_from_source_duplicates(plot_vectors, min_n=4))
    result.warnings.extend(warnings_from_source_ratio_artifacts(plot_vectors, min_n=4))
    result.warnings.extend(warnings_from_derived_precision(plot_vectors, claim_texts))

    # --- P-SOURCE-DATA-*: 雑誌 Source Data（Figure ごとのブロック） ---
    source_blocks = parse_source_data_bundle(bundle.get(FileKind.EXCEL))
    result.artifacts["source_data_blocks"] = [
        {
            "path": str(b.path),
            "cell": f"{b.sheet}!{b.header_cell}",
            "figure": b.figure.label() if b.figure else None,
            "panels": list(b.all_panels),
            "title": b.title,
            "layout": b.layout,
            "n": b.n,
            "n_comparable": b.n_comparable,
            "groups": [
                {"name": g, "n": n, **({} if ex else {"n_lower_bound": True})}
                for g, n, ex in b.group_ns[:50]
            ],
            "columns": [c.header for c in b.columns],
        }
        for b in source_blocks
    ]
    if source_blocks:
        reuse = warnings_from_source_data_reuse(source_blocks, disclosure=disclosure)
        if reuse:
            source_paths = {b.path.resolve() for b in source_blocks}
            # exact block-vs-block copies are reported by the Source Data reuse check
            result.warnings = [
                w
                for w in result.warnings
                if not (
                    w.metadata.get("pattern_id")
                    in {"P-SHARED-CONTROL-UNDISCLOSED", "P-DATA-SWAP-CROSS-CONDITION"}
                    and w.metadata.get("exact")
                    and len(w.sources) == 2
                    and {Path(s).resolve() for s in w.sources} <= source_paths
                )
            ]
        result.warnings.extend(reuse)
        result.warnings.extend(warnings_from_source_data_summaries(source_blocks))
        result.warnings.extend(
            warnings_from_source_data_panels(source_blocks, exact_panel_ns)
        )

    # --- P-NUMERIC-CROSSREF-MISMATCH / P-METHODS-CLAIM-MISMATCH ---
    tracker.update(detail="本文中の数値・統計記載とデータを照合中")
    result.warnings.extend(
        warnings_from_numeric_crossref(claim_texts, plot_vectors)
    )
    result.warnings.extend(warnings_from_methods_claims(claim_texts))
    result.warnings.extend(
        warnings_from_antibody_hosts(
            manuscript_paras, sources=[str(p) for p in manuscripts]
        )
    )

    # --- P1/P2: errorbar / multiplicity / survival / count-n ---
    result.warnings.extend(
        warnings_from_errorbar_sem_sd(claim_texts, plot_vectors)
    )
    result.warnings.extend(
        warnings_from_multiplicity_gap(claim_texts, plot_vectors)
    )
    result.warnings.extend(
        warnings_from_survival_counts(claim_texts, list(table_paths))
    )
    result.warnings.extend(warnings_from_count_n(claim_texts, all_vectors))

    # --- deferred→配線: STAT-METHOD / CONFIG / REF ---
    script_texts: list[str] = []
    script_paths: list[Path] = []
    for kind in (FileKind.R_SCRIPT, FileKind.PYTHON):
        for sp in bundle.get(kind):
            script_paths.append(Path(sp))
            try:
                script_texts.append(Path(sp).read_text(encoding="utf-8", errors="ignore")[:80_000])
            except Exception:
                continue
    result.warnings.extend(
        warnings_from_stat_method(
            claim_texts,
            plot_vectors,
            script_texts=script_texts,
            script_paths=script_paths,
        )
    )
    result.warnings.extend(
        warnings_from_config_annotation(
            list(result.artifacts.get("yaml") or []),
            all_vectors if all_vectors else plot_vectors,
        )
    )
    tracker.update(detail=f"本文の Fig 参照と図上のパネルラベルを照合中（{len(fig_files)} 件）")
    result.warnings.extend(
        warnings_from_ref_labels(claim_texts, labels_by_figure)
    )

    # --- 参考文献メタ（原稿内）+ 任意: 引用先 PDF ---
    tracker.start("references", "原稿の References と本文中の引用を照合中")
    ref_bundle = None
    ref_sources = bundle.get(FileKind.DOCX) if manuscript_kind == "docx" else manuscripts
    for p in ref_sources:
        try:
            ref_bundle = parse_references_from_paragraphs(manuscript_paragraphs(p))
            if ref_bundle.entries or ref_bundle.in_text:
                break
        except Exception:
            continue
    if ref_bundle is not None:
        result.artifacts["reference_bundle"] = ref_bundle.to_dict()
        orphans = orphan_entry_keys(ref_bundle)
        if orphans:
            result.artifacts["reference_orphans"] = orphans
        result.warnings.extend(warnings_from_ref_biblio(ref_bundle))

        cited_entry_dirs: list[Path] = []
        if cited_paper_roots:
            tracker.update(detail="引用先 PDF を読み込み・引用主張の根拠を検索中")
            try:
                cited_entry_dirs = ensure_pdfs_ingested(cited_paper_roots)
            except Exception as exc:  # noqa: BLE001
                result.artifacts["cited_papers_error"] = str(exc)
                cited_entry_dirs = []
            result.artifacts["cited_paper_dirs"] = [str(p) for p in cited_entry_dirs]
            link_report = match_bib_to_pdfs(ref_bundle, cited_entry_dirs)
            result.artifacts["ref_pdf_link"] = {
                "matches": link_report.get("matches"),
                "ambiguous": link_report.get("ambiguous"),
                "unmatched_bib": link_report.get("unmatched_bib"),
                "n_pdf_entries": link_report.get("n_pdf_entries"),
                "n_unmatched_pdf": len(link_report.get("unmatched_pdf") or []),
            }
            result.warnings.extend(warnings_from_ref_pdf_meta(link_report))
            claims = claims_from_in_text(ref_bundle.in_text)
            result.artifacts["citation_claims"] = [c.to_dict() for c in claims]
            reviews, claim_warns = review_claims_against_pdfs(claims, link_report)
            result.artifacts["citation_evidence_reviews"] = reviews
            result.warnings.extend(claim_warns)
        else:
            result.artifacts["cited_paper_dirs"] = []
            result.artifacts["ref_pdf_link"] = {"note": "cited_papers not provided"}
    else:
        result.artifacts["reference_bundle"] = {
            "note": "no manuscript references parsed",
            "entries": [],
            "in_text": [],
        }

    # --- P-N-MISMATCH / P-EXCLUSION-UNDECLARED: Legend n vs 生データ ---
    tracker.start("plots", "Legend の n と生データの行数を突合中")
    from pre_peer_checker.llm.legend_schema import detect_exclusion_criteria

    exclusion_blobs: list[str] = list(claim_texts)
    for j in legend_jsons:
        excl = getattr(j, "exclusion_criteria", None)
        if excl is not None and getattr(excl, "mentioned", False):
            exclusion_blobs.extend(list(getattr(excl, "spans", None) or []) or ["exclusion"])
        raw = getattr(j, "raw_excerpt", None)
        if isinstance(raw, str) and raw.strip():
            exclusion_blobs.append(raw)
    exclusion_mentioned = detect_exclusion_criteria("\n".join(exclusion_blobs)).mentioned
    result.artifacts["exclusion_criteria_mentioned"] = exclusion_mentioned
    n_mismatches = match_legend_n_to_vectors(exact_panel_ns, all_vectors)
    result.warnings.extend(
        warnings_from_n_mismatches(
            n_mismatches, exclusion_mentioned=exclusion_mentioned
        )
    )
    result.warnings.extend(
        warnings_from_exclusion_id_trace(
            exclusion_blobs,
            list(table_paths),
            exclusion_mentioned=exclusion_mentioned,
        )
    )
    result.artifacts["legend_panel_ns"] = [pn.__dict__ for pn in panel_ns]

    # Fallback: bare n= only when no structured panel n was extracted
    if not panel_ns:
        from pre_peer_checker.parsers.legend_struct import _N_BARE_RE, extract_structured_legends as _esl

        bare_ns: list[tuple[str, int]] = []
        for p in bundle.get(FileKind.DOCX) if manuscript_kind == "docx" else manuscripts:
            try:
                legs = _esl(p)
            except Exception:
                continue
            for leg in legs:
                if leg.panel_ns:
                    continue
                for m in _N_BARE_RE.finditer(leg.text):
                    bare_ns.append((leg.figure, int(m.group(1))))
        seen_bare: set[tuple[str, int]] = set()
        for fig, legend_n in bare_ns:
            key = (fig, legend_n)
            if key in seen_bare:
                continue
            seen_bare.add(key)
            for v in plot_vectors:
                if v.n != legend_n and abs(v.n - legend_n) == 1 and v.n >= 5:
                    result.warnings.append(
                        WarningItem(
                            tag=WarningTag.SAMPLE_SIZE,
                            title=f"{fig}: サンプルサイズの乖離候補",
                            location=f"{fig} / {v.source.name}[{v.group_key}]",
                            reason=(
                                f"Legend の n={legend_n} に対し生データ群 n={v.n}。"
                                f"{N_AUTHORITY_FOOTER}"
                                "パネル紐付けが無いため候補警告です。"
                            ),
                            sources=[str(v.source)],
                            metadata={
                                "pattern_id": "P-N-MISMATCH-LEGEND-VS-DATA",
                                "bare": True,
                                "n_authority": "raw_data_nrows",
                            },
                        )
                    )
                    break

    # --- ファイル名ヒューリスティック ---
    result.warnings.extend(filename_content_warnings(table_paths))

    # --- R 残渣 + 統計突合 ---
    cleaned = []
    residue_paths: list[Path] = []
    for root in roots:
        base = root if root.is_dir() else root.parent
        for rp in find_residue_files(base):
            if not rp.name.lower().endswith(".textclipping"):
                continue
            residue_paths.append(rp)
            try:
                st = parse_textclipping(rp)
                cleaned.append(
                    {
                        "path": str(st.path),
                        "kind": st.kind,
                        "means": st.means,
                        "p_value": st.p_value,
                        "df": st.df,
                        "group_sizes": list(st.group_sizes),
                    }
                )
            except Exception:
                pass
    result.artifacts["r_residue"] = cleaned

    stats_by_path = {s.path.resolve(): s for s in table_stats}
    result.warnings.extend(warnings_from_residue_stats(residue_paths, stats_by_path))

    # --- H1/H4: Rplot 等のベクトル PDF 数字化 ↔ 同フォルダ表 ---
    vectors_by_table: dict[Path, list] = defaultdict(list)
    for v in all_vectors:
        if is_plot_quant_table(v.source):
            vectors_by_table[v.source.resolve()].append(v)

    digitized_plots = []
    plot_pdfs: list[Path] = []
    for root in roots:
        # Directory roots: scan for Rplot/graph residue PDFs only.
        # Single-file PDF roots: digitize only if residue-like — never treat a
        # publication multi-panel figure PDF as a ggplot export, and never
        # rglob its parent (that floods H2b with unrelated residue plots).
        if root.is_dir():
            plot_pdfs.extend(find_plot_pdfs(root))
        elif root.is_file() and root.suffix.lower() == ".pdf":
            name = root.name.lower()
            if name.startswith("rplot") or name.startswith("graph"):
                plot_pdfs.append(root)
    # also include bundle PDFs named Rplot*
    for p in bundle.get(FileKind.PDF):
        if p.name.lower().startswith("rplot") or p.name.lower().startswith("graph"):
            plot_pdfs.append(p)
    seen_pdf: set[Path] = set()
    uniq_plots: list[Path] = []
    for p in plot_pdfs:
        rp = p.resolve()
        if rp not in seen_pdf:
            seen_pdf.add(rp)
            uniq_plots.append(p)

    dig_arts = []
    seen_div: set[tuple[str, str, str, str]] = set()
    tracker.update(done=0, total=len(uniq_plots))
    for i_plot, pdf in enumerate(uniq_plots):
        tracker.update(
            done=i_plot,
            detail=f"作図 PDF を数値化中: {pdf.name}（{i_plot + 1}/{len(uniq_plots)}）",
        )
        try:
            plots = digitize_plot_pdf(pdf, max_pages=1)
        except Exception:
            continue
        for plot in plots:
            digitized_plots.append(plot)
            dig_arts.append(
                {
                    "path": str(plot.path),
                    "groups": [{"label": g.label, "n": g.n} for g in plot.groups],
                }
            )
            # Prefer co-located tables; fall back to all plot quant tables
            local = {
                k: v
                for k, v in vectors_by_table.items()
                if k.parent.resolve() == plot.path.parent.resolve()
            }
            candidates = local or dict(vectors_by_table)
            hits = best_table_for_plot(plot, candidates)
            result.warnings.extend(warnings_from_plot_table_mismatch(plot, hits))
            if hits and hits[0].mean_score >= 0.9:
                plot_l = plot.path.name.lower()
                for rp_path in residue_paths:
                    if rp_path.parent.resolve() != plot.path.parent.resolve():
                        continue
                    res_l = rp_path.name.lower()
                    # Same-side residue+plot only (<Side>Welch vs Rplot<Side>, not other side)
                    same_side = any(
                        t in plot_l and t in res_l
                        for t in get_case_profile().side_label_tokens()
                    )
                    if not same_side:
                        continue
                    rhits = match_residue_to_tables(rp_path, stats_by_path)
                    if not rhits or not rhits[0].matched:
                        continue
                    key = (
                        rp_path.name,
                        plot.path.name,
                        rhits[0].table.name,
                        hits[0].table.name,
                    )
                    if key in seen_div:
                        continue
                    seen_div.add(key)
                    result.warnings.extend(
                        warnings_residue_plot_table_divergence(
                            residue_best_table=rhits[0].table,
                            plot_best_table=hits[0].table,
                            residue_path=rp_path,
                            plot_path=plot.path,
                        )
                    )
    result.warnings.extend(warnings_from_cross_plot_identity(digitized_plots))
    result.warnings.extend(
        warnings_inconsistent_n_identical_plots(exact_panel_ns, all_vectors, digitized_plots)
    )
    result.artifacts["digitized_plots"] = dig_arts

    # --- n 対照表（原稿 / 実験データ / 作図 / 統計）---
    tracker.start(
        "n_matrix_llm" if legend_llm == "on" else "n_matrix",
        "Legend の群名と表の列名を対応付け中",
    )
    legend_json_by_panel: dict[tuple[str, str], dict] = {}
    manuscript_by_figure: dict[str, str] = {}
    for meta in legend_llm_meta:
        docx_path = str(meta.get("path") or "")
        for ch in meta.get("figure_chunks") or []:
            fid = str(ch.get("figure_id") or ch.get("figure") or "")
            if fid and docx_path:
                manuscript_by_figure.setdefault(fid, docx_path)
    for fig in legend_jsons:
        if fig.figure and manuscript_by_figure.get(fig.figure) is None:
            # fall back: first docx that produced this figure via legends
            for meta in legend_llm_meta:
                if meta.get("path"):
                    manuscript_by_figure.setdefault(fig.figure, str(meta["path"]))
                    break
        for p in fig.panels:
            if not p.panel:
                continue
            legend_json_by_panel[(fig.figure, p.panel.upper())] = {
                "extractor": fig.extractor,
                "evidence_span": p.evidence_span,
                "n_scope": p.n_scope,
                "confidence": p.confidence,
                "n": p.n,
                "groups": list(getattr(p, "groups", None) or []),
            }
    script_arts = (
        list(result.artifacts.get("python") or [])
        + list(result.artifacts.get("r") or [])
        + list(result.artifacts.get("prism") or [])
        + list(result.artifacts.get("kaleida") or [])
    )
    case_roots = [Path(r) if not isinstance(r, Path) else r for r in roots]

    # Tier3 key proposals (rules + optional LLM) before n_matrix so linking can adopt
    key_alias_map: dict[str, set[str]] | None = None
    try:
        from pre_peer_checker.engine.key_normalize import (
            alias_map_from_candidates,
            propose_key_candidates,
            propose_llm_key_aliases,
        )

        legend_gs = sorted(
            {
                str(getattr(pn, "group", "") or "").strip()
                for pn in panel_ns
                if getattr(pn, "group", None)
            }
            | {
                g
                for meta in (legend_json_by_panel or {}).values()
                for g in (meta.get("groups") or [])
                if g
            }
        )
        table_gs = sorted({v.group_key for v in all_vectors if v.group_key})
        cands = propose_key_candidates(legend_gs, table_gs)
        if legend_llm != "off" and legend_gs and table_gs:
            try:
                backend = shared_llm
                # auto: reuse the model only if the legend step already loaded it
                if backend is not None and (
                    legend_llm == "on" or getattr(backend, "_model", None) is not None
                ):

                    def _alias_llm(prompt: str) -> str:
                        return backend.generate(prompt, max_tokens=512)

                    cands = cands + propose_llm_key_aliases(
                        legend_gs, table_gs, _alias_llm
                    )
            except Exception:  # noqa: BLE001
                pass
        key_alias_map = alias_map_from_candidates(cands) or None
        result.artifacts["tier3_key_candidates"] = [
            {
                "source": c.source,
                "normalized": c.normalized,
                "aliases": list(c.aliases),
                "method": c.method,
            }
            for c in cands
        ]
    except Exception:  # noqa: BLE001
        result.artifacts["tier3_key_candidates"] = []
        key_alias_map = None

    n_rows = build_n_matrix(
        exact_panel_ns,
        all_vectors,
        digitized_plots=digitized_plots,
        legend_json_by_panel=legend_json_by_panel,
        script_artifacts=script_arts,
        manuscript_by_figure=manuscript_by_figure,
        case_roots=case_roots,
        table_paths=table_paths,
        key_alias_map=key_alias_map,
    )
    result.artifacts["n_matrix"] = n_matrix_to_artifact(n_rows)
    from pre_peer_checker.engine.link_candidates import (
        link_candidate_cards,
        warnings_from_candidate_cards,
    )

    link_cards = link_candidate_cards(exact_panel_ns, all_vectors, n_rows)
    result.artifacts["n_link_candidates"] = [c.to_dict() for c in link_cards]
    result.warnings.extend(warnings_from_candidate_cards(link_cards))
    # Free the text model before the VLM and image models load (unified memory
    # on Mac is shared by all three).
    shared_llm = None
    _release_model_memory()

    # --- H1 補完: 出版 Figure PDF の多パネル点列同一性 ---
    tracker.start(
        "figure_panels_vlm" if vlm_assist else "figure_panels",
        "Figure PDF のパネル間で同一の点列がないか確認中",
    )
    pub_figs = [p for p in fig_files if p.suffix.lower() == ".pdf"]
    # Also discover Fig*.pdf under roots even if classify missed
    for root in roots:
        base = root if root.is_dir() else root.parent
        for p in base.rglob("*.pdf"):
            if is_publication_figure_pdf(p) and p.resolve() not in {x.resolve() for x in pub_figs}:
                pub_figs.append(p)
    panel_warns, panel_arts = warnings_from_figure_panel_identity(pub_figs)
    result.warnings.extend(panel_warns)
    result.artifacts["figure_panel_plots"] = panel_arts
    attach_fig_pdf_counts(result.artifacts["n_matrix"], panel_arts, roots=case_roots)
    from pre_peer_checker.engine.panel_kind_filter import demote_picture_panel_warnings

    result.artifacts["picture_panel_demoted"] = demote_picture_panel_warnings(
        result.warnings, panel_arts
    )
    # Paths only here; JPEG embed happens when writing HTML (keeps warnings.json small)
    result.artifacts["figure_preview_sources"] = [str(p) for p in fig_files]
    # Phase 6A: vector panel geometry; optional VLM assist when empty
    try:
        from pre_peer_checker.llm.panel_map_assist import extract_panel_regions_vector_then_vlm

        def _on_panel_pdf(done: int, total: int, label: str) -> None:
            tracker.update(done=done, total=total)
            if done < total:
                tracker.update(detail=f"パネル分割: {label}（{done + 1}/{total}）")

        panel_regions, geom_status = extract_panel_regions_vector_then_vlm(
            pub_figs[:6],
            vlm_assist=bool(vlm_assist),
            vlm_prefer=vlm_prefer,
            vlm_profile=vlm_profile,
            vlm_model=vlm_model,
            max_pages_vector=4,
            min_vector_panels=1,
            on_item=_on_panel_pdf,
        )
        raster_regions = raster_regions_from_meta(panel_label_meta)
        if raster_regions:
            covered = {
                (str(r.get("source") or ""), str(r.get("panel") or ""), int(r.get("page_index") or 0))
                for r in panel_regions
            }
            for r in raster_regions:
                key = (
                    str(r.get("source") or ""),
                    str(r.get("panel") or ""),
                    int(r.get("page_index") or 0),
                )
                if key not in covered:
                    panel_regions.append(r)
            geom_status["n_raster"] = len(raster_regions)
        result.artifacts["figure_panel_regions"] = panel_regions
        result.artifacts["figure_panel_regions_n"] = len(panel_regions)
        result.artifacts["panel_geometry_status"] = geom_status
    except Exception as exc:  # noqa: BLE001
        raster_regions = raster_regions_from_meta(panel_label_meta)
        result.artifacts["figure_panel_regions"] = raster_regions
        result.artifacts["figure_panel_regions_n"] = len(raster_regions)
        result.artifacts["panel_geometry_status"] = {"error": str(exc), "n_raster": len(raster_regions)}

    # --- PDF 埋め込み画像 + 顕微鏡/ラスタ同一セット重複 ---
    pdf_meta = []
    pdf_image_paths: list[Path] = []
    tmp_dirs: list[Path] = []
    all_pdfs = list(bundle.get(FileKind.PDF))
    tracker.start("images_extract")
    tracker.update(done=0, total=len(all_pdfs))
    try:
        for i_pdf, p in enumerate(all_pdfs):
            tracker.update(
                done=i_pdf,
                detail=f"PDF から埋め込み画像を抽出中: {p.name}（{i_pdf + 1}/{len(all_pdfs)}）",
            )
            meta = extract_pdf(p, extract_images=False)
            pdf_meta.append({"path": str(p), "pages": len(meta.pages)})
            name_l = p.name.lower()
            if any(k in name_l for k in ("fig", "figure", "supp")) and "editorial" not in name_l:
                td = Path(tempfile.mkdtemp(prefix="mc_pdfimg_"))
                tmp_dirs.append(td)
                pdf_image_paths.extend(
                    export_embedded_images(p, td, min_side=120, max_images=60)
                )
        result.artifacts["pdf"] = pdf_meta

        # Word 原稿の埋め込み図（Supplemental 等）。PDF 原稿でも Word 原稿でも同じ
        # 画像プールで照合できるよう、ここで一度だけ取り出して以降で使い回す。
        docx_image_paths: list[Path] = []
        docx_hashes: set[str] = set()
        docx_sorted = sorted(
            bundle.get(FileKind.DOCX),
            key=lambda p: (
                not any(k in p.name.lower() for k in ("supp", "fig")),
                p.name.lower(),
            ),
        )
        if docx_sorted:
            docx_td = Path(tempfile.mkdtemp(prefix="mc_docximg_"))
            tmp_dirs.append(docx_td)
            for i_docx, p in enumerate(docx_sorted):
                docx_image_paths.extend(
                    export_docx_images(
                        p, docx_td / str(i_docx), min_side=120, seen_hashes=docx_hashes
                    )
                )
        result.artifacts["docx_images"] = len(docx_image_paths)

        tracker.start(
            "images_dup",
            f"Figure 内画像の類似度スキャン中（{len(pdf_image_paths)} 枚・初回は画像モデル読込を含む）",
        )
        if len(pdf_image_paths) >= 2:
            matches, method = scan_image_duplicates_auto(
                pdf_image_paths, prefer_dino=True, fallback_threshold=0.998
            )
            dupes = [m for m in matches if m.likely_duplicate]
            result.artifacts["image_pair_duplicates"] = len(dupes)
            result.artifacts["pdf_image_scan_method"] = method
            seen_pairs: set[tuple[str, str]] = set()
            for m in dupes:
                key = tuple(sorted((m.path_a.name, m.path_b.name)))
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                result.warnings.append(
                    WarningItem(
                        tag=WarningTag.DATA_SWAP,
                        title="Figure PDF 内の高類似度画像ペア",
                        location=f"{m.path_a.name} ↔ {m.path_b.name}",
                        reason=(
                            f"類似度 {m.cosine_similarity:.4f}（method={m.method}）。"
                            "別パネル間でのプロット／画像取り違えの可能性があります。"
                            + inversion_note(m.precise_inverted)
                        ),
                        sources=[str(m.path_a), str(m.path_b)],
                        metadata={
                            "pattern_id": "P-DATA-SWAP-CROSS-CONDITION",
                            "similarity": m.cosine_similarity,
                            "method": m.method,
                            "inverted": bool(m.precise_inverted),
                        },
                    )
                )

        # --- 原稿内のパネル単位の使い回し（Word / PDF どちらの原稿でも同じ扱い） ---
        internal_imgs = (
            docx_image_paths
            + list(pdf_image_paths)
            + list(bundle.get(FileKind.IMAGE))
            + list(bundle.get(FileKind.LIF))
            + list(bundle.get(FileKind.CZI))
        )
        tracker.start(
            "images_panel_reuse",
            f"原稿内の画像をパネルに分割して照合中（{len(internal_imgs)} 枚）",
        )

        def _on_panel_reuse(done: int, total: int, label: str) -> None:
            if total:
                tracker.update(done=done, total=total)
            if label:
                tracker.update(detail=label)

        reuse_result = scan_internal_panel_reuse(
            internal_imgs,
            prefer_dino=True,
            prefer_lightglue=True,
            on_progress=_on_panel_reuse,
            panel_boxes=_letter_crop_boxes_by_source(panel_label_meta),
        )
        result.warnings.extend(reuse_result.warnings)
        result.artifacts["internal_panel_reuse"] = reuse_result.artifacts

        n_micro_inputs = len(
            bundle.get(FileKind.LIF) + bundle.get(FileKind.CZI) + bundle.get(FileKind.IMAGE)
        )
        tracker.start(
            "images_micro",
            f"顕微鏡・ラスタ画像の重複スキャン中（候補 {n_micro_inputs} ファイル）",
        )
        micro = scan_microscopy_duplicates(
            bundle.get(FileKind.LIF) + bundle.get(FileKind.CZI),
            bundle.get(FileKind.IMAGE),
            max_files=36,
            prefer_dino=True,
            corpus_provided=bool(corpus_roots),
        )
        result.warnings.extend(micro.warnings)
        result.artifacts["microscopy_scan"] = micro.artifacts

        tracker.start("images_meta", "ブロットのレーン再利用・スケールバーを確認中")
        blot_imgs = list(bundle.get(FileKind.IMAGE))
        if pdf_image_paths:
            blot_imgs = list(pdf_image_paths) + blot_imgs
        blot_result = scan_blot_lane_reuse(
            blot_imgs[:24],
            legend_has_reuse_note=legend_cited,
        )
        result.warnings.extend(blot_result.warnings)
        result.artifacts["blot_lane_scan"] = blot_result.artifacts

        result.warnings.extend(
            warnings_from_scale_mag(claim_texts, blot_imgs[:36])
        )
        tracker.update(detail="図中のスケールバー表記と Legend を照合中")

        def _on_scale_fig(done: int, total: int, label: str) -> None:
            if label:
                tracker.update(detail=f"図中のスケールバー表記を読み取り中: {label}（{done + 1}/{total}）")

        try:
            scale_warns, scale_art = warnings_from_scale_bar_legend(
                legend_pairs, fig_files, on_item=_on_scale_fig
            )
            result.warnings.extend(scale_warns)
            result.artifacts["scale_bar_legend"] = scale_art
        except Exception as exc:  # noqa: BLE001
            result.artifacts["scale_bar_legend"] = {"error": str(exc)}
        finally:
            unload_raster_ocr_models()
        # LIF/CZI acquisition meta × Legend（顕微鏡接地・内部照合）
        tracker.update(detail="顕微鏡の取得メタデータと Legend を照合中")
        micro_paths = (
            list(bundle.get(FileKind.LIF))
            + list(bundle.get(FileKind.CZI))
            + blot_imgs[:24]
        )
        from pre_peer_checker.imaging.czi_meta import collect_czi_acquisition_meta
        from pre_peer_checker.imaging.lif_meta import collect_lif_acquisition_meta

        lif_acq = collect_lif_acquisition_meta(list(bundle.get(FileKind.LIF)), max_files=48)
        czi_acq = collect_czi_acquisition_meta(list(bundle.get(FileKind.CZI)), max_files=48)
        result.artifacts["lif_acquisition_meta"] = [m.to_dict() for m in lif_acq]
        result.artifacts["czi_acquisition_meta"] = [m.to_dict() for m in czi_acq]
        result.warnings.extend(
            warnings_from_microscopy_meta(
                claim_texts,
                micro_paths[:64],
                lif_meta=lif_acq,
                czi_meta=czi_acq,
            )
        )

        # --- H3: 外部コーパス照合（指定時のみ） ---
        if corpus_roots:
            tracker.start("corpus", "原稿の画像と過去論文の図を照合中")
            # Word 埋め込み図（Supplemental 等）→ Figure PDF 埋め込み → 生画像 → 顕微鏡の順
            result.artifacts["corpus_docx_images"] = len(docx_image_paths)
            query_imgs = internal_imgs

            def _on_corpus(done: int, total: int, label: str) -> None:
                if total:
                    tracker.update(done=done, total=total)
                if label:
                    tracker.update(detail=label)

            corpus_result = scan_against_corpus(
                query_imgs,
                corpus_roots,
                legend_has_citation=legend_reuse_stated,
                prefer_dino=True,
                prefer_lightglue=True,
                on_progress=_on_corpus,
            )
            result.warnings.extend(corpus_result.warnings)
            result.artifacts["corpus_scan"] = corpus_result.artifacts
            result.artifacts["corpus_present"] = corpus_result.corpus_present
        else:
            result.artifacts["corpus_present"] = False
            result.artifacts["corpus_scan"] = {
                "note": "corpus not provided — H3 external match skipped"
            }
    finally:
        import shutil

        tracker.start("report", "照合カバレッジを集計中")
        # Coverage must be built before wiping zip extracts (paths still listed on bundle).
        try:
            result.artifacts["run_coverage"] = build_run_coverage(
                bundle,
                result.artifacts,
                corpus_provided=bool(corpus_roots),
                cited_papers_provided=bool(cited_paper_roots),
            )
        except Exception as exc:  # noqa: BLE001
            result.artifacts["run_coverage"] = {
                "file_counts": {},
                "sample_files": {},
                "docx_used_for_legend": [],
                "extracted_zips": list(getattr(bundle, "extracted_from", []) or []),
                "checks": [
                    {
                        "id": "coverage_error",
                        "name": "カバレッジ集計",
                        "status": "skipped",
                        "detail": f"集計に失敗: {exc}",
                    }
                ],
                "notes": ["カバレッジの一部を生成できませんでした。"],
            }
        attach_figure_compares(result.warnings)
        for td in tmp_dirs:
            shutil.rmtree(td, ignore_errors=True)
        bundle.cleanup_extracts()

    # アクティブカタログで無効な pattern_id の Warning を抑制
    kept, suppressed = filter_warnings_by_catalog(result.warnings, catalog=catalog)
    result.warnings = list(kept)
    result.artifacts["patterns_catalog_filter"] = {
        "kept": len(kept),
        "suppressed": len(suppressed),
        "suppressed_pattern_ids": sorted(
            {
                str((getattr(w, "metadata", None) or {}).get("pattern_id") or "")
                for w in suppressed
                if (getattr(w, "metadata", None) or {}).get("pattern_id")
            }
        ),
    }
    # カバレッジ再生成（フィルタ後件数を反映）
    cov = result.artifacts.get("run_coverage")
    if isinstance(cov, dict):
        notes = list(cov.get("notes") or [])
        notes.append(
            f"照合カタログ: {catalog_path.name}（有効 {len(enabled_pattern_ids(catalog))} / "
            f"抑制 Warning {len(suppressed)}）"
        )
        cov["notes"] = notes
        result.artifacts["run_coverage"] = cov

    # 統計手法ヒントは Warning 洪水になるため artifacts のみ（Legend 突合は Phase 2）
    # R/Python 同一 data 多重作図・保存名不一致は script_dag で付与済み
    return result
