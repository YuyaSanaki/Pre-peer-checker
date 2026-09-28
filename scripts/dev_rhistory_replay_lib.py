"""DEV ONLY helper — reconstruct readable .R from .Rhistory.

Not a product module. Used by ``scripts/dev_export_rhistory_replay.py`` only.
Do not import from the verification pipeline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from pre_peer_checker.parsers.r_residue import ResidueStats, parse_textclipping


_VIEW_RE = re.compile(r"^\s*View\s*\(", re.I)
_LIBRARY_RE = re.compile(r"^\s*(?:library|require)\s*\(\s*([A-Za-z0-9.]+)", re.I)
_ASSIGN_READ_RE = re.compile(
    r"^\s*([A-Za-z.][A-Za-z0-9.]*)\s*<-\s*(read_(?:excel|csv|table|delim|tsv)|"
    r"read\.csv|read\.table|read\.delim)\s*\(",
    re.I,
)
_GGPLOT_RE = re.compile(r"ggplot\s*\(\s*([^,\)]+)", re.I)
_BARE_IDENT_RE = re.compile(r"^\s*[A-Za-z.][A-Za-z0-9.]*\s*$")
_PATH_IN_QUOTES_RE = re.compile(r"""(['"])([^'"]+\.(?:xlsx|xls|csv|tsv|rds|rda))\1""", re.I)


@dataclass
class ReconstructedScript:
    source_path: Path
    code: str
    n_kept: int = 0
    n_dropped: int = 0
    nearby_tables: list[str] = field(default_factory=list)
    nearby_plots: list[str] = field(default_factory=list)
    nearby_stats: list[ResidueStats] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "source_path": str(self.source_path),
            "n_kept": self.n_kept,
            "n_dropped": self.n_dropped,
            "nearby_tables": list(self.nearby_tables),
            "nearby_plots": list(self.nearby_plots),
            "nearby_stats": [
                {
                    "path": str(s.path),
                    "kind": s.kind,
                    "p_value": s.p_value,
                    "t_stat": s.t_stat,
                    "means": list(s.means) if s.means else None,
                }
                for s in self.nearby_stats
            ],
            "code": self.code,
        }


def _paren_balance(text: str) -> int:
    """Rough () balance ignoring quotes (good enough for history cleanup)."""
    depth = 0
    quote: str | None = None
    escape = False
    for ch in text:
        if quote:
            if escape:
                escape = False
            elif ch == "\\" and quote != "'":
                escape = True
            elif ch == quote:
                quote = None
            continue
        if ch in {'"', "'"}:
            quote = ch
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
    return depth


def _is_complete_statement(text: str) -> bool:
    s = text.rstrip()
    if not s:
        return False
    if _paren_balance(s) != 0:
        return False
    # trailing operator / open pipe → incomplete
    if re.search(r"(?:%>%|\+|,|=)\s*$", s):
        return False
    return True


def _strip_console_prompt(line: str) -> str:
    """Drop a lone R continuation-prompt ``+`` prefix when it is not ggplot ``+``."""
    # History sometimes stores " + scale_..." (ggplot) — keep that.
    # Rarely stores console prompt alone as "+ ".
    if re.match(r"^\+\s*$", line):
        return ""
    return line


def join_history_statements(text: str) -> list[str]:
    """Join multi-line history entries into complete R statements."""
    statements: list[str] = []
    buf: list[str] = []
    for raw in text.splitlines():
        line = _strip_console_prompt(raw.rstrip())
        if not line.strip():
            continue
        if not buf:
            buf.append(line)
        else:
            # ggplot / pipe continuation often starts with +
            buf.append(line)
        joined = "\n".join(buf)
        if _is_complete_statement(joined):
            statements.append(joined.strip())
            buf = []
    if buf:
        # incomplete leftover — keep only if somehow useful later (usually drop)
        leftover = "\n".join(buf).strip()
        if leftover:
            statements.append(leftover)
    return statements


