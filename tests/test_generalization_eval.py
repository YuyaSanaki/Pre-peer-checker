"""dev / holdout generalization harness (eval/generalization.py) on synthetic mini cases."""

from __future__ import annotations

import json
import shutil
import sys
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


def test_gold_paths_reuse_gold_kept_elsewhere(roots: Path) -> None:
    shared = roots / "shared" / "panel_extract_gold.json"
    shared.parent.mkdir(parents=True)
    shutil.move(roots / "gold" / "dev_a" / "panel_extract_gold.json", shared)
    manifest_path = roots / "gold" / "dev_a" / "case_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["gold_paths"] = {"legend": "../../shared/panel_extract_gold.json"}
    manifest_path.write_text(json.dumps(manifest))

    (case,) = _cases(roots, case_ids=["dev_a"])
    assert case.gold_path("legend") == shared.resolve()
    assert case.gold_path("panel_ocr").name == "panel_labels_gold.json"
    assert [c.case_id for c in gz.cases_with_gold(_cases(roots), "legend")] == ["dev_a", "ho_a", "ho_b"]


def test_caption_split_handles_journal_header_styles() -> None:
    from pre_peer_checker.parsers.legend_struct import extract_figure_captions_from_pdf_text

    plos = "Body cites Fig 1B here.\nFig 1. Title one. (A) n = 3.\nBody.\nFig 2. Title two. (B) n = 4.\n"
    assert [f for f, _ in extract_figure_captions_from_pdf_text(plos)] == ["Figure 1", "Figure 2"]

    frontiers = "FIGURE 1\nCaption one n = 5.\nFIGURE 2\nCaption two.\nFIGURE 2 (Continued)\nmore\n"
    pairs = extract_figure_captions_from_pdf_text(frontiers)
    assert [f for f, _ in pairs] == ["Figure 1", "Figure 2"]
    assert "(Continued)" in pairs[1][1]

    # In-text "Figure 2." must not shadow the line-start "Fig.\u00a0N." headers.
    sci_rep = (
        "as shown in Figure 2. Next.\n"
        + "".join(f"Fig.\u00a0{i}. Caption {i}.\n" for i in range(1, 5))
    )
    assert [f for f, _ in extract_figure_captions_from_pdf_text(sci_rep)] == [
        f"Figure {i}" for i in range(1, 5)
    ]


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


def test_exhaustive_precision_scores_figures_without_gold_n(roots: Path) -> None:
    gold_path = roots / "gold" / "ho_a" / "panel_extract_gold.json"
    gold = json.loads(gold_path.read_text())
    gold["items"] = [it for it in gold["items"] if it["figure"] == "Figure 4"] + [
        {"id": "F4-K-range", "figure": "Figure 4", "panel": "K", "group": "", "n": None,
         "review_status": "dropped"},
    ]
    gold_path.write_text(json.dumps(gold))
    (case,) = _cases(roots, case_ids=["ho_a"])
    preds = [
        {"figure": "Figure 4", "panel": "H", "group": "", "n": 11},
        {"figure": "Figure 4", "panel": "K", "group": "", "n": 28},
        {"figure": "Figure 2", "panel": "A", "group": "", "n": 4},
        {"figure": "Figure 5", "panel": "A", "group": "", "n": 7},
    ]
    sc = gz.score_legend_case(case, preds)
    assert (sc["n_pred_hit"], sc["n_pred"]) == (1, 2)
    assert sc["false_positives"] == [{"figure": "Figure 2", "panel": "A", "group": "", "n": 4}]


def test_range_n_matches_only_the_same_range() -> None:
    from pre_peer_checker.eval.panel_extract_score import score_one

    gold = {"items": [{"id": "r", "figure": "Figure 3", "panel": "H", "group": "", "n": None,
                       "n_range": [28, 32]}]}
    base = {"figure": "Figure 3", "panel": "H", "group": ""}
    assert score_one(gold, [{**base, "n": 28}])["n_hit"] == 0
    sc = score_one(gold, [{**base, "n": None, "n_range": [28, 32]}])
    assert (sc["n_hit"], sc["n_pred_hit"], sc["n_pred"]) == (1, 1, 1)
    assert gz._legend_row("Figure 3", "h", "", 28, 32) == {**base, "n": None, "n_range": [28, 32]}


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


