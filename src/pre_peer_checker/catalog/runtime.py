"""実行時に使うアクティブ照合カタログ（WebUI キュレーション後）.

正本（fixtures）は配布・git 用。照合は ``cache/catalog_active/pubpeer_patterns.json``
があればそちらを優先する。
"""

from __future__ import annotations

import json
import shutil
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pre_peer_checker.catalog.paths import FIXTURES_PATTERNS, REPO_ROOT
from pre_peer_checker.catalog.store import load_patterns, save_patterns
from pre_peer_checker.resources import patterns_json_path

ACTIVE_DIR = REPO_ROOT / "cache" / "catalog_active"
ACTIVE_PATTERNS = ACTIVE_DIR / "pubpeer_patterns.json"
CURATION_QUEUE = ACTIVE_DIR / "curation_queue.json"
TRIAGE_REPORT = ACTIVE_DIR / "triage_latest.json"
ACTIVE_META = ACTIVE_DIR / "meta.json"


def active_patterns_path() -> Path:
    return ACTIVE_PATTERNS


def resolve_patterns_path(explicit: Path | str | None = None) -> Path:
    """照合に使うカタログパス（明示 > アクティブ > fixtures > shipped resources）."""
    if explicit is not None:
        p = Path(explicit)
        if p.is_file():
            return p.resolve()
        raise FileNotFoundError(f"patterns catalog not found: {p}")
    if ACTIVE_PATTERNS.is_file():
        # CLI / 照合でも fixtures 昇格を取り込む（カタログ更新タブ以外でも抑制しない）
        sync_active_from_fixtures()
        return ACTIVE_PATTERNS.resolve()
    if FIXTURES_PATTERNS.is_file():
        return FIXTURES_PATTERNS.resolve()
    return patterns_json_path().resolve()


def load_runtime_catalog(explicit: Path | str | None = None) -> dict[str, Any]:
    return load_patterns(resolve_patterns_path(explicit))


def ensure_active_catalog(*, reset: bool = False, sync: bool = True) -> Path:
    """アクティブカタログを用意する.

    - 無ければ fixtures からコピー
    - ``reset=True`` なら fixtures で上書き（ローカル無効化も消える）
    - 既存があり ``sync=True`` なら fixtures 正本をマージ（明示 disable は保持）
    """
    ACTIVE_DIR.mkdir(parents=True, exist_ok=True)
    if reset or not ACTIVE_PATTERNS.is_file():
        src = FIXTURES_PATTERNS if FIXTURES_PATTERNS.is_file() else patterns_json_path()
        shutil.copy2(src, ACTIVE_PATTERNS)
        _write_meta({"initialized_from": str(src), "reset": reset})
        return ACTIVE_PATTERNS
    if sync:
        sync_active_from_fixtures()
    return ACTIVE_PATTERNS


# fixtures → active へ常に揃えるフィールド（ローカル curation の対象外）
_FIXTURE_CANONICAL_FIELDS = (
    "status",
    "phase",
    "priority",
    "source_kinds",
    "warning_tags",
    "abstract_rule",
    "inputs",
    "not_sufficient",
    "taxonomy",
)


def _curation_locks_disable(pat: dict[str, Any]) -> bool:
    """人手で明示 disable/reject した行は fixtures 昇格でも自動 enable しない."""
    decision = str((pat.get("curation") or {}).get("decision") or "").lower()
    return decision in {"disabled", "rejected"}


def _curation_locks_enable(pat: dict[str, Any]) -> bool:
    """人手で明示 enable した行は fixtures が planned でも落とさない."""
    decision = str((pat.get("curation") or {}).get("decision") or "").lower()
    return decision in {"enabled"}


