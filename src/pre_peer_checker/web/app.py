"""Local WebUI (FastAPI) — parent folder with manuscript/ + data/."""

from __future__ import annotations

import platform
import subprocess
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from pre_peer_checker.gui.worker import GuiRunConfig, run_verification_job
from pre_peer_checker.pipeline.progress import ProgressTracker, load_history
from pre_peer_checker.pipeline.run_archive import (
    DEFAULT_RUNS_DIR,
    REPORT_DIR,
    RunArchive,
    list_runs,
    resolve_run_dir,
)
from pre_peer_checker.web.case_layout import validate_case_root
from pre_peer_checker.web.folder_picker import pick_folder

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
RUNS_DIR = DEFAULT_RUNS_DIR

_TEMPLATE = Path(__file__).resolve().parent / "templates" / "index.html"
_pick_lock = threading.Lock()
_state_lock = threading.Lock()
_last_report: Path | None = None


class RootBody(BaseModel):
    root: str = Field(..., min_length=1)


class RunBody(BaseModel):
    root: str = Field(..., min_length=1)
    legend_llm: bool = True
    legend_llm_prefer: str = "auto"
    legend_llm_profile: str | None = None
    legend_llm_model: str | None = None
    vlm_profile: str | None = None
    vlm_assist: bool = True
    vlm_prefer: str = "auto"
    use_active_catalog: bool = True
    corpus_ids: list[str] = Field(default_factory=list)
    cited_paper_ids: list[str] = Field(default_factory=list)
    # 保存フォルダ名に使う論文タイトル（空なら原稿から推定）
    run_title: str | None = None


class CatalogPullBody(BaseModel):
    pull: bool = True


class CatalogTriageBody(BaseModel):
    pull: bool = False
    use_llm: bool = True
    legend_llm_prefer: str = "auto"
    legend_llm_profile: str | None = None
    legend_llm_model: str | None = None
    include_already_curated: bool = False


class CatalogApplyBody(BaseModel):
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    also_update_fixtures: bool = False


class CatalogResetBody(BaseModel):
    confirm: bool = False


class CatalogShareBody(BaseModel):
    mode: str = "issue"  # issue | pr | draft
    title: str | None = None
    include_active_diff: bool = True


@dataclass
class _RunJob:
    id: str
    tracker: ProgressTracker
    done: bool = False
    result: dict[str, Any] | None = None


# 直近 1 件のみ保持（ローカル単一ユーザー前提）
_jobs: dict[str, _RunJob] = {}
_jobs_lock = threading.Lock()


def _running_job() -> _RunJob | None:
    return next((j for j in _jobs.values() if not j.done), None)


def _run_error_payload(error: str | None) -> dict[str, Any]:
    return {
        "ok": False,
        "error": error or "照合に失敗しました",
        "n_warnings": 0,
        "warning_rows": [],
    }


def _build_run_config(body: RunBody) -> tuple[GuiRunConfig | None, str | None]:
    from pre_peer_checker.catalog.runtime import ACTIVE_PATTERNS, ensure_active_catalog
    from pre_peer_checker.imaging.past_paper_ingest import resolve_corpus_roots
    from pre_peer_checker.parsers.cited_paper_ingest import resolve_cited_paper_dirs

    v = validate_case_root(body.root)
    if not v.ok:
        return None, v.error

    prefer = (body.legend_llm_prefer or "auto").strip().lower()
    if prefer not in {"auto", "mlx", "cuda", "transformers", "none"}:
        prefer = "auto"

    patterns_path = None
    if body.use_active_catalog:
        ensure_active_catalog(sync=True)
        patterns_path = ACTIVE_PATTERNS

    return (
        GuiRunConfig(
            inputs=list(v.inputs),
            legend_llm=bool(body.legend_llm),
            legend_llm_prefer=prefer,
            legend_llm_profile=(body.legend_llm_profile or "").strip() or None,
            legend_llm_model=(body.legend_llm_model or "").strip() or None,
            vlm_profile=(body.vlm_profile or "").strip() or None,
            vlm_assist=bool(body.vlm_assist),
            vlm_prefer=body.vlm_prefer or "auto",
            patterns_path=patterns_path,
            corpus=resolve_corpus_roots(body.corpus_ids),
            cited_papers=resolve_cited_paper_dirs(body.cited_paper_ids),
        ),
        None,
    )


