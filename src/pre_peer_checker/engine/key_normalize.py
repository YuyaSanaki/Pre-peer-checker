"""表記揺れの候補キー正規化（Tier3・読む＝規則／任意 LLM、採用＝機械）."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass


def normalize_group_token(raw: str) -> str:
    """Compact lowercase token for fuzzy genotype / condition matching."""
    s = (raw or "").strip().lower()
    s = s.replace("−", "-").replace("–", "-").replace("—", "-")
    s = re.sub(r"\s+", " ", s)
    # common genotype punctuation → compact
    s = s.replace("-/-", "null").replace("+/-", "het").replace("+/+", "wt")
    s = s.replace("/", "")
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


_GENE_STEM_RE = re.compile(
    r"^(?P<stem>[a-z][a-z0-9]{1,20}?)(?P<suf>null|mutant|mut|ko|kd|rnai|het|wt)?$",
    re.I,
)

# Small closed synonym table (rules). LLM may append via propose_llm_key_aliases.
_SYNONYM_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"wt", "wildtype", "wild", "control", "ctrl", "cont", "vehicle", "0"}),
    frozenset({"mut", "mutant", "ko", "null", "knockout"}),
    frozenset({"kd", "knockdown", "rnai", "sirna"}),
)

_LLM_ALIAS_SYSTEM = (
    "You map legend group labels to table group names. "
    "Return ONLY JSON: {\"pairs\":[{\"legend\":\"...\",\"table\":\"...\"}]}. "
    "Each table value MUST be copied exactly from table_groups. "
    "Do not judge data integrity or invent groups."
)


@dataclass(frozen=True)
class KeyCandidate:
    source: str
    normalized: str
    aliases: tuple[str, ...]
    method: str = "rules"


def expand_candidate_keys(raw: str) -> set[str]:
    """Return matching tokens including normalized / synonym expansions."""
    raw = (raw or "").strip()
    if not raw:
        return set()
    out: set[str] = {raw.lower(), raw.lower().replace(" ", ""), raw.lower().replace("-", "")}
    norm = normalize_group_token(raw)
    if norm:
        out.add(norm)

    soft = raw.lower().replace("−", "-")
    soft = re.sub(r"\s+", " ", soft).strip()
    stem_src = soft.replace("-/-", " null").replace("+/-", " het").replace("+/+", " wt")
    stem_src = re.sub(r"[^a-z0-9\s]", " ", stem_src)
    parts = [p for p in stem_src.split() if p]
    if parts:
        stem = parts[0]
        out.add(stem)
        out.add(normalize_group_token(stem))
        for syn in ("mutant", "mut", "null", "ko", "kd", "rnai", "het", "wt"):
            out.add(f"{stem}{syn}")
            out.add(normalize_group_token(f"{stem} {syn}"))
        out.add(f"{stem}-/-")
        out.add(f"{stem}+/-")
        out.add(f"{stem}+/+")
        out.add(f"{stem} mutant")
        out.add(f"{stem} mut")

    m = _GENE_STEM_RE.match(norm)
    if m:
        stem = m.group("stem")
        out.add(stem)
        for syn in ("mutant", "mut", "null", "ko"):
            out.add(f"{stem}{syn}")

    for group in _SYNONYM_GROUPS:
        if out & group or norm in group:
            out |= set(group)

    return {x for x in out if x}


def expand_key_set(
    keys: set[str],
    *,
    extra_aliases: dict[str, set[str]] | None = None,
) -> set[str]:
    """Expand legend/table group keys with Tier3 candidates + optional LLM aliases."""
    out: set[str] = set(keys)
    for k in list(keys):
        out |= expand_candidate_keys(k)
    if extra_aliases:
        for src, aliases in extra_aliases.items():
            src_l = (src or "").strip().lower()
            if not src_l:
                continue
            src_exp = expand_candidate_keys(src) | {src_l, normalize_group_token(src)}
            if out & src_exp or any(k.lower() == src_l for k in keys):
                out.add(src_l)
                out |= {a.strip().lower() for a in aliases if a and str(a).strip()}
                for a in aliases:
                    out |= expand_candidate_keys(str(a))
    return out


def alias_map_from_candidates(cands: list[KeyCandidate]) -> dict[str, set[str]]:
    """Collapse KeyCandidate list into expand_key_set extra_aliases map."""
    out: dict[str, set[str]] = {}
    for c in cands:
        bucket = out.setdefault(c.source, set())
        bucket.update(c.aliases)
    return out


def propose_key_candidates(
    legend_groups: list[str],
    table_groups: list[str],
) -> list[KeyCandidate]:
    """Pairs of legend labels that normalize onto a table group (artifact / review)."""
    table_norm: dict[str, list[str]] = {}
    for tg in table_groups:
        for alias in expand_candidate_keys(tg):
            table_norm.setdefault(alias, []).append(tg)

    out: list[KeyCandidate] = []
    seen: set[str] = set()
    for lg in legend_groups:
        if not lg:
            continue
        aliases = tuple(sorted(expand_candidate_keys(lg)))
        hits: list[str] = []
        for a in aliases:
            for tg in table_norm.get(a, []):
                if tg.lower() != lg.lower() and tg not in hits:
                    hits.append(tg)
        if not hits:
            continue
        key = f"{lg}|{'|'.join(hits)}"
        if key in seen:
            continue
        seen.add(key)
        out.append(
            KeyCandidate(
                source=lg,
                normalized=normalize_group_token(lg),
                aliases=tuple(hits),
                method="rules",
            )
        )
    return out


def propose_llm_key_aliases(
    legend_groups: list[str],
    table_groups: list[str],
    llm_generate: Callable[[str], str],
    *,
    max_pairs: int = 20,
) -> list[KeyCandidate]:
    """Ask LLM for legend↔table label pairs; keep only exact table_groups hits.

    LLM never sets link status — proposals feed ``expand_key_set`` only.
    """
    legend_gs = [g for g in legend_groups if g and str(g).strip()]
    table_gs = [g for g in table_groups if g and str(g).strip()]
    if not legend_gs or not table_gs:
        return []
    table_exact = {g: g for g in table_gs}
    table_lower = {g.lower(): g for g in table_gs}
    prompt = (
        f"{_LLM_ALIAS_SYSTEM}\n\n"
        f"legend_groups={json.dumps(legend_gs, ensure_ascii=False)}\n"
        f"table_groups={json.dumps(table_gs, ensure_ascii=False)}\n"
    )
    try:
        raw = llm_generate(prompt)
    except Exception:  # noqa: BLE001
        return []
    payload = _parse_pairs_json(raw)
    if not payload:
        return []

    out: list[KeyCandidate] = []
    seen: set[str] = set()
    for pair in payload[:max_pairs]:
        if not isinstance(pair, dict):
            continue
        legend = str(pair.get("legend") or "").strip()
        table = str(pair.get("table") or "").strip()
        if not legend or not table:
            continue
        resolved = table_exact.get(table) or table_lower.get(table.lower())
        if resolved is None:
            continue
        if resolved.lower() == legend.lower():
            continue
        key = f"{legend}|{resolved}"
        if key in seen:
            continue
        seen.add(key)
        out.append(
            KeyCandidate(
                source=legend,
                normalized=normalize_group_token(legend),
                aliases=(resolved,),
                method="llm_proposal",
            )
        )
    return out


def _parse_pairs_json(raw: str) -> list[dict]:
    text = (raw or "").strip()
    if not text:
        return []
    if "```" in text:
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
        if m:
            text = m.group(1)
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        obj = json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        return []
    pairs = obj.get("pairs") if isinstance(obj, dict) else None
    return pairs if isinstance(pairs, list) else []


def keys_overlap_via_tier3(
    legend_keys: set[str],
    table_key: str,
    *,
    extra_aliases: dict[str, set[str]] | None = None,
) -> bool:
    """True if match requires expansion (base keys alone do not contain table_key)."""
    base = {k.lower() for k in legend_keys}
    tk = table_key.lower().strip()
    compact = tk.replace(" ", "").replace("-", "").replace("_", "")
    if tk in base or compact in base:
        return False
    expanded = expand_key_set(legend_keys, extra_aliases=extra_aliases)
    return bool(_vector_token_in(expanded, table_key))


def _vector_token_in(keys: set[str], group_key: str) -> bool:
    gk = group_key.lower().strip()
    if gk in keys:
        return True
    compact = gk.replace(" ", "").replace("-", "").replace("_", "")
    if compact in keys:
        return True
    return normalize_group_token(group_key) in keys
