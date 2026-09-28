"""WebUI 向けカタログ更新 API ヘルパ."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pre_peer_checker.catalog.paths import CATALOG_FIXTURES, RW_CACHE_DIR
from pre_peer_checker.catalog.propose import build_proposal, write_proposal
from pre_peer_checker.catalog.reason_mine import mine_reasons
from pre_peer_checker.catalog.runtime import (
    apply_curation_decisions,
    catalog_status,
    ensure_active_catalog,
    load_curation_queue,
    save_curation_queue,
)
from pre_peer_checker.catalog.rw_pull import RwPullError, ensure_rw_repo
from pre_peer_checker.catalog.store import export_verification_yaml, sync_resources
from pre_peer_checker.catalog.triage_llm import run_triage_pipeline


def catalog_pull_and_mine(*, pull: bool = True, csv: Path | None = None) -> dict[str, Any]:
    from pre_peer_checker.catalog.runtime import ensure_active_catalog

    ensure_active_catalog(sync=True)
    pull_note = "ok"
    if csv is not None:
        csv_path = Path(csv)
        pull_note = f"csv override: {csv_path}"
    else:
        try:
            csv_path = ensure_rw_repo(RW_CACHE_DIR, pull=pull)
            pull_note = "pulled" if pull else "cache"
        except RwPullError as exc:
            fallback = CATALOG_FIXTURES / "sample_rw_snippet.csv"
            if not fallback.is_file():
                return {"ok": False, "error": str(exc)}
            csv_path = fallback
            pull_note = f"RW unavailable ({exc}); fixture sample"

    mine = mine_reasons(csv_path)
    proposal = build_proposal(rw_mine=mine)
    out = Path("outputs/catalog_proposals")
    out.mkdir(parents=True, exist_ok=True)
    write_proposal(proposal, out / "latest.json")
    (out / "rw_mine_latest.json").write_text(
        json.dumps(mine, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "ok": True,
        "csv": str(csv_path),
        "pull_note": pull_note,
        "mine": {
            "rows_total": mine.get("rows_total"),
            "rows_life_science": mine.get("rows_life_science"),
            "mapped_reason_rows": len(mine.get("hits") or []),
            "unmapped_life_reason_count": len(mine.get("unmapped_life_reasons") or []),
        },
        "proposal": proposal,
        "status": catalog_status(),
    }


def catalog_triage(
    *,
    use_llm: bool = True,
    prefer: str = "auto",
    profile_id: str | None = None,
    model_id: str | None = None,
    csv: Path | None = None,
    pull: bool = False,
    include_already_curated: bool = False,
) -> dict[str, Any]:
    from pre_peer_checker.catalog.runtime import ensure_active_catalog

    ensure_active_catalog(sync=True)
    mine_pack = catalog_pull_and_mine(pull=pull, csv=csv)
    if not mine_pack.get("ok"):
        return mine_pack
    mine_path = Path("outputs/catalog_proposals/rw_mine_latest.json")
    mine = json.loads(mine_path.read_text(encoding="utf-8")) if mine_path.is_file() else None
    result = run_triage_pipeline(
        rw_mine=mine,
        prefer=prefer,
        profile_id=profile_id,
        model_id=model_id,
        use_llm=use_llm,
        include_already_curated=include_already_curated,
    )
    return {
        "ok": True,
        "pull_note": mine_pack.get("pull_note"),
        "csv": mine_pack.get("csv"),
        "mine": mine_pack.get("mine"),
        "triage": result["triage"],
        "queue": result["queue"],
        "status": catalog_status(),
        "apply_hint": (
            "前回の適用はアクティブ（および fixtures に書いた場合は正本）に保存済みです。"
            "仕分けを再実行しても、既キュレーション済 pattern は既定でキューから除外されます。"
        ),
    }


def catalog_apply(
    decisions: list[dict[str, Any]],
    *,
    also_update_fixtures: bool = False,
) -> dict[str, Any]:
    # Apply 前に fixtures 昇格を取り込む（古い active のまま仕分けすると新 ID が欠ける）
    ensure_active_catalog(sync=True)
    result = apply_curation_decisions(
        decisions, also_update_fixtures=also_update_fixtures
    )
    export_verification_yaml()
    if also_update_fixtures:
        sync_resources()
    queue = load_curation_queue() or {"items": []}
    decided = {
        str(d.get("pattern_id") or "")
        for d in decisions
        if d.get("pattern_id")
    } | {str(d.get("reason") or "") for d in decisions if d.get("reason")}
    for item in queue.get("items") or []:
        key = str(item.get("pattern_id") or item.get("reason") or "")
        if key in decided:
            item["decision"] = "applied"
            item["selected"] = False
    save_curation_queue(queue)
    result["queue"] = queue
    return result
