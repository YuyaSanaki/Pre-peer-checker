"""Resolve script-declared paths (often absolute / Google Drive) onto local bundle files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pre_peer_checker.parsers.r_treesitter import RScriptDAG


def basename_key(path_str: str | None) -> str | None:
    if not path_str:
        return None
    # Strip R-style ~ and normalize
    cleaned = path_str.strip().strip("\"'").replace("\\", "/")
    if cleaned.startswith("~/"):
        cleaned = cleaned[2:]
    name = Path(cleaned).name
    return name.lower() if name else None


def index_tables_by_basename(tables: list[Path]) -> dict[str, list[Path]]:
    out: dict[str, list[Path]] = {}
    for p in tables:
        out.setdefault(p.name.lower(), []).append(p)
    return out


def pick_local_for_basename(
    basename: str,
    *,
    by_name: dict[str, list[Path]],
    script_dir: Path | None = None,
) -> Path | None:
    hits = by_name.get(basename.lower()) or []
    if not hits:
        return None
    if len(hits) == 1:
        return hits[0]
    if script_dir is not None:
        same_dir = [h for h in hits if h.parent.resolve() == script_dir.resolve()]
        if len(same_dir) == 1:
            return same_dir[0]
        if same_dir:
            return sorted(same_dir, key=lambda p: p.stat().st_mtime, reverse=True)[0]
        # Prefer files under a shared ancestor with the script
        scored: list[tuple[int, Path]] = []
        script_parts = set(script_dir.resolve().parts)
        for h in hits:
            shared = len(script_parts & set(h.resolve().parts))
            scored.append((shared, h))
        scored.sort(key=lambda t: t[0], reverse=True)
        if scored and scored[0][0] > 0:
            return scored[0][1]
    return hits[0]


def enrich_r_dag_with_local_paths(
    dag: RScriptDAG,
    *,
    script_path: Path,
    table_paths: list[Path],
) -> dict[str, str]:
    """Attach ``resolved_path`` onto read/save dicts via basename match.

    Mutates nothing on *dag*; returns mapping declared_basename → local path str
    and sets attributes ``resolved_path`` on read/save objects when found.
    """
    by_name = index_tables_by_basename(table_paths)
    script_dir = script_path.parent
    resolved: dict[str, str] = {}

    for r in dag.reads:
        key = basename_key(r.path)
        if not key:
            continue
        local = pick_local_for_basename(key, by_name=by_name, script_dir=script_dir)
        if local is None:
            continue
        setattr(r, "resolved_path", str(local))
        resolved[key] = str(local)
        # Prefer resolved path for downstream token matching
        if r.assigned_to:
            dag.var_sources[r.assigned_to] = str(local)

    for s in dag.saves:
        key = basename_key(s.path)
        if not key:
            continue
        local = pick_local_for_basename(key, by_name=by_name, script_dir=script_dir)
        if local is None:
            continue
        setattr(s, "resolved_path", str(local))
        resolved[key] = str(local)

    return resolved


def r_dag_to_artifact_dict(
    dag: RScriptDAG,
    *,
    script_path: Path,
    source_kind: str,
) -> dict:
    """Serialize R DAG including resolved_path fields when present."""

    def _read(r) -> dict:
        d = {
            "function": r.function,
            "path": r.path,
            "assigned_to": r.assigned_to,
            "lineno": r.lineno,
            "arguments": r.arguments,
        }
        rp = getattr(r, "resolved_path", None)
        if rp:
            d["resolved_path"] = rp
        return d

    def _save(s) -> dict:
        d = {
            "function": s.function,
            "path": s.path,
            "plot_expr": s.plot_expr,
            "lineno": s.lineno,
            "arguments": s.arguments,
        }
        rp = getattr(s, "resolved_path", None)
        if rp:
            d["resolved_path"] = rp
        return d

    base = dag.to_dict()
    base["path"] = str(script_path)
    base["source_kind"] = source_kind
    base["reads"] = [_read(r) for r in dag.reads]
    base["saves"] = [_save(s) for s in dag.saves]
    return base


def enrich_python_dag_with_local_paths(
    dag: Any,
    *,
    script_path: Path,
    table_paths: list[Path],
) -> dict[str, str]:
    """Same basename resolution for Python ScriptDAG reads/saves."""
    by_name = index_tables_by_basename(table_paths)
    script_dir = script_path.parent
    resolved: dict[str, str] = {}
    for r in getattr(dag, "reads", []) or []:
        key = basename_key(getattr(r, "path", None))
        if not key:
            continue
        local = pick_local_for_basename(key, by_name=by_name, script_dir=script_dir)
        if local is None:
            continue
        setattr(r, "resolved_path", str(local))
        resolved[key] = str(local)
        if getattr(r, "assigned_to", None):
            dag.var_sources[r.assigned_to] = str(local)
    for s in getattr(dag, "saves", []) or []:
        key = basename_key(getattr(s, "path", None))
        if not key:
            continue
        local = pick_local_for_basename(key, by_name=by_name, script_dir=script_dir)
        if local is None:
            continue
        setattr(s, "resolved_path", str(local))
        resolved[key] = str(local)
    return resolved


def python_dag_to_artifact_dict(
    dag: Any,
    *,
    script_path: Path,
    source_kind: str = "python",
) -> dict:
    def _read(r) -> dict:
        d = {
            "function": getattr(r, "func", None) or getattr(r, "function", None),
            "path": getattr(r, "path", None),
            "assigned_to": getattr(r, "assigned_to", None),
            "lineno": getattr(r, "lineno", None),
        }
        rp = getattr(r, "resolved_path", None)
        if rp:
            d["resolved_path"] = rp
        return d

    def _save(s) -> dict:
        d = {
            "function": getattr(s, "func", None) or getattr(s, "function", None),
            "path": getattr(s, "path", None),
            "lineno": getattr(s, "lineno", None),
            "plot_var": getattr(s, "plot_var", None),
        }
        rp = getattr(s, "resolved_path", None)
        if rp:
            d["resolved_path"] = rp
        return d

    base = dag.to_dict() if hasattr(dag, "to_dict") else {}
    base["path"] = str(script_path)
    base["source_kind"] = source_kind
    base["reads"] = [_read(r) for r in getattr(dag, "reads", []) or []]
    base["saves"] = [_save(s) for s in getattr(dag, "saves", []) or []]
    return base
