"""Clean-corpus false-positive measurement.

Published open-access papers with no known issue are run through the full pipeline.
Any active (non-demoted) warning on them is a false-positive candidate. A paper with
zero warnings is unrealistic, so the gate is relative: the per-``pattern_id`` count of
unexplained active warnings must not exceed the frozen baseline.

``expected.json`` lists warnings a reviewer judged legitimate (abstract reason only).
"""

from __future__ import annotations

import json
import os
import subprocess
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pre_peer_checker.eval.gold_eval import FIXTURES, REPO_ROOT

CLEAN_DIR = FIXTURES / "gold" / "clean"
BASELINE_PATH = CLEAN_DIR / "baseline.json"
EXPECTED_PATH = CLEAN_DIR / "expected.json"
DEFAULT_ROOT = REPO_ROOT / "input" / "panel_extract"

_MANUSCRIPT_SUFFIXES = {".pdf", ".docx"}
# eval-only artefacts next to the manuscript (review PNGs duplicate the figures)
_SKIP_DIRS = {"review", "gold_review", "_cache"}
_FAST_ENV = {
    "PRE_PEER_CHECKER_RASTER_PANEL_OCR": "0",
    "PRE_PEER_CHECKER_SCALE_BAR_OCR": "0",
}


@dataclass
class CleanCase:
    case_id: str
    inputs: list[Path]


def discover_cases(root: Path = DEFAULT_ROOT, only: list[str] | None = None) -> list[CleanCase]:
    cases: list[CleanCase] = []
    if not root.is_dir():
        return cases
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        if only and d.name not in only:
            continue
        if d.name.startswith(("_", ".")) or d.name in _SKIP_DIRS:
            continue
        inputs = [
            p for p in sorted(d.iterdir()) if p.is_file() and p.suffix.lower() in _MANUSCRIPT_SUFFIXES
        ]
        if not inputs:
            continue
        figs = d / "figures"
        if figs.is_dir():
            inputs.append(figs)
        cases.append(CleanCase(d.name, inputs))
    return cases


def pattern_counts(warnings: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """``{pattern_id: {"active": n, "demoted": m}}``."""
    out: dict[str, dict[str, int]] = {}
    for w in warnings:
        meta = w.get("metadata") or {}
        pid = str(meta.get("pattern_id") or w.get("pattern_id") or "unknown")
        row = out.setdefault(pid, {"active": 0, "demoted": 0})
        row["demoted" if w.get("demoted") else "active"] += 1
    return out


def run_case(
    case: CleanCase,
    *,
    out_dir: Path,
    legend_llm: bool | str = False,
    legend_llm_profile: str | None = None,
    fast: bool = True,
) -> dict[str, Any]:
    from pre_peer_checker.pipeline.orchestrator import run_verification

    saved = {k: os.environ.get(k) for k in _FAST_ENV}
    if fast:
        os.environ.update(_FAST_ENV)
    try:
        result = run_verification(
            [str(p) for p in case.inputs],
            legend_llm=legend_llm,
            legend_llm_profile=legend_llm_profile,
        )
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    warnings = [w.to_dict() for w in result.warnings]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{case.case_id}_warnings.json").write_text(
        json.dumps({"case_id": case.case_id, "warnings": warnings}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"case_id": case.case_id, "counts": pattern_counts(warnings), "n_warnings": len(warnings)}


def load_cached(out_dir: Path, case_id: str) -> dict[str, Any] | None:
    path = out_dir / f"{case_id}_warnings.json"
    if not path.is_file():
        return None
    warnings = json.loads(path.read_text(encoding="utf-8")).get("warnings") or []
    return {"case_id": case_id, "counts": pattern_counts(warnings), "n_warnings": len(warnings)}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def unexplained(counts: dict[str, dict[str, int]], expected: dict[str, Any]) -> dict[str, int]:
    """Active warnings per pattern minus the reviewer-accepted ones."""
    out: dict[str, int] = {}
    for pid, row in counts.items():
        ok = int((expected.get(pid) or {}).get("count", 0))
        left = row["active"] - ok
        if left > 0:
            out[pid] = left
    return out


def summarize(
    case_reports: list[dict[str, Any]],
    *,
    expected: dict[str, Any] | None = None,
) -> dict[str, Any]:
    expected = expected if expected is not None else _load_json(EXPECTED_PATH).get("cases", {})
    per_case: dict[str, dict[str, int]] = {}
    totals: Counter[str] = Counter()
    demoted: Counter[str] = Counter()
    for rep in case_reports:
        cid = rep["case_id"]
        u = unexplained(rep["counts"], expected.get(cid) or {})
        per_case[cid] = u
        totals.update(u)
        for pid, row in rep["counts"].items():
            demoted[pid] += row["demoted"]
    n = len(case_reports)
    return {
        "n_cases": n,
        "unexplained_by_case": per_case,
        "unexplained_by_pattern": dict(sorted(totals.items())),
        "demoted_by_pattern": dict(sorted((k, v) for k, v in demoted.items() if v)),
        "fp_per_paper": round(sum(totals.values()) / n, 3) if n else None,
        "papers_with_fp": sum(1 for u in per_case.values() if u),
    }


def compare_to_baseline(summary: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    """Regression = a case's unexplained count for a pattern rose above the frozen value."""
    base_cases = (baseline.get("summary") or {}).get("unexplained_by_case") or {}
    regressions: list[dict[str, Any]] = []
    improvements: list[dict[str, Any]] = []
    for cid, now in summary["unexplained_by_case"].items():
        if cid not in base_cases:
            continue
        before = base_cases[cid]
        for pid in sorted(set(now) | set(before)):
            a, b = int(before.get(pid, 0)), int(now.get(pid, 0))
            if b > a:
                regressions.append({"case_id": cid, "pattern_id": pid, "baseline": a, "now": b})
            elif b < a:
                improvements.append({"case_id": cid, "pattern_id": pid, "baseline": a, "now": b})
    return {"regressions": regressions, "improvements": improvements, "ok": not regressions}


def git_commit() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def freeze(summary: dict[str, Any], *, config: dict[str, Any], path: Path = BASELINE_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "commit": git_commit(),
        "config": config,
        "summary": summary,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