def sync_active_from_fixtures() -> dict[str, Any]:
    """fixtures 正本の昇格・新規 ID をアクティブへ取り込む.

    カタログ更新タブの Apply や照合前 ``ensure_active_catalog`` から呼ばれる。
    - 新規 pattern_id → 追加
    - status / abstract_rule 等の正本フィールド → fixtures 優先で更新
    - enabled: fixtures が実装済で有効なら、明示 disable でなければ True に上げる
    - ローカルのみの行（accept_planned 追加）は残す
    """
    if not ACTIVE_PATTERNS.is_file():
        ensure_active_catalog(reset=False, sync=False)
        return {"ok": True, "changed": [], "note": "initialized"}

    fixtures_path = (
        FIXTURES_PATTERNS if FIXTURES_PATTERNS.is_file() else patterns_json_path()
    )
    fixtures_doc = load_patterns(fixtures_path)
    active_doc = load_patterns(ACTIVE_PATTERNS)
    f_idx = {p["id"]: p for p in fixtures_doc.get("patterns") or [] if p.get("id")}
    a_list = list(active_doc.get("patterns") or [])
    a_idx = {p["id"]: p for p in a_list if p.get("id")}
    changed: list[dict[str, Any]] = []

    # ドキュメント先頭のポリシー（倫理ルール等）は正本優先。revision は active を残す。
    f_policy = dict(fixtures_doc.get("catalog_policy") or {})
    a_policy = dict(active_doc.get("catalog_policy") or {})
    kept_rev = a_policy.get("catalog_revision")
    for key, val in f_policy.items():
        if key == "catalog_revision":
            continue
        if a_policy.get(key) != val:
            a_policy[key] = deepcopy(val)
            changed.append({"field": f"catalog_policy.{key}", "action": "synced"})
    if kept_rev:
        a_policy["catalog_revision"] = kept_rev
    active_doc["catalog_policy"] = a_policy
    for top in ("schema_version", "purpose", "product_goal"):
        if top in fixtures_doc and active_doc.get(top) != fixtures_doc.get(top):
            active_doc[top] = fixtures_doc[top]
            changed.append({"field": top, "action": "synced"})

    for pid, fpat in f_idx.items():
        if pid not in a_idx:
            a_list.append(deepcopy(fpat))
            a_idx[pid] = a_list[-1]
            changed.append({"pattern_id": pid, "action": "added"})
            continue

        apat = a_idx[pid]
        for field in _FIXTURE_CANONICAL_FIELDS:
            if field not in fpat:
                continue
            if apat.get(field) != fpat.get(field):
                apat[field] = deepcopy(fpat[field])
                changed.append({"pattern_id": pid, "action": f"sync_{field}"})

        # enabled ポリシー（is_pattern_enabled と同式。前方参照を避ける）
        if "enabled" in fpat and fpat["enabled"] is not None:
            want_on = bool(fpat["enabled"])
        else:
            want_on = fpat.get("status") == "implemented"
        if _curation_locks_disable(apat):
            if apat.get("enabled") is not False:
                apat["enabled"] = False
                changed.append({"pattern_id": pid, "action": "keep_disabled"})
        elif _curation_locks_enable(apat):
            if apat.get("enabled") is not True:
                apat["enabled"] = True
                changed.append({"pattern_id": pid, "action": "keep_enabled"})
        elif want_on:
            if apat.get("enabled") is not True:
                apat["enabled"] = True
                changed.append({"pattern_id": pid, "action": "enabled_from_fixtures"})
            # 古い accepted_planned のまま残ると PDF Apply が demote しうる → 実装済へ揃える
            cur = dict(apat.get("curation") or {})
            if cur.get("decision") == "accepted_planned":
                cur["decision"] = "enabled"
                cur["note"] = (
                    (cur.get("note") or "")
                    + " [synced: fixtures implemented]"
                ).strip()
                apat["curation"] = cur
                changed.append({"pattern_id": pid, "action": "curation_accepted_to_enabled"})
        else:
            # fixtures が planned / 無効: 明示 enable ロック以外は False に揃える
            if apat.get("enabled") is True and not _curation_locks_enable(apat):
                apat["enabled"] = False
                changed.append({"pattern_id": pid, "action": "disabled_from_fixtures"})

    active_doc["patterns"] = a_list
    if changed:
        save_patterns(active_doc, ACTIVE_PATTERNS)
        _write_meta(
            {
                "last_sync_from_fixtures": datetime.now(timezone.utc).isoformat(),
                "last_sync_n_changes": len(changed),
            }
        )
    return {
        "ok": True,
        "path": str(ACTIVE_PATTERNS.resolve()),
        "n_changes": len(changed),
        "changed": changed[:80],
        "truncated": len(changed) > 80,
    }


