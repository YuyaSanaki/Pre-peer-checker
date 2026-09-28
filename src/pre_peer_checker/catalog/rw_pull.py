"""Crossref Retraction Watch Git の clone / pull."""

from __future__ import annotations

import subprocess
from pathlib import Path

from pre_peer_checker.catalog.paths import RW_CACHE_DIR, RW_CSV_NAME, RW_GIT_URL


class RwPullError(RuntimeError):
    pass


def ensure_rw_repo(
    cache_dir: Path | None = None,
    git_url: str = RW_GIT_URL,
    *,
    pull: bool = True,
) -> Path:
    """キャッシュに RW リポジトリを用意し、必要なら ``git pull --ff-only``.

    Returns:
        ``retraction_watch.csv`` のパス
    """
    root = cache_dir or RW_CACHE_DIR
    csv_path = root / RW_CSV_NAME
    if not (root / ".git").is_dir():
        root.parent.mkdir(parents=True, exist_ok=True)
        if root.exists() and not any(root.iterdir()):
            root.rmdir()
        elif root.exists():
            raise RwPullError(f"cache path exists but is not a git repo: {root}")
        _run(["git", "clone", "--depth", "1", git_url, str(root)])
    elif pull:
        _run(["git", "-C", str(root), "pull", "--ff-only"])
    if not csv_path.is_file():
        raise RwPullError(f"missing {RW_CSV_NAME} under {root}")
    return csv_path


def _run(cmd: list[str]) -> None:
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RwPullError("git is required for Retraction Watch pull") from exc
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or exc.stdout or "").strip()
        raise RwPullError(f"git command failed: {' '.join(cmd)}\n{err}") from exc
