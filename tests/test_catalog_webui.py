"""アクティブカタログ・仕分け・WebAPI."""

from __future__ import annotations

from pathlib import Path

import pytest

from pre_peer_checker.catalog.runtime import (
    ACTIVE_PATTERNS,
    apply_curation_decisions,
    enabled_pattern_ids,
    ensure_active_catalog,
    filter_warnings_by_catalog,
    load_runtime_catalog,
)
from pre_peer_checker.catalog.triage_llm import _heuristic_triage, run_triage_pipeline
from pre_peer_checker.catalog.propose import build_proposal
from pre_peer_checker.catalog.reason_mine import mine_reasons
from pre_peer_checker.warnings import WarningItem, WarningTag


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_CSV = ROOT / "fixtures" / "catalog" / "sample_rw_snippet.csv"


def test_compare_catalogs_detects_disable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pre_peer_checker.catalog.runtime.ACTIVE_DIR", tmp_path / "active")
    monkeypatch.setattr(
        "pre_peer_checker.catalog.runtime.ACTIVE_PATTERNS",
        tmp_path / "active" / "pubpeer_patterns.json",
    )
    monkeypatch.setattr(
        "pre_peer_checker.catalog.runtime.ACTIVE_META",
        tmp_path / "active" / "meta.json",
    )
    from pre_peer_checker.catalog.runtime import compare_catalogs

    ensure_active_catalog()
    before = compare_catalogs()
    assert before["ok"] is True
    assert before["active_exists"] is True
    assert before["counts"]["same"] >= 1

    apply_curation_decisions(
        [{"pattern_id": "P-DATA-SWAP-CROSS-CONDITION", "action": "disable"}]
    )
    after = compare_catalogs()
    changed = [r for r in after["rows"] if r["pattern_id"] == "P-DATA-SWAP-CROSS-CONDITION"]
    assert len(changed) == 1
    assert changed[0]["diff"] == "changed"
    assert changed[0]["fixtures"]["enabled"] is True
    assert changed[0]["active"]["enabled"] is False


def test_ensure_active_and_filter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pre_peer_checker.catalog.runtime.ACTIVE_DIR", tmp_path / "active")
    monkeypatch.setattr(
        "pre_peer_checker.catalog.runtime.ACTIVE_PATTERNS",
        tmp_path / "active" / "pubpeer_patterns.json",
    )
    monkeypatch.setattr(
        "pre_peer_checker.catalog.runtime.ACTIVE_META",
        tmp_path / "active" / "meta.json",
    )
    path = ensure_active_catalog()
    assert path.is_file()
    doc = load_runtime_catalog(path)
    enabled = enabled_pattern_ids(doc)
    assert "P-DATA-SWAP-CROSS-CONDITION" in enabled

    apply_curation_decisions(
        [{"pattern_id": "P-DATA-SWAP-CROSS-CONDITION", "action": "disable"}]
    )
    doc2 = load_runtime_catalog(path)
    assert "P-DATA-SWAP-CROSS-CONDITION" not in enabled_pattern_ids(doc2)

    w_keep = WarningItem(
        tag=WarningTag.SAMPLE_SIZE,
        title="n",
        location="a",
        reason="r",
        metadata={"pattern_id": "P-N-MISMATCH-LEGEND-VS-DATA"},
    )
    w_drop = WarningItem(
        tag=WarningTag.DATA_SWAP,
        title="swap",
        location="b",
        reason="r",
        metadata={"pattern_id": "P-DATA-SWAP-CROSS-CONDITION"},
    )
    kept, suppressed = filter_warnings_by_catalog([w_keep, w_drop], catalog=doc2)
    assert len(kept) == 1
    assert kept[0].title == "n"
    assert len(suppressed) == 1


