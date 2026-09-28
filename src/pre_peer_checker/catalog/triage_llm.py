"""カタログ拡充候補をローカル LLM（Qwen 等）で優先度仕分けする."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from pre_peer_checker.catalog.propose import build_proposal
from pre_peer_checker.catalog.runtime import (
    TRIAGE_REPORT,
    ACTIVE_DIR,
    load_runtime_catalog,
    save_curation_queue,
)
from pre_peer_checker.catalog.store import pattern_index

TRIAGE_SYSTEM = """You triage research-integrity verification catalog updates for a life-science manuscript checker.
You do NOT judge guilt. You only prioritize mechanical cross-check opportunities.
Return ONLY valid JSON (no markdown) with this shape:
{
  "items": [
    {
      "pattern_id": "P-..." or null,
      "reason": "RW reason tag or null",
      "kind": "pattern_priority|unmapped_reason|seed_gap",
      "suggested_priority": "P0|P1|P2",
      "suggested_action": "enable|disable|accept_planned|set_priority|map_reason|defer|skip",
      "score": 0.0-1.0,
      "rationale": "one short Japanese sentence"
    }
  ],
  "summary_ja": "short Japanese summary for the curator"
}
Prefer P0 for image reuse, data swap, undeclared exclusion, source duplicate/ratio.
Skip peer-review / paper-mill / authorship reasons (not mechanically checkable here).
Max 25 items, highest score first.
"""


def _heuristic_triage(proposal: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
    """LLM 無しでも動く規則ベース仕分け."""
    idx = pattern_index(catalog)
    items: list[dict[str, Any]] = []
    actions = proposal.get("actions") or {}

    for row in actions.get("priority_heat_from_rw_life_science") or []:
        pid = row.get("pattern_id")
        if not pid:
            continue
        hits = int(row.get("life_science_reason_hits") or 0)
        status = row.get("status") or (idx.get(pid) or {}).get("status")
        if status == "planned":
            action = "accept_planned"
            pri = "P0" if hits >= 2000 else "P1" if hits >= 500 else "P2"
        else:
            action = "enable"
            pri = "P0" if hits >= 5000 else "P1"
        items.append(
            {
                "pattern_id": pid,
                "reason": None,
                "kind": "pattern_priority",
                "suggested_priority": pri,
                "suggested_action": action,
                "score": min(1.0, hits / 15000.0),
                "rationale": f"生命科学系 RW ヒット約 {hits}（規則ベース）",
                "life_science_reason_hits": hits,
                "status": status,
            }
        )

    for row in (actions.get("suggest_add_to_rw_reason_map") or [])[:12]:
        reason = row.get("reason")
        life_n = int(row.get("life_science_count") or 0)
        low = str(reason or "").lower()
        if any(
            x in low
            for x in (
                "peer review",
                "paper mill",
                "authorship",
                "plagiarism of text",
                "referencing",
                "investigation by",
                "computer-aided",
                "rogue editor",
            )
        ):
            action = "skip"
            pri = "P2"
            score = 0.1
            rationale = "照合エンジン対象外の類型（規則ベースで skip 推奨）"
        elif any(x in low for x in ("image", "data", "method", "analys", "result")):
            action = "map_reason"
            pri = "P1"
            score = min(0.85, life_n / 8000.0)
            rationale = f"未マップ Reason（生命科学 {life_n}）。pattern 割当を検討"
        else:
            action = "defer"
            pri = "P2"
            score = 0.2
            rationale = "優先度低または要人手判断"
        items.append(
            {
                "pattern_id": None,
                "reason": reason,
                "kind": "unmapped_reason",
                "suggested_priority": pri,
                "suggested_action": action,
                "score": score,
                "rationale": rationale,
                "life_science_count": life_n,
            }
        )

    items.sort(key=lambda x: float(x.get("score") or 0), reverse=True)
    items = items[:25]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "backend": "heuristic",
        "summary_ja": (
            f"規則ベースで {len(items)} 件を仕分けました。"
            "LLM 利用時はより文脈に沿った優先度になります。"
        ),
        "items": items,
        "proposal_rw_summary": proposal.get("rw_summary"),
    }


def _extract_json_object(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _compact_proposal_for_llm(proposal: dict[str, Any]) -> dict[str, Any]:
    actions = proposal.get("actions") or {}
    return {
        "rw_summary": proposal.get("rw_summary"),
        "planned_backed": (actions.get("planned_patterns_backed_by_open_sources") or [])[:20],
        "implemented_backed": (
            actions.get("implemented_patterns_backed_by_open_sources") or []
        )[:20],
        "priority_heat": (actions.get("priority_heat_from_rw_life_science") or [])[:20],
        "unmapped_life_reasons": (actions.get("suggest_add_to_rw_reason_map") or [])[:15],
        "ori_seed_count": proposal.get("ori_seed_count"),
        "cope_seed_count": proposal.get("cope_seed_count"),
    }


def triage_with_llm(
    proposal: dict[str, Any],
    *,
    prefer: str = "auto",
    profile_id: str | None = None,
    model_id: str | None = None,
    max_tokens: int = 1200,
) -> dict[str, Any]:
    """提案を LLM で仕分け。失敗時は heuristic にフォールバック."""
    catalog = load_runtime_catalog()
    heuristic = _heuristic_triage(proposal, catalog)

    from pre_peer_checker.llm.backend import select_backend

    backend = select_backend(prefer, profile_id=profile_id, model_id=model_id)
    if backend is None:
        heuristic["fallback_reason"] = "no_llm_backend"
        return heuristic

    payload = _compact_proposal_for_llm(proposal)
    prompt = (
        f"{TRIAGE_SYSTEM}\n\n"
        f"Catalog pattern count: {len(catalog.get('patterns') or [])}\n"
        f"INPUT_JSON:\n{json.dumps(payload, ensure_ascii=False)}\n"
    )
    try:
        raw = backend.generate(prompt, max_tokens=max_tokens)
    except Exception as exc:  # noqa: BLE001
        heuristic["fallback_reason"] = f"llm_error: {exc}"
        return heuristic

    parsed = _extract_json_object(raw)
    if not parsed or not isinstance(parsed.get("items"), list):
        heuristic["fallback_reason"] = "llm_parse_failed"
        heuristic["raw_preview"] = (raw or "")[:500]
        return heuristic

    items = []
    for row in parsed["items"][:25]:
        if not isinstance(row, dict):
            continue
        items.append(
            {
                "pattern_id": row.get("pattern_id"),
                "reason": row.get("reason"),
                "kind": row.get("kind") or "pattern_priority",
                "suggested_priority": row.get("suggested_priority") or "P2",
                "suggested_action": row.get("suggested_action") or "defer",
                "score": float(row.get("score") or 0.5),
                "rationale": row.get("rationale") or "",
            }
        )
    info = backend.info()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "backend": info.name,
        "model_id": info.model_id,
        "summary_ja": parsed.get("summary_ja")
        or f"LLM（{info.name}）が {len(items)} 件を仕分けました。",
        "items": items,
        "proposal_rw_summary": proposal.get("rw_summary"),
        "heuristic_backup_n": len(heuristic.get("items") or []),
    }


def run_triage_pipeline(
    *,
    rw_mine: dict[str, Any] | None = None,
    prefer: str = "auto",
    profile_id: str | None = None,
    model_id: str | None = None,
    use_llm: bool = True,
    include_already_curated: bool = False,
) -> dict[str, Any]:
    proposal = build_proposal(rw_mine=rw_mine)
    if use_llm:
        triage = triage_with_llm(
            proposal, prefer=prefer, profile_id=profile_id, model_id=model_id
        )
    else:
        triage = _heuristic_triage(proposal, load_runtime_catalog())
        triage["backend"] = "heuristic"

    catalog = load_runtime_catalog()
    idx = pattern_index(catalog)
    items_out: list[dict[str, Any]] = []
    skipped_curated = 0
    for item in triage.get("items") or []:
        pid = item.get("pattern_id")
        already = None
        if pid and pid in idx:
            cur = (idx[pid].get("curation") or {}) if isinstance(idx[pid], dict) else {}
            decision = cur.get("decision")
            if decision in {
                "enabled",
                "disabled",
                "rejected",
                "accepted_planned",
                "priority_updated",
            }:
                already = {
                    "decision": decision,
                    "reviewed_at": cur.get("reviewed_at"),
                    "priority": idx[pid].get("priority"),
                    "enabled": idx[pid].get("enabled"),
                }
        if already and not include_already_curated:
            skipped_curated += 1
            continue
        selected = item.get("suggested_action") not in {"skip", "defer", None}
        if already:
            selected = False
        items_out.append(
            {
                **item,
                "decision": already["decision"] if already else None,
                "already_curated": bool(already),
                "previous": already,
                "user_priority": item.get("suggested_priority"),
                "selected": selected,
            }
        )

    summary = triage.get("summary_ja") or ""
    if skipped_curated:
        summary = (
            f"{summary} 既にキュレーション済の {skipped_curated} 件はキューから除外しました。"
        ).strip()

    queue = {
        "generated_at": triage.get("generated_at"),
        "summary_ja": summary,
        "backend": triage.get("backend"),
        "skipped_already_curated": skipped_curated,
        "include_already_curated": include_already_curated,
        "items": items_out,
    }
    ACTIVE_DIR.mkdir(parents=True, exist_ok=True)
    TRIAGE_REPORT.write_text(
        json.dumps({"proposal": proposal, "triage": triage}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    save_curation_queue(queue)
    return {"proposal": proposal, "triage": triage, "queue": queue}
