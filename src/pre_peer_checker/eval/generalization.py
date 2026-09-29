"""dev / holdout 分離の汎化評価（Legend→(figure, panel, group, n) とパネル文字 OCR）。

holdout の規約（ツール出力に合わせて gold やルールを寄せないための仕組み）:

- gold はツールを動かす前に人が確定する（``review.status=confirmed``。Legend は
  ``coverage=exhaustive`` で Figure 内の n を全件）。
- 初回の採点で gold の sha256 を manifest に凍結する。以後の変更は理由付きでのみ通す。
- レポートは holdout を集計値だけで返す。個別の誤りを見る（reveal）と、そのケースは
  dev へ移る（burn）。
- 変更の採否は dev 非悪化 + holdout 許容幅内（``gate_check``）で決め、各ガード／
  プロンプト規則の holdout 寄与は ``fixtures/gold/rule_ledger.json`` に残す。

手順書: fixtures/gold/panel_extract/HUMAN_REVIEW.md「holdout（汎化評価）」
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import random
import subprocess
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pre_peer_checker.eval.panel_extract_score import score_one
from pre_peer_checker.llm.legend_extract import ALL_GUARDS

REPO_ROOT = Path(__file__).resolve().parents[3]
GOLD_ROOT = REPO_ROOT / "fixtures" / "gold" / "panel_extract"
INPUT_ROOT = REPO_ROOT / "input" / "panel_extract"
OUTPUT_ROOT = REPO_ROOT / "outputs" / "generalization"
HISTORY_PATH = OUTPUT_ROOT / "history.jsonl"
LEDGER_PATH = REPO_ROOT / "fixtures" / "gold" / "rule_ledger.json"

TASKS = ("legend", "panel_ocr")
SPLITS = ("dev", "holdout")
_PLACEHOLDER = "REPLACE_WITH_SLOT_ID"
_GOLD_FILES = {"legend": "panel_extract_gold.json", "panel_ocr": "panel_labels_gold.json"}
_FROZEN_KEYS = {"legend": "legend_sha256", "panel_ocr": "panel_labels_sha256"}
_EPS = 1e-9


class GoldPolicyError(RuntimeError):
    """Holdout gold is missing, unconfirmed, or not exhaustive."""


class GoldFrozenError(RuntimeError):
    """Holdout gold changed after it was frozen."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def figure_key(figure: str) -> str:
    from pre_peer_checker.parsers.figure_chunks import figure_num_key

    return figure_num_key(str(figure or ""))


# --------------------------------------------------------------------------- cases


