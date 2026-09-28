"""Background verification worker (QThread) — no GUI widgets here for testability."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pre_peer_checker.pipeline.progress import ProgressTracker


@dataclass
class GuiRunConfig:
    inputs: list[Path]
    corpus: list[Path] = field(default_factory=list)
    cited_papers: list[Path] = field(default_factory=list)
    output_html: Path = Path("outputs/report.html")
    output_json: Path | None = Path("outputs/warnings.json")
    legend_llm: bool = False
    legend_llm_prefer: str = "auto"
    legend_llm_model: str | None = None
    legend_llm_profile: str | None = None
    vlm_profile: str | None = None
    vlm_assist: bool = False
    vlm_prefer: str = "auto"
    patterns_path: Path | None = None
    # 指定時は進捗を報告し、成功時にステージ所要時間を次回の残り時間推定用に保存する
    progress: ProgressTracker | None = None


@dataclass
class GuiRunResult:
    ok: bool
    n_warnings: int
    report_path: Path | None
    json_path: Path | None
    warning_rows: list[dict[str, Any]] = field(default_factory=list)
    coverage: dict[str, Any] | None = None
    coverage_lines: list[str] = field(default_factory=list)
    legend_llm_status: dict[str, Any] | None = None
    error: str | None = None


def _ensure_coverage_lines(
    cov_dict: dict[str, Any] | None,
    *,
    n_warnings: int,
    n_inputs: int,
) -> tuple[dict[str, Any], list[str]]:
    from pre_peer_checker.pipeline.run_coverage import coverage_lines

    if not isinstance(cov_dict, dict):
        cov_dict = {
            "file_counts": {},
            "checks": [],
            "notes": [],
        }
    lines = coverage_lines(cov_dict)
    if not lines:
        lines = [
            f"照合完了: 入力パス {n_inputs} · Warning {n_warnings} 件",
            "注: 詳細カバレッジが空でした（ファイル種別が未対応の可能性）。",
        ]
        cov_dict = {
            **cov_dict,
            "checks": cov_dict.get("checks")
            or [
                {
                    "id": "fallback",
                    "name": "カバレッジ",
                    "status": "ran",
                    "detail": lines[0],
                }
            ],
        }
        lines = coverage_lines(cov_dict) or lines
    return cov_dict, lines


def run_verification_job(config: GuiRunConfig) -> GuiRunResult:
    """Synchronous verification used by GUI worker and unit tests."""
    try:
        from pre_peer_checker.pipeline.orchestrator import run_verification

        if not config.inputs:
            return GuiRunResult(False, 0, None, None, error="入力フォルダがありません")
        for p in config.inputs:
            if not p.exists():
                return GuiRunResult(False, 0, None, None, error=f"見つかりません: {p}")

        result = run_verification(
            [str(p) for p in config.inputs],
            corpus=[str(p) for p in config.corpus] or None,
            cited_papers=[str(p) for p in config.cited_papers] or None,
            legend_llm=config.legend_llm,
            legend_llm_prefer=config.legend_llm_prefer,
            legend_llm_model=config.legend_llm_model,
            legend_llm_profile=config.legend_llm_profile,
            vlm_profile=config.vlm_profile,
            vlm_assist=bool(config.vlm_assist),
            vlm_prefer=config.vlm_prefer,
            patterns_path=config.patterns_path,
            progress=config.progress,
        )
        if config.progress is not None:
            config.progress.update(detail="HTML レポートを書き出し中")
        config.output_html.parent.mkdir(parents=True, exist_ok=True)
        report = result.write_report(config.output_html)
        cov_raw = result.artifacts.get("run_coverage")
        cov_dict, lines = _ensure_coverage_lines(
            cov_raw if isinstance(cov_raw, dict) else None,
            n_warnings=len(result.warnings),
            n_inputs=len(config.inputs),
        )
        result.artifacts["run_coverage"] = cov_dict
        pc = result.artifacts.get("patterns_catalog")
        if isinstance(pc, dict) and pc.get("path"):
            lines = [
                f"照合カタログ: {pc.get('path')}（有効 {pc.get('n_enabled')}）"
            ] + lines
        pf = result.artifacts.get("patterns_catalog_filter")
        if isinstance(pf, dict) and int(pf.get("suppressed") or 0) > 0:
            lines = [
                f"カタログ抑制: Warning {pf.get('suppressed')} 件"
            ] + lines
        llm_status = result.artifacts.get("legend_llm_status")
        if isinstance(llm_status, dict):
            # Put LLM status near the top of coverage lines for WebUI visibility
            msg = str(llm_status.get("message") or "")
            if msg:
                lines = [msg] + [ln for ln in lines if ln != msg]
        json_path = None
        if config.output_json:
            config.output_json.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "warnings": [w.to_dict() for w in result.warnings],
                "n_warnings": len(result.warnings),
                "run_coverage": cov_dict,
                "legend_llm_status": llm_status if isinstance(llm_status, dict) else None,
                "artifacts": {
                    k: result.artifacts[k]
                    for k in ("corpus_present", "microscopy_scan")
                    if k in result.artifacts
                },
            }
            config.output_json.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            json_path = config.output_json
        rows = [
            {
                "tag": w.tag.value,
                "title": w.title,
                "location": w.location,
                "pattern_id": (w.metadata or {}).get("pattern_id", ""),
            }
            for w in result.warnings
        ]
        if config.progress is not None:
            from pre_peer_checker.pipeline.progress import save_history

            config.progress.finish()
            save_history(config.progress.stage_ratios())
        return GuiRunResult(
            ok=True,
            n_warnings=len(result.warnings),
            report_path=report,
            json_path=json_path,
            warning_rows=rows,
            coverage=cov_dict,
            coverage_lines=lines,
            legend_llm_status=llm_status if isinstance(llm_status, dict) else None,
        )
    except Exception as exc:  # noqa: BLE001
        return GuiRunResult(False, 0, None, None, error=str(exc))
