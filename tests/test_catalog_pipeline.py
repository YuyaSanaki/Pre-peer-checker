"""カタログ拡充パイプラインの回帰テスト（RW フル clone 不要）."""

from __future__ import annotations

import json
from pathlib import Path

from pre_peer_checker.catalog.ori_cope import load_cope_seeds, load_ori_seeds, seed_pattern_ids
from pre_peer_checker.catalog.propose import build_proposal
from pre_peer_checker.catalog.reason_mine import mine_reasons
from pre_peer_checker.catalog.store import (
    ERR_ALIASES,
    export_verification_yaml,
    load_patterns,
    patterns_to_yaml_rules,
    sync_resources,
)

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_CSV = ROOT / "fixtures" / "catalog" / "sample_rw_snippet.csv"


def test_mine_sample_filters_non_life_and_maps_patterns():
    report = mine_reasons(SAMPLE_CSV)
    assert report["rows_total"] == 6
    assert report["rows_life_science"] == 5
    reasons = {h["reason"] for h in report["hits"]}
    assert "Duplication of/in Image" in reasons
    assert "Duplication of Data" in reasons
    assert "Falsification/Fabrication of Results" in reasons
    assert "Concerns/Issues about Referencing/Attributions" in reasons
    assert "Contamination of Cell Lines/Tissues" in reasons
    assert "Paper Mill" in reasons  # mapped as out-of-scope (empty pattern_ids)
    assert "P-IMAGE-REUSE-UNCITED" in report["mapped_pattern_ids"]
    assert "P-DATA-SWAP-CROSS-CONDITION" in report["mapped_pattern_ids"]
    assert "P-STATS-RECALC-MISMATCH" in report["mapped_pattern_ids"]
    # Paper Mill must not contribute pattern heat
    paper = next(h for h in report["hits"] if h["reason"] == "Paper Mill")
    assert paper["pattern_ids"] == []


def test_rw_reason_map_no_duplicate_reasons_and_valid_patterns():
    from pre_peer_checker.catalog.reason_mine import load_reason_map
    from pre_peer_checker.catalog.store import load_patterns

    rm = load_reason_map()
    reasons = [m["reason"] for m in rm["mappings"]]
    assert len(reasons) == len(set(reasons))
    known = {p["id"] for p in load_patterns()["patterns"]}
    for m in rm["mappings"]:
        for pid in m.get("pattern_ids") or []:
            assert pid in known, f"{m['reason']} → unknown {pid}"
        # out-of-scope rows should carry a note
        if not m.get("pattern_ids"):
            assert m.get("note"), f"{m['reason']} empty map needs note"


def test_proposal_references_exclusion_and_seeds(tmp_path: Path):
    mine = mine_reasons(SAMPLE_CSV)
    proposal = build_proposal(rw_mine=mine)
    assert proposal["policy"]["auto_merge"] is False
    planned = set(proposal["actions"]["planned_patterns_backed_by_open_sources"])
    # Manipulation of Data → P-EXCLUSION-UNDECLARED 等
    assert "P-EXCLUSION-UNDECLARED" in planned or "P-EXCLUSION-UNDECLARED" in set(
        proposal["actions"]["implemented_patterns_backed_by_open_sources"]
    )
    assert proposal["ori_seed_count"] >= 1
    assert proposal["cope_seed_count"] >= 1
    seed_ids = seed_pattern_ids(load_ori_seeds(), load_cope_seeds())
    assert "P-BLOT-LANE-REUSE" in seed_ids
    assert "P-SHARED-CONTROL-UNDISCLOSED" in seed_ids


def test_yaml_export_has_err_aliases(tmp_path: Path):
    doc = load_patterns()
    rows = patterns_to_yaml_rules(doc)
    by_pid = {r["pattern_id"]: r for r in rows}
    assert "P-DATA-SWAP-CROSS-CONDITION" in by_pid
    assert by_pid["P-DATA-SWAP-CROSS-CONDITION"]["id"] == ERR_ALIASES["P-DATA-SWAP-CROSS-CONDITION"]
    assert "P-EXCLUSION-UNDECLARED" in by_pid
    out = export_verification_yaml(doc, out=tmp_path / "verification_catalog.yaml")
    text = out.read_text(encoding="utf-8")
    assert "ERR_SAMPLE_SIZE_001" in text
    assert "canonical_store" in text


def test_sync_resources_roundtrip(tmp_path: Path):
    # 実ファイル同期はリポジトリ側で実行（破壊しない）
    sync_resources()
    a = (ROOT / "fixtures/patterns/pubpeer_patterns.json").read_text(encoding="utf-8")
    b = (
        ROOT / "src/pre_peer_checker/resources/pubpeer_patterns.json"
    ).read_text(encoding="utf-8")
    assert a == b
    # 新 pattern が正本にある
    doc = json.loads(a)
    ids = {p["id"] for p in doc["patterns"]}
    assert "P-EXCLUSION-UNDECLARED" in ids
