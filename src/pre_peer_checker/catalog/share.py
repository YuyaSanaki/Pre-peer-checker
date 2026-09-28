"""カタログ共有下書き → GitHub Issue Intent / PR 用 CLI（抽象ルールのみ）.

セキュリティ方針:
- WebUI から GitHub API / PAT は使わない
- Issue は Web Intent URL（``issues/new?title=&body=``）を開くだけ
- PR はローカルで実行する CLI コマンドをコピーさせる（自動 push しない）
- PDF・PubPeer コメント全文・著者名・感情的告発文は絶対に載せない
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pre_peer_checker.catalog.paths import REPO_ROOT
from pre_peer_checker.catalog.runtime import compare_catalogs, load_curation_queue, load_runtime_catalog
from pre_peer_checker.catalog.store import pattern_index

SHARE_SCHEMA_VERSION = "1.1"
DEFAULT_SOURCE = "PubPeer Community Contribution (Abstracted)"
# GitHub issues/new URL の実用上限（ブラウザ差あり）。余裕を見て切り詰め。
_MAX_INTENT_URL_CHARS = 7200


def _run(cmd: list[str], *, cwd: Path | None = None) -> tuple[int, str, str]:
    p = subprocess.run(
        cmd,
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
    )
    return p.returncode, p.stdout or "", p.stderr or ""


def resolve_github_repo_slug(*, fallback: str = "OWNER/REPO") -> str:
    """``owner/repo`` を origin から解決。失敗時は製品デフォルト."""
    code, out, _ = _run(["git", "remote", "get-url", "origin"])
    if code == 0 and out.strip():
        url = out.strip()
        m = re.search(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/.]+)", url)
        if m:
            return f"{m.group('owner')}/{m.group('repo')}"
    return fallback


def _title_from_rule(abstract_rule: str, pattern_id: str | None) -> str:
    rule = (abstract_rule or "").strip()
    if rule:
        # 一文目を短く
        first = re.split(r"[。．\n]", rule, maxsplit=1)[0].strip()
        if 8 <= len(first) <= 80:
            return first
        if first:
            return first[:77] + ("…" if len(first) > 77 else "")
    if pattern_id:
        return pattern_id.replace("P-", "").replace("-", " ").title()
    return "Abstract verification pattern"


def _scrub_identifying(text: str | None) -> str:
    """共有文面から DOI・メール・典型的な固有名痕跡を落とす."""
    if not text:
        return ""
    s = str(text)
    s = re.sub(r"\b10\.\d{4,9}/[^\s\]）)]+", "[redacted-doi]", s)
    s = re.sub(r"[\w.+-]+@[\w.-]+\.\w+", "[redacted-email]", s)
    s = re.sub(
        r"\b(?:University|Institute|Hospital|College)\s+of\s+[A-Z][\w\- ]{2,40}",
        "[redacted-affiliation]",
        s,
        flags=re.I,
    )
    s = re.sub(r"\b(?:et\s+al\.?|博士|教授)\b", "", s, flags=re.I)
    return re.sub(r"\s{2,}", " ", s).strip()


def normalize_pattern_proposal(
    item: dict[str, Any],
    *,
    catalog_by_id: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Issue/PR 添付用の標準 JSON 1 件（固有情報なし）."""
    pid = item.get("pattern_id") or item.get("suggested_new_id") or item.get("id")
    abstract = _scrub_identifying(
        item.get("abstract_rule") or item.get("rationale") or ""
    )
    if not pid and not abstract:
        return None

    catalog_by_id = catalog_by_id or {}
    existing = catalog_by_id.get(str(pid)) if pid else None

    tags = item.get("warning_tags") or (
        [item["warning_tag"]] if item.get("warning_tag") else None
    )
    if not tags and existing:
        tags = list(existing.get("warning_tags") or [])
    category = (tags or ["Warning [実験データとの不一致]"])[0]

    title = _scrub_identifying(item.get("title") or "")
    if not title:
        title = _title_from_rule(
            abstract or (existing or {}).get("abstract_rule") or "",
            str(pid) if pid else None,
        )

    if not abstract and existing:
        abstract = _scrub_identifying(existing.get("abstract_rule") or "")

    target_inputs = item.get("target_inputs") or item.get("inputs")
    if not target_inputs and existing:
        target_inputs = list(existing.get("inputs") or [])
    target_inputs = list(target_inputs or [])

    detection_logic = item.get("detection_logic")
    if not isinstance(detection_logic, dict):
        detection_logic = {}
    # 空なら既存のヒントがあれば薄い記述だけ
    if not detection_logic and existing and existing.get("not_sufficient"):
        detection_logic = {
            "note": "決定論照合はエンジン実装時に定義。有罪断定はしない。",
        }

    sources = item.get("sources")
    if not sources:
        if item.get("kind") == "pubpeer_pdf" or item.get("source_kind") == "curated_pubpeer_pdf":
            sources = [DEFAULT_SOURCE]
        else:
            sources = ["Catalog Curation (Abstracted)"]

    out: dict[str, Any] = {
        "pattern_id": pid,
        "category": category,
        "title": title,
        "abstract_rule": abstract,
        "target_inputs": target_inputs,
        "detection_logic": detection_logic,
        "sources": list(sources),
    }
    # 任意メタ（固有書誌は入れない）
    pri = item.get("user_priority") or item.get("suggested_priority") or item.get("priority")
    if pri:
        out["priority"] = pri
    action = item.get("suggested_action") or item.get("decision") or item.get("action")
    if action:
        out["action"] = action
    tax = item.get("taxonomy") or (existing or {}).get("taxonomy")
    if tax:
        out["taxonomy"] = list(tax)
    return out


