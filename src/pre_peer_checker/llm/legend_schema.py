"""Figure Legend → 強制 JSON スキーマ（規則＋LLM ハイブリッドの橋渡し）."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

# Citation / reuse cues for H3 (P-IMAGE-REUSE-UNCITED)
_CITATION_RE = re.compile(
    r"(reproduced\s+from|adapted\s+from|modified\s+from|taken\s+from|"
    r"courtesy\s+of|with\s+permission|previously\s+published|"
    r"doi\s*:\s*\S+|https?://\S+|et\s+al\.?\s*,?\s*\d{4}|"
    r"出典|転載|改変|許可を得て)",
    re.IGNORECASE,
)

LEGEND_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "FigureCheckItemExtraction",
    "type": "object",
    "required": ["figure", "panels", "tests", "p_values", "citation"],
    "properties": {
        "figure": {"type": "string"},
        "panels": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["panel"],
                "properties": {
                    "panel": {"type": "string"},
                    "n": {"type": ["integer", "null"]},
                    "groups": {"type": "array", "items": {"type": "string"}},
                    "notes": {"type": "string"},
                    "n_scope": {
                        "type": "string",
                        "description": "per_group | total | per_genotype | unknown",
                    },
                    "evidence_span": {"type": "string"},
                    "confidence": {"type": ["number", "null"]},
                },
            },
        },
        "tests": {"type": "array", "items": {"type": "string"}},
        "p_values": {"type": "array", "items": {"type": "number"}},
        "citation": {
            "type": "object",
            "required": ["mentioned"],
            "properties": {
                "mentioned": {"type": "boolean"},
                "reproduced_from": {"type": ["string", "null"]},
                "spans": {"type": "array", "items": {"type": "string"}},
            },
        },
        "error_bar_type": {
            "type": ["string", "null"],
            "description": "G4: sem|sd|ci|range|unknown; null if absent from text",
        },
        "independence_claims": {
            "type": "object",
            "required": ["mentioned"],
            "properties": {
                "mentioned": {"type": "boolean"},
                "kind": {
                    "type": ["string", "null"],
                    "description": "independent | shared_control | unknown",
                },
                "spans": {"type": "array", "items": {"type": "string"}},
            },
        },
        "exclusion_criteria": {
            "type": "object",
            "required": ["mentioned"],
            "properties": {
                "mentioned": {"type": "boolean"},
                "spans": {"type": "array", "items": {"type": "string"}},
            },
        },
        "raw_excerpt": {"type": "string"},
        "extractor": {"type": "string"},
    },
}

LEGEND_JSON_SYSTEM_PROMPT = """You extract structured Figure Legend metadata for manuscript integrity checks.
Return ONLY valid JSON matching this schema (no markdown):
{
  "figure": "Figure 1",
  "panels": [{"panel": "F", "n": 12, "groups": ["WT"], "notes": "", "n_scope": "per_group", "evidence_span": "n=12 (F)", "confidence": 0.9}],
  "tests": ["Welch's t-test"],
  "p_values": [0.01],
  "citation": {"mentioned": false, "reproduced_from": null, "spans": []},
  "error_bar_type": "sem",
  "independence_claims": {"mentioned": true, "kind": "independent", "spans": ["n=3 independent experiments"]},
  "exclusion_criteria": {"mentioned": false, "spans": []},
  "raw_excerpt": "...",
  "extractor": "llm"
}
Critical rules:
- Emit ONE panels[] object per (panel letter, group, n). Split multi-n lists into multiple objects.
- Figure PANEL letters are uppercase in structure: (A), (B), (K-M), (R).
- Genotype/condition labels after n are often lowercase or words:
  e.g. "quantification … (M). n=18 (a) and 10 (b)" → panel M with groups a/b (NOT panels A and B).
- Legend typos: if text says n=140 (k) and 88 (l) but the quantified panels are clearly L/M
  (section K-M / figure panel list), map to panels L and M (case/letter slip). Prefer panels
  that exist on the figure over inventing new lowercase panel ids.
