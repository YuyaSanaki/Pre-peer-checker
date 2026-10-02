"""Append-only metric history (``outputs/metrics/history.jsonl``).

Each eval script appends one line per run so attempts are comparable over commits.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pre_peer_checker.eval.gold_eval import REPO_ROOT

HISTORY_PATH = REPO_ROOT / "outputs" / "metrics" / "history.jsonl"


def _git(*args: str) -> str | None:
    try:
        return subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def append_history(
    kind: str,
    metrics: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
    path: Path = HISTORY_PATH,
) -> dict[str, Any]:
    row = {
        "at": datetime.now(timezone.utc).isoformat(),
        "commit": _git("rev-parse", "--short", "HEAD"),
        "dirty": bool(_git("status", "--porcelain", "--untracked-files=no")),
        "kind": kind,
        "config": config or {},
        "metrics": metrics,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def read_history(kind: str | None = None, path: Path = HISTORY_PATH) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if kind is None or row.get("kind") == kind:
            rows.append(row)
    return rows


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Show metric history")
    ap.add_argument("--kind", default=None)
    ap.add_argument("--last", type=int, default=20)
    args = ap.parse_args(argv)
    for row in read_history(args.kind)[-args.last :]:
        flag = "*" if row.get("dirty") else " "
        print(
            f"{row['at'][:19]} {row.get('commit') or '-':>8}{flag} {row['kind']:<18} "
            + json.dumps(row.get("metrics"), ensure_ascii=False)[:200]
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
