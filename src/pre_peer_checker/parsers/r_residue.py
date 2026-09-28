"""R 作図残渣（.textClipping / .Rhistory）の軽量パーサ。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


_MEAN_RE = re.compile(
    r"mean of x\s+mean of y\s+([\d.]+)\s+([\d.]+)",
    re.IGNORECASE,
)
_P_RE = re.compile(r"p-value\s*=\s*([0-9.eE\-]+)", re.IGNORECASE)
_T_RE = re.compile(r"t\s*=\s*([-\d.]+)", re.IGNORECASE)
_WELCH_RE = re.compile(r"Welch Two Sample t-test", re.IGNORECASE)
_DUNNETT_RE = re.compile(r"Dunnett", re.IGNORECASE)


@dataclass
class ResidueStats:
    path: Path
    kind: str
    means: tuple[float, float] | None = None
    p_value: float | None = None
    t_stat: float | None = None
    raw_excerpt: str = ""


def _decode_textclipping(data: bytes) -> str:
    # Apple textClipping often embeds UTF-16 text
    if b"Welch" in data or b"Dunnett" in data or b"Simultaneous" in data:
        # try utf-16 chunks
        try:
            text = data.decode("utf-16", errors="ignore")
            if "Welch" in text or "Dunnett" in text or "mean" in text:
                return text
        except Exception:
            pass
    return data.decode("utf-8", errors="ignore")


def parse_textclipping(path: Path | str) -> ResidueStats:
    path = Path(path)
    raw = _decode_textclipping(path.read_bytes())
    # also try reading as latin1 for printable
    if "mean of" not in raw and "p-value" not in raw:
        raw2 = path.read_bytes().decode("latin-1", errors="ignore")
        if raw2.count("p-value") > raw.count("p-value"):
            raw = raw2

    kind = "unknown"
    if _WELCH_RE.search(raw):
        kind = "welch_t"
    elif _DUNNETT_RE.search(raw):
        kind = "dunnett"

    means = None
    mm = _MEAN_RE.search(raw)
    if mm:
        means = (float(mm.group(1)), float(mm.group(2)))

    p_value = None
    pm = _P_RE.search(raw)
    if pm:
        try:
            p_value = float(pm.group(1))
        except ValueError:
            pass

    t_stat = None
    tm = _T_RE.search(raw)
    if tm:
        try:
            t_stat = float(tm.group(1))
        except ValueError:
            pass

    return ResidueStats(
        path=path,
        kind=kind,
        means=means,
        p_value=p_value,
        t_stat=t_stat,
        raw_excerpt=raw[:500],
    )


def find_residue_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for pat in ("*.textClipping", ".Rhistory", "*.Rhistory"):
        out.extend(root.rglob(pat))
    # unique
    seen: set[Path] = set()
    uniq: list[Path] = []
    for p in out:
        rp = p.resolve()
        if rp not in seen and p.is_file():
            seen.add(rp)
            uniq.append(p)
    return uniq
