"""PubPeer PDF（人手保存）の取り込み → 抽象照合ルール候補.

ポリシー:
- PDF / コメント全文は git に入れない（``cache/catalog_uploads/`` のみ）
- 抽出結果は abstract_rule / pattern_id 提案のみ
- PubPeer サイトの自動クロールは行わない
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pre_peer_checker.catalog.paths import REPO_ROOT
from pre_peer_checker.catalog.runtime import (
    ACTIVE_DIR,
    load_curation_queue,
    load_runtime_catalog,
    save_curation_queue,
)
from pre_peer_checker.catalog.store import pattern_index

UPLOAD_DIR = REPO_ROOT / "cache" / "catalog_uploads"

INGEST_SYSTEM = """あなたは研究公正の照合ルール設計者です。
入力された PubPeer 指摘 PDF のテキストから、具体的な論文名・著者名・DOI・大学名・個人名・告発文・感情表現を完全に排除し、
本ソフトウェアが検知すべき【普遍的な不整合パターン（抽象ルール）】のみを JSON として抽出してください。

絶対禁止:
- コメント原文のコピー、有罪断定、「捏造」「不正」などの断定語
- 著者名・所属・DOI・論文タイトル・ジャーナル名・日付・URL の出力
- 試薬妥当性・抗体特異性など、原稿＋データから機械照合できない論点

必須:
- 幾何・統計・表・画像類似・n・DAG など、機械照合可能な構造だけを書く
- 既存 pattern_id に寄せられるならそれを使う