def test_legend_source_product_scores_legends_the_product_cannot_locate(
    roots: Path, monkeypatch
) -> None:
    from pre_peer_checker.parsers import legend_struct
    from pre_peer_checker.parsers.legend_struct import StructuredLegend

    inp = roots / "input" / "dev_a"
    (inp / "legend_excerpt.txt").unlink()
    (inp / "paper.pdf").write_bytes(b"%PDF-stub")
    monkeypatch.setattr(
        legend_struct,
        "extract_structured_legends",
        lambda _p: [StructuredLegend(figure="Figure 2", text=FIG2)],
    )
    monkeypatch.setattr(
        legend_struct, "extract_figure_captions_from_pdf", lambda _p, keep=None: [("Figure 2", FIG2), ("Figure 4", FIG4)]
    )
    cases = _cases(roots, case_ids=["dev_a"])

    product = gz.run_generalization("legend", ["rules_only"], cases, bootstrap_iters=0)
    dev = product["results"]["rules_only"]["splits"]["dev"]
    assert product["legend_source"] == "product"
    assert (dev["figures_found"], dev["figures_expected"]) == (1, 2)
    assert dev["n_hit"] == 2  # Figure 4's three n are lost with its legend

    raw = gz.run_generalization("legend", ["rules_only"], cases, bootstrap_iters=0, legend_source="raw")
    dev = raw["results"]["rules_only"]["splits"]["dev"]
    assert (dev["figures_found"], dev["n_hit"]) == (2, 5)


def test_gate_fails_when_fewer_legends_are_located() -> None:
    def summary(found: int, source: str = "product") -> dict:
        agg = {"n_items": 5, "recall": 1.0, "precision": None, "figures_expected": 2, "figures_found": found}
        return {"config": "current", "cases": {"a": "dev"}, "splits": {"dev": agg}, "legend_source": source}

    base = {"summary": summary(2)}
    assert gz.gate_check(summary(2), base)["passed"]
    assert not gz.gate_check(summary(1), base)["passed"]
    assert not gz.gate_check(summary(2, "raw"), base)["passed"]


def test_auto_config_skips_llm_when_rules_read_every_n(roots: Path) -> None:
    calls: list[str] = []

    def counting_llm(prompt: str) -> str:
        calls.append(prompt)
        return _stub_llm(prompt)

    report = gz.run_generalization(
        "legend", ["auto"], _cases(roots, split="holdout"), llm_factory=lambda: counting_llm
    )
    assert calls == []
    assert report["results"]["auto"]["splits"]["holdout"]["recall"] == pytest.approx(1.0)


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

    assert _load_prepare().main(["--case", "paper_99", "--new"]) == 0

    case = gz.load_case(gold_root / "paper_99")
    assert case is not None and case.split == "holdout"
    assert case.figures_in_scope == {"1"}
    assert (inp / "figures" / "Fig1.pdf").is_file() and (inp / "review" / "Fig1.png").is_file()
    legend_gold = json.loads(case.gold_path("legend").read_text())
    assert legend_gold["coverage"] == "exhaustive" and legend_gold["items"] == []
    labels_gold = json.loads(case.gold_path("panel_ocr").read_text())
    assert labels_gold["figures"] == [{"figure": "Figure 1", "case": "", "panels": []}]


def _load_prepare():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "prep", Path(__file__).resolve().parents[1] / "scripts" / "dev_holdout_prepare.py"
    )
    prep = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = prep  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(prep)
    return prep


def _figure_page(doc, *, caption: str | None, image: bool) -> None:
    import pymupdf

    page = doc.new_page()
    if image:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), False)
        pix.clear_with(200)
        page.insert_image(pymupdf.Rect(60, 80, 540, 600), pixmap=pix)
        page.insert_text((50, 70), "A")
    if caption:
        page.insert_text((50, 700 if image else 100), caption)


def test_figure_pages_follow_caption_to_preceding_artwork_page(tmp_path: Path) -> None:
    import pymupdf

    doc = pymupdf.open()
    _figure_page(doc, caption=None, image=False)
    _figure_page(doc, caption=None, image=True)  # figure 1 artwork only
    _figure_page(doc, caption="Fig 1. Caption on the next page.", image=False)
    _figure_page(doc, caption="Fig 2. Caption with its figure.", image=True)
    pdf = tmp_path / "paper.pdf"
    doc.save(pdf)

    pages = _load_prepare()._figure_pages(pdf, {"1", "2"}, {})
    assert {k: i for k, (i, _clip) in pages.items()} == {"1": 1, "2": 3}
    clip = pages["2"][1]
    assert clip.y0 < 70 and clip.y1 < 690  # panel letter above the image kept, caption left out