def test_sync_active_upgrades_stale_planned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fixtures で implemented に昇格した ID が、古い active の enabled:false を直す."""
    monkeypatch.setattr("pre_peer_checker.catalog.runtime.ACTIVE_DIR", tmp_path / "active")
    active = tmp_path / "active" / "pubpeer_patterns.json"
    monkeypatch.setattr("pre_peer_checker.catalog.runtime.ACTIVE_PATTERNS", active)
    monkeypatch.setattr(
        "pre_peer_checker.catalog.runtime.ACTIVE_META",
        tmp_path / "active" / "meta.json",
    )
    ensure_active_catalog(sync=False)
    doc = load_runtime_catalog(active)
    for pat in doc["patterns"]:
        if pat["id"] in {"P-SOURCE-DUPLICATE-VALUES", "P-SOURCE-RATIO-ARTIFACT"}:
            pat["status"] = "planned"
            pat["enabled"] = False
            pat["curation"] = {"decision": "accepted_planned"}
    from pre_peer_checker.catalog.store import save_patterns

    save_patterns(doc, active)
    assert "P-SOURCE-DUPLICATE-VALUES" not in enabled_pattern_ids(load_runtime_catalog(active))

    from pre_peer_checker.catalog.runtime import sync_active_from_fixtures

    report = sync_active_from_fixtures()
    assert report["ok"] is True
    assert any(
        c.get("pattern_id") == "P-SOURCE-DUPLICATE-VALUES"
        and c.get("action") == "enabled_from_fixtures"
        for c in report["changed"]
    )
    enabled = enabled_pattern_ids(load_runtime_catalog(active))
    assert "P-SOURCE-DUPLICATE-VALUES" in enabled
    assert "P-SOURCE-RATIO-ARTIFACT" in enabled
    # 明示 disable は保持
    apply_curation_decisions(
        [{"pattern_id": "P-SOURCE-DUPLICATE-VALUES", "action": "disable"}]
    )
    sync_active_from_fixtures()
    assert "P-SOURCE-DUPLICATE-VALUES" not in enabled_pattern_ids(load_runtime_catalog(active))
    assert "P-SOURCE-RATIO-ARTIFACT" in enabled_pattern_ids(load_runtime_catalog(active))


def test_accept_planned_does_not_demote_implemented(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PubPeer PDF 由来の accept_planned が実装済 ID を抑制しない."""
    monkeypatch.setattr("pre_peer_checker.catalog.runtime.ACTIVE_DIR", tmp_path / "active")
    active = tmp_path / "active" / "pubpeer_patterns.json"
    monkeypatch.setattr("pre_peer_checker.catalog.runtime.ACTIVE_PATTERNS", active)
    monkeypatch.setattr(
        "pre_peer_checker.catalog.runtime.ACTIVE_META",
        tmp_path / "active" / "meta.json",
    )
    ensure_active_catalog(sync=True)
    assert "P-SOURCE-DUPLICATE-VALUES" in enabled_pattern_ids(load_runtime_catalog(active))

    result = apply_curation_decisions(
        [{"pattern_id": "P-SOURCE-DUPLICATE-VALUES", "action": "accept_planned"}]
    )
    assert result["ok"] is True
    applied = result["applied"][0]
    assert applied["action"] == "enable"
    assert applied["enabled"] is True
    doc = load_runtime_catalog(active)
    pat = next(p for p in doc["patterns"] if p["id"] == "P-SOURCE-DUPLICATE-VALUES")
    assert pat["status"] == "implemented"
    assert pat["enabled"] is True
    assert "P-SOURCE-DUPLICATE-VALUES" in enabled_pattern_ids(doc)


def test_heuristic_triage_from_sample() -> None:
    mine = mine_reasons(SAMPLE_CSV)
    proposal = build_proposal(rw_mine=mine)
    triage = _heuristic_triage(proposal, load_runtime_catalog())
    assert triage["backend"] == "heuristic"
    assert triage["items"]
    assert any(i.get("pattern_id") for i in triage["items"])