- "n=10 (D, E, G and H)" assigns n=10 to panels D,E,G,H.
- "n=10 (N), 9 (O), 10 (P), and 10 (Q)" assigns each value to that panel.
- "(K) Quantification…. n=11 (H), 20 (I), and 9 (J)" assigns n to H/I/J — NOT to section letter K.
- "n=4 (early L3) and 3 (late L3)" under (A) → two objects, panel A, groups early L3 / late L3.
- "n=9 (control, early L3), 8 (geneX+/-, early L3), 8 (control, late L3), and 5 (geneX+/-, late L3)"
  under (C) → FOUR objects on panel C; group string must keep BOTH genotype and timepoint.
- "n=13 (WT), 10 (geneX+/-)" under (J) → two objects on panel J.
- "n=5 (1x), 8 (4x)" under (E) → panel E only (groups 1x/4x). Do NOT also create panel A.
- error_bar_type: sem|sd|ci|range|unknown|null from mean±SEM / SD / CI wording (null if absent).
- independence_claims.kind: independent | shared_control | unknown when text claims biological independence or shared controls.
- exclusion_criteria.mentioned true if exclusion/omission criteria appear.
- Do not invent panels; prefer evidence_span quotes from the legend.
- citation.mentioned true if reproduced/adapted from / DOI / prior paper
"""

FIGURE_CHUNK_SYSTEM_PROMPT = """You extract check-item JSON for ONE figure from manuscript text (+ optional panel labels seen on the figure image/PDF).
Sections may include [legend], [results_mentions], [methods_related], and [figure_panel_labels].
Your job is EXTRACTION ONLY (sample sizes, tests, p-values, citation cues) — do not judge integrity.
Return ONLY valid JSON (no markdown):
{
  "figure": "Figure 2",
  "panels": [
    {
      "panel": "M",
      "n": 18,
      "groups": ["a"],
      "notes": "geneY clone volume group a",
      "n_scope": "per_genotype",
      "evidence_span": "quantification of geneY-/- clone volume(M). n=18 (a) and 10 (b)",
      "confidence": 0.9
    },
    {
      "panel": "M",
      "n": 10,
      "groups": ["b"],
      "notes": "geneY clone volume group b",
      "n_scope": "per_genotype",
      "evidence_span": "n=18 (a) and 10 (b)",
      "confidence": 0.9
    }
  ],
  "tests": ["Welch's t-test"],
  "p_values": [0.001],
  "citation": {"mentioned": false, "reproduced_from": null, "spans": []},
  "error_bar_type": "sem",
  "independence_claims": {"mentioned": false, "kind": null, "spans": []},
  "exclusion_criteria": {"mentioned": false, "spans": []},
  "raw_excerpt": "short quote",
  "extractor": "llm"
}
Critical disambiguation:
- ONE panels[] row per (panel, group, n). Expand every n=… list fully.
- UPPERCASE (A)/(B)/(N) in "n=10 (N)" = figure panels.
- "(K) Quantification of clone size. n=11 (H), 20 (I), and 9 (J)" → panels H,I,J (not K).
- "n=5 (1x), 8 (4x)" under (E) → panel E with groups 1x/4x only — never duplicate onto A/B/C.
- Timepoint × genotype lists must keep both axes in groups, e.g. "control (early L3)",
  "geneX+/- (early L3)", "control (late L3)", "geneX+/- (late L3)" — four rows, not two.
- "n=13 (WT), 10 (geneX+/-)" → two rows (do not drop the second genotype).
- lowercase (a)/(b) after n under "quantification … (M)" = groups for panel M.
- Legend letter typos: n=…(k)/(l) that clearly quantify adult/phenotype panels L/M
  (see [figure_panel_labels] / K-M block) → panels L/M, not groups k/l and not skip.