Return ONLY valid JSON (no markdown fences):
{
  "items": [
    {
      "pattern_id": "existing P-* id if it fits, else null",
      "suggested_new_id": "P-NEW-SLUG or null",
      "kind": "pubpeer_pdf",
      "suggested_priority": "P0|P1|P2",
      "suggested_action": "accept_planned|enable|map_reason|defer",
      "title": "短い抽象タイトル（固有名なし）",
      "abstract_rule": "一文: 何と何を機械的に突合するか（日本語可）",
      "target_inputs": ["r_script", "python_script", "excel", "fig_pdf", "table_vectors", "legend_n"],
      "detection_logic": {
        "tier1": "決定論的な第一照合（例: 点列ハッシュ一致）",
        "tier2": "緩和照合（例: 平均・SD・n の ε 一致）"
      },
      "warning_tag": "Warning [データ取り違え]|Warning [サンプルサイズ記載誤記]|Warning [画像重複・再利用（要出典確認）]|Warning [実験データとの不一致]|Warning [コントロール群共有]|Warning [統計手法の不整合]|Warning [設定・アノテーション不整合]|Warning [表記揺れ・参照不整合]",
      "taxonomy": ["B1"],
      "score": 0.0,
      "rationale": "投稿前チェックで有用な理由（固有名なし・短く）"
    }
  ]
}
Max 8 items. Prefer mapping to existing pattern ids when possible.
"""


def scrub_pdf_text_for_llm(text: str, *, max_chars: int = 12000) -> str:
    """LLM に渡す前に DOI・URL・メール等の固有痕跡を薄める."""
    compact = re.sub(r"https?://\S+", "[url]", text)
    compact = re.sub(r"\b10\.\d{4,9}/[^\s\]）)]+", "[doi]", compact)
    compact = re.sub(r"[\w.+-]+@[\w.-]+\.\w+", "[email]", compact)
    compact = re.sub(
        r"(?i)\b(?:doi|orcid|pmid|pmcid)\s*[:：]?\s*\S+",
        "[id]",
        compact,
    )
    compact = re.sub(r"\n{3,}", "\n\n", compact)
    return compact[:max_chars]


def extract_pdf_text(path: Path, *, max_chars: int = 20000) -> str:
    import fitz

    doc = fitz.open(path)
    parts: list[str] = []
    try:
        for page in doc:
            parts.append(page.get_text("text") or "")
            if sum(len(p) for p in parts) >= max_chars:
                break
    finally:
        doc.close()
    text = "\n".join(parts)
    return text[:max_chars]


def _guess_title(text: str, filename: str) -> str | None:
    m = re.search(
        r"Home\s*\(/\)\s*/\s*Publications.*?\n\s*\n\s*(.+?)(?:\n\n|\n[A-Z])",
        text,
        re.S,
    )
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()[:160]
    # filename fallback
    name = Path(filename).stem
    if name.lower().startswith("pubpeer"):
        name = name.split("-", 1)[-1].strip(" .-")
    return name[:160] or None


def _guess_doi(text: str) -> str | None:
    m = re.search(r"doi:\s*(10\.\S+)", text, re.I)
    if m:
        return m.group(1).rstrip(").,;")
    m = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", text, re.I)
    return m.group(0).rstrip(").,;") if m else None


def _heuristic_items(text: str, known_ids: set[str]) -> list[dict[str, Any]]:
    low = text.lower()
    items: list[dict[str, Any]] = []

    def add(pid: str | None, rule: str, tag: str, tax: list[str], pri: str, score: float, why: str) -> None:
        items.append(
            {
                "pattern_id": pid if pid in known_ids else None,
                "suggested_new_id": None if pid in known_ids else pid,
                "kind": "pubpeer_pdf",
                "suggested_priority": pri,
                "suggested_action": "enable" if pid in known_ids and pid and pid.startswith("P-") else "accept_planned",
                "abstract_rule": rule,
                "warning_tag": tag,
                "taxonomy": tax,
                "score": score,
                "rationale": why,
            }
        )

    if any(x in low for x in ("identical", "same value", "same data", "duplicat")):
        add(
            "P-SOURCE-DUPLICATE-VALUES",
            "独立標本と読めるソース値の厳密一致頻度を検査する。",
            "Warning [データ取り違え]",
            ["F1"],
            "P0",
            0.75,
            "同一値・重複データ指摘",
        )
        add(
            "P-DATA-SWAP-CROSS-CONDITION",
            "別条件パネル間で点列／棒＋誤差が同一でないか検査する。",
            "Warning [データ取り違え]",
            ["B1"],
            "P0",
            0.7,
            "条件間データ同一性の指摘",
        )
    if any(x in low for x in ("image", "western", "blot", "gel", "figure") ) and "duplicat" in low:
        add(
            "P-IMAGE-REUSE-UNCITED",
            "パネル／コーパス間の画像類似度と出典有無を突合する。",
            "Warning [画像重複・再利用（要出典確認）]",
            ["A1"],
            "P0",
            0.8,
            "画像重複・再利用の指摘",
        )
    if any(x in low for x in ("n =", "n=", "sample size", "excluded", "exclusion")):
        add(
            "P-EXCLUSION-UNDECLARED",
            "記載 n より多いソース行があり除外基準が無いか検査する。",
            "Warning [サンプルサイズ記載誤記]",
            ["C3"],
            "P0",
            0.72,
            "除外・n 不一致の指摘",
        )
        add(
            "P-N-MISMATCH-LEGEND-VS-DATA",
            "Legend n と生データ有効行数を突合する。",
            "Warning [サンプルサイズ記載誤記]",
            ["C1"],
            "P1",
            0.65,
            "サンプルサイズ記載の指摘",
        )
    if any(x in low for x in ("survival", "kaplan", "live flies", "non-integer", "decimal")):
        add(
            "P-SURVIVAL-COUNT-NONINTEGER",
            "固定初期個体数×生存率から復元した個体数が整数か検査する。",
            "Warning [サンプルサイズ記載誤記]",
            ["F3"],
            "P1",
            0.7,
            "生存曲線の個体数整合",
        )
    if any(x in low for x in ("integer multiple", "exact ratio", "proportional", "times larger")):
        add(
            "P-SOURCE-RATIO-ARTIFACT",
            "独立測定間の整数倍・定数比・小数部指紋を検査する。",
            "Warning [実験データとの不一致]",
            ["F2"],
            "P0",
            0.78,
            "比・スケール指紋の指摘",
        )
    if any(x in low for x in ("shared control", "lowest data point", "highest data point", "subset")):
        add(
            "P-SHARED-CONTROL-UNDISCLOSED",
            "複数パネルのコントロール点列一致／subset と共有明示を突合する。",
            "Warning [コントロール群共有]",
            ["B2"],
            "P0",
            0.8,
            "共有コントロール指摘",
        )
    if any(x in low for x in ("methods", "inconsist", "stoichiometry", "reconstruct")):
        add(
            "P-METHODS-CLAIM-MISMATCH",
            "Methods の定量主張と図・表メタの矛盾を突合する。",
            "Warning [設定・アノテーション不整合]",
            ["E3"],
            "P2",
            0.55,
            "Methods↔結果の食い違い",
        )
    if any(x in low for x in ("recalculat", "p <", "statistical", "kruskal", "t-test")):
        add(
            "P-STATS-RECALC-MISMATCH",
            "生データ再計算の統計量と記載値を突合する。",
            "Warning [実験データとの不一致]",
            ["D3"],
            "P1",
            0.6,
            "統計再計算不一致の指摘",
        )

    # de-dupe by pattern_id / suggested_new_id
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for it in items:
        key = str(it.get("pattern_id") or it.get("suggested_new_id") or it.get("abstract_rule"))
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out[:8]


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


def suggest_from_text(
    text: str,
    *,
    filename: str,
    use_llm: bool = True,
    prefer: str = "auto",
    profile_id: str | None = None,
    model_id: str | None = None,
) -> dict[str, Any]:
    from pre_peer_checker.catalog.runtime import ensure_active_catalog

    # ユーザー更新前に fixtures 昇格を取り込む（古い active で planned 扱いしない）
    ensure_active_catalog(sync=True)
    catalog = load_runtime_catalog()
    known = set(pattern_index(catalog))
    status_by_id = {
        p["id"]: p.get("status")
        for p in (catalog.get("patterns") or [])
        if p.get("id")
    }
    title = _guess_title(text, filename)
    doi = _guess_doi(text)
    heuristic = _heuristic_items(text, known)

    backend_name = "heuristic"
    items = heuristic
    if use_llm:
        from pre_peer_checker.llm.backend import select_backend

        backend = select_backend(prefer, profile_id=profile_id, model_id=model_id)
        if backend is not None:
            compact = scrub_pdf_text_for_llm(text)
            # ファイル名からも論文固有情報を渡さない
            safe_name = "pubpeer_upload.pdf" if "pubpeer" in filename.lower() else "upload.pdf"
            known_list = sorted(known)[:40]
            prompt = (
                f"{INGEST_SYSTEM}\n\n"
                f"Known pattern_ids: {known_list}\n"
                f"Upload_label: {safe_name}\n"
                f"PDF_TEXT (identifiers redacted):\n{compact}\n"
            )
            try:
                raw = backend.generate(prompt, max_tokens=1600)
                parsed = _extract_json_object(raw)
                if parsed and isinstance(parsed.get("items"), list) and parsed["items"]:
                    items = []
                    for row in parsed["items"][:8]:
                        if not isinstance(row, dict):
                            continue
                        logic = row.get("detection_logic")
                        if not isinstance(logic, dict):
                            logic = {}
                        items.append(
                            {
                                "pattern_id": row.get("pattern_id"),
                                "suggested_new_id": row.get("suggested_new_id"),
                                "kind": "pubpeer_pdf",
                                "suggested_priority": row.get("suggested_priority") or "P2",
                                "suggested_action": row.get("suggested_action") or "accept_planned",
                                "title": (row.get("title") or "")[:120],
                                "abstract_rule": row.get("abstract_rule") or "",
                                "target_inputs": list(row.get("target_inputs") or []),
                                "detection_logic": logic,
                                "warning_tag": row.get("warning_tag")
                                or "Warning [実験データとの不一致]",
                                "taxonomy": row.get("taxonomy") or [],
                                "score": float(row.get("score") or 0.5),
                                "rationale": row.get("rationale") or "",
                                "sources": ["PubPeer Community Contribution (Abstracted)"],
                            }
                        )
                    backend_name = backend.info().name
                    # LLM 出力の書誌は採用しない（ローカル表示用ヒューリスティックのみ維持）
                else:
                    backend_name = f"heuristic(llm_parse_failed:{backend.info().name})"
            except Exception as exc:  # noqa: BLE001
                backend_name = f"heuristic(llm_error:{exc})"

    # 実装済 ID への accept_planned 提案は enable に正規化（Apply 抑制を防ぐ）
    for it in items:
        pid = it.get("pattern_id")
        if not pid:
            continue
        if status_by_id.get(pid) == "implemented":
            it["suggested_action"] = "enable"

    return {
        "paper_title": title,
        "doi": doi,
        "backend": backend_name,
        "items": items,
        "text_chars": len(text),
    }


def save_upload(filename: str, data: bytes) -> Path:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w.\- +=（）()]+", "_", Path(filename).name)[:180]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = UPLOAD_DIR / f"{stamp}_{safe}"
    dest.write_bytes(data)
    return dest


def ingest_pubpeer_pdf(
    path: Path,
    *,
    use_llm: bool = True,
    prefer: str = "auto",
    profile_id: str | None = None,
    model_id: str | None = None,
    merge_into_queue: bool = True,
) -> dict[str, Any]:
    from pre_peer_checker.catalog.runtime import ensure_active_catalog

    ensure_active_catalog(sync=True)
    text = extract_pdf_text(path)
    suggestion = suggest_from_text(
        text,
        filename=path.name,
        use_llm=use_llm,
        prefer=prefer,
        profile_id=profile_id,
        model_id=model_id,
    )
    queue_items = []
    for it in suggestion.get("items") or []:
        pid = it.get("pattern_id") or it.get("suggested_new_id")
        queue_items.append(
            {
                **it,
                "pattern_id": pid,
                "source_pdf": path.name,
                "paper_title": suggestion.get("paper_title"),
                "doi": suggestion.get("doi"),
                "decision": None,
                "already_curated": False,
                "user_priority": it.get("suggested_priority"),
                "selected": it.get("suggested_action") not in {"skip", "defer", None},
                "warning_tags": [it["warning_tag"]] if it.get("warning_tag") else [],
                "abstract_rule": it.get("abstract_rule"),
            }
        )

    queue = load_curation_queue() or {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary_ja": "",
        "backend": suggestion.get("backend"),
        "items": [],
    }
    if merge_into_queue:
        existing = list(queue.get("items") or [])
        # prepend new pdf items
        queue["items"] = queue_items + existing
        queue["generated_at"] = datetime.now(timezone.utc).isoformat()
        queue["summary_ja"] = (
            f"PubPeer PDF「{suggestion.get('paper_title') or path.name}」から "
            f"{len(queue_items)} 件の抽象ルール候補を追加しました（backend={suggestion.get('backend')}）。"
        )
        queue["backend"] = suggestion.get("backend")
        save_curation_queue(queue)

    meta_path = ACTIVE_DIR / "last_ingest.json"
    ACTIVE_DIR.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(
        json.dumps(
            {
                "path": str(path),
                "suggestion": suggestion,
                "n_queue_items": len(queue_items),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    return {
        "ok": True,
        "saved_path": str(path),
        "paper_title": suggestion.get("paper_title"),
        "doi": suggestion.get("doi"),
        "backend": suggestion.get("backend"),
        "n_items": len(queue_items),
        "items": queue_items,
        "queue": queue if merge_into_queue else None,
        "policy_note": (
            "PDF は cache/catalog_uploads/ にのみ保存（gitignore）。"
            "共有時は抽象ルールのみを Issue/PR に載せ、コメント全文は載せません。"
        ),
    }


def copy_upload_bytes(filename: str, data: bytes) -> Path:
    return save_upload(filename, data)