def _statement_key(stmt: str) -> str:
    one = re.sub(r"\s+", " ", stmt.strip())
    lm = _LIBRARY_RE.match(one)
    if lm:
        return f"lib:{lm.group(1).lower()}"
    am = _ASSIGN_READ_RE.match(one)
    if am:
        return f"read:{am.group(1)}"
    if "ggplot" in one.lower():
        gm = _GGPLOT_RE.search(one)
        data = (gm.group(1).strip() if gm else "?")[:40]
        return f"ggplot:{data}"
    low = one.lower()
    if "t.test(" in low:
        return "ttest:" + re.sub(r"\s+", "", one)[:100]
    if "glht(" in low or re.search(r"\baov\s*\(", low):
        return "glht:" + re.sub(r"\s+", "", one)[:100]
    if "psdcflig(" in low:
        return "psdc:" + re.sub(r"\s+", "", one)[:80]
    if "tidyr::spread" in low or "spread(" in low:
        m = re.match(r"^\s*([A-Za-z.][A-Za-z0-9.]*)\s*<-", one)
        return f"spread:{m.group(1) if m else one[:40]}"
    return f"other:{one[:80]}"


def _should_drop(stmt: str) -> bool:
    if not stmt.strip():
        return True
    if _VIEW_RE.match(stmt):
        return True
    if _BARE_IDENT_RE.match(stmt):
        return True
    if not _is_complete_statement(stmt):
        return True
    # typo / aborted calls often look like t.test((a,b))
    if re.search(r"t\.test\s*\(\s*\([^)]+,[^)]+\)\s*\)", stmt):
        return True
    # t.test(x, x) self-compare
    m = re.search(r"t\.test\s*\(\s*([^,]+)\s*,\s*([^)]+)\s*\)", stmt)
    if m and m.group(1).strip() == m.group(2).strip():
        return True
    return False


def _rewrite_paths_to_local(stmt: str, local_names: set[str]) -> str:
    if not local_names:
        return stmt

    def repl(m: re.Match[str]) -> str:
        quote, path = m.group(1), m.group(2)
        base = Path(path).name
        if base in local_names or base.lower() in {n.lower() for n in local_names}:
            # preserve original casing when possible
            for n in local_names:
                if n.lower() == base.lower():
                    return f"{quote}{n}{quote}"
            return f"{quote}{base}{quote}"
        return m.group(0)

    return _PATH_IN_QUOTES_RE.sub(repl, stmt)


def _scan_nearby(dir_path: Path) -> tuple[list[str], list[str], list[ResidueStats]]:
    tables: list[str] = []
    plots: list[str] = []
    stats: list[ResidueStats] = []
    if not dir_path.is_dir():
        return tables, plots, stats
    for p in sorted(dir_path.iterdir()):
        if not p.is_file():
            continue
        low = p.name.lower()
        if low.endswith((".xlsx", ".xls", ".csv")) and (
            low.startswith("graph") or "graph" in low or low.startswith("quant")
        ):
            tables.append(p.name)
        elif low.startswith("rplot") and low.endswith(".pdf"):
            plots.append(p.name)
        elif low.endswith(".textclipping"):
            try:
                stats.append(parse_textclipping(p))
            except Exception:  # noqa: BLE001
                pass
    return tables, plots, stats


def _header(
    source: Path,
    *,
    tables: list[str],
    plots: list[str],
    stats: list[ResidueStats],
) -> str:
    lines = [
        "# Reconstructed analysis script (NOT author-written).",
        f"# Source: {source.name} · {source.parent}",
        "# Built from .Rhistory (deduped) + nearby graph/stats residues.",
        "# Use for provenance / DAG linking only — do not treat as original code.",
    ]
    if tables:
        lines.append("# Nearby plot tables: " + ", ".join(tables))
    if plots:
        lines.append("# Nearby Rplot PDFs: " + ", ".join(plots))
    for s in stats[:8]:
        bits = [s.kind]
        if s.p_value is not None:
            bits.append(f"p={s.p_value:g}")
        if s.t_stat is not None:
            bits.append(f"t={s.t_stat:g}")
        if s.means:
            bits.append(f"means={s.means[0]:g},{s.means[1]:g}")
        lines.append(f"# Residue {s.path.name}: " + " · ".join(bits))
    return "\n".join(lines) + "\n\n"


def dedupe_keep_last(statements: list[str]) -> tuple[list[str], int]:
    """Keep last statement per logical slot; preserve final chronological order."""
    last_idx: dict[str, int] = {}
    for i, stmt in enumerate(statements):
        last_idx[_statement_key(stmt)] = i
    keep = {i for i in last_idx.values()}
    out = [statements[i] for i in range(len(statements)) if i in keep]
    dropped = len(statements) - len(out)
    return out, dropped