def test_figure_page_pin_agreeing_with_detection_keeps_detected_crop(tmp_path: Path) -> None:
    import pymupdf

    doc = pymupdf.open()
    _figure_page(doc, caption="Fig 1. Caption with its figure.", image=True)
    _figure_page(doc, caption=None, image=True)
    pdf = tmp_path / "paper.pdf"
    doc.save(pdf)

    prep = _load_prepare()
    detected = prep._figure_pages(pdf, {"1"}, {})["1"]
    assert prep._figure_pages(pdf, {"1"}, {"1": 1})["1"] == detected
    moved = prep._figure_pages(pdf, {"1"}, {"1": 2})["1"]
    assert moved[0] == 1


def test_prepare_dev_slot_keeps_excerpt_and_legend_gold(tmp_path: Path, monkeypatch) -> None:
    import pymupdf

    gold_root = tmp_path / "gold"
    monkeypatch.setattr(gz, "GOLD_ROOT", gold_root)
    monkeypatch.setattr(gz, "INPUT_ROOT", tmp_path / "input")
    _write(
        gold_root / "dev_x" / "case_manifest.json",
        {"case_id": "dev_x", "split": "dev", "figures_in_scope": ["1"]},
    )
    legend_gold = gold_root / "dev_x" / "panel_extract_gold.json"
    _write(legend_gold, {"coverage": "hard_span", "items": [{"id": "kept"}]})
    inp = tmp_path / "input" / "dev_x"
    inp.mkdir(parents=True)
    (inp / "legend_excerpt.txt").write_text("curated\n", encoding="utf-8")
    doc = pymupdf.open()
    _figure_page(doc, caption="Fig 1. Caption. n = 4 (A).", image=True)
    _figure_page(doc, caption="Fig 2. Out of scope.", image=True)
    doc.save(inp / "paper.pdf")

    assert _load_prepare().main(["--case", "dev_x"]) == 0
    case = gz.load_case(gold_root / "dev_x")
    assert case is not None and case.split == "dev" and case.figures_in_scope == {"1"}
    assert (inp / "legend_excerpt.txt").read_text(encoding="utf-8") == "curated\n"
    assert json.loads(legend_gold.read_text())["items"] == [{"id": "kept"}]
    labels = json.loads(case.gold_path("panel_ocr").read_text())
    assert labels["figures"] == [{"figure": "Figure 1", "case": "", "panels": []}]
    assert sorted(p.name for p in (inp / "figures").iterdir()) == ["Fig1.pdf"]


def test_prepare_new_dev_slot_gets_hard_span_legend_template(tmp_path: Path, monkeypatch) -> None:
    import pymupdf

    gold_root = tmp_path / "gold"
    shutil.copytree(gz.GOLD_ROOT / "_template", gold_root / "_template")
    monkeypatch.setattr(gz, "GOLD_ROOT", gold_root)
    monkeypatch.setattr(gz, "INPUT_ROOT", tmp_path / "input")
    inp = tmp_path / "input" / "paper_98"
    inp.mkdir(parents=True)
    doc = pymupdf.open()
    _figure_page(doc, caption="Figure 1 | Caption. n = 4 (A).", image=True)
    doc.save(inp / "paper.pdf")

    assert _load_prepare().main(["--case", "paper_98", "--new", "--split", "dev"]) == 0
    case = gz.load_case(gold_root / "paper_98")
    assert case is not None and case.split == "dev"
    legend = json.loads(case.gold_path("legend").read_text())
    assert legend["coverage"] == "hard_span" and legend["items"] == []


def _real_holdout_cases() -> list[gz.Case]:
    try:
        return [c for c in gz.discover_cases(split="holdout") if c.load_gold("legend") or c.load_gold("panel_ocr")]
    except (OSError, ValueError):
        return []


@pytest.mark.skipif(not _real_holdout_cases(), reason="no local holdout gold")
def test_real_holdout_gold_is_confirmed_and_frozen_intact() -> None:
    """Read-only: frozen local holdout gold follows the policy and matches its freeze.

    Unfrozen gold that is not confirmed yet is still being curated and is skipped.
    """
    for case in _real_holdout_cases():
        for task in gz.TASKS:
            gold = case.load_gold(task)
            if gold is None:
                continue
            frozen = (case.manifest.get("frozen") or {}).get(gz._FROZEN_KEYS[task])
            if not frozen and (gold.get("review") or {}).get("status") != "confirmed":
                continue
            gz.check_holdout_gold(case, task)
            if frozen:
                assert gz.sha256_file(case.gold_path(task)) == frozen, case.case_id
