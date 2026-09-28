"""References biblio + cited-paper claim checks."""

from __future__ import annotations

from pathlib import Path

import pytest

from pre_peer_checker.eval.gold_eval import run_and_evaluate
from pre_peer_checker.engine.ref_biblio import warnings_from_ref_biblio
from pre_peer_checker.engine.ref_claim import review_claims_against_pdfs
from pre_peer_checker.engine.ref_pdf_meta import match_bib_to_pdfs, warnings_from_ref_pdf_meta
from pre_peer_checker.llm.claim_cite_schema import claims_from_in_text
from pre_peer_checker.parsers.cited_paper_ingest import ensure_pdfs_ingested, ingest_cited_paper_pdf
from pre_peer_checker.parsers.references import parse_references_from_docx, parse_references_from_paragraphs
from pre_peer_checker.pipeline.orchestrator import run_verification

ROOT = Path(__file__).resolve().parents[1]
SYN = ROOT / "fixtures" / "synthetic"


def test_parse_references_numbered_and_cites():
    paras = [
        "Results",
        "We cite prior work [1,2] and also [4].",
        "References",
        "1. Adams A. First paper. J Demo. 2019. doi: 10.1000/a",
        "2. Baker B. Second. J Demo. 2020.",
    ]
    bundle = parse_references_from_paragraphs(paras)
    assert bundle.references_section_found
    assert len(bundle.entries) == 2
    assert bundle.entries[0].key == "1"
    assert bundle.entries[0].doi == "10.1000/a"
    keys = {k for c in bundle.in_text for k in c.keys}
    assert "1" in keys and "2" in keys and "4" in keys


def test_ref_biblio_unit_warnings():
    bundle = parse_references_from_docx(SYN / "ref_biblio" / "manuscript.docx")
    warns = warnings_from_ref_biblio(bundle)
    pids = {w.metadata.get("pattern_id") for w in warns}
    assert "P-REF-MISSING-ENTRY" in pids
    assert "P-REF-DUPLICATE-KEY" in pids
    assert "P-REF-META-INCONSISTENT" in pids


def test_ref_biblio_orchestrator_and_gold(tmp_path: Path):
    result = run_verification([SYN / "ref_biblio"])
    pids = {w.metadata.get("pattern_id") for w in result.warnings}
    assert "P-REF-MISSING-ENTRY" in pids
    assert "P-REF-DUPLICATE-KEY" in pids
    assert "P-REF-META-INCONSISTENT" in pids
    report = run_and_evaluate(
        "ref_biblio",
        input_paths=[SYN / "ref_biblio"],
        warnings_out=tmp_path / "ref_biblio.json",
    )
    assert report["required_recall"] == 1.0, report


def test_cited_paper_ingest_and_claim(tmp_path: Path):
    lib = tmp_path / "cited_lib"
    pdf_dir = SYN / "ref_claim" / "cited_pdfs"
    entries = ensure_pdfs_ingested([pdf_dir], library_root=lib)
    assert len(entries) >= 2
    bundle = parse_references_from_docx(SYN / "ref_claim" / "manuscript.docx")
    link = match_bib_to_pdfs(bundle, entries)
    assert link["matches"], link
    meta_warns = warnings_from_ref_pdf_meta(link)
    assert any(w.metadata.get("pattern_id") == "P-REF-PDF-META-MISMATCH" for w in meta_warns)
    claims = claims_from_in_text(bundle.in_text)
    reviews, claim_warns = review_claims_against_pdfs(claims, link)
    assert reviews
    assert any(w.metadata.get("pattern_id") == "P-REF-CLAIM-CONTRADICTION" for w in claim_warns)


def test_ref_claim_orchestrator_and_gold(tmp_path: Path):
    result = run_verification(
        [SYN / "ref_claim"],
        cited_papers=[SYN / "ref_claim" / "cited_pdfs"],
    )
    pids = {w.metadata.get("pattern_id") for w in result.warnings}
    assert "P-REF-PDF-META-MISMATCH" in pids, pids
    assert "P-REF-CLAIM-CONTRADICTION" in pids, pids
    cov = result.artifacts.get("run_coverage") or {}
    assert cov.get("citation_evidence_reviews")
    report = run_and_evaluate(
        "ref_claim",
        input_paths=[SYN / "ref_claim"],
        cited_papers_paths=[SYN / "ref_claim" / "cited_pdfs"],
        warnings_out=tmp_path / "ref_claim.json",
    )
    assert report["required_recall"] == 1.0, report


def test_ingest_single_pdf(tmp_path: Path):
    pdf = SYN / "ref_claim" / "cited_pdfs" / "notch_2021.pdf"
    res = ingest_cited_paper_pdf(pdf, library_root=tmp_path / "lib")
    assert res["ok"]
    assert (tmp_path / "lib" / "entries" / res["id"] / "text.txt").is_file()


def test_ingest_same_pdf_reuses_entry(tmp_path: Path):
    lib = tmp_path / "lib"
    pdf = SYN / "ref_claim" / "cited_pdfs" / "notch_2021.pdf"
    first = ingest_cited_paper_pdf(pdf, library_root=lib)
    second = ingest_cited_paper_pdf(pdf, library_root=lib)
    assert second["id"] == first["id"]
    assert second.get("reused") is True
    assert len(list((lib / "entries").iterdir())) == 1
