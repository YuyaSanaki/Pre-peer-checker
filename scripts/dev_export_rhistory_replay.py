#!/usr/bin/env python3
"""DEV ONLY — one-shot export of cleaned .R from .Rhistory.

NOT a product feature. Do not wire into CLI/orchestrator.
Synthetic .R next to real data would look like author scripts (misconduct risk).

Usage (from repo root)::

    .venv/bin/python scripts/dev_export_rhistory_replay.py \\
        input/private_benchmark/Manuscript/data \\
        outputs/dev_only_rhistory_replay
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow importing sibling lib without installing as package feature
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from dev_rhistory_replay_lib import reconstruct_tree  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if len(argv) != 2:
        print(__doc__)
        print("args: <data_root> <out_dir>")
        return 2
    root = Path(argv[0])
    out = Path(argv[1])
    results = reconstruct_tree(root, out)
    usable = [r for r in results if r[2].n_kept > 0]
    for _src, dest, rec in results:
        if rec.n_kept == 0 and dest.exists():
            dest.unlink()
    readme = out / "README.md"
    readme.write_text(
        "# DEV ONLY — reconstructed R from .Rhistory\n\n"
        "**Not author-written. Not a product feature.**\n\n"
        "Generated for pre-peer-checker system development only. "
        "Do not treat as evidence or ship as user-facing output.\n",
        encoding="utf-8",
    )
    print(f"wrote {len(usable)} scripts under {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