- Use [figure_panel_labels] when present to know which panel letters exist on the artwork.
- Prefer Legend; use Results/Methods only when they clearly state n for this figure.
- error_bar_type / independence_claims / exclusion_criteria from Legend/Methods when present.
- confidence 0–1; omit invented single n for ranges like n=5–8.
- Do not invent panels or statistics absent from the provided text.
"""


@dataclass
class LegendCitation:
    mentioned: bool = False
    reproduced_from: str | None = None
    spans: list[str] = field(default_factory=list)


@dataclass
class IndependenceClaims:
    mentioned: bool = False
    kind: str | None = None  # independent | shared_control | unknown
    spans: list[str] = field(default_factory=list)


@dataclass
class ExclusionCriteria:
    mentioned: bool = False
    spans: list[str] = field(default_factory=list)


@dataclass
class LegendPanelJSON:
    panel: str
    n: int | None = None
    groups: list[str] = field(default_factory=list)
    notes: str = ""
    n_scope: str = "unknown"
    evidence_span: str = ""
    confidence: float | None = None
    n_max: int | None = None


@dataclass
class LegendFigureJSON:
    figure: str
    panels: list[LegendPanelJSON] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    p_values: list[float] = field(default_factory=list)
    citation: LegendCitation = field(default_factory=LegendCitation)
    error_bar_type: str | None = None
    independence_claims: IndependenceClaims = field(default_factory=IndependenceClaims)
    exclusion_criteria: ExclusionCriteria = field(default_factory=ExclusionCriteria)
    raw_excerpt: str = ""
    extractor: str = "rules"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


def detect_citation(text: str) -> LegendCitation:
    spans = [m.group(0) for m in _CITATION_RE.finditer(text or "")]
    reproduced = None
    m = re.search(
        r"(?:reproduced|adapted|modified|taken)\s+from\s+([^.;\n]{3,120})",
        text or "",
        re.IGNORECASE,
    )
    if m:
        reproduced = m.group(1).strip()
    return LegendCitation(mentioned=bool(spans), reproduced_from=reproduced, spans=spans)


_ERROR_BAR_SEM_RE = re.compile(
    r"(?:\bSEM\b|\bs\.e\.m\.?\b|standard\s+errors?(?:\s+of\s+(?:the\s+)?mean)?|"
    r"mean\s*[±\+\-]\s*SEM)",
    re.I,
)
_ERROR_BAR_SD_RE = re.compile(
    r"(?:\bS\.?D\.?\b|standard\s+deviations?|mean\s*[±\+\-]\s*SD)",
    re.I,
)
_ERROR_BAR_CI_RE = re.compile(
    r"(?:\bCIs?\b|confidence\s+intervals?|\d+\s*%\s*CI)",
    re.I,
)
_ERROR_BAR_RANGE_RE = re.compile(r"(?:mean\s*[±\+\-]\s*range|\brange\b)", re.I)

_INDEP_RE = re.compile(
    r"(?:independent\s+experiments?|biological\s+replicates?|"
    r"n\s*=\s*\d+\s+independent|technically\s+independent)",
    re.I,
)
_SHARED_CTRL_RE = re.compile(
    r"(?:shared\s+controls?|same\s+controls?\s+(?:group|data)|"
    r"controls?\s+(?:were|was)\s+shared)",
    re.I,
)
_EXCLUSION_RE = re.compile(
    r"(?:exclusion\s+criteria|animals?\s+(?:were\s+)?exclu(?:ded|sion)|"
    r"outliers?\s+(?:were\s+)?exclu(?:ded|sion)|"
    r"exclu(?:ded|sion)\s+(?:from|due)|were\s+omitted|not\s+included\s+in\s+(?:the\s+)?analysis)",
    re.I,
)

_ERROR_BAR_OK = {"sem", "sd", "ci", "range", "unknown"}
_INDEP_KIND_OK = {"independent", "shared_control", "unknown"}


def detect_error_bar_type(text: str) -> str | None:
    """Return sem|sd|ci|range|unknown|None from legend/methods cues."""
    t = text or ""
    hits: list[str] = []
    if _ERROR_BAR_SEM_RE.search(t):
        hits.append("sem")
    if _ERROR_BAR_SD_RE.search(t):
        hits.append("sd")
    if _ERROR_BAR_CI_RE.search(t):
        hits.append("ci")
    if _ERROR_BAR_RANGE_RE.search(t) and "sem" not in hits and "sd" not in hits:
        hits.append("range")
    if not hits:
        return None
    if len(hits) == 1:
        return hits[0]
    # Prefer mean±SEM / mean±SD wording order in text
    m = re.search(r"mean\s*[±\+\-]\s*(SEM|SD|s\.e\.m\.?|S\.?D\.?)", t, re.I)
    if m:
        tok = m.group(1).lower().replace(".", "")
        if tok.startswith("se"):
            return "sem"
        if tok.startswith("sd"):
            return "sd"
    return "unknown"


def detect_independence_claims(text: str) -> IndependenceClaims:
    spans = [m.group(0) for m in _INDEP_RE.finditer(text or "")]
    shared = [m.group(0) for m in _SHARED_CTRL_RE.finditer(text or "")]
    if shared and not spans:
        return IndependenceClaims(
            mentioned=True, kind="shared_control", spans=shared[:5]
        )
    if spans and shared:
        return IndependenceClaims(
            mentioned=True, kind="unknown", spans=(spans + shared)[:5]
        )
    if spans:
        return IndependenceClaims(mentioned=True, kind="independent", spans=spans[:5])
    return IndependenceClaims()


def detect_exclusion_criteria(text: str) -> ExclusionCriteria:
    spans = [m.group(0) for m in _EXCLUSION_RE.finditer(text or "")]
    return ExclusionCriteria(mentioned=bool(spans), spans=spans[:5])


def legend_json_schema() -> dict[str, Any]:
    return dict(LEGEND_JSON_SCHEMA)


_MINIMAL_RULE = (
    "Rules:\n"
    "- Emit one panels[] object per (panel, group, n) stated in the text.\n"
    "- Do not invent panels or statistics absent from the provided text.\n"
)

# Schema-only baselines for generalization ablation (no case-derived disambiguation rules).
LEGEND_JSON_MINIMAL_PROMPT = (
    LEGEND_JSON_SYSTEM_PROMPT.split("Critical rules:")[0] + _MINIMAL_RULE
)
FIGURE_CHUNK_MINIMAL_PROMPT = (
    FIGURE_CHUNK_SYSTEM_PROMPT.split("Critical disambiguation:")[0] + _MINIMAL_RULE
)

PROMPT_VARIANTS = ("full", "minimal")


def _check_variant(variant: str) -> None:
    if variant not in PROMPT_VARIANTS:
        raise ValueError(f"unknown prompt variant: {variant!r}")


def build_legend_llm_prompt(
    legend_text: str,
    figure_hint: str | None = None,
    *,
    variant: str = "full",
) -> str:
    _check_variant(variant)
    hint = figure_hint or "unknown"
    system = LEGEND_JSON_SYSTEM_PROMPT if variant == "full" else LEGEND_JSON_MINIMAL_PROMPT
    return (
        f"{system}\n\n"
        f"Figure hint: {hint}\n\n"
        f"Legend text:\n{legend_text}\n"
    )


def build_figure_chunk_llm_prompt(
    chunk_text: str,
    *,
    figure_hint: str | None = None,
    variant: str = "full",
) -> str:
    _check_variant(variant)
    hint = figure_hint or "unknown"
    system = FIGURE_CHUNK_SYSTEM_PROMPT if variant == "full" else FIGURE_CHUNK_MINIMAL_PROMPT
    return (
        f"{system}\n\n"
        f"Target figure: {hint}\n\n"
        f"{chunk_text}\n"
    )


def validate_legend_dict(data: dict[str, Any]) -> list[str]:
    """Lightweight schema checks (no jsonschema dependency). Returns error strings."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["root must be object"]
    for key in ("figure", "panels", "tests", "p_values", "citation"):
        if key not in data:
            errors.append(f"missing:{key}")
    if "panels" in data and not isinstance(data["panels"], list):
        errors.append("panels must be array")
    cit = data.get("citation")
    if isinstance(cit, dict) and "mentioned" not in cit:
        errors.append("citation.mentioned required")
    return errors