def _library_hashes(dirs: list[Path]) -> list[dict[str, Any]]:
    from pre_peer_checker.pipeline.run_archive import sha256_file

    out = []
    for d in dirs:
        base = d if d.is_dir() else d.parent
        files = sorted(p for p in d.rglob("*") if p.is_file()) if d.is_dir() else [d]
        entries = [
            {
                "path": p.relative_to(base).as_posix(),
                "sha256": sha256_file(p),
                "size": p.stat().st_size,
            }
            for p in files
        ]
        out.append({"path": str(d), "files": entries})
    return out


def _run_urls(name: str) -> dict[str, str]:
    base = "/api/runs/" + quote(name, safe="")
    return {"report_url": base + "/report", "open_url": base + "/open"}


def _prepare_archive(config: GuiRunConfig, body: RunBody) -> RunArchive:
    """照合フォルダを作り、入力・照合条件を保存して config の出力先を差し替える。"""
    from pre_peer_checker.catalog.runtime import resolve_patterns_path

    case_root = config.inputs[0].parent
    archive = RunArchive.create(
        RUNS_DIR,
        case_root=case_root,
        manuscript_dir=config.inputs[0],
        title=body.run_title,
    )
    if config.progress is not None:
        config.progress.update(detail="照合フォルダを作成しました: " + archive.name)
    archive.snapshot_inputs(config.inputs, progress=config.progress)

    catalog_src = resolve_patterns_path(config.patterns_path)
    settings = {
        "request": body.model_dump(),
        "case_root": str(case_root),
        "inputs": [str(p) for p in config.inputs],
        "patterns_catalog_source": str(catalog_src),
        "corpus": [str(p) for p in config.corpus],
        "cited_papers": [str(p) for p in config.cited_papers],
    }
    config.patterns_path = archive.snapshot_conditions(settings, patterns_path=catalog_src)
    if config.corpus or config.cited_papers:
        if config.progress is not None:
            config.progress.update(detail="参照ライブラリの SHA-256 を記録中")
        archive.add_condition(
            "reference_libraries.json",
            {
                "corpus": _library_hashes(config.corpus),
                "cited_papers": _library_hashes(config.cited_papers),
            },
        )
    config.output_html = archive.report_dir / "report.html"
    config.output_json = archive.report_dir / "warnings.json"
    return archive


def _run_and_collect(config: GuiRunConfig, body: RunBody) -> dict[str, Any]:
    try:
        archive = _prepare_archive(config, body)
    except Exception as exc:  # noqa: BLE001
        return _run_error_payload(f"照合フォルダの保存に失敗しました: {exc}")

    try:
        result = run_verification_job(config)
    except Exception as exc:
        archive.finalize(ok=False, error=str(exc), progress=config.progress)
        raise
    manifest = archive.finalize(
        ok=result.ok,
        error=result.error,
        n_warnings=result.n_warnings if result.ok else None,
        progress=config.progress,
    )
    run_info = {
        "run_name": archive.name,
        "run_dir": str(archive.run_dir),
        "run_title": archive.title,
        "input_hash": manifest["hashes"].get("input"),
        "inputs_changed_during_run": manifest["inputs_changed_during_run"],
        **_run_urls(archive.name),
    }
    if not result.ok:
        return {**_run_error_payload(result.error), **run_info}

    with _state_lock:
        global _last_report
        _last_report = result.report_path

    patterns_path = config.patterns_path
    return {
        "ok": True,
        "n_warnings": result.n_warnings,
        "report_path": str(result.report_path) if result.report_path else None,
        "warning_rows": result.warning_rows,
        "coverage": result.coverage,
        "coverage_lines": result.coverage_lines or ["照合完了（カバレッジ詳細なし）"],
        "legend_llm_status": result.legend_llm_status,
        "patterns_path": str(patterns_path) if patterns_path else None,
        **run_info,
    }


