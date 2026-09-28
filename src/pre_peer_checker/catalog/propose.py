"""オープンソース集計からカタログ拡充提案を生成（自動 merge しない）."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pre_peer_checker.catalog.ori_cope import load_cope_seeds, load_ori_seeds, seed_pattern_ids
from pre_peer_checker.catalog.reason_mine import mine_reasons
from pre_peer_checker.catalog.store import load_patterns, pattern_index


def build_proposal(
    *,
    rw_csv: Path | None = None,
    rw_mine: dict[str, Any] | None = None,
    patterns_doc: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """ギャップ・優先度ヒントをまとめた提案ドキュメントを返す."""
    doc = patterns_doc or load_patterns()
    idx = pattern_index(doc)
    known = set(idx)

    mine = rw_mine
    if mine is None and rw_csv is not None:
        mine = mine_reasons(rw_csv)

    ori = load_ori_seeds()
    cope = load_cope_seeds()
    seed_ids = seed_pattern_ids(ori, cope)

    rw_ids = set((mine or {}).get("mapped_pattern_ids") or [])
    referenced = rw_ids | seed_ids

    missing_from_catalog = sorted(pid for pid in referenced if pid not in known)
    planned = sorted(
        pid for pid, pat in idx.items() if pat.get("status") == "planned" and pid in referenced
    )
    implemented_covered = sorted(
        pid
        for pid, pat in idx.items()
        if pat.get("status") == "implemented" and pid in referenced
    )
    never_referenced = sorted(
        pid for pid in known if pid not in referenced and idx[pid].get("status") == "planned"
    )

    # 生命科学ヒットが多いのに pattern 未割当の Reason → 人手マップ追加候補
    unmapped = list((mine or {}).get("unmapped_life_reasons") or [])[:30]

    # pattern ごとの生命科学頻度（mapped reasons の合算）
    pattern_heat: dict[str, int] = {pid: 0 for pid in known}
    for hit in (mine or {}).get("hits") or []:
        life_n = int(hit.get("life_science_count") or 0)
        for pid in hit.get("pattern_ids") or []:
            if pid in pattern_heat:
                pattern_heat[pid] += life_n
    hot_planned = sorted(
        (
            {"pattern_id": pid, "life_science_reason_hits": n, "status": idx[pid].get("status")}
            for pid, n in pattern_heat.items()
            if n > 0
        ),
        key=lambda x: x["life_science_reason_hits"],
        reverse=True,
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy": {
            "auto_merge": False,
            "forbidden": [
                "pubpeer_automated_scrape",
                "guilt_score_ml",
                "retracted_paper_figures_in_git",
            ],
            "canonical_store": "fixtures/patterns/pubpeer_patterns.json",
        },
        "rw_summary": {
            "csv": (mine or {}).get("csv"),
            "rows_total": (mine or {}).get("rows_total"),
            "rows_life_science": (mine or {}).get("rows_life_science"),
            "unique_reasons": (mine or {}).get("unique_reasons"),
            "mapped_reason_rows": len((mine or {}).get("hits") or []),
            "unmapped_life_reason_count": len((mine or {}).get("unmapped_life_reasons") or []),
        },
        "ori_seed_count": len(ori.get("rules") or []),
        "cope_seed_count": len(cope.get("rules") or []),
        "actions": {
            "missing_pattern_ids_referenced_by_sources": missing_from_catalog,
            "planned_patterns_backed_by_open_sources": planned,
            "implemented_patterns_backed_by_open_sources": implemented_covered,
            "planned_patterns_without_open_source_backing": never_referenced,
            "priority_heat_from_rw_life_science": hot_planned[:40],
            "suggest_add_to_rw_reason_map": unmapped,
        },
        "review_checklist": [
            "missing_pattern_ids があれば JSON に planned 行を追加（エンジン実装は別タスク）",
            "suggest_add_to_rw_reason_map の上位 Reason を人手で pattern に割当",
            "priority_heat 上位の planned をエンジン実装キューに入れる",
            "YAML は export のみ。正本 JSON を更新してから sync-resources / export-yaml",
        ],
    }


def write_proposal(proposal: dict[str, Any], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(proposal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out
