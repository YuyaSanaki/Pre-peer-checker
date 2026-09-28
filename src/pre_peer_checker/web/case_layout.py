"""Parent-folder convention for WebUI: ``manuscript/`` + ``data/`` required."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


MANUSCRIPT_DIR = "manuscript"
DATA_DIR = "data"


@dataclass(frozen=True)
class CaseRootValidation:
    ok: bool
    root: Path | None
    manuscript: Path | None
    data: Path | None
    inputs: list[Path]
    error: str | None = None


def validate_case_root(root: str | Path) -> CaseRootValidation:
    """Validate that *root* contains ``manuscript/`` and ``data/`` directories."""
    path = Path(root).expanduser()
    if not path.is_absolute():
        path = path.resolve()
    else:
        path = path.resolve()

    if not path.exists():
        return CaseRootValidation(
            False, None, None, None, [], f"フォルダが見つかりません: {path}"
        )
    if not path.is_dir():
        return CaseRootValidation(
            False, None, None, None, [], f"ディレクトリではありません: {path}"
        )

    manuscript = path / MANUSCRIPT_DIR
    data = path / DATA_DIR
    missing: list[str] = []
    if not manuscript.is_dir():
        missing.append(f"{MANUSCRIPT_DIR}/")
    if not data.is_dir():
        missing.append(f"{DATA_DIR}/")
    if missing:
        return CaseRootValidation(
            False,
            path,
            manuscript if manuscript.is_dir() else None,
            data if data.is_dir() else None,
            [],
            "親フォルダに次のサブフォルダが必要です: "
            + ", ".join(missing)
            + f"（例: {path.name}/{MANUSCRIPT_DIR}/ と {path.name}/{DATA_DIR}/）",
        )

    return CaseRootValidation(
        ok=True,
        root=path,
        manuscript=manuscript,
        data=data,
        inputs=[manuscript, data],
        error=None,
    )
