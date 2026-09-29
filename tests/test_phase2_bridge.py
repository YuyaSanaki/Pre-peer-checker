"""LightGlue / corpus H3 / Legend JSON bridge tests."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageEnhance

from pre_peer_checker.imaging.corpus_scan import collect_corpus_images, scan_against_corpus
from pre_peer_checker.imaging.duplicate_scan import scan_image_duplicates_auto
from pre_peer_checker.imaging.lightglue_match import verify_image_pair
from pre_peer_checker.llm.legend_extract import (
    extract_legend_json_hybrid,
    structured_to_legend_json,
)
from pre_peer_checker.llm.legend_schema import (
    detect_citation,
    legend_json_schema,
    parse_legend_llm_response,
    validate_legend_dict,
)
from pre_peer_checker.parsers.legend_struct import StructuredLegend, PanelN
from pre_peer_checker.pipeline.orchestrator import run_verification
from pre_peer_checker.warnings import WarningTag


def test_precise_match_identical_inverted_and_different(tmp_path: Path):
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    c = tmp_path / "c.png"
    d = tmp_path / "d.png"
    arr = np.random.default_rng(0).integers(0, 255, (96, 96), dtype=np.uint8)
    Image.fromarray(arr).save(a)
    Image.fromarray(arr).save(b)
    Image.fromarray(255 - arr).save(c)
    # Unrelated image with its own geometry (two independent noise fields already get
    # >100 spurious LightGlue matches in the normal orientation).
    other = Image.new("L", (96, 96), 120)
    draw = ImageDraw.Draw(other)
    for r in range(8, 48, 8):
        draw.ellipse([48 - r, 48 - r, 48 + r, 48 + r], outline=30, width=2)
    for y in range(0, 96, 12):
        draw.line([0, y, 96, y], fill=220, width=1)
    other.save(d)
    ok = verify_image_pair(a, b)
    assert ok.verified
    assert not ok.inverted
    assert ok.method in {
        "ncc-multiscale",
        "orb-ransac",
        "lightglue+superpoint",
        "lightglue+aliked",
    }
    # Inverted reuse is flagged whichever extractor / device is active.
    flipped = verify_image_pair(a, c)
    assert flipped.verified
    assert flipped.inverted
    assert flipped.method.endswith("+inverted")
    assert not verify_image_pair(a, d).verified


def test_inverted_pass_rejects_scattered_matches(tmp_path: Path):
    from pre_peer_checker.imaging.lightglue_match import (
        _verify_lightglue,
        inverted_copy,
        lightglue_available,
    )

    if not lightglue_available():
        pytest.skip("lightglue+torch not installed")
    a = tmp_path / "a.png"
    d = tmp_path / "d.png"
    Image.fromarray(np.random.default_rng(0).integers(0, 255, (96, 96), dtype=np.uint8)).save(a)
    Image.fromarray(np.random.default_rng(1).integers(0, 255, (96, 96), dtype=np.uint8)).save(d)
    # Independent noise: plenty of raw matches, but they do not share one homography.
    vr = _verify_lightglue(a, inverted_copy(d), features="superpoint", min_inlier_ratio=0.85)
    assert vr.num_matches >= 35
    assert not vr.verified


def test_precise_match_inverted_without_lightglue(tmp_path: Path):
    a = tmp_path / "a.png"
    c = tmp_path / "c.png"
    arr = np.random.default_rng(5).integers(0, 255, (96, 96), dtype=np.uint8)
    Image.fromarray(arr).save(a)
    Image.fromarray(255 - arr).save(c)
    vr = verify_image_pair(a, c, prefer_lightglue=False)
    assert vr.verified and vr.inverted


def test_fallback_scan_keeps_inverted_pair(tmp_path: Path):
    a = tmp_path / "a.png"
    c = tmp_path / "c.png"
    # ORB finds too few keypoints below ~96 px even for identical images.
    arr = np.random.default_rng(6).integers(30, 220, (128, 128, 3), dtype=np.uint8)
    Image.fromarray(arr).save(a)
    Image.fromarray(255 - arr).save(c)
    matches, _ = scan_image_duplicates_auto(
        [a, c], prefer_dino=False, refine_with_precise=True, prefer_lightglue=False
    )
    assert len(matches) == 1
    m = matches[0]
    assert m.cosine_similarity > 0.99
    assert m.coarse_inverted
    assert m.likely_duplicate and m.precise_verified and m.precise_inverted


def test_panel_shortlist_includes_inverted_pair(tmp_path: Path):
    from pre_peer_checker.imaging.panel_units import rank_similarity

    rng = np.random.default_rng(7)
    arrs = [rng.integers(0, 255, (64, 64), dtype=np.uint8) for _ in range(3)]
    paths = []
    for i, arr in enumerate([arrs[0], arrs[1], arrs[2], 255 - arrs[0]]):
        p = tmp_path / f"p{i}.png"
        Image.fromarray(arr).save(p)
        paths.append(p)
    sims = rank_similarity(paths, prefer_dino=False)
    assert np.allclose(sims, sims.T)
    assert sims[0, 3] > 0.99
    assert sims[0, 3] > sims[0, 1] + 0.5
    cross = rank_similarity(paths[:1], paths[1:], prefer_dino=False)
    assert cross.shape == (1, 3)
    assert int(np.argmax(cross[0])) == 2


def test_corpus_scan_flags_inverted_reuse(tmp_path: Path):
    manuscript = tmp_path / "ms"
    corpus = tmp_path / "corpus"
    manuscript.mkdir()
    corpus.mkdir()
    arr = np.random.default_rng(8).integers(0, 255, (80, 80, 3), dtype=np.uint8)
    q = manuscript / "fig_panel.png"
    Image.fromarray(arr).save(q)
    Image.fromarray(255 - arr).save(corpus / "prior_study_a.png")
    result = scan_against_corpus(
        [q], [corpus], legend_has_citation=False, prefer_dino=False
    )
    reuse = [w for w in result.warnings if w.tag == WarningTag.IMAGE_REUSE]
    assert reuse
    assert any(w.metadata.get("inverted") for w in reuse)
    assert any("反転" in w.reason for w in reuse)


def test_scan_auto_refines_with_precise(tmp_path: Path, monkeypatch):
    # 64px noise: ALIKED finds only ~26 matches (< 50); SuperPoint path is the one under test.
    monkeypatch.setenv("PRE_PEER_CHECKER_USAGE", "academic")
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    arr = np.random.default_rng(2).integers(30, 220, (64, 64, 3), dtype=np.uint8)
    Image.fromarray(arr).save(a)
    # slight brightness change still should verify
    ImageEnhance.Brightness(Image.fromarray(arr)).enhance(1.05).save(b)
    matches, method = scan_image_duplicates_auto(
        [a, b], prefer_dino=False, refine_with_precise=True
    )
    assert "precise" in method or "ncc" in method or "orb" in method or "lightglue" in method
    assert any(m.likely_duplicate and m.precise_verified for m in matches)


def test_legend_json_schema_and_rules():
    schema = legend_json_schema()
    assert schema["required"]
    text = (
        "Figure 1. Quantification. n=12 (F) and n=17 (N). "
        "Welch's t-test, p<0.01. Reproduced from curated external case, 2016."
    )
    cit = detect_citation(text)
    assert cit.mentioned
    assert cit.reproduced_from
    leg = extract_legend_json_hybrid(text, figure_hint="Figure 1")
    assert leg.citation.mentioned
    assert any(p.panel == "F" and p.n == 12 for p in leg.panels)
    d = leg.to_dict()
    assert validate_legend_dict(d) == []


def test_parse_legend_llm_json_fence():
    raw = """```json
{"figure": "Figure 1", "panels": [{"panel": "A", "n": 5, "groups": [], "notes": ""}],
 "tests": [], "p_values": [], "citation": {"mentioned": false, "reproduced_from": null, "spans": []},
 "raw_excerpt": "x", "extractor": "llm"}
```"""
    parsed = parse_legend_llm_response(raw)
    assert parsed is not None
    assert parsed.panels[0].n == 5


def test_structured_to_legend_json():
    leg = StructuredLegend(
        figure="Figure 1",
        text="n=12 (F) adapted from Smith 2019",
        panel_ns=[PanelN(panel="F", n=12, figure="Figure 1", context="n=12 (F)")],
        tests=["ANOVA"],
        p_values=[0.05],
    )
    js = structured_to_legend_json(leg)
    assert js.citation.mentioned
    assert js.panels[0].n == 12


def test_corpus_scan_flags_uncited_reuse(tmp_path: Path):
    manuscript = tmp_path / "ms"
    corpus = tmp_path / "corpus"
    manuscript.mkdir()
    corpus.mkdir()
    arr = np.random.default_rng(3).integers(0, 255, (80, 80, 3), dtype=np.uint8)
    q = manuscript / "fig_panel.png"
    c = corpus / "prior_study_a.png"
    Image.fromarray(arr).save(q)
    Image.fromarray(arr).save(c)
    result = scan_against_corpus(
        [q], [corpus], legend_has_citation=False, prefer_dino=False
    )
    assert result.corpus_present
    assert any(w.tag == WarningTag.IMAGE_REUSE for w in result.warnings)

    cited = scan_against_corpus(
        [q], [corpus], legend_has_citation=True, prefer_dino=False
    )
    assert not cited.warnings
    assert cited.artifacts.get("cited_matches")


def test_orchestrator_corpus_and_legend_json(tmp_path: Path):
    from docx import Document

    ms = tmp_path / "ms"
    corp = tmp_path / "corp"
    ms.mkdir()
    corp.mkdir()
    doc = Document()
    doc.add_paragraph(
        "Figure 1. Clone size. n=12 (F). Reproduced from curated external case, 2016."
    )
    doc.save(ms / "main.docx")
    arr = np.random.default_rng(4).integers(0, 255, (48, 48, 3), dtype=np.uint8)
    Image.fromarray(arr).save(ms / "panel.png")
    Image.fromarray(arr).save(corp / "old.png")

    result = run_verification([ms], corpus=[corp])
    assert result.artifacts.get("legend_json")
    assert result.artifacts.get("legend_citation_mentioned") is True
    assert result.artifacts.get("corpus_present") is True
    # citation present → no IMAGE_REUSE hard warning
    assert not any(w.tag == WarningTag.IMAGE_REUSE for w in result.warnings)


def test_collect_corpus_images(tmp_path: Path):
    (tmp_path / "a.png").write_bytes(b"")
    # empty file still collected by extension
    found = collect_corpus_images([tmp_path])
    assert found