def _bucket(stmt: str) -> int:
    """Order: library → read → transform → stats → plot → other."""
    one = stmt.strip()
    if _LIBRARY_RE.match(one):
        return 0
    if _ASSIGN_READ_RE.match(one):
        return 1
    low = one.lower()
    if re.search(r"\b(?:spread|gather|mutate|factor)\s*\(", low) or "tidyr::" in low:
        return 2
    if re.match(r"^\s*[A-Za-z.][A-Za-z0-9.]*\s*<-\s*c\s*\(", one):
        return 2
    if re.search(
        r"\b(?:t\.test|glht|aov|wilcox\.test|pSDCFlig)\s*\(", one, re.I
    ) or "summary(glht" in low.replace(" ", ""):
        return 3
    if "ggplot" in low:
        return 4
    return 5


def reconstruct_from_rhistory(
    path: Path | str,
    *,
    rewrite_local_paths: bool = True,
    scan_nearby: bool = True,
) -> ReconstructedScript:
    path = Path(path)
    raw = path.read_text(encoding="utf-8", errors="replace")
    stmts = join_history_statements(raw)

    local_names: set[str] = set()
    tables: list[str] = []
    plots: list[str] = []
    stats: list[ResidueStats] = []
    if scan_nearby:
        tables, plots, stats = _scan_nearby(path.parent)
        local_names = (
            {p.name for p in path.parent.iterdir() if p.is_file()}
            if path.parent.is_dir()
            else set()
        )

    cleaned: list[str] = []
    dropped = 0
    for stmt in stmts:
        if _should_drop(stmt):
            dropped += 1
            continue
        if rewrite_local_paths:
            stmt = _rewrite_paths_to_local(stmt, local_names)
        cleaned.append(stmt)

    kept, dup_drop = dedupe_keep_last(cleaned)
    dropped += dup_drop

    # Drop ggplot(x, ...) style typos when x was never read/assigned in kept set
    assigned = set()
    for s in kept:
        m = re.match(r"^\s*([A-Za-z.][A-Za-z0-9.]*)\s*<-", s)
        if m:
            assigned.add(m.group(1))
    filtered: list[str] = []
    for s in kept:
        gm = _GGPLOT_RE.search(s)
        if gm:
            data = gm.group(1).strip()
            if re.fullmatch(r"[A-Za-z.][A-Za-z0-9.]*", data) and data not in assigned:
                dropped += 1
                continue
        rhs_vars = re.findall(r"\b([A-Za-z.][A-Za-z0-9.]*)\s*\$", s)
        if rhs_vars and all(v not in assigned for v in rhs_vars):
            dropped += 1
            continue
        filtered.append(s)

    body = [s for _, s in sorted(enumerate(filtered), key=lambda it: (_bucket(it[1]), it[0]))]

    code = _header(path, tables=tables, plots=plots, stats=stats)
    code += "\n\n".join(body)
    if body:
        code += "\n"

    return ReconstructedScript(
        source_path=path,
        code=code,
        n_kept=len(body),
        n_dropped=dropped,
        nearby_tables=tables,
        nearby_plots=plots,
        nearby_stats=stats,
    )


def write_reconstructed(
    rec: ReconstructedScript,
    out_dir: Path | str,
    *,
    relative_to: Path | None = None,
) -> Path:
    """Write ``…/<rel>/from_Rhistory.R`` under ``out_dir``."""
    out_dir = Path(out_dir)
    src = rec.source_path
    if relative_to is not None:
        try:
            rel_parent = src.parent.resolve().relative_to(Path(relative_to).resolve())
        except ValueError:
            rel_parent = Path(src.parent.name)
    else:
        rel_parent = Path(src.parent.name)
    dest_dir = out_dir / rel_parent
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "from_Rhistory.R"
    dest.write_text(rec.code, encoding="utf-8")
    return dest


def reconstruct_tree(
    root: Path | str,
    out_dir: Path | str,
) -> list[tuple[Path, Path, ReconstructedScript]]:
    """Find all ``.Rhistory`` under ``root`` and write reconstructed scripts."""
    root = Path(root)
    out_dir = Path(out_dir)
    results: list[tuple[Path, Path, ReconstructedScript]] = []
    histories = sorted(root.rglob(".Rhistory")) + sorted(root.rglob("*.Rhistory"))
    seen: set[Path] = set()
    for h in histories:
        rp = h.resolve()
        if rp in seen or not h.is_file():
            continue
        seen.add(rp)
        rec = reconstruct_from_rhistory(h)
        dest = write_reconstructed(rec, out_dir, relative_to=root)
        results.append((h, dest, rec))
    return results