_PANEL_ID_RE = re.compile(r"^[A-Za-z]\d?$")
_N_SCOPE_OK = {"unknown", "per_group", "per_genotype", "total", "shared", "per_panel"}


def coerce_legend_dict(data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Schema-force: fill defaults, coerce types, drop invalid panel rows.

    Returns ``(coerced, soft_errors)``. Soft errors do not reject the whole object
    when at least the root shape is valid — invalid panels are skipped.
    """
    soft: list[str] = []
    if not isinstance(data, dict):
        return {}, ["root must be object"]
    out: dict[str, Any] = dict(data)
    out.setdefault("figure", "")
    out.setdefault("panels", [])
    out.setdefault("tests", [])
    out.setdefault("p_values", [])
    out.setdefault("citation", {"mentioned": False, "reproduced_from": None, "spans": []})
    out.setdefault(
        "independence_claims", {"mentioned": False, "kind": None, "spans": []}
    )
    out.setdefault("exclusion_criteria", {"mentioned": False, "spans": []})
    out.setdefault("error_bar_type", None)
    out.setdefault("raw_excerpt", "")
    out.setdefault("extractor", "llm")

    if not isinstance(out["panels"], list):
        soft.append("panels reset to []")
        out["panels"] = []
    if not isinstance(out["tests"], list):
        out["tests"] = []
    if not isinstance(out["p_values"], list):
        out["p_values"] = []

    cit = out.get("citation")
    if not isinstance(cit, dict):
        cit = {}
        soft.append("citation reset")
    cit.setdefault("mentioned", False)
    cit.setdefault("reproduced_from", None)
    cit.setdefault("spans", [])
    if not isinstance(cit.get("spans"), list):
        cit["spans"] = []
    cit["mentioned"] = bool(cit.get("mentioned"))
    out["citation"] = cit

    indep = out.get("independence_claims")
    if not isinstance(indep, dict):
        indep = {}
        soft.append("independence_claims reset")
    indep.setdefault("mentioned", False)
    indep.setdefault("kind", None)
    indep.setdefault("spans", [])
    if not isinstance(indep.get("spans"), list):
        indep["spans"] = []
    indep["mentioned"] = bool(indep.get("mentioned"))
    kind = indep.get("kind")
    if kind is not None:
        kind_s = str(kind).strip().lower().replace("-", "_").replace(" ", "_")
        if kind_s in {"shared", "shared_controls"}:
            kind_s = "shared_control"
        indep["kind"] = kind_s if kind_s in _INDEP_KIND_OK else "unknown"
        if indep["kind"]:
            indep["mentioned"] = True
    else:
        indep["kind"] = None
    indep["spans"] = [str(s)[:160] for s in indep["spans"] if s is not None][:8]
    out["independence_claims"] = indep

    excl = out.get("exclusion_criteria")
    if not isinstance(excl, dict):
        excl = {}
        soft.append("exclusion_criteria reset")
    excl.setdefault("mentioned", False)
    excl.setdefault("spans", [])
    if not isinstance(excl.get("spans"), list):
        excl["spans"] = []
    excl["mentioned"] = bool(excl.get("mentioned"))
    excl["spans"] = [str(s)[:160] for s in excl["spans"] if s is not None][:8]
    if excl["spans"]:
        excl["mentioned"] = True
    out["exclusion_criteria"] = excl

    ebt = out.get("error_bar_type")
    if ebt is None or ebt == "":
        out["error_bar_type"] = None
    else:
        ebt_s = str(ebt).strip().lower().replace(".", "")
        aliases = {
            "s": "sd",
            "stderr": "sem",
            "standard_error": "sem",
            "standard_deviation": "sd",
            "confidence_interval": "ci",
        }
        ebt_s = aliases.get(ebt_s, ebt_s)
        if ebt_s in _ERROR_BAR_OK:
            out["error_bar_type"] = ebt_s
        else:
            soft.append(f"error_bar_type coerced unknown:{ebt!r}")
            out["error_bar_type"] = "unknown"

    coerced_panels: list[dict[str, Any]] = []
    for i, p in enumerate(out["panels"]):
        if not isinstance(p, dict):
            soft.append(f"panels[{i}] skipped: not object")
            continue
        panel_raw = str(p.get("panel") or "").strip().upper()
        if not _PANEL_ID_RE.match(panel_raw):
            soft.append(f"panels[{i}] skipped: bad panel id {panel_raw!r}")
            continue
        n_val: int | None
        try:
            n_raw = p.get("n")
            n_val = int(n_raw) if n_raw is not None and n_raw != "" else None
        except (TypeError, ValueError):
            soft.append(f"panels[{i}] n coerced to null")
            n_val = None
        if n_val is not None and n_val < 0:
            soft.append(f"panels[{i}] negative n dropped")
            n_val = None
        groups = p.get("groups") or []
        if not isinstance(groups, list):
            groups = [str(groups)]
            soft.append(f"panels[{i}] groups wrapped")
        groups = [str(g) for g in groups if str(g).strip()]
        conf: float | None
        try:
            c_raw = p.get("confidence")
            conf = float(c_raw) if c_raw is not None and c_raw != "" else None
        except (TypeError, ValueError):
            conf = None
        if conf is not None:
            conf = max(0.0, min(1.0, conf))
        n_scope = str(p.get("n_scope") or "unknown").lower()
        if n_scope not in _N_SCOPE_OK:
            n_scope = "unknown"
        coerced_panels.append(
            {
                "panel": panel_raw,
                "n": n_val,
                "groups": groups,
                "notes": str(p.get("notes") or "")[:240],
                "n_scope": n_scope,
                "evidence_span": str(p.get("evidence_span") or "")[:240],
                "confidence": conf,
            }
        )
    out["panels"] = coerced_panels
    out["figure"] = str(out.get("figure") or "")
    out["tests"] = [str(t) for t in out["tests"] if t is not None]
    pvals: list[float] = []
    for pv in out["p_values"]:
        try:
            pvals.append(float(pv))
        except (TypeError, ValueError):
            soft.append("p_value skipped")
    out["p_values"] = pvals
    out["extractor"] = str(out.get("extractor") or "llm")
    out["raw_excerpt"] = str(out.get("raw_excerpt") or "")[:500]
    return out, soft


def parse_legend_llm_response(raw: str) -> LegendFigureJSON | None:
    """Parse model output with schema coerce (stdlib; no free-form keep)."""
    data = _loads_legend_json(raw)
    if data is None:
        return None
    coerced, _soft = coerce_legend_dict(data)
    if validate_legend_dict(coerced):
        return None
    panels = [
        LegendPanelJSON(
            panel=str(p.get("panel") or "").upper(),
            n=p.get("n") if isinstance(p.get("n"), int) else None,
            groups=list(p.get("groups") or []),
            notes=str(p.get("notes") or ""),
            n_scope=str(p.get("n_scope") or "unknown"),
            evidence_span=str(p.get("evidence_span") or ""),
            confidence=(
                float(p["confidence"])
                if isinstance(p.get("confidence"), (int, float))
                else None
            ),
        )
        for p in coerced.get("panels") or []
        if isinstance(p, dict)
    ]
    cit_raw = coerced.get("citation") or {}
    citation = LegendCitation(
        mentioned=bool(cit_raw.get("mentioned")),
        reproduced_from=cit_raw.get("reproduced_from"),
        spans=list(cit_raw.get("spans") or []),
    )
    indep_raw = coerced.get("independence_claims") or {}
    independence = IndependenceClaims(
        mentioned=bool(indep_raw.get("mentioned")),
        kind=indep_raw.get("kind"),
        spans=list(indep_raw.get("spans") or []),
    )
    excl_raw = coerced.get("exclusion_criteria") or {}
    exclusion = ExclusionCriteria(
        mentioned=bool(excl_raw.get("mentioned")),
        spans=list(excl_raw.get("spans") or []),
    )
    ebt = coerced.get("error_bar_type")
    return LegendFigureJSON(
        figure=str(coerced.get("figure") or ""),
        panels=panels,
        tests=list(coerced.get("tests") or []),
        p_values=list(coerced.get("p_values") or []),
        citation=citation,
        error_bar_type=str(ebt) if ebt else None,
        independence_claims=independence,
        exclusion_criteria=exclusion,
        raw_excerpt=str(coerced.get("raw_excerpt") or ""),
        extractor=str(coerced.get("extractor") or "llm"),
    )


def _loads_legend_json(text: str) -> dict[str, Any] | None:
    """Parse JSON object from model text; tolerate fences and mild truncation."""
    text = (text or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    candidates = [text]
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        candidates.append(m.group(0))
    # Truncated object: close open braces/brackets after last complete panel.
    if "{" in text and not text.rstrip().endswith("}"):
        trimmed = text[text.find("{") :]
        for cut in (trimmed.rfind("},"), trimmed.rfind("}]"), trimmed.rfind("}")):
            if cut > 0:
                frag = trimmed[: cut + 1]
                opens = frag.count("{") - frag.count("}")
                bracks = frag.count("[") - frag.count("]")
                candidates.append(frag + ("]" * max(0, bracks)) + ("}" * max(0, opens)))
                break
    for cand in candidates:
        try:
            data = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None