def build_share_payload(
    *,
    items: list[dict[str, Any]] | None = None,
    include_active_diff: bool = True,
) -> dict[str, Any]:
    """共有用の抽象ペイロード（PDFパス・原文・書誌なし）."""
    queue = load_curation_queue() or {}
    src_items = items
    if src_items is None:
        src_items = [
            it
            for it in (queue.get("items") or [])
            if it.get("selected") or it.get("kind") == "pubpeer_pdf"
        ]

    catalog = load_runtime_catalog()
    by_id = pattern_index(catalog)

    proposals: list[dict[str, Any]] = []
    for it in src_items:
        norm = normalize_pattern_proposal(it, catalog_by_id=by_id)
        if norm:
            proposals.append(norm)

    diff_rows = []
    if include_active_diff:
        cmp = compare_catalogs()
        for row in cmp.get("rows") or []:
            if row.get("diff") in {"changed", "only_active"}:
                active = row.get("active") or {}
                diff_rows.append(
                    {
                        "pattern_id": row.get("pattern_id"),
                        "diff": row.get("diff"),
                        "changed_fields": [
                            f
                            for f in (row.get("changed_fields") or [])
                            if f
                            not in {
                                "curation",
                                "paper_title",
                                "doi",
                                "source_pdf",
                            }
                        ],
                        "active": {
                            k: active.get(k)
                            for k in (
                                "status",
                                "enabled",
                                "priority",
                                "abstract_rule",
                            )
                            if k in active
                        }
                        or None,
                    }
                )

    return {
        "schema_version": SHARE_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy": {
            "no_pdf_in_git": True,
            "no_verbatim_pubpeer_comments": True,
            "no_automated_pubpeer_scrape": True,
            "no_identifying_bibliography": True,
            "no_github_pat_in_webui": True,
            "absolute_rules": [
                "no_verbatim_expression",
                "abstract_to_inspection_spec",
                "no_commenter_targeting",
            ],
        },
        "proposals": proposals,
        "active_vs_fixtures_diff": diff_rows[:40],
    }


