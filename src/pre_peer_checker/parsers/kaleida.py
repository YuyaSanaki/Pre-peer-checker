"""KaleidaGraph project/data best-effort extraction.

``.qpd`` / ``.qpc`` are largely proprietary binary. We:
- classify and surface them in coverage
- extract printable path-like / numeric tokens for soft linking
- fully parse tab/CSV text exports that look like KaleidaGraph dumps
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

_PATH_RE = re.compile(
    r"[\w./\\ \-]+\.(?:xlsx?|csv|tsv|txt|qpd|qpc)",
    re.IGNORECASE,
)
_FLOAT_RE = re.compile(r"[-+]?(?:\d+\.\d+|\d+\.|\.\d+)(?:[eE][-+]?\d+)?")


@dataclass
class KaleidaRef:
    path: Path
    referenced_names: list[str] = field(default_factory=list)
    numeric_samples: list[float] = field(default_factory=list)
    backend: str = "kaleida-strings"
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "backend": self.backend,
            "source_kind": "kaleida",
            "referenced_names": list(self.referenced_names),
            "numeric_samples": list(self.numeric_samples[:50]),
            "note": self.note,
            "reads": [
                {
                    "function": "kaleida_ref",
                    "path": name,
                    "assigned_to": None,
                    "resolved_path": None,
                }
                for name in self.referenced_names
            ],
            "plots": [],
            "saves": [],
        }


def _printable_chunks(data: bytes, *, min_len: int = 4) -> list[str]:
    out: list[str] = []
    cur: list[int] = []
    for b in data:
        if 32 <= b < 127:
            cur.append(b)
        else:
            if len(cur) >= min_len:
                out.append(bytes(cur).decode("ascii", errors="ignore"))
            cur = []
    if len(cur) >= min_len:
        out.append(bytes(cur).decode("ascii", errors="ignore"))
    return out


def parse_kaleida_file(path: Path | str, *, max_bytes: int = 2_000_000) -> KaleidaRef:
    path = Path(path)
    raw = path.read_bytes()[:max_bytes]
    # Text export?
    if b"\x00" not in raw[:200] and (
        b"," in raw[:200] or b"\t" in raw[:200] or raw.lstrip().startswith((b"#", b"Column"))
    ):
        text = raw.decode("utf-8", errors="ignore")
        names = sorted({m.group(0).split("/")[-1].split("\\")[-1] for m in _PATH_RE.finditer(text)})
        nums = [float(x) for x in _FLOAT_RE.findall(text)[:200]]
        return KaleidaRef(
            path=path,
            referenced_names=names,
            numeric_samples=nums,
            backend="kaleida-text",
            note="text/CSV-like Kaleida export",
        )

    chunks = _printable_chunks(raw)
    blob = "\n".join(chunks)
    names = sorted(
        {
            m.group(0).split("/")[-1].split("\\")[-1]
            for m in _PATH_RE.finditer(blob)
        }
    )
    nums: list[float] = []
    for ch in chunks:
        for m in _FLOAT_RE.finditer(ch):
            try:
                nums.append(float(m.group(0)))
            except ValueError:
                pass
            if len(nums) >= 100:
                break
        if len(nums) >= 100:
            break
    return KaleidaRef(
        path=path,
        referenced_names=names,
        numeric_samples=nums,
        backend="kaleida-strings",
        note="binary KaleidaGraph project — string scrape only; prefer CSV export for full linking",
    )


def kaleida_to_artifact(ref: KaleidaRef, *, table_paths: list[Path] | None = None) -> dict:
    """Serialize and optionally resolve referenced basenames onto local tables."""
    from pre_peer_checker.engine.script_resolve import (
        index_tables_by_basename,
        pick_local_for_basename,
    )

    art = ref.to_dict()
    by_name = index_tables_by_basename(table_paths or [])
    reads = []
    for name in ref.referenced_names:
        local = pick_local_for_basename(name.lower(), by_name=by_name, script_dir=ref.path.parent)
        reads.append(
            {
                "function": "kaleida_ref",
                "path": name,
                "assigned_to": None,
                "resolved_path": str(local) if local else None,
            }
        )
    art["reads"] = reads
    return art
