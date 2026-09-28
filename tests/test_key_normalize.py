"""Tier3 key_normalize — rules + LLM proposal validation (no guilt)."""

from __future__ import annotations

import json

from pre_peer_checker.engine.key_normalize import (
    alias_map_from_candidates,
    expand_key_set,
    propose_llm_key_aliases,
)


def test_propose_llm_keeps_only_exact_table_groups():
    def fake(_prompt: str) -> str:
        return json.dumps(
            {
                "pairs": [
                    {"legend": "protocol X", "table": "alpha"},
                    {"legend": "junk", "table": "does-not-exist"},
                    {"legend": "protocol X", "table": "ALPHA"},  # case fold OK
                ]
            }
        )

    cands = propose_llm_key_aliases(
        ["protocol X"], ["alpha", "beta"], fake
    )
    assert len(cands) == 1
    assert cands[0].method == "llm_proposal"
    assert cands[0].aliases == ("alpha",)


def test_expand_key_set_applies_extra_aliases():
    keys = {"protocol-x"}
    expanded = expand_key_set(keys, extra_aliases={"protocol-x": {"alpha"}})
    assert "alpha" in expanded


def test_alias_map_from_candidates_merges():
    from pre_peer_checker.engine.key_normalize import KeyCandidate

    m = alias_map_from_candidates(
        [
            KeyCandidate("a", "a", ("x",), method="rules"),
            KeyCandidate("a", "a", ("y",), method="llm_proposal"),
        ]
    )
    assert m["a"] == {"x", "y"}