def format_issue_body(payload: dict[str, Any], *, compact: bool = False) -> str:
    proposals = payload.get("proposals") or []
    lines = [
        "## カタログ共有提案（抽象ルールのみ）",
        "",
        "WebUI のカタログ更新から生成。**PDF・コメント全文・著者名・DOI・大学名は含みません。**",
        "",
        f"- schema: `{payload.get('schema_version')}`",
        f"- 生成時刻: `{payload.get('generated_at')}`",
        f"- 提案数: **{len(proposals)}**",
        f"- アクティブ差分行: **{len(payload.get('active_vs_fixtures_diff') or [])}**",
        "",
        "### 提案ルール",
        "",
    ]
    for i, p in enumerate(proposals, 1):
        lines.append(f"{i}. **`{p.get('pattern_id') or '(new)'}`** — {p.get('title')}")
        lines.append(f"   - category: `{p.get('category')}`")
        if p.get("priority"):
            lines.append(f"   - priority: `{p.get('priority')}`")
        lines.append(f"   - rule: {p.get('abstract_rule')}")
        if p.get("target_inputs"):
            lines.append(f"   - inputs: {', '.join(p['target_inputs'])}")
        lines.append("")

    if not compact and payload.get("active_vs_fixtures_diff"):
        lines.extend(["### アクティブ ↔ fixtures 差分（要約）", ""])
        for row in payload["active_vs_fixtures_diff"][:15]:
            lines.append(
                f"- `{row.get('pattern_id')}` · {row.get('diff')} · fields={row.get('changed_fields')}"
            )
        lines.append("")

    # 機械可読は proposals 配列を標準形で
    machine = {
        "schema_version": payload.get("schema_version"),
        "proposals": proposals,
    }
    json_blob = json.dumps(machine, ensure_ascii=False, indent=2)
    if compact and len(json_blob) > 3500:
        json_blob = json.dumps(
            {"schema_version": payload.get("schema_version"), "proposals": proposals[:5]},
            ensure_ascii=False,
            indent=2,
        )
        json_blob += "\n/* truncated for URL length; paste full JSON from WebUI preview */\n"

    lines.extend(
        [
            "### 機械可読ペイロード（標準スキーマ）",
            "",
            "```json",
            json_blob[:10000],
            "```",
            "",
            "### レビュー観点",
            "- 機械照合可能か（有罪断定・感情表現になっていないか）",
            "- 既存 `pattern_id` に寄せられるか",
            "- fixtures へ入れるなら `status: planned` でよいか",
            "",
        ]
    )
    return "\n".join(lines)


def build_issue_intent(
    payload: dict[str, Any] | None = None,
    *,
    title: str | None = None,
    repo_slug: str | None = None,
) -> dict[str, Any]:
    """GitHub Web Intent URL を生成（API / PAT 不要）."""
    payload = payload or build_share_payload()
    if not (payload.get("proposals") or payload.get("active_vs_fixtures_diff")):
        return {"ok": False, "error": "共有する提案・差分が空です"}

    slug = repo_slug or resolve_github_repo_slug()
    issue_title = title or (
        f"catalog: abstract pattern proposal ×{len(payload.get('proposals') or [])} "
        f"({datetime.now(timezone.utc).strftime('%Y-%m-%d')})"
    )

    full_body = format_issue_body(payload, compact=False)
    compact_body = format_issue_body(payload, compact=True)

    def _url(body: str) -> str:
        q = urllib.parse.urlencode({"title": issue_title, "body": body})
        return f"https://github.com/{slug}/issues/new?{q}"

    intent_url = _url(compact_body)
    truncated = False
    if len(intent_url) > _MAX_INTENT_URL_CHARS:
        # 提案をさらに短く
        tiny = dict(payload)
        tiny["proposals"] = (payload.get("proposals") or [])[:3]
        tiny["active_vs_fixtures_diff"] = []
        tiny_body = format_issue_body(tiny, compact=True)
        tiny_body += (
            "\n\n> URL 長制限のため要約のみ。WebUI の下書きプレビューから全文を貼ってください。\n"
        )
        intent_url = _url(tiny_body)
        truncated = True
        if len(intent_url) > _MAX_INTENT_URL_CHARS:
            # 最後の手段: title のみ
            intent_url = _url(
                "（本文が長すぎるため WebUI プレビューから貼り付けてください）\n\n"
                f"提案数: {len(payload.get('proposals') or [])}"
            )
            truncated = True

    return {
        "ok": True,
        "mode": "issue_intent",
        "url": intent_url,
        "intent_url": intent_url,
        "title": issue_title,
        "repo": slug,
        "body_markdown": full_body,
        "payload": payload,
        "url_truncated": truncated,
        "hint": (
            "ブラウザで Issue 下書きが開きます。トークン入力は不要です。"
            + (" URL 制限で本文を短縮したので、必要ならプレビューから追記してください。" if truncated else "")
        ),
    }