@dataclass
class Case:
    case_id: str
    gold_dir: Path
    input_dir: Path
    manifest: dict[str, Any]

    @property
    def manifest_path(self) -> Path:
        return self.gold_dir / "case_manifest.json"

    @property
    def split(self) -> str:
        s = str(self.manifest.get("split") or "dev")
        return s if s in SPLITS else "dev"

    @property
    def style_tags(self) -> dict[str, str]:
        tags = self.manifest.get("style_tags") or {}
        return {str(k): str(v) for k, v in tags.items()}

    @property
    def figures_in_scope(self) -> set[str] | None:
        figs = self.manifest.get("figures_in_scope")
        return {str(f).upper() for f in figs} if figs else None

    def gold_path(self, task: str) -> Path:
        return self.gold_dir / _GOLD_FILES[task]

    def load_gold(self, task: str) -> dict[str, Any] | None:
        p = self.gold_path(task)
        if not p.is_file():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def legend_source(self) -> tuple[str, Path] | None:
        """Published PDF > Word manuscript > legend_excerpt.txt (all under input/)."""
        d = self.input_dir
        if not d.is_dir():
            return None
        pdfs = sorted(p for p in d.glob("*.pdf") if p.is_file())
        if len(pdfs) == 1:
            return "pdf", pdfs[0]
        if len(pdfs) > 1:
            raise GoldPolicyError(f"{self.case_id}: expected one PDF under {d}, found {len(pdfs)}")
        docx = sorted(p for p in d.glob("*.docx") if p.is_file() and not p.name.startswith("~$"))
        if len(docx) == 1:
            return "docx", docx[0]
        excerpt = d / "legend_excerpt.txt"
        if excerpt.is_file():
            return "excerpt", excerpt
        return None

    def figure_files(self) -> dict[str, Path]:
        """``input/<case>/figures/FigN.*`` — PDF preferred over raster for the same figure."""
        from pre_peer_checker.parsers.figure_panel_labels import looks_like_figure_filename

        d = self.input_dir / "figures"
        out: dict[str, Path] = {}
        if not d.is_dir():
            return out
        for p in sorted(d.iterdir()):
            if not p.is_file() or not looks_like_figure_filename(p):
                continue
            if p.suffix.lower() not in {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
                continue
            key = figure_key(p.stem)
            if key in out and out[key].suffix.lower() == ".pdf":
                continue
            out[key] = p
        return out

    def save_manifest(self) -> None:
        self.manifest_path.write_text(
            json.dumps(self.manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


def load_case(gold_dir: Path, input_root: Path | None = None) -> Case | None:
    gold_dir = Path(gold_dir)
    input_root = Path(input_root) if input_root is not None else INPUT_ROOT
    if gold_dir.name.startswith("_") or not gold_dir.is_dir():
        return None
    manifest: dict[str, Any] | None = None
    for name in ("case_manifest.json", "case_manifest.example.json"):
        p = gold_dir / name
        if p.is_file():
            manifest = json.loads(p.read_text(encoding="utf-8"))
            break
    if manifest is None:
        return None
    cid = str(manifest.get("case_id") or gold_dir.name)
    if cid == _PLACEHOLDER:
        return None
    return Case(case_id=cid, gold_dir=gold_dir, input_dir=input_root / cid, manifest=manifest)


def discover_cases(
    *,
    gold_root: Path | None = None,
    input_root: Path | None = None,
    split: str | None = None,
    case_ids: Iterable[str] | None = None,
) -> list[Case]:
    wanted = set(case_ids or [])
    root = Path(gold_root) if gold_root is not None else GOLD_ROOT
    found = [c for d in sorted(root.iterdir()) if (c := load_case(d, input_root))]
    missing = wanted - {c.case_id for c in found}
    if missing:
        raise ValueError(f"unknown case ids: {sorted(missing)}")
    return [
        c
        for c in found
        if (not wanted or c.case_id in wanted)
        and (not split or split == "all" or c.split == split)
    ]


def cases_with_gold(cases: Iterable[Case], task: str) -> list[Case]:
    return [c for c in cases if c.gold_path(task).is_file()]


# --------------------------------------------------------------------------- holdout policy


def check_holdout_gold(case: Case, task: str) -> None:
    gold = case.load_gold(task)
    if gold is None:
        raise GoldPolicyError(f"{case.case_id}: holdout gold missing ({case.gold_path(task).name})")
    status = (gold.get("review") or {}).get("status")
    if status != "confirmed":
        raise GoldPolicyError(
            f"{case.case_id}: holdout gold review.status={status!r}; confirm it before scoring"
        )
    if task == "legend" and gold.get("coverage") != "exhaustive":
        raise GoldPolicyError(
            f"{case.case_id}: holdout legend gold must be coverage=exhaustive (every n in scope)"
        )


def ensure_frozen(case: Case, task: str, *, revise_reason: str | None = None) -> str:
    """Freeze holdout gold on first score; refuse silent edits afterwards.

    Returns ``dev`` | ``frozen`` (first time) | ``ok`` | ``revised``.
    """
    if case.split != "holdout":
        return "dev"
    check_holdout_gold(case, task)
    digest = sha256_file(case.gold_path(task))
    frozen = dict(case.manifest.get("frozen") or {})
    key = _FROZEN_KEYS[task]
    old = frozen.get(key)
    if old == digest:
        return "ok"
    if old is None:
        frozen[key] = digest
        frozen["frozen_at"] = frozen.get("frozen_at") or _now()
        case.manifest["frozen"] = frozen
        case.save_manifest()
        return "frozen"
    if not revise_reason:
        raise GoldFrozenError(
            f"{case.case_id}: {case.gold_path(task).name} changed after freeze; "
            "pass --revise-gold '<reason>' if the correction is independent of tool output"
        )
    revisions = list(case.manifest.get("gold_revisions") or [])
    revisions.append(
        {"task": task, "at": _now(), "reason": revise_reason, "old_sha256": old, "new_sha256": digest}
    )
    frozen[key] = digest
    case.manifest["frozen"] = frozen
    case.manifest["gold_revisions"] = revisions
    case.save_manifest()
    return "revised"


def burn_case(case: Case, reason: str) -> None:
    """Move a holdout case to dev once its individual errors have been seen."""
    case.manifest["split"] = "dev"
    case.manifest["burned_at"] = _now()
    case.manifest["burned_reason"] = reason
    case.save_manifest()


# --------------------------------------------------------------------------- legend task


@dataclass(frozen=True)
class LegendConfig:
    name: str
    use_llm: bool
    prompt: str = "full"
    guards: frozenset[str] = ALL_GUARDS


LEGEND_BASE_CONFIGS = ("rules_only", "llm_minimal", "prompt_minimal", "current")
_MINUS = "current-minus-"


def legend_ablation_configs() -> list[str]:
    return [f"{_MINUS}{g}" for g in sorted(ALL_GUARDS)]


def legend_config(name: str) -> LegendConfig:
    if name == "rules_only":
        return LegendConfig(name, use_llm=False)
    if name == "llm_minimal":
        return LegendConfig(name, use_llm=True, prompt="minimal", guards=frozenset({"grounding"}))
    if name == "prompt_minimal":
        return LegendConfig(name, use_llm=True, prompt="minimal")
    if name == "current":
        return LegendConfig(name, use_llm=True)
    if name.startswith(_MINUS):
        g = name[len(_MINUS):]
        if g not in ALL_GUARDS:
            raise ValueError(f"unknown guard in config {name!r}; known: {sorted(ALL_GUARDS)}")
        return LegendConfig(name, use_llm=True, guards=ALL_GUARDS - {g})
    raise ValueError(f"unknown legend config {name!r}")


def legend_captions(case: Case) -> list[tuple[str, str]]:
    """(``Figure N``, caption text) for figures in scope."""
    from pre_peer_checker.parsers.legend_struct import (
        extract_figure_captions_from_pdf,
        extract_figure_captions_from_pdf_text,
        extract_structured_legends,
    )

    scope = case.figures_in_scope
    src = case.legend_source()
    if src is None:
        return []
    kind, path = src
    if kind == "pdf":
        pairs = extract_figure_captions_from_pdf(path, keep=scope)
    elif kind == "excerpt":
        pairs = extract_figure_captions_from_pdf_text(
            path.read_text(encoding="utf-8", errors="replace"), keep=scope
        )
    else:
        pairs = [(leg.figure, leg.text) for leg in extract_structured_legends(path)]
    out: list[tuple[str, str]] = []
    for fig, text in pairs:
        key = figure_key(fig)
        if scope and key not in scope:
            continue
        out.append((f"Figure {key}", text))
    return out


def legend_predictions(
    case: Case,
    cfg: LegendConfig,
    llm_generate: Callable[[str], str] | None = None,
) -> list[dict[str, Any]]:
    from pre_peer_checker.llm.legend_extract import extract_check_items_from_chunk
    from pre_peer_checker.parsers.figure_chunks import FigureChunk
    from pre_peer_checker.parsers.legend_struct import parse_panel_ns

    rows: list[dict[str, Any]] = []
    for fig, text in legend_captions(case):
        if not cfg.use_llm:
            for pn in parse_panel_ns(fig, text):
                rows.append(_legend_row(fig, pn.panel, pn.group or "", pn.n))
            continue
        if llm_generate is None:
            raise RuntimeError(f"config {cfg.name!r} needs an LLM backend")
        chunk = FigureChunk(figure_id=fig, figure_num=figure_key(fig), legend=text)
        item = extract_check_items_from_chunk(
            chunk,
            llm_generate=llm_generate,
            prefer_llm=True,
            legend_only_prompt=True,
            guards=cfg.guards,
            prompt_variant=cfg.prompt,
        )
        for p in item.panels:
            if p.n is None:
                continue
            rows.append(_legend_row(fig, p.panel, p.groups[0] if p.groups else "", p.n))
    return rows


def _legend_row(fig: str, panel: str, group: str, n: Any) -> dict[str, Any]:
    return {"figure": fig, "panel": str(panel).upper(), "group": str(group or ""), "n": int(n)}


def score_legend_case(case: Case, preds: list[dict[str, Any]]) -> dict[str, Any]:
    gold = case.load_gold("legend") or {}
    items = [
        it for it in (gold.get("items") or []) if it.get("review_status", "confirmed") == "confirmed"
    ]
    sc = score_one({**gold, "items": items}, preds)
    sc["exhaustive"] = gold.get("coverage") == "exhaustive"
    if not sc["exhaustive"]:
        # hard-span gold omits easy n, so unmatched predictions are not errors.
        sc["precision"] = None
        sc["f1"] = None
    return sc


# --------------------------------------------------------------------------- panel OCR task

_ENGINES = "PRE_PEER_CHECKER_RASTER_OCR_ENGINES"
_RUN_FILTER = "PRE_PEER_CHECKER_PANEL_RUN_FILTER"
OCR_CONFIGS: dict[str, dict[str, str]] = {
    "current": {},
    "vision": {_ENGINES: "vision"},
    "florence": {_ENGINES: "florence"},
    "union": {_ENGINES: "vision florence"},
    "union_no_run_filter": {_ENGINES: "vision florence", _RUN_FILTER: "0"},
}


@contextlib.contextmanager
def _env(overrides: dict[str, str]) -> Iterator[None]:
    saved = {k: os.environ.get(k) for k in overrides}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def ocr_predictions(
    case: Case,
    config: str,
    collect_fn: Callable[[list[Path]], tuple[dict[str, list[str]], Any]] | None = None,
) -> dict[str, set[str]]:
    """Panel letters per figure key via the product path (vector, then raster OCR)."""
    if config not in OCR_CONFIGS:
        raise ValueError(f"unknown panel_ocr config {config!r}; known: {sorted(OCR_CONFIGS)}")
    if collect_fn is None:
        from pre_peer_checker.parsers.figure_panel_labels import (
            collect_panel_labels_by_figure_detailed as collect_fn,
        )
    gold = case.load_gold("panel_ocr") or {}
    wanted = {figure_key(f.get("figure", "")) for f in gold.get("figures") or []}
    files = [p for k, p in case.figure_files().items() if not wanted or k in wanted]
    if not files:
        return {}
    with _env(OCR_CONFIGS[config]):
        by_fig, _meta = collect_fn(files)
    return {k: {str(c).upper() for c in v} for k, v in by_fig.items()}


def score_ocr_case(case: Case, preds: dict[str, set[str]]) -> dict[str, Any]:
    gold = case.load_gold("panel_ocr") or {}
    n_items = n_hit = n_pred = 0
    details: list[dict[str, Any]] = []
    extras: list[dict[str, Any]] = []
    for f in gold.get("figures") or []:
        key = figure_key(f.get("figure", ""))
        gt = {str(c).upper() for c in f.get("panels") or []}
        found = preds.get(key, set())
        hit = gt & found
        n_items += len(gt)
        n_hit += len(hit)
        n_pred += len(found)
        missed = sorted(gt - found)
        extra = sorted(found - gt)
        details.append(
            {"figure": f"Figure {key}", "n_gt": len(gt), "n_hit": len(hit), "missed": missed, "extra": extra}
        )
        if extra:
            extras.append({"figure": f"Figure {key}", "letters": extra})
    return {
        "n_items": n_items,
        "n_hit": n_hit,
        "recall": (n_hit / n_items) if n_items else None,
        "n_pred": n_pred,
        "n_pred_hit": n_hit,
        "precision": (n_hit / n_pred) if n_pred else None,
        "n_extra": n_pred - n_hit,
        "exhaustive": True,
        "false_positives": extras,
        "items": details,
    }


# --------------------------------------------------------------------------- configs


def expand_configs(task: str, spec: str | None) -> list[str]:
    """Comma list; keywords ``default`` / ``ablation`` / ``all``."""
    tokens = [t.strip() for t in (spec or "default").split(",") if t.strip()]
    out: list[str] = []
    for t in tokens:
        if task == "legend":
            if t == "default":
                names = list(LEGEND_BASE_CONFIGS)
            elif t == "ablation":
                names = ["current", *legend_ablation_configs()]
            elif t == "all":
                names = [*LEGEND_BASE_CONFIGS, *legend_ablation_configs()]
            else:
                legend_config(t)
                names = [t]
        else:
            if t in {"default", "all"}:
                names = list(OCR_CONFIGS)
            elif t == "ablation":
                names = ["union", "union_no_run_filter"]
            elif t in OCR_CONFIGS:
                names = [t]
            else:
                raise ValueError(f"unknown panel_ocr config {t!r}; known: {sorted(OCR_CONFIGS)}")
        for n in names:
            if n not in out:
                out.append(n)
    return out


# --------------------------------------------------------------------------- LLM


def make_llm_generate(
    *,
    prefer: str = "auto",
    profile_id: str | None = None,
    max_tokens: int = 1024,
    cache_dir: Path | None = None,
) -> Callable[[str], str] | None:
    """Product-path generator (schema-forced JSON) with a prompt-keyed response cache.

    Ablations that share a prompt (every ``current-minus-*``) reuse one model call.
    """
    from pre_peer_checker.llm.backend import select_backend
    from pre_peer_checker.llm.json_mode import structured_legend_generate

    backend = select_backend(prefer, profile_id=profile_id)
    if backend is None:
        return None
    info = backend.info()
    tag = f"{info.name}:{info.model_id}:{max_tokens}"
    mem: dict[str, str] = {}
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)

    def gen(prompt: str) -> str:
        key = hashlib.sha256(f"{tag}\0{prompt}".encode()).hexdigest()
        if key in mem:
            return mem[key]
        path = cache_dir / f"{key}.txt" if cache_dir is not None else None
        if path is not None and path.is_file():
            text = path.read_text(encoding="utf-8")
        else:
            text, _meta = structured_legend_generate(backend, prompt, max_tokens=max_tokens)
            if path is not None:
                path.write_text(text, encoding="utf-8")
        mem[key] = text
        return text

    gen.backend_info = dict(info.__dict__)  # type: ignore[attr-defined]
    return gen


# --------------------------------------------------------------------------- run + report


def _git_rev() -> str | None:
    try:
        rev = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=REPO_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        return f"{rev}{'-dirty' if dirty else ''}"
    except (OSError, subprocess.CalledProcessError):
        return None


def _aggregate(scores: list[dict[str, Any]]) -> dict[str, Any]:
    n_items = sum(s["n_items"] for s in scores)
    n_hit = sum(s["n_hit"] for s in scores)
    ex = [s for s in scores if s.get("exhaustive")]
    n_pred = sum(s["n_pred"] for s in ex)
    n_pred_hit = sum(s["n_pred_hit"] for s in ex)
    out: dict[str, Any] = {
        "n_cases": len(scores),
        "n_items": n_items,
        "n_hit": n_hit,
        "recall": (n_hit / n_items) if n_items else None,
        "n_cases_precision": len(ex),
        "n_pred": n_pred,
        "n_pred_hit": n_pred_hit,
        "precision": (n_pred_hit / n_pred) if n_pred else None,
    }
    if any("forbid_panel_fp" in s for s in scores):
        out["forbid_panel_fp"] = sum(int(s.get("forbid_panel_fp") or 0) for s in scores)
    return out


def _bootstrap_recall(
    scores: list[dict[str, Any]], *, iters: int, seed: int
) -> list[float] | None:
    """95% interval of micro recall, resampling whole papers (items within a paper correlate)."""
    pairs = [(s["n_hit"], s["n_items"]) for s in scores if s["n_items"]]
    if len(pairs) < 2 or iters <= 0:
        return None
    rng = random.Random(seed)
    vals: list[float] = []
    for _ in range(iters):
        sample = [rng.choice(pairs) for _ in pairs]
        vals.append(sum(h for h, _ in sample) / sum(n for _, n in sample))
    vals.sort()
    return [round(vals[int(0.025 * iters)], 4), round(vals[max(int(0.975 * iters) - 1, 0)], 4)]


_REDACT_KEEP = (
    "n_items",
    "n_hit",
    "recall",
    "n_pred",
    "n_pred_hit",
    "precision",
    "f1",
    "exhaustive",
    "forbid_panel_fp",
    "n_extra",
)


def _case_view(sc: dict[str, Any], *, redact: bool) -> dict[str, Any]:
    view = {k: sc[k] for k in _REDACT_KEEP if k in sc}
    if redact:
        view["redacted"] = True
        return view
    items = sc.get("items") or []
    if items and "matched" in items[0]:
        view["misses"] = [
            {"id": d.get("id"), "gold": d.get("gold"), "difficulty": d.get("difficulty")}
            for d in items
            if not d.get("matched")
        ]
        by_diff: dict[str, dict[str, int]] = {}
        for d in items:
            slot = by_diff.setdefault(str(d.get("difficulty") or "other"), {"hit": 0, "n": 0})
            slot["n"] += 1
            slot["hit"] += int(bool(d.get("matched")))
        view["by_difficulty"] = by_diff
    else:
        view["figures"] = items
    view["false_positives"] = sc.get("false_positives") or []
    return view


def build_report(
    task: str,
    cases: list[Case],
    per_config: dict[str, dict[str, dict[str, Any]]],
    *,
    reveal: set[str],
    freeze_status: dict[str, str],
    llm_info: dict[str, Any] | None = None,
    bootstrap_iters: int = 1000,
    seed: int = 0,
) -> dict[str, Any]:
    split_of = {c.case_id: c.split for c in cases}
    results: dict[str, Any] = {}
    for name, per_case in per_config.items():
        splits: dict[str, Any] = {}
        for s in SPLITS:
            scores = [per_case[c.case_id] for c in cases if c.split == s]
            if not scores:
                continue
            agg = _aggregate(scores)
            agg["recall_ci95"] = _bootstrap_recall(scores, iters=bootstrap_iters, seed=seed)
            splits[s] = agg
        by_style: dict[str, Any] = {}
        for c in cases:
            for k, v in c.style_tags.items():
                by_style.setdefault(f"{c.split}:{k}={v}", []).append(per_case[c.case_id])
        dev_r = (splits.get("dev") or {}).get("recall")
        ho_r = (splits.get("holdout") or {}).get("recall")
        results[name] = {
            "splits": splits,
            "gap_recall_dev_minus_holdout": (dev_r - ho_r) if dev_r is not None and ho_r is not None else None,
            "by_style": {k: _aggregate(v) for k, v in sorted(by_style.items())},
            "cases": {
                cid: _case_view(sc, redact=split_of[cid] == "holdout" and cid not in reveal)
                for cid, sc in per_case.items()
            },
        }
    return {
        "schema_version": "1.0",
        "task": task,
        "created_at": _now(),
        "git_rev": _git_rev(),
        "llm": llm_info,
        "configs": list(per_config),
        "cases": {
            c.case_id: {
                "split": c.split,
                "style_tags": c.style_tags,
                "gold_freeze": freeze_status.get(c.case_id),
                "revealed": c.case_id in reveal,
            }
            for c in cases
        },
        "results": results,
    }


def run_generalization(
    task: str,
    configs: list[str],
    cases: list[Case],
    *,
    llm_factory: Callable[[], Callable[[str], str] | None] | None = None,
    collect_fn: Callable[..., Any] | None = None,
    reveal: Iterable[str] = (),
    revise_reason: str | None = None,
    bootstrap_iters: int = 1000,
    seed: int = 0,
) -> dict[str, Any]:
    if task not in TASKS:
        raise ValueError(f"unknown task {task!r}")
    reveal_set = set(reveal)
    by_id = {c.case_id: c for c in cases}
    for rid in reveal_set:
        if rid not in by_id or by_id[rid].split != "holdout":
            raise ValueError(f"--reveal {rid}: not a holdout case in this run")
    freeze_status = {c.case_id: ensure_frozen(c, task, revise_reason=revise_reason) for c in cases}

    gen = None
    if task == "legend" and any(legend_config(n).use_llm for n in configs):
        gen = llm_factory() if llm_factory is not None else None
        if gen is None:
            raise RuntimeError(
                "LLM configs requested but no MLX/transformers backend; use --configs rules_only"
            )

    per_config: dict[str, dict[str, dict[str, Any]]] = {}
    for name in configs:
        per_case: dict[str, dict[str, Any]] = {}
        for c in cases:
            if task == "legend":
                preds = legend_predictions(c, legend_config(name), gen)
                per_case[c.case_id] = score_legend_case(c, preds)
            else:
                per_case[c.case_id] = score_ocr_case(c, ocr_predictions(c, name, collect_fn))
        per_config[name] = per_case

    report = build_report(
        task,
        cases,
        per_config,
        reveal=reveal_set,
        freeze_status=freeze_status,
        llm_info=getattr(gen, "backend_info", None),
        bootstrap_iters=bootstrap_iters,
        seed=seed,
    )
    for rid in sorted(reveal_set):
        burn_case(by_id[rid], f"errors revealed in {task} eval {report['created_at']}")
    return report


def format_report(report: dict[str, Any]) -> str:
    def f(x: Any) -> str:
        return "  n/a" if x is None else f"{x:.3f}"

    lines = [f"task={report['task']} git={report.get('git_rev')}"]
    for name, res in report["results"].items():
        for s, agg in res["splits"].items():
            ci = agg.get("recall_ci95")
            ci_s = f" ci95=[{ci[0]:.3f},{ci[1]:.3f}]" if ci else ""
            lines.append(
                f"  {name:32} {s:7} recall={f(agg['recall'])} ({agg['n_hit']}/{agg['n_items']}) "
                f"precision={f(agg['precision'])} ({agg['n_pred_hit']}/{agg['n_pred']}) "
                f"cases={agg['n_cases']}{ci_s}"
            )
        gap = res.get("gap_recall_dev_minus_holdout")
        if gap is not None:
            lines.append(f"  {name:32} gap(dev-holdout recall)={gap:+.3f}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- gate + history


def gate_summary(report: dict[str, Any], config: str) -> dict[str, Any]:
    res = report["results"].get(config)
    if res is None:
        raise ValueError(f"gate config {config!r} not in this run")
    splits = {
        s: {k: v for k, v in agg.items() if k != "recall_ci95"} for s, agg in res["splits"].items()
    }
    return {
        "config": config,
        "cases": {cid: meta["split"] for cid, meta in report["cases"].items()},
        "splits": splits,
    }


def load_baseline(
    task: str, config: str, history_path: Path = HISTORY_PATH
) -> dict[str, Any] | None:
    if not history_path.is_file():
        return None
    last = None
    for line in history_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("task") == task and row.get("accepted") and row["summary"].get("config") == config:
            last = row
    return last


def gate_check(
    current: dict[str, Any],
    baseline: dict[str, Any] | None,
    *,
    tol_items: int = 1,
    tol_points: float = 0.02,
) -> dict[str, Any]:
    """Pass when dev does not regress and holdout stays within tolerance.

    Holdout recall may drop by at most max(tol_items / n_items, tol_points);
    precision (dev and holdout) may not drop.
    """
    if baseline is None:
        return {"passed": True, "reasons": ["no accepted baseline yet; this run becomes the baseline"]}
    base = baseline["summary"]
    if current["cases"] != base["cases"]:
        return {
            "passed": False,
            "reasons": ["case set or splits changed since the baseline; re-baseline with --rebaseline"],
        }
    reasons: list[str] = []
    for s in SPLITS:
        cur = current["splits"].get(s)
        old = base["splits"].get(s)
        if not cur or not old:
            continue
        allowed = 0.0
        if s == "holdout" and cur["n_items"]:
            allowed = max(tol_items / cur["n_items"], tol_points)
        r_new, r_old = cur["recall"], old["recall"]
        if r_new is not None and r_old is not None and r_new < r_old - allowed - _EPS:
            reasons.append(f"{s} recall {r_old:.3f} -> {r_new:.3f} (allowed -{allowed:.3f})")
        p_new, p_old = cur["precision"], old["precision"]
        if p_new is not None and p_old is not None and p_new < p_old - _EPS:
            reasons.append(f"{s} precision {p_old:.3f} -> {p_new:.3f}")
    return {"passed": not reasons, "reasons": reasons}


def append_history(entry: dict[str, Any], history_path: Path = HISTORY_PATH) -> None:
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------- rule ledger


def _delta(with_rule: float | None, without_rule: float | None) -> float | None:
    if with_rule is None or without_rule is None:
        return None
    return round(with_rule - without_rule, 4)


def _suggest(values: list[float | None]) -> str:
    vals = [v for v in values if v is not None]
    if not vals:
        return "no_data"
    pos = any(v > _EPS for v in vals)
    neg = any(v < -_EPS for v in vals)
    if pos and neg:
        return "narrow"
    if pos:
        return "keep"
    if neg:
        return "remove"
    return "no_evidence"


def update_ledger(
    ledger: dict[str, Any], report: dict[str, Any], *, split: str = "holdout"
) -> list[str]:
    """Write each rule's measured contribution (with rule minus without) on ``split``.

    Returns ids of rules that were updated. ``decision`` stays human-owned;
    only ``measured`` and ``suggestion`` are machine-written.
    """
    results = report["results"]
    updated: list[str] = []
    for rule in ledger.get("rules") or []:
        if rule.get("task") != report["task"]:
            continue
        with_cfg = results.get(rule.get("baseline_config", "current"))
        without_cfg = results.get(rule.get("ablation_config", ""))
        if not with_cfg or not without_cfg:
            continue
        a = with_cfg["splits"].get(split)
        b = without_cfg["splits"].get(split)
        if not a or not b:
            continue
        dr = _delta(a["recall"], b["recall"])
        dp = _delta(a["precision"], b["precision"])
        measured = dict(rule.get("measured") or {})
        measured[split] = {
            "contribution_recall": dr,
            "contribution_precision": dp,
            "n_cases": a["n_cases"],
            "n_items": a["n_items"],
            "measured_at": report["created_at"],
            "git_rev": report.get("git_rev"),
        }
        rule["measured"] = measured
        if split == "holdout":
            rule["suggestion"] = _suggest([dr, dp])
        updated.append(str(rule.get("id")))
    if updated:
        ledger["updated_at"] = report["created_at"]
    return updated
