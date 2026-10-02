"""Candidate cards for legend rows the linker leaves unlinked."""

from __future__ import annotations

from pathlib import Path

from pre_peer_checker.data.group_vectors import GroupVector
from pre_peer_checker.engine.link_candidates import (
    link_candidate_cards,
    warnings_from_candidate_cards,
)
from pre_peer_checker.engine.n_matrix import build_n_matrix
from pre_peer_checker.parsers.legend_struct import PanelN

CTX = "relative clone size, n=12 (E), 12 (F), and 12 (G). ***p<0.001"


def _vecs(path: Path, ns: list[int], offset: float = 0.0) -> list[GroupVector]:
    return [
        GroupVector(
            source=path,
            group_key=str(i),
            values=tuple(float(i * 100 + j) + offset for j in range(n)),
            n=n,
        )
        for i, n in enumerate(ns)
    ]


def _panels() -> list[PanelN]:
    return [PanelN(panel=p, n=12, figure="Figure 1", context=CTX) for p in "EFG"]


def test_statement_shape_card_lists_near_table(tmp_path: Path):
    a = tmp_path / "Fig1I" / "graphMARCM.xlsx"
    copy = tmp_path / "confocal" / "Fig1I" / "graphMARCM.xlsx"
    vecs = _vecs(a, [11, 11, 11]) + _vecs(copy, [11, 11, 11])
    pns = _panels()
    rows = build_n_matrix(pns, vecs, case_roots=[tmp_path])
    assert not any(r.mismatch for r in rows)

    cards = link_candidate_cards(pns, vecs, rows)
    assert len(cards) == 1
    card = cards[0]
    assert card.kind == "statement_shape"
    assert card.panels == ["E", "F", "G"]
    assert len(card.candidates) == 1 and card.candidates[0].copies == 2

    (w,) = warnings_from_candidate_cards(cards)
    assert w.is_demoted()
    assert w.metadata["info_card"] is True
    assert "graphMARCM.xlsx" in w.reason


def test_no_card_when_shape_differs_or_too_many(tmp_path: Path):
    pns = _panels()
    far = _vecs(tmp_path / "Fig1" / "far.xlsx", [30, 30, 30])
    rows = build_n_matrix(pns, far, case_roots=[tmp_path])
    assert link_candidate_cards(pns, far, rows) == []

    many = []
    for i in range(4):
        many += _vecs(tmp_path / "Fig1" / f"t{i}.xlsx", [11, 11, 11], offset=0.5 * i)
    rows = build_n_matrix(pns, many, case_roots=[tmp_path])
    assert link_candidate_cards(pns, many, rows) == []