def _write_meta(extra: dict[str, Any] | None = None) -> None:
    ACTIVE_DIR.mkdir(parents=True, exist_ok=True)
    meta: dict[str, Any] = {}
    if ACTIVE_META.is_file():
        try:
            meta = json.loads(ACTIVE_META.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            meta = {}
    meta.update(extra or {})
    meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    ACTIVE_META.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _pattern_snapshot(pat: dict[str, Any] | None) -> dict[str, Any] | None:
    if not pat:
        return None
    return {
        "id": pat.get("id"),
        "status": pat.get("status"),
        "enabled": is_pattern_enabled(pat),
        "enabled_explicit": pat.get("enabled"),
        "priority": pat.get("priority"),
        "phase": pat.get("phase"),
        "warning_tags": list(pat.get("warning_tags") or []),
        "taxonomy": list(pat.get("taxonomy") or []),
        "curation": pat.get("curation"),
        "abstract_rule": (pat.get("abstract_rule") or "")[:200],
    }


def _snap_key_fields(snap: dict[str, Any] | None) -> tuple[Any, ...]:
    if not snap:
        return ()
    return (
        snap.get("status"),
        snap.get("enabled"),
        snap.get("enabled_explicit"),
        snap.get("priority"),
        tuple(snap.get("warning_tags") or []),
        tuple(snap.get("taxonomy") or []),
        json.dumps(snap.get("curation") or {}, sort_keys=True, ensure_ascii=False),
        snap.get("abstract_rule"),
    )


def compare_catalogs() -> dict[str, Any]:
    """fixtures 正本とアクティブカタログを行単位で比較する."""
    fixtures_path = (
        FIXTURES_PATTERNS if FIXTURES_PATTERNS.is_file() else patterns_json_path()
    )
    fixtures_doc = load_patterns(fixtures_path)
    fixtures_idx = {p["id"]: p for p in fixtures_doc.get("patterns") or [] if p.get("id")}

    active_exists = ACTIVE_PATTERNS.is_file()
    if active_exists:
        active_doc = load_patterns(ACTIVE_PATTERNS)
        active_path = str(ACTIVE_PATTERNS.resolve())
    else:
        active_doc = {"patterns": []}
        active_path = None
    active_idx = {p["id"]: p for p in active_doc.get("patterns") or [] if p.get("id")}

    all_ids = sorted(set(fixtures_idx) | set(active_idx))
    rows: list[dict[str, Any]] = []
    counts = {"same": 0, "changed": 0, "only_fixtures": 0, "only_active": 0}

    for pid in all_ids:
        f_snap = _pattern_snapshot(fixtures_idx.get(pid))
        a_snap = _pattern_snapshot(active_idx.get(pid))
        if f_snap and a_snap:
            if _snap_key_fields(f_snap) == _snap_key_fields(a_snap):
                diff = "same"
            else:
                diff = "changed"
                # 細かい差分キー
                changed_fields = [
                    k
                    for k in (
                        "status",
                        "enabled",
                        "enabled_explicit",
                        "priority",
                        "warning_tags",
                        "taxonomy",
                        "curation",
                        "abstract_rule",
                    )
                    if f_snap.get(k) != a_snap.get(k)
                ]
        elif f_snap and not a_snap:
            diff = "only_fixtures"
            changed_fields = []
        else:
            diff = "only_active"
            changed_fields = []
        counts[diff] = counts.get(diff, 0) + 1
        rows.append(
            {
                "pattern_id": pid,
                "diff": diff,
                "changed_fields": changed_fields if diff == "changed" else [],
                "fixtures": f_snap,
                "active": a_snap,
            }
        )

    return {
        "ok": True,
        "fixtures_path": str(fixtures_path.resolve()),
        "active_path": active_path,
        "active_exists": active_exists,
        "fixtures_revision": (fixtures_doc.get("catalog_policy") or {}).get(
            "catalog_revision"
        ),
        "active_revision": (active_doc.get("catalog_policy") or {}).get("catalog_revision"),
        "counts": counts,
        "n_rows": len(rows),
        "rows": rows,
        "status": catalog_status(),
    }


def catalog_status() -> dict[str, Any]:
    path = resolve_patterns_path()
    using_active = path.resolve() == ACTIVE_PATTERNS.resolve() and ACTIVE_PATTERNS.is_file()
    doc = load_patterns(path)
    patterns = doc.get("patterns") or []
    enabled = enabled_pattern_ids(doc)
    meta = {}
    if ACTIVE_META.is_file():
        try:
            meta = json.loads(ACTIVE_META.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            meta = {}
    return {
        "path": str(path),
        "using_active": using_active,
        "active_exists": ACTIVE_PATTERNS.is_file(),
        "n_patterns": len(patterns),
        "n_enabled": len(enabled),
        "n_implemented": sum(1 for p in patterns if p.get("status") == "implemented"),
        "n_planned": sum(1 for p in patterns if p.get("status") == "planned"),
        "meta": meta,
        "queue_exists": CURATION_QUEUE.is_file(),
        "triage_exists": TRIAGE_REPORT.is_file(),
    }


def is_pattern_enabled(pat: dict[str, Any]) -> bool:
    if "enabled" in pat and pat["enabled"] is not None:
        return bool(pat["enabled"])
    # 既定: 実装済のみ照合に出す（planned はエンジン未接続でも ID 抑制用に残す）
    return pat.get("status") == "implemented"


def enabled_pattern_ids(doc: dict[str, Any] | None = None) -> set[str]:
    d = doc or load_runtime_catalog()
    return {p["id"] for p in d.get("patterns") or [] if is_pattern_enabled(p)}


def filter_warnings_by_catalog(
    warnings: list[Any],
    *,
    catalog: dict[str, Any] | None = None,
) -> tuple[list[Any], list[Any]]:
    """Return (kept, suppressed). Warnings without pattern_id are kept."""
    enabled = enabled_pattern_ids(catalog)
    kept: list[Any] = []
    suppressed: list[Any] = []
    for w in warnings:
        meta = getattr(w, "metadata", None) or {}
        if isinstance(w, dict):
            meta = w.get("metadata") or {}
            pid = meta.get("pattern_id") or w.get("pattern_id")
        else:
            pid = meta.get("pattern_id") if isinstance(meta, dict) else None
        if not pid or str(pid) in enabled:
            kept.append(w)
        else:
            suppressed.append(w)
    return kept, suppressed


def apply_curation_decisions(
    decisions: list[dict[str, Any]],
    *,
    also_update_fixtures: bool = False,
) -> dict[str, Any]:
    """ユーザー決定をアクティブカタログへ反映.

    decision 例::
      {"pattern_id": "P-...", "action": "enable"|"disable"|"set_priority"|"accept_planned",
       "priority": "P0", "note": "..."}
    """
    ensure_active_catalog(sync=True)
    doc = load_patterns(ACTIVE_PATTERNS)
    idx = {p["id"]: p for p in doc.get("patterns") or []}
    fixtures_path = (
        FIXTURES_PATTERNS if FIXTURES_PATTERNS.is_file() else patterns_json_path()
    )
    fixtures_status = {
        p["id"]: p.get("status")
        for p in load_patterns(fixtures_path).get("patterns") or []
        if p.get("id")
    }
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for raw in decisions:
        pid = str(raw.get("pattern_id") or "").strip()
        action = str(raw.get("action") or "").strip().lower()
        if not pid or not action:
            skipped.append({**raw, "skip_reason": "missing pattern_id or action"})
            continue
        pat = idx.get(pid)
        if pat is None and action in {"accept_planned", "add_planned"}:
            # 最小 planned 行を追加
            pat = {
                "id": pid,
                "status": "planned",
                "phase": "5",
                "priority": raw.get("priority") or "P2",
                "source_kinds": ["curated_webui", "retraction_watch_crossref"],
                "warning_tags": raw.get("warning_tags")
                or ["Warning [実験データとの不一致]"],
                "abstract_rule": raw.get("abstract_rule")
                or raw.get("rationale")
                or f"WebUI curation accepted {pid}",
                "inputs": raw.get("inputs") or ["tables", "figure_panels"],
                "enabled": False,
                "taxonomy": raw.get("taxonomy") or [],
            }
            doc.setdefault("patterns", []).append(pat)
            idx[pid] = pat
        if pat is None:
            skipped.append({**raw, "skip_reason": "unknown pattern_id"})
            continue

        curation = dict(pat.get("curation") or {})
        curation["reviewed_at"] = datetime.now(timezone.utc).isoformat()
        if raw.get("note"):
            curation["note"] = raw["note"]
        if raw.get("llm_rationale"):
            curation["llm_rationale"] = raw["llm_rationale"]

        if action in {"enable", "accept"}:
            pat["enabled"] = True
            # fixtures 正本が実装済なら status も上げる（PDF の accept 由来でも demote しない）
            if fixtures_status.get(pid) == "implemented" or pat.get("status") == "implemented":
                pat["status"] = "implemented"
            if pat.get("status") == "planned" and action == "accept":
                curation["decision"] = "accepted_planned"
            else:
                curation["decision"] = "enabled"
        elif action in {"disable", "reject"}:
            pat["enabled"] = False
            curation["decision"] = "disabled" if action == "disable" else "rejected"
        elif action == "accept_planned":
            # 実装済 ID を planned+disabled に戻すと照合が抑制される — 禁止
            if fixtures_status.get(pid) == "implemented" or pat.get("status") == "implemented":
                pat["status"] = "implemented"
                pat["enabled"] = True
                curation["decision"] = "enabled"
                action = "enable"  # 記録上も enable 扱い
            else:
                pat["status"] = "planned"
                pat["enabled"] = False
                curation["decision"] = "accepted_planned"
        elif action == "set_priority":
            if raw.get("priority"):
                pat["priority"] = raw["priority"]
            curation["decision"] = "priority_updated"
        elif action == "defer":
            curation["decision"] = "deferred"
        else:
            skipped.append({**raw, "skip_reason": f"unknown action {action}"})
            continue

        if raw.get("priority"):
            pat["priority"] = raw["priority"]
        if raw.get("abstract_rule"):
            pat["abstract_rule"] = raw["abstract_rule"]
        if raw.get("warning_tags"):
            pat["warning_tags"] = list(raw["warning_tags"])
        if raw.get("taxonomy"):
            pat["taxonomy"] = list(raw["taxonomy"])
        pat["curation"] = curation
        applied.append({"pattern_id": pid, "action": action, "enabled": pat.get("enabled")})

    doc.setdefault("catalog_policy", {})["catalog_revision"] = (
        f"webui-curated {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
    )
    save_patterns(doc, ACTIVE_PATTERNS)
    if also_update_fixtures and FIXTURES_PATTERNS.parent.is_dir():
        # 開発者向け: fixtures にも書く（resources 同期は別途）
        save_patterns(deepcopy(doc), FIXTURES_PATTERNS)

    _write_meta(
        {
            "last_curation_n": len(applied),
            "last_curation_skipped": len(skipped),
            "also_update_fixtures": also_update_fixtures,
        }
    )
    return {
        "ok": True,
        "path": str(ACTIVE_PATTERNS),
        "applied": applied,
        "skipped": skipped,
        "status": catalog_status(),
    }


def save_curation_queue(queue: dict[str, Any]) -> Path:
    ACTIVE_DIR.mkdir(parents=True, exist_ok=True)
    CURATION_QUEUE.write_text(
        json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return CURATION_QUEUE


def load_curation_queue() -> dict[str, Any] | None:
    if not CURATION_QUEUE.is_file():
        return None
    return json.loads(CURATION_QUEUE.read_text(encoding="utf-8"))