def build_pr_cli_bundle(
    payload: dict[str, Any] | None = None,
    *,
    branch_name: str | None = None,
    commit_message: str | None = None,
) -> dict[str, Any]:
    """PR 用のワンクリックコピー CLI（自動 commit/push しない）."""
    payload = payload or build_share_payload()
    if not (payload.get("proposals") or payload.get("active_vs_fixtures_diff")):
        return {"ok": False, "error": "共有する提案・差分が空です"}

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    branch = branch_name or f"catalog/webui-{stamp}"
    msg = commit_message or (
        "Add curated catalog pattern updates from WebUI (abstract rules only)."
    )
    slug = resolve_github_repo_slug()
    body = format_issue_body(payload, compact=False)

    # 標準 proposals を cache に書き、CLI から参照しやすくする（git 外）
    draft_dir = REPO_ROOT / "cache" / "catalog_active"
    draft_dir.mkdir(parents=True, exist_ok=True)
    draft_json = draft_dir / "share_proposals_draft.json"
    draft_md = draft_dir / "share_pr_body.md"
    draft_json.write_text(
        json.dumps(
            {"schema_version": SHARE_SCHEMA_VERSION, "proposals": payload.get("proposals")},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    draft_md.write_text(body, encoding="utf-8")

    fixtures_rel = "fixtures/patterns/pubpeer_patterns.json"
    resources_rel = "src/pre_peer_checker/resources/pubpeer_patterns.json"
    draft_json_rel = "cache/catalog_active/share_proposals_draft.json"
    draft_md_rel = "cache/catalog_active/share_pr_body.md"
    try:
        draft_json_rel = str(draft_json.relative_to(REPO_ROOT))
        draft_md_rel = str(draft_md.relative_to(REPO_ROOT))
    except ValueError:
        pass

    cli = f"""# Pre-peer-checker — catalog PR（抽象ルールのみ / トークンを WebUI に渡さない）
# 事前: fixtures に pattern を人手で反映（またはアクティブ差分をマージ）してから実行

git checkout -b {branch}
git add {fixtures_rel} {resources_rel}
git commit -m "{msg}"
git push -u origin HEAD
gh pr create --repo {slug} --title "{msg[:72]}" --body-file {draft_md_rel}
"""

    return {
        "ok": True,
        "mode": "pr_cli",
        "cli_commands": cli.strip() + "\n",
        "branch": branch,
        "commit_message": msg,
        "repo": slug,
        "body_markdown": body,
        "payload": payload,
        "draft_json": draft_json_rel,
        "draft_body": draft_md_rel,
        "fixtures_path": fixtures_rel,
        "hint": (
            "CLI をコピーしてターミナルで実行してください。"
            "WebUI は git push / gh を実行しません。"
            f" 下書き: {draft_md_rel}"
        ),
    }


# 後方互換エイリアス（旧テスト・呼び出し用）
def create_github_issue(
    payload: dict[str, Any] | None = None,
    *,
    title: str | None = None,
) -> dict[str, Any]:
    """互換: 実 API は呼ばず Web Intent を返す."""
    return build_issue_intent(payload, title=title)


def create_catalog_pr(**kwargs: Any) -> dict[str, Any]:
    """互換: 自動 PR せず CLI バンドルを返す."""
    return build_pr_cli_bundle(
        branch_name=kwargs.get("branch_name"),
        commit_message=kwargs.get("commit_message"),
    )
