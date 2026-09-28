"""Case vocabulary profile — experiment-side tokens, panel hints, filename group rules.

Engines that reason about "which experiment arm does this file / panel belong to"
(plot↔table swap, residue↔table, script save-name conflicts) read their vocabulary
from here instead of hard-coding lab- or paper-specific words.

- Built-in default: neutral tokens used by synthetic fixtures (``alphaexp`` / ``betaexp``).
  No panel-letter → side mapping (that would overfit to one figure layout).
- Local extension (gitignored): ``rules/local_case_profile.json`` or the path in
  ``$PRE_PEER_CHECKER_CASE_PROFILE``. Set the env var to ``none`` to use the default only.

JSON schema (all keys optional)::

    {
      "sides": [
        {"name": "armA", "tokens": ["arma"], "figure": "1",
         "figure_tokens": ["fig1c"], "panels": ["C", "D"]}
      ],
      "panel_group_keys": {"C": ["1", "cont"]},
      "experiment_tokens": ["geneX"],
      "residue_folder_tokens": ["plotdump"],
      "figure_alias_tokens": {"1": ["plotdump"]},
      "filename_group_rules": [["genex", "genex"]]
    }
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

ENV_VAR = "PRE_PEER_CHECKER_CASE_PROFILE"
LOCAL_PROFILE = Path(__file__).resolve().parents[3] / "rules" / "local_case_profile.json"

DEFAULT_PROFILE: dict[str, Any] = {
    "sides": [
        {"name": "alphaexp", "tokens": ["alphaexp"]},
        {"name": "betaexp", "tokens": ["betaexp"]},
    ],
    "panel_group_keys": {},
    "experiment_tokens": [],
    "residue_folder_tokens": [],
    "figure_alias_tokens": {},
    "filename_group_rules": [],
}


@dataclass(frozen=True)
class Side:
    name: str
    tokens: frozenset[str]
    figure_tokens: frozenset[str] = frozenset()
    panels: frozenset[str] = frozenset()
    figure: str | None = None

    @property
    def all_tokens(self) -> frozenset[str]:
        return self.tokens | self.figure_tokens

    def name_hit(self, name: str) -> bool:
        """True when a filename (lowercased) carries one of the side label tokens."""
        return any(t in name for t in self.tokens)


@dataclass(frozen=True)
class CaseProfile:
    sides: tuple[Side, ...] = ()
    panel_group_keys: dict[str, frozenset[str]] = field(default_factory=dict)
    experiment_tokens: tuple[str, ...] = ()
    residue_folder_tokens: frozenset[str] = frozenset()
    figure_alias_tokens: dict[str, frozenset[str]] = field(default_factory=dict)
    filename_group_rules: tuple[tuple[str, str], ...] = ()

    def side(self, name: str | None) -> Side | None:
        if not name:
            return None
        return next((s for s in self.sides if s.name == name), None)

    def panel_side(self, panel: str) -> str | None:
        p = panel.upper()
        for s in self.sides:
            if p in s.panels:
                return s.name
        return None

    def side_tokens(self, name: str | None) -> frozenset[str]:
        s = self.side(name)
        return s.all_tokens if s else frozenset()

    def other_side_tokens(self, name: str | None) -> frozenset[str]:
        out: set[str] = set()
        for s in self.sides:
            if s.name != name:
                out |= s.all_tokens
        return frozenset(out)

    def all_side_tokens(self) -> frozenset[str]:
        out: set[str] = set()
        for s in self.sides:
            out |= s.all_tokens
        return frozenset(out)

    def side_label_tokens(self) -> frozenset[str]:
        """Side label tokens only (no figure-panel tokens)."""
        out: set[str] = set()
        for s in self.sides:
            out |= s.tokens
        return frozenset(out)

    def alias_tokens_for_figure(self, fnum: str | None) -> frozenset[str]:
        """Tokens that imply a path belongs to figure ``fnum`` without an explicit FigN."""
        if not fnum:
            return frozenset()
        out: set[str] = set(self.figure_alias_tokens.get(fnum, frozenset()))
        for s in self.sides:
            if s.figure == fnum:
                out |= s.all_tokens
        return frozenset(out)

    def path_vocabulary(self) -> frozenset[str]:
        out: set[str] = set(self.experiment_tokens)
        out |= self.all_side_tokens()
        out |= self.residue_folder_tokens
        for toks in self.figure_alias_tokens.values():
            out |= toks
        return frozenset(out)


def _lower_set(values: Any) -> frozenset[str]:
    return frozenset(str(v).lower() for v in (values or []))


def _merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    out = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v)
           for k, v in base.items()}
    sides = {s["name"]: s for s in out.get("sides", [])}
    for s in extra.get("sides", []) or []:
        sides[s["name"]] = s
    out["sides"] = list(sides.values())
    for key in ("panel_group_keys", "figure_alias_tokens"):
        merged = dict(out.get(key) or {})
        merged.update(extra.get(key) or {})
        out[key] = merged
    for key in ("experiment_tokens", "residue_folder_tokens", "filename_group_rules"):
        merged_list = list(extra.get(key) or [])
        for item in out.get(key) or []:
            if item not in merged_list:
                merged_list.append(item)
        out[key] = merged_list
    return out


def build_profile(raw: dict[str, Any]) -> CaseProfile:
    sides = tuple(
        Side(
            name=str(s["name"]).lower(),
            tokens=_lower_set(s.get("tokens") or [s["name"]]),
            figure_tokens=_lower_set(s.get("figure_tokens")),
            panels=frozenset(str(p).upper() for p in s.get("panels") or []),
            figure=str(s["figure"]) if s.get("figure") is not None else None,
        )
        for s in raw.get("sides") or []
    )
    return CaseProfile(
        sides=sides,
        panel_group_keys={
            str(k).upper(): _lower_set(v) for k, v in (raw.get("panel_group_keys") or {}).items()
        },
        experiment_tokens=tuple(str(t).lower() for t in raw.get("experiment_tokens") or []),
        residue_folder_tokens=_lower_set(raw.get("residue_folder_tokens")),
        figure_alias_tokens={
            str(k): _lower_set(v) for k, v in (raw.get("figure_alias_tokens") or {}).items()
        },
        filename_group_rules=tuple(
            (str(a).lower(), str(b)) for a, b in raw.get("filename_group_rules") or []
        ),
    )


def _local_profile_path() -> Path | None:
    env = os.environ.get(ENV_VAR, "").strip()
    if env.lower() in {"none", "off", "0", "default"}:
        return None
    if env:
        return Path(env).expanduser()
    return LOCAL_PROFILE if LOCAL_PROFILE.is_file() else None


def load_profile(path: Path | None = None) -> CaseProfile:
    raw = DEFAULT_PROFILE
    local = path if path is not None else _local_profile_path()
    if local is not None and local.is_file():
        raw = _merge(DEFAULT_PROFILE, json.loads(local.read_text(encoding="utf-8")))
    return build_profile(raw)


_ACTIVE: CaseProfile | None = None


def get_case_profile() -> CaseProfile:
    global _ACTIVE
    if _ACTIVE is None:
        _ACTIVE = load_profile()
    return _ACTIVE


@contextmanager
def use_case_profile(raw: dict[str, Any] | CaseProfile | None) -> Iterator[CaseProfile]:
    """Temporarily activate a profile (tests / programmatic callers)."""
    global _ACTIVE
    prev = _ACTIVE
    if raw is None:
        prof = build_profile(DEFAULT_PROFILE)
    elif isinstance(raw, CaseProfile):
        prof = raw
    else:
        prof = build_profile(_merge(DEFAULT_PROFILE, raw))
    _ACTIVE = prof
    try:
        yield prof
    finally:
        _ACTIVE = prev