def create_app() -> FastAPI:
    app = FastAPI(title="Pre-peer-checker", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _TEMPLATE.read_text(encoding="utf-8")

    @app.post("/api/validate-root")
    def api_validate_root(body: RootBody) -> dict[str, Any]:
        v = validate_case_root(body.root)
        if not v.ok:
            return {"ok": False, "error": v.error}
        return {
            "ok": True,
            "root": str(v.root),
            "manuscript": str(v.manuscript),
            "data": str(v.data),
        }

    @app.post("/api/pick-folder")
    def api_pick_folder() -> dict[str, Any]:
        """Open a native directory dialog on the machine running the server."""
        if not _pick_lock.acquire(blocking=False):
            return {"error": "別のフォルダ選択が進行中です。"}
        try:
            result = pick_folder()
            if result.error:
                return {"error": result.error}
            if result.cancelled or not result.path:
                return {"cancelled": True}
            return {"path": result.path}
        finally:
            _pick_lock.release()

    @app.get("/api/llm-status")
    def api_llm_status() -> dict[str, Any]:
        """Whether a local Legend LLM backend is importable on this machine."""
        from pre_peer_checker.llm.backend import probe_backends, select_backend
        from pre_peer_checker.llm.registry import (
            default_profile_id,
            effective_llm_profile_id,
            effective_vlm_profile_id,
            registry_public_dict,
        )

        catalog = registry_public_dict()
        probed = [b.__dict__ for b in probe_backends()]
        default_llm = catalog["default_llm_profile"]
        effective_llm = effective_llm_profile_id()
        # Prefer host-aware profile for auto-select (MLX無し → 7b-hf)
        selected = select_backend("auto", profile_id=effective_llm)
        available = selected is not None
        return {
            "available": available,
            "backends": probed,
            "selected": selected.info().__dict__ if selected is not None else None,
            "default_llm_profile": default_llm,
            "effective_llm_profile": effective_llm,
            "default_vlm_profile": catalog["default_vlm_profile"],
            "effective_vlm_profile": effective_vlm_profile_id(),
            "profiles": catalog["profiles"],
            "text_profiles": catalog["text_profiles"],
            "vision_profiles": catalog["vision_profiles"],
            "env_llm_profile": default_profile_id("text"),
            "install_hint": (
                None
                if available
                else "./install.sh を再実行（Apple Silicon は MLX を自動導入）"
                ' または pip install -e ".[llm-cuda]"'
            ),
        }

    @app.get("/api/catalog/status")
    def api_catalog_status() -> dict[str, Any]:
        from pre_peer_checker.catalog.runtime import (
            catalog_status,
            ensure_active_catalog,
            load_curation_queue,
        )

        # タブ表示時点でも fixtures 昇格を取り込む
        ensure_active_catalog(sync=True)
        return {"ok": True, "status": catalog_status(), "queue": load_curation_queue()}

    @app.get("/api/catalog/compare")
    def api_catalog_compare() -> dict[str, Any]:
        from pre_peer_checker.catalog.runtime import compare_catalogs, ensure_active_catalog

        ensure_active_catalog(sync=True)
        return compare_catalogs()

    @app.post("/api/catalog/pull")
    def api_catalog_pull(body: CatalogPullBody) -> dict[str, Any]:
        from pre_peer_checker.catalog.web_ops import catalog_pull_and_mine

        return catalog_pull_and_mine(pull=body.pull)

    @app.post("/api/catalog/triage")
    def api_catalog_triage(body: CatalogTriageBody) -> dict[str, Any]:
        from pre_peer_checker.catalog.web_ops import catalog_triage

        prefer = (body.legend_llm_prefer or "auto").strip().lower()
        if prefer not in {"auto", "mlx", "cuda", "transformers", "none"}:
            prefer = "auto"
        return catalog_triage(
            pull=body.pull,
            use_llm=bool(body.use_llm),
            prefer=prefer,
            profile_id=(body.legend_llm_profile or "").strip() or None,
            model_id=(body.legend_llm_model or "").strip() or None,
            include_already_curated=bool(body.include_already_curated),
        )

    @app.post("/api/catalog/apply")
    def api_catalog_apply(body: CatalogApplyBody) -> dict[str, Any]:
        from pre_peer_checker.catalog.web_ops import catalog_apply

        if not body.decisions:
            return {"ok": False, "error": "決定が空です"}
        return catalog_apply(
            body.decisions, also_update_fixtures=bool(body.also_update_fixtures)
        )

    @app.post("/api/catalog/reset-active")
    def api_catalog_reset(body: CatalogResetBody) -> dict[str, Any]:
        from pre_peer_checker.catalog.runtime import catalog_status, ensure_active_catalog

        if not body.confirm:
            return {"ok": False, "error": "confirm=true が必要です"}
        path = ensure_active_catalog(reset=True)
        return {"ok": True, "path": str(path), "status": catalog_status()}

    @app.post("/api/catalog/ingest-pdf")
    async def api_catalog_ingest_pdf(
        file: UploadFile = File(...),
        use_llm: str = Form("true"),
        legend_llm_prefer: str = Form("auto"),
        legend_llm_profile: str | None = Form(None),
    ) -> dict[str, Any]:
        """PubPeer PDF をアップロードし、抽象ルール候補をキューへ追加."""
        from pre_peer_checker.catalog.pubpeer_ingest import (
            copy_upload_bytes,
            ingest_pubpeer_pdf,
        )
        from pre_peer_checker.catalog.runtime import catalog_status

        name = file.filename or "upload.pdf"
        if not name.lower().endswith(".pdf"):
            return {"ok": False, "error": "PDF ファイルのみ受け付けます"}
        data = await file.read()
        if not data:
            return {"ok": False, "error": "空のファイルです"}
        if len(data) > 80 * 1024 * 1024:
            return {"ok": False, "error": "ファイルが大きすぎます（80MB 超）"}
        path = copy_upload_bytes(name, data)
        prefer = (legend_llm_prefer or "auto").strip().lower()
        if prefer not in {"auto", "mlx", "cuda", "transformers", "none"}:
            prefer = "auto"
        use_llm_flag = str(use_llm).strip().lower() in {"1", "true", "yes", "on"}
        result = ingest_pubpeer_pdf(
            path,
            use_llm=use_llm_flag,
            prefer=prefer,
            profile_id=(legend_llm_profile or "").strip() or None,
        )
        result["status"] = catalog_status()
        return result

    @app.get("/api/corpus/list")
    def api_corpus_list() -> dict[str, Any]:
        """ローカル過去論文コーパスライブラリ一覧（H3）。"""
        from pre_peer_checker.imaging.past_paper_ingest import ensure_library, list_entries

        ensure_library()
        entries = list_entries()
        return {
            "ok": True,
            "entries": entries,
            "n_entries": len(entries),
            "n_figures": sum(int(e.get("n_figures") or 0) for e in entries),
        }

    @app.post("/api/corpus/ingest-pdf")
    async def api_corpus_ingest_pdf(
        file: UploadFile = File(...),
    ) -> dict[str, Any]:
        """過去論文 PDF をアップロードし、図を抽出してローカルコーパスへ追加."""
        from pre_peer_checker.imaging.past_paper_ingest import (
            ingest_past_paper_bytes,
            list_entries,
        )

        name = file.filename or "upload.pdf"
        data = await file.read()
        result = ingest_past_paper_bytes(name, data)
        if result.get("ok"):
            result["entries"] = list_entries()
        return result

    @app.delete("/api/corpus/{entry_id}")
    def api_corpus_delete(entry_id: str) -> dict[str, Any]:
        from pre_peer_checker.imaging.past_paper_ingest import delete_entry

        return delete_entry(entry_id)

    @app.get("/api/cited-papers/list")
    def api_cited_papers_list() -> dict[str, Any]:
        """ローカル引用先論文ライブラリ一覧（文献メタ＋引用整合）。"""
        from pre_peer_checker.parsers.cited_paper_ingest import ensure_library, list_entries

        ensure_library()
        entries = list_entries()
        return {
            "ok": True,
            "entries": entries,
            "n_entries": len(entries),
        }

    @app.post("/api/cited-papers/ingest-pdf")
    async def api_cited_papers_ingest_pdf(
        file: UploadFile = File(...),
    ) -> dict[str, Any]:
        from pre_peer_checker.parsers.cited_paper_ingest import (
            ingest_cited_paper_bytes,
            list_entries,
        )

        name = file.filename or "upload.pdf"
        data = await file.read()
        result = ingest_cited_paper_bytes(name, data)
        if result.get("ok"):
            result["entries"] = list_entries()
        return result

    @app.delete("/api/cited-papers/{entry_id}")
    def api_cited_papers_delete(entry_id: str) -> dict[str, Any]:
        from pre_peer_checker.parsers.cited_paper_ingest import delete_entry

        return delete_entry(entry_id)

    @app.post("/api/catalog/share")
    def api_catalog_share(body: CatalogShareBody) -> dict[str, Any]:
        """抽象ルールのみを共有下書き化（GitHub PAT 不要）.

        - issue: Web Intent URL（issues/new）
        - pr: コピー用 CLI（自動 push しない）
        - draft: 本文プレビュー
        """
        from pre_peer_checker.catalog.share import (
            build_issue_intent,
            build_pr_cli_bundle,
            build_share_payload,
            format_issue_body,
        )

        mode = (body.mode or "issue").strip().lower()
        payload = build_share_payload(include_active_diff=bool(body.include_active_diff))
        if mode == "draft":
            return {
                "ok": True,
                "mode": "draft",
                "title": body.title,
                "body_markdown": format_issue_body(payload),
                "payload": payload,
            }
        if mode in {"pr", "pr_cli"}:
            return build_pr_cli_bundle()
        # default / issue / issue_intent
        return build_issue_intent(payload, title=body.title)

    @app.post("/api/run")
    def api_run(body: RunBody) -> dict[str, Any]:
        """同期実行（完了まで応答しない）。WebUI は /api/run/start + 進捗ポーリングを使う。"""
        config, error = _build_run_config(body)
        if config is None:
            return _run_error_payload(error)
        return _run_and_collect(config, body)

    @app.post("/api/run/start")
    def api_run_start(body: RunBody) -> dict[str, Any]:
        """照合をバックグラウンドで開始し job_id を返す（進捗は /api/run/status/{job_id}）。"""
        with _jobs_lock:
            running = _running_job()
            if running is not None:
                return {
                    "ok": False,
                    "error": "別の照合が実行中です。完了までお待ちください。",
                    "job_id": running.id,
                }
            config, error = _build_run_config(body)
            if config is None:
                return _run_error_payload(error)
            config.progress = ProgressTracker(history=load_history())
            job = _RunJob(id=uuid.uuid4().hex[:12], tracker=config.progress)
            _jobs.clear()
            _jobs[job.id] = job

        def _worker() -> None:
            try:
                payload = _run_and_collect(config, body)
            except Exception as exc:  # noqa: BLE001
                payload = _run_error_payload(str(exc))
            job.result = payload
            job.done = True

        threading.Thread(target=_worker, name=f"verify-{job.id}", daemon=True).start()
        return {"ok": True, "job_id": job.id}

    @app.get("/api/run/status/{job_id}")
    def api_run_status(job_id: str) -> dict[str, Any]:
        with _jobs_lock:
            job = _jobs.get(job_id)
        if job is None:
            return {"ok": False, "error": "照合ジョブが見つかりません（サーバー再起動の可能性）"}
        return {
            "ok": True,
            "job_id": job.id,
            "done": job.done,
            "progress": job.tracker.snapshot(),
            "result": job.result if job.done else None,
        }

    @app.get("/api/run/current")
    def api_run_current() -> dict[str, Any]:
        """ページ再読込時に実行中の照合へ再接続するため。"""
        with _jobs_lock:
            job = _running_job()
        return {"ok": True, "job_id": job.id if job else None}

    @app.get("/api/report")
    def api_report() -> FileResponse:
        with _state_lock:
            path = _last_report
        if path is None or not path.is_file():
            latest = next((r for r in list_runs(RUNS_DIR) if r.get("has_report")), None)
            if latest is None:
                raise HTTPException(status_code=404, detail="レポートがまだありません")
            path = Path(latest["run_dir"]) / REPORT_DIR / "report.html"
        return FileResponse(path, media_type="text/html; charset=utf-8")

    @app.get("/api/runs")
    def api_runs() -> dict[str, Any]:
        """保存済み照合フォルダ（新しい順）。"""
        runs = [{**r, **_run_urls(r["run_name"])} for r in list_runs(RUNS_DIR)]
        return {"ok": True, "runs_dir": str(RUNS_DIR.resolve()), "runs": runs}

    @app.get("/api/runs/{name}/report")
    def api_run_report(name: str) -> FileResponse:
        run_dir = resolve_run_dir(RUNS_DIR, name)
        path = run_dir / REPORT_DIR / "report.html" if run_dir else None
        if path is None or not path.is_file():
            raise HTTPException(status_code=404, detail="この照合のレポートはありません")
        return FileResponse(path, media_type="text/html; charset=utf-8")

    @app.post("/api/runs/{name}/open")
    def api_run_open(name: str) -> dict[str, Any]:
        """照合フォルダをサーバー側（ローカル）のファイルマネージャで開く。"""
        run_dir = resolve_run_dir(RUNS_DIR, name)
        if run_dir is None:
            return {"ok": False, "error": "照合フォルダが見つかりません"}
        system = platform.system()
        cmd = {"Darwin": ["open"], "Windows": ["explorer"]}.get(system, ["xdg-open"])
        try:
            subprocess.Popen([*cmd, str(run_dir)])
        except OSError as exc:
            return {"ok": False, "error": str(exc), "path": str(run_dir)}
        return {"ok": True, "path": str(run_dir)}

    return app


app = create_app()


def main(argv: list[str] | None = None) -> int:
    del argv  # CLI parity with other entry points
    import os

    import uvicorn

    port = int(os.environ.get("PRE_PEER_CHECKER_PORT", str(DEFAULT_PORT)))
    print(f"Pre-peer-checker WebUI → http://{HOST}:{port}")
    print("ブラウザで上記を開いてください（ショートカット起動時は自動で開きます）。")
    uvicorn.run(
        "pre_peer_checker.web.app:app",
        host=HOST,
        port=port,
        log_level="info",
        reload=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
