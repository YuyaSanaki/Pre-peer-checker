"""Local WebUI (FastAPI) — parent folder with manuscript/ + data/."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from pre_peer_checker.gui.worker import GuiRunConfig, run_verification_job
from pre_peer_checker.web.case_layout import validate_case_root
from pre_peer_checker.web.folder_picker import pick_folder

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_REPORT = Path("outputs/web_report.html")
DEFAULT_JSON = Path("outputs/web_warnings.json")

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
        from pre_peer_checker.catalog.runtime import ACTIVE_PATTERNS, ensure_active_catalog

        v = validate_case_root(body.root)
        if not v.ok:
            return {"ok": False, "error": v.error, "n_warnings": 0, "warning_rows": []}

        prefer = (body.legend_llm_prefer or "auto").strip().lower()
        if prefer not in {"auto", "mlx", "cuda", "transformers", "none"}:
            prefer = "auto"

        llm_profile = (body.legend_llm_profile or "").strip() or None
        vlm_profile = (body.vlm_profile or "").strip() or None
        llm_model = (body.legend_llm_model or "").strip() or None

        patterns_path = None
        if body.use_active_catalog:
            ensure_active_catalog(sync=True)
            patterns_path = ACTIVE_PATTERNS

        from pre_peer_checker.imaging.past_paper_ingest import resolve_corpus_roots
        from pre_peer_checker.parsers.cited_paper_ingest import resolve_cited_paper_dirs

        corpus_roots = resolve_corpus_roots(body.corpus_ids)
        cited_dirs = resolve_cited_paper_dirs(body.cited_paper_ids)

        report_path = DEFAULT_REPORT.resolve()
        json_path = DEFAULT_JSON.resolve()
        result = run_verification_job(
            GuiRunConfig(
                inputs=list(v.inputs),
                output_html=report_path,
                output_json=json_path,
                legend_llm=bool(body.legend_llm),
                legend_llm_prefer=prefer,
                legend_llm_profile=llm_profile,
                legend_llm_model=llm_model,
                vlm_profile=vlm_profile,
                vlm_assist=bool(getattr(body, "vlm_assist", False)),
                vlm_prefer=(getattr(body, "vlm_prefer", None) or "auto"),
                patterns_path=patterns_path,
                corpus=corpus_roots,
                cited_papers=cited_dirs,
            )
        )
        if not result.ok:
            return {
                "ok": False,
                "error": result.error or "照合に失敗しました",
                "n_warnings": 0,
                "warning_rows": [],
            }

        with _state_lock:
            global _last_report
            _last_report = result.report_path

        return {
            "ok": True,
            "n_warnings": result.n_warnings,
            "report_path": str(result.report_path) if result.report_path else None,
            "warning_rows": result.warning_rows,
            "coverage": result.coverage,
            "coverage_lines": result.coverage_lines
            or (["照合完了（カバレッジ詳細なし）"] if result.ok else []),
            "legend_llm_status": result.legend_llm_status,
            "patterns_path": str(patterns_path) if patterns_path else None,
        }

    @app.get("/api/report")
    def api_report() -> FileResponse:
        with _state_lock:
            path = _last_report
        if path is None or not path.is_file():
            fallback = DEFAULT_REPORT.resolve()
            if fallback.is_file():
                path = fallback
            else:
                raise HTTPException(status_code=404, detail="レポートがまだありません")
        return FileResponse(path, media_type="text/html; charset=utf-8")

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