def test_run_triage_pipeline_heuristic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pre_peer_checker.catalog.runtime.ACTIVE_DIR", tmp_path / "active")
    monkeypatch.setattr(
        "pre_peer_checker.catalog.triage_llm.ACTIVE_DIR", tmp_path / "active"
    )
    monkeypatch.setattr(
        "pre_peer_checker.catalog.triage_llm.TRIAGE_REPORT",
        tmp_path / "active" / "triage_latest.json",
    )
    monkeypatch.setattr(
        "pre_peer_checker.catalog.runtime.CURATION_QUEUE",
        tmp_path / "active" / "curation_queue.json",
    )
    mine = mine_reasons(SAMPLE_CSV)
    out = run_triage_pipeline(rw_mine=mine, use_llm=False)
    assert out["queue"]["items"]
    assert (tmp_path / "active" / "curation_queue.json").is_file()


def test_web_catalog_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from pre_peer_checker.web.app import create_app

    monkeypatch.setattr("pre_peer_checker.catalog.runtime.ACTIVE_DIR", tmp_path / "active")
    monkeypatch.setattr(
        "pre_peer_checker.catalog.runtime.ACTIVE_PATTERNS",
        tmp_path / "active" / "pubpeer_patterns.json",
    )
    monkeypatch.setattr(
        "pre_peer_checker.catalog.runtime.ACTIVE_META",
        tmp_path / "active" / "meta.json",
    )
    monkeypatch.setattr(
        "pre_peer_checker.catalog.runtime.CURATION_QUEUE",
        tmp_path / "active" / "curation_queue.json",
    )
    monkeypatch.setattr(
        "pre_peer_checker.catalog.triage_llm.ACTIVE_DIR", tmp_path / "active"
    )
    monkeypatch.setattr(
        "pre_peer_checker.catalog.triage_llm.TRIAGE_REPORT",
        tmp_path / "active" / "triage.json",
    )
    monkeypatch.chdir(tmp_path)
    # copy sample fixtures path still from repo via absolute SAMPLE - mine uses package fixtures
    client = TestClient(create_app())

    st = client.get("/api/catalog/status")
    assert st.status_code == 200
    assert st.json()["ok"] is True

    # Force fixture CSV via monkeypatch of ensure_rw_repo failure path:
    # call triage with use_llm false; pull will try RW then fallback if we break git
    from pre_peer_checker.catalog import web_ops

    def _fake_pull(**kwargs):
        mine = mine_reasons(SAMPLE_CSV)
        from pre_peer_checker.catalog.propose import build_proposal, write_proposal
        import json

        proposal = build_proposal(rw_mine=mine)
        out = Path("outputs/catalog_proposals")
        out.mkdir(parents=True, exist_ok=True)
        write_proposal(proposal, out / "latest.json")
        (out / "rw_mine_latest.json").write_text(
            json.dumps(mine, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return {
            "ok": True,
            "csv": str(SAMPLE_CSV),
            "pull_note": "test",
            "mine": {
                "rows_total": mine["rows_total"],
                "rows_life_science": mine["rows_life_science"],
                "mapped_reason_rows": len(mine["hits"]),
                "unmapped_life_reason_count": 0,
            },
            "proposal": proposal,
            "status": {"using_active": False},
        }

    monkeypatch.setattr(web_ops, "catalog_pull_and_mine", _fake_pull)

    tri = client.post(
        "/api/catalog/triage",
        json={"pull": False, "use_llm": False},
    )
    assert tri.status_code == 200
    body = tri.json()
    assert body["ok"] is True
    assert body["queue"]["items"]

    # apply enable on first pattern item
    pid = next(
        i["pattern_id"] for i in body["queue"]["items"] if i.get("pattern_id")
    )
    apply = client.post(
        "/api/catalog/apply",
        json={
            "decisions": [
                {"pattern_id": pid, "action": "enable", "priority": "P0"},
            ]
        },
    )
    assert apply.status_code == 200
    assert apply.json()["ok"] is True
    assert ACTIVE_PATTERNS.is_file() or (tmp_path / "active" / "pubpeer_patterns.json").is_file()
