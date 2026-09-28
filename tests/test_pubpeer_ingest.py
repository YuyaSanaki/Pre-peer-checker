"""PubPeer PDF 取込と共有下書き."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote_plus

from pre_peer_checker.catalog.pubpeer_ingest import (
    ingest_pubpeer_pdf,
    scrub_pdf_text_for_llm,
    suggest_from_text,
)
from pre_peer_checker.catalog.share import (
    SHARE_SCHEMA_VERSION,
    build_issue_intent,
    build_pr_cli_bundle,
    build_share_payload,
    format_issue_body,
    normalize_pattern_proposal,
)


def test_suggest_from_text_heuristic_maps_patterns() -> None:
    text = """
    Fig. 1I and Fig. 1Q appear identical. The WT shared control lowest data point is absent.
    Source data has the same RT-PCR values. Survival rate yields non-integer live flies.
    Exclusion criteria are not degeneYed; n=6 but Excel has 8 mice.
    """
    out = suggest_from_text(text, filename="PubPeer - demo.pdf", use_llm=False)
    assert out["backend"] == "heuristic"
    ids = {i.get("pattern_id") or i.get("suggested_new_id") for i in out["items"]}
    assert "P-SHARED-CONTROL-UNDISCLOSED" in ids or "P-DATA-SWAP-CROSS-CONDITION" in ids
    assert "P-EXCLUSION-UNDECLARED" in ids or "P-N-MISMATCH-LEGEND-VS-DATA" in ids


def test_scrub_pdf_text_redacts_doi_and_url() -> None:
    raw = "See https://pubpeer.com/x and doi: 10.1234/abc.DEF author@univ.edu"
    scrubbed = scrub_pdf_text_for_llm(raw)
    assert "10.1234" not in scrubbed
    assert "https://" not in scrubbed
    assert "@" not in scrubbed
    assert "[doi]" in scrubbed or "[url]" in scrubbed


def test_ingest_pdf_into_queue(tmp_path: Path, monkeypatch) -> None:
    import fitz

    monkeypatch.setattr(
        "pre_peer_checker.catalog.pubpeer_ingest.UPLOAD_DIR", tmp_path / "uploads"
    )
    monkeypatch.setattr(
        "pre_peer_checker.catalog.pubpeer_ingest.ACTIVE_DIR", tmp_path / "active"
    )
    monkeypatch.setattr(
        "pre_peer_checker.catalog.runtime.CURATION_QUEUE",
        tmp_path / "active" / "curation_queue.json",
    )
    monkeypatch.setattr(
        "pre_peer_checker.catalog.runtime.ACTIVE_DIR", tmp_path / "active"
    )

    pdf = tmp_path / "PubPeer - identical data.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (72, 72),
        "Identical data points across panels. Shared control subset. Image duplication.",
    )
    doc.save(pdf)
    doc.close()

    result = ingest_pubpeer_pdf(pdf, use_llm=False, merge_into_queue=True)
    assert result["ok"] is True
    assert result["n_items"] >= 1
    assert result["queue"]["items"]


def test_share_payload_standard_schema_no_bibliography() -> None:
    payload = build_share_payload(
        items=[
            {
                "pattern_id": "P-SOURCE-DUPLICATE-VALUES",
                "kind": "pubpeer_pdf",
                "selected": True,
                "abstract_rule": "独立標本の同一値を検査",
                "suggested_priority": "P0",
                "suggested_action": "accept_planned",
                "paper_title": "Secret Paper About Fraud",
                "doi": "10.0/demo",
                "source_pdf": "secret.pdf",
                "title": "独立標本間の同一値",
                "target_inputs": ["excel"],
                "detection_logic": {"tier1": "値多重集合の完全一致"},
            }
        ],
        include_active_diff=False,
    )
    assert payload["schema_version"] == SHARE_SCHEMA_VERSION
    assert payload["policy"]["no_github_pat_in_webui"] is True
    assert len(payload["proposals"]) == 1
    prop = payload["proposals"][0]
    assert prop["pattern_id"] == "P-SOURCE-DUPLICATE-VALUES"
    assert prop["category"].startswith("Warning")
    assert "abstract_rule" in prop
    assert prop["target_inputs"] == ["excel"]
    assert prop["detection_logic"]["tier1"]
    assert "PubPeer" in prop["sources"][0]
    assert "paper_title" not in prop
    assert "doi" not in prop
    assert "source_pdf" not in prop

    body = format_issue_body(payload)
    assert "独立標本" in body
    assert "secret.pdf" not in body
    assert "Secret Paper" not in body
    assert "10.0/demo" not in body
    assert "コメント全文" in body or "DOI" in body


def test_normalize_strips_doi_from_rule() -> None:
    norm = normalize_pattern_proposal(
        {
            "pattern_id": "P-N-MISMATCH-LEGEND-VS-DATA",
            "abstract_rule": "Legend n と行数を突合 doi:10.9999/leak",
            "warning_tag": "Warning [サンプルサイズ記載誤記]",
            "kind": "pubpeer_pdf",
        }
    )
    assert norm is not None
    assert "10.9999" not in norm["abstract_rule"]


def test_issue_intent_is_github_new_url() -> None:
    result = build_issue_intent(
        {
            "schema_version": SHARE_SCHEMA_VERSION,
            "generated_at": "2026-09-24T00:00:00+00:00",
            "policy": {},
            "proposals": [
                {
                    "pattern_id": "P-DATA-SWAP-CROSS-CONDITION",
                    "category": "Warning [データ取り違え]",
                    "title": "条件間の点列一致",
                    "abstract_rule": "別条件パネルの点列同一性を検査する",
                    "target_inputs": ["table_vectors"],
                    "detection_logic": {"tier1": "ハッシュ一致"},
                    "sources": ["PubPeer Community Contribution (Abstracted)"],
                }
            ],
            "active_vs_fixtures_diff": [],
        },
        repo_slug="OWNER/REPO",
    )
    assert result["ok"] is True
    url = result["intent_url"]
    assert url.startswith("https://github.com/OWNER/REPO/issues/new?")
    qs = parse_qs(urlparse(url).query)
    assert "title" in qs and "body" in qs
    body = unquote_plus(qs["body"][0])
    assert "P-DATA-SWAP-CROSS-CONDITION" in body
    assert "secret.pdf" not in body


def test_pr_cli_bundle_no_auto_push(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "pre_peer_checker.catalog.share.REPO_ROOT", tmp_path
    )
    # draft dirs under tmp
    (tmp_path / "cache" / "catalog_active").mkdir(parents=True)
    result = build_pr_cli_bundle(
        {
            "schema_version": SHARE_SCHEMA_VERSION,
            "generated_at": "t",
            "policy": {},
            "proposals": [
                {
                    "pattern_id": "P-SOURCE-DUPLICATE-VALUES",
                    "category": "Warning [データ取り違え]",
                    "title": "同一値",
                    "abstract_rule": "同一値を検査",
                    "target_inputs": ["excel"],
                    "detection_logic": {},
                    "sources": ["PubPeer Community Contribution (Abstracted)"],
                }
            ],
            "active_vs_fixtures_diff": [],
        }
    )
    assert result["ok"] is True
    cli = result["cli_commands"]
    assert "git checkout -b" in cli
    assert "gh pr create" in cli
    assert "git push" in cli
    # 自動実行ではなくコピー用テキストであること
    assert result["mode"] == "pr_cli"
    assert (tmp_path / "cache" / "catalog_active" / "share_pr_body.md").is_file()
