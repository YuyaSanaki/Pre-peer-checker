"""dev / holdout generalization harness (eval/generalization.py) on synthetic mini cases."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from pre_peer_checker.eval import generalization as gz
from pre_peer_checker.llm.legend_schema import build_legend_llm_prompt

FIG2 = "Figure 2. (A) Quantification of body size. n=4 (early L3) and 3 (late L3). (B) Representative images.\n"
FIG4 = "Figure 4. (K) Quantification of clones. n=11 (H), 20 (I), and 9 (J).\n"

LEGEND_ITEMS = [
    {"id": "F2-A-early", "figure": "Figure 2", "panel": "A", "group": "early L3", "n": 4},
    {"id": "F2-A-late", "figure": "Figure 2", "panel": "A", "group": "late L3", "n": 3},
    {"id": "F4-H", "figure": "Figure 4", "panel": "H", "group": "", "n": 11},
    {"id": "F4-I", "figure": "Figure 4", "panel": "I", "group": "", "n": 20},
    {"id": "F4-J", "figure": "Figure 4", "panel": "J", "group": "", "n": 9},
]


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _make_case(
    root: Path,
    case_id: str,
    split: str,
    *,
    coverage: str = "exhaustive",
    status: str = "confirmed",
    style: str = "upper",
) -> None:
    gold_dir = root / "gold" / case_id
    _write(
        gold_dir / "case_manifest.json",
        {
            "case_id": case_id,
            "split": split,
            "style_tags": {"panel_case": style},
            "figures_in_scope": ["2", "4"],
        },
    )
    _write(
        gold_dir / "panel_extract_gold.json",
        {
            "case_id": case_id,
            "coverage": coverage,
            "review": {"status": status},
            "items": [
                {**it, "difficulty": "other", "review_status": "confirmed"} for it in LEGEND_ITEMS
            ],
        },
    )
    _write(
        gold_dir / "panel_labels_gold.json",
        {
            "case_id": case_id,
            "review": {"status": status},
            "figures": [{"figure": "Figure 1", "case": "upper", "panels": ["A", "B", "C"]}],
        },
    )
    inp = root / "input" / case_id
    inp.mkdir(parents=True, exist_ok=True)
    (inp / "legend_excerpt.txt").write_text(FIG2 + FIG4, encoding="utf-8")
    (inp / "figures").mkdir(exist_ok=True)
    (inp / "figures" / "Fig1.pdf").write_bytes(b"%PDF-stub")


@pytest.fixture
def roots(tmp_path: Path) -> Path:
    _make_case(tmp_path, "dev_a", "dev", coverage="hard_span")
    _make_case(tmp_path, "ho_a", "holdout", style="upper")
    _make_case(tmp_path, "ho_b", "holdout", style="lower")
    tpl = tmp_path / "gold" / "_template"
    _write(tpl / "case_manifest.example.json", {"case_id": "REPLACE_WITH_SLOT_ID", "split": "holdout"})
    return tmp_path


def _cases(root: Path, **kw) -> list[gz.Case]:
    return gz.discover_cases(gold_root=root / "gold", input_root=root / "input", **kw)


def test_discover_skips_template_and_filters_split(roots: Path) -> None:
    assert [c.case_id for c in _cases(roots)] == ["dev_a", "ho_a", "ho_b"]
    assert [c.case_id for c in _cases(roots, split="holdout")] == ["ho_a", "ho_b"]
    with pytest.raises(ValueError):
        _cases(roots, case_ids=["nope"])


def test_rules_only_reports_splits_and_redacts_holdout(roots: Path) -> None:
    report = gz.run_generalization("legend", ["rules_only"], _cases(roots), bootstrap_iters=50)
    res = report["results"]["rules_only"]
    assert res["splits"]["dev"]["n_items"] == 5
    assert res["splits"]["holdout"]["n_cases"] == 2
    assert res["splits"]["holdout"]["recall"] == pytest.approx(1.0)
    assert res["splits"]["holdout"]["recall_ci95"] is not None
    assert res["splits"]["dev"]["precision"] is None  # hard_span gold: precision not meaningful
    assert res["cases"]["ho_a"] == {**res["cases"]["ho_a"], "redacted": True}
    assert "misses" not in res["cases"]["ho_a"]
    assert "misses" in res["cases"]["dev_a"]
    assert "holdout:panel_case=lower" in res["by_style"]
    assert report["cases"]["ho_a"]["gold_freeze"] == "frozen"
    manifest = json.loads((roots / "gold" / "ho_a" / "case_manifest.json").read_text())
    assert manifest["frozen"]["legend_sha256"]


def test_frozen_gold_edit_is_refused_unless_revised(roots: Path) -> None:
    gz.run_generalization("legend", ["rules_only"], _cases(roots))
    gold_path = roots / "gold" / "ho_a" / "panel_extract_gold.json"
    gold = json.loads(gold_path.read_text())
    gold["items"] = gold["items"][:-1]
    gold_path.write_text(json.dumps(gold))
    with pytest.raises(gz.GoldFrozenError):
        gz.run_generalization("legend", ["rules_only"], _cases(roots))
    report = gz.run_generalization(
        "legend", ["rules_only"], _cases(roots), revise_reason="typo in n unrelated to output"
    )
    assert report["cases"]["ho_a"]["gold_freeze"] == "revised"
    manifest = json.loads((roots / "gold" / "ho_a" / "case_manifest.json").read_text())
    assert len(manifest["gold_revisions"]) == 1


@pytest.mark.parametrize(
    ("coverage", "status"), [("hard_span", "confirmed"), ("exhaustive", "draft")]
)
def test_holdout_gold_must_be_confirmed_and_exhaustive(tmp_path: Path, coverage: str, status: str) -> None:
    _make_case(tmp_path, "ho_x", "holdout", coverage=coverage, status=status)
    with pytest.raises(gz.GoldPolicyError):
        gz.run_generalization("legend", ["rules_only"], _cases(tmp_path))


def test_reveal_shows_errors_and_burns_case(roots: Path) -> None:
    report = gz.run_generalization("legend", ["rules_only"], _cases(roots), reveal=["ho_a"])
    view = report["results"]["rules_only"]["cases"]["ho_a"]
    assert "redacted" not in view and "misses" in view
    manifest = json.loads((roots / "gold" / "ho_a" / "case_manifest.json").read_text())
    assert manifest["split"] == "dev" and manifest["burned_at"]
    assert [c.case_id for c in _cases(roots, split="holdout")] == ["ho_b"]
    with pytest.raises(ValueError):
        gz.run_generalization("legend", ["rules_only"], _cases(roots), reveal=["dev_a"])


def _stub_llm(prompt: str) -> str:
    """Correct H/I/J plus one hallucinated H=99 whose evidence is not in the legend."""
    panels = [
        {"panel": "H", "n": 11, "groups": [], "evidence_span": "n=11 (H)", "confidence": 0.9},
        {"panel": "I", "n": 20, "groups": [], "evidence_span": "20 (I)", "confidence": 0.9},
        {"panel": "J", "n": 9, "groups": [], "evidence_span": "9 (J)", "confidence": 0.9},
        {"panel": "H", "n": 99, "groups": [], "evidence_span": "n=99 (H)", "confidence": 0.9},
    ]
    return json.dumps({"figure": "Figure 4", "panels": panels if "(K)" in prompt else []})


def test_guard_ablation_isolates_grounding(roots: Path) -> None:
    cases = _cases(roots, split="holdout")
    report = gz.run_generalization(
        "legend",
        ["current", "current-minus-grounding", "current-minus-rule_fill"],
        cases,
        llm_factory=lambda: _stub_llm,
    )
    ho = {name: r["splits"]["holdout"] for name, r in report["results"].items()}
    assert ho["current"]["precision"] == pytest.approx(1.0)
    assert ho["current-minus-grounding"]["precision"] < 1.0
    # Without rule_fill the rules' Figure 2 rows (LLM returned nothing there) are gone.
    assert ho["current-minus-rule_fill"]["recall"] < ho["current"]["recall"]


def test_llm_configs_need_backend(roots: Path) -> None:
    with pytest.raises(RuntimeError):
        gz.run_generalization("legend", ["llm_minimal"], _cases(roots), llm_factory=lambda: None)


def test_expand_configs() -> None:
    assert gz.expand_configs("legend", None) == list(gz.LEGEND_BASE_CONFIGS)
    abl = gz.expand_configs("legend", "ablation")
    assert abl[0] == "current" and "current-minus-stolen_from_rules" in abl
    assert gz.expand_configs("panel_ocr", "ablation") == ["union", "union_no_run_filter"]
    with pytest.raises(ValueError):
        gz.expand_configs("legend", "current-minus-nothing")


def test_minimal_prompt_drops_case_rules() -> None:
    full = build_legend_llm_prompt("Figure 1. n=3 (A).", "Figure 1")
    minimal = build_legend_llm_prompt("Figure 1. n=3 (A).", "Figure 1", variant="minimal")
    assert "Critical rules" in full and "Critical rules" not in minimal
    assert '"panels"' in minimal and "n=3 (A)" in minimal
    with pytest.raises(ValueError):
        build_legend_llm_prompt("x", variant="other")


def test_panel_ocr_configs_go_through_env(roots: Path) -> None:
    seen: list[str | None] = []

    def collect(files):
        import os

        assert all(Path(f).name == "Fig1.pdf" for f in files)
        seen.append(os.environ.get("PRE_PEER_CHECKER_RASTER_OCR_ENGINES"))
        extra = ["X"] if os.environ.get("PRE_PEER_CHECKER_PANEL_RUN_FILTER") == "0" else []
        return {"1": ["A", "B", *extra]}, {}

    report = gz.run_generalization(
        "panel_ocr", ["union", "union_no_run_filter"], _cases(roots), collect_fn=collect
    )
    ho = {n: r["splits"]["holdout"] for n, r in report["results"].items()}
    assert ho["union"]["recall"] == pytest.approx(2 / 3)
    assert ho["union"]["precision"] == pytest.approx(1.0)
    assert ho["union_no_run_filter"]["precision"] < 1.0
    assert set(seen) == {"vision florence"}
    dev_view = report["results"]["union"]["cases"]["dev_a"]
    assert dev_view["figures"][0]["missed"] == ["C"]


def _summary(dev_r: float, ho_r: float, ho_p: float = 1.0, cases: dict | None = None) -> dict:
    return {
        "config": "current",
        "cases": cases or {"dev_a": "dev", "ho_a": "holdout"},
        "splits": {
            "dev": {"recall": dev_r, "precision": None, "n_items": 10},
            "holdout": {"recall": ho_r, "precision": ho_p, "n_items": 20},
        },
    }


def test_gate_check_tolerance() -> None:
    base = {"summary": _summary(0.9, 0.80)}
    assert gz.gate_check(_summary(0.9, 0.80), None)["passed"]
    assert gz.gate_check(_summary(0.9, 0.75), base)["passed"]  # 1 item of 20 = 0.05 allowed
    assert not gz.gate_check(_summary(0.9, 0.70), base)["passed"]
    assert not gz.gate_check(_summary(0.8, 0.80), base)["passed"]  # dev regressed
    assert not gz.gate_check(_summary(0.9, 0.80, ho_p=0.9), base)["passed"]
    moved = _summary(0.9, 0.80, cases={"dev_a": "dev", "ho_a": "dev"})
    assert not gz.gate_check(moved, base)["passed"]


def test_history_baseline_roundtrip(tmp_path: Path) -> None:
    hist = tmp_path / "history.jsonl"
    gz.append_history({"task": "legend", "accepted": False, "summary": _summary(0.5, 0.5)}, hist)
    gz.append_history({"task": "legend", "accepted": True, "summary": _summary(0.9, 0.8)}, hist)
    gz.append_history({"task": "panel_ocr", "accepted": True, "summary": _summary(0.1, 0.1)}, hist)
    base = gz.load_baseline("legend", "current", hist)
    assert base["summary"]["splits"]["dev"]["recall"] == 0.9


def test_update_ledger_contribution_and_suggestion() -> None:
    def res(r: float, p: float) -> dict:
        return {"splits": {"holdout": {"recall": r, "precision": p, "n_cases": 2, "n_items": 10}}}

    report = {
        "task": "legend",
        "created_at": "t",
        "git_rev": "abc",
        "results": {
            "current": res(0.8, 0.9),
            "current-minus-grounding": res(0.8, 0.7),
            "current-minus-stolen_from_rules": res(0.9, 0.9),
        },
    }
    ledger = json.loads(gz.LEDGER_PATH.read_text(encoding="utf-8"))
    updated = gz.update_ledger(ledger, report)
    rules = {r["id"]: r for r in ledger["rules"]}
    assert set(updated) == {"guard:grounding", "guard:stolen_from_rules"}
    assert rules["guard:grounding"]["suggestion"] == "keep"
    assert rules["guard:grounding"]["measured"]["holdout"]["contribution_precision"] == pytest.approx(0.2)
    assert rules["guard:stolen_from_rules"]["suggestion"] == "remove"
    assert rules["guard:dose_on_wrong"]["suggestion"] == "no_data"


def test_rule_ledger_configs_exist() -> None:
    ledger = json.loads(gz.LEDGER_PATH.read_text(encoding="utf-8"))
    for rule in ledger["rules"]:
        for key in ("baseline_config", "ablation_config"):
            assert gz.expand_configs(rule["task"], rule[key]) == [rule[key]]


def test_holdout_prepare_writes_blind_templates(tmp_path: Path, monkeypatch) -> None:
    import importlib.util

    import pymupdf

    if shutil.which("pdftotext") is None:
        from pre_peer_checker.parsers import legend_struct

        monkeypatch.setattr(
            legend_struct,
            "extract_figure_captions_from_pdf",
            lambda path, keep=None: [("Figure 1", "Figure 1. (A) Quantification. n=4 (A).")],
        )
    gold_root = tmp_path / "gold"
    shutil.copytree(gz.GOLD_ROOT / "_template", gold_root / "_template")
    monkeypatch.setattr(gz, "GOLD_ROOT", gold_root)
    monkeypatch.setattr(gz, "INPUT_ROOT", tmp_path / "input")
    inp = tmp_path / "input" / "paper_99"
    inp.mkdir(parents=True)
    doc = pymupdf.open()
    page = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), False)
    pix.clear_with(200)
    page.insert_image(pymupdf.Rect(50, 50, 550, 500), pixmap=pix)
    page.insert_text((50, 560), "Figure 1. (A) Quantification. n=4 (A).")
    doc.save(inp / "paper.pdf")

    spec = importlib.util.spec_from_file_location(
        "prep", Path(__file__).resolve().parents[1] / "scripts" / "dev_holdout_prepare.py"
    )
    prep = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(prep)
    assert prep.main(["--case", "paper_99", "--new"]) == 0

    case = gz.load_case(gold_root / "paper_99")
    assert case is not None and case.split == "holdout"
    assert case.figures_in_scope == {"1"}
    assert (inp / "figures" / "Fig1.pdf").is_file() and (inp / "review" / "Fig1.png").is_file()
    legend_gold = json.loads(case.gold_path("legend").read_text())
    assert legend_gold["coverage"] == "exhaustive" and legend_gold["items"] == []
    labels_gold = json.loads(case.gold_path("panel_ocr").read_text())
    assert labels_gold["figures"] == [{"figure": "Figure 1", "case": "", "panels": []}]


def _real_holdout_cases() -> list[gz.Case]:
    try:
        return [c for c in gz.discover_cases(split="holdout") if c.load_gold("legend") or c.load_gold("panel_ocr")]
    except (OSError, ValueError):
        return []


@pytest.mark.skipif(not _real_holdout_cases(), reason="no local holdout gold")
def test_real_holdout_gold_is_confirmed_and_frozen_intact() -> None:
    """Read-only: local holdout gold follows the policy and matches its freeze."""
    for case in _real_holdout_cases():
        for task in gz.TASKS:
            if not case.gold_path(task).is_file():
                continue
            gz.check_holdout_gold(case, task)
            frozen = (case.manifest.get("frozen") or {}).get(gz._FROZEN_KEYS[task])
            if frozen:
                assert gz.sha256_file(case.gold_path(task)) == frozen, case.case_id
