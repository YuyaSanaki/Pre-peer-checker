"""R 作図残渣（.textClipping / .Rhistory）の軽量パーサ。"""

from __future__ import annotations

import plistlib
import re
from dataclasses import dataclass
from pathlib import Path


_NUM = r"(-?[\d.]+(?:[eE][-+]?\d+)?)"
_MEAN_RE = re.compile(
    rf"mean of x\s+mean of y\s+{_NUM}\s+{_NUM}",
    re.IGNORECASE,
)
_P_RE = re.compile(r"p-value\s*[=<]\s*([0-9.eE\-]+)", re.IGNORECASE)
_T_RE = re.compile(r"(?<![A-Za-z])t\s*=\s*([-\d.]+)", re.IGNORECASE)
_DF_RE = re.compile(r"(?<![A-Za-z])df\s*=\s*([\d.]+)", re.IGNORECASE)
_KIND_RES: list[tuple[str, re.Pattern[str]]] = [
    ("welch_t", re.compile(r"Welch Two Sample t-test", re.IGNORECASE)),
    ("paired_t", re.compile(r"Paired t-test", re.IGNORECASE)),
    ("student_t", re.compile(r"(?<!Welch )Two Sample t-test", re.IGNORECASE)),
    ("wilcoxon", re.compile(r"Wilcoxon|Mann-Whitney", re.IGNORECASE)),
    ("dunnett", re.compile(r"Dunnett", re.IGNORECASE)),
    ("tukey", re.compile(r"Tukey", re.IGNORECASE)),
    ("steel_dwass", re.compile(r"Dwass|pSDCFlig", re.IGNORECASE)),
    ("chisq", re.compile(r"Chi-squared test", re.IGNORECASE)),
    (
        "multcomp",
        re.compile(r"Simultaneous Tests|Multiple Comparisons of Means|Linear Hypotheses", re.IGNORECASE),
    ),
]
_GROUP_SIZES_RE = re.compile(r"Group sizes:\s*((?:\d+\s+)*\d+)", re.IGNORECASE)
_MEANS_DIFF_RE = re.compile(r"true difference in means", re.IGNORECASE)
_WELCH_RE = dict(_KIND_RES)["welch_t"]
_DUNNETT_RE = dict(_KIND_RES)["dunnett"]
_PLIST_TEXT_KEYS = (
    "public.utf8-plain-text",
    "public.utf16-external-plain-text",
    "public.utf16-plain-text",
    "com.apple.traditional-mac-plain-text",
)


@dataclass
class ResidueStats:
    path: Path
    kind: str
    means: tuple[float, float] | None = None
    p_value: float | None = None
    t_stat: float | None = None
    raw_excerpt: str = ""
    df: float | None = None
    # per-group n printed by the test (e.g. Steel-Dwass "Group sizes: 9 10 7 11")
    group_sizes: tuple[int, ...] = ()


def _plist_text(data: bytes) -> str | None:
    """Plain text of a macOS clipping (binary plist with UTI-Data payloads)."""
    if not data.startswith(b"bplist"):
        return None
    try:
        doc = plistlib.loads(data)
    except Exception:  # noqa: BLE001
        return None
    uti = doc.get("UTI-Data") if isinstance(doc, dict) else None
    if not isinstance(uti, dict):
        return None
    for key in _PLIST_TEXT_KEYS:
        v = uti.get(key)
        if isinstance(v, str) and v.strip():
            return v
        if isinstance(v, bytes) and v:
            enc = "utf-16" if "utf16" in key else "mac_roman"
            try:
                return v.decode(enc).replace("\r", "\n")
            except UnicodeDecodeError:
                continue
    return None


def _decode_textclipping(data: bytes) -> str:
    text = _plist_text(data)
    if text:
        return text
    # older clippings keep UTF-16 text in a resource-fork-like blob
    if b"Welch" in data or b"Dunnett" in data or b"Simultaneous" in data:
        try:
            text = data.decode("utf-16", errors="ignore")
            if "Welch" in text or "Dunnett" in text or "mean" in text:
                return text
        except Exception:
            pass
    return data.decode("utf-8", errors="ignore")


def _float(m: re.Match[str] | None) -> float | None:
    if m is None:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def parse_textclipping(path: Path | str) -> ResidueStats:
    path = Path(path)
    data = path.read_bytes()
    raw = _decode_textclipping(data)
    if "mean of" not in raw and "p-value" not in raw:
        raw2 = data.decode("latin-1", errors="ignore")
        if raw2.count("p-value") > raw.count("p-value"):
            raw = raw2

    kind = next((k for k, rx in _KIND_RES if rx.search(raw)), "unknown")
    df = _float(_DF_RE.search(raw))
    if kind == "unknown" and _MEANS_DIFF_RE.search(raw) and df is not None:
        # clipping cut below the header: only Welch reports a fractional df
        kind = "welch_t" if not float(df).is_integer() else "student_t"
    mm = _MEAN_RE.search(raw)
    means = (float(mm.group(1)), float(mm.group(2))) if mm else None
    gs = _GROUP_SIZES_RE.search(raw)

    return ResidueStats(
        path=path,
        kind=kind,
        means=means,
        p_value=_float(_P_RE.search(raw)),
        t_stat=_float(_T_RE.search(raw)),
        raw_excerpt=raw[:500],
        df=df,
        group_sizes=tuple(int(x) for x in gs.group(1).split()) if gs else (),
    )


def find_residue_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for p in root.rglob("*"):
        name = p.name.lower()
        if name.endswith(".textclipping") or name == ".rhistory" or name.endswith(".rhistory"):
            out.append(p)
    seen: set[Path] = set()
    uniq: list[Path] = []
    for p in out:
        rp = p.resolve()
        if rp not in seen and p.is_file():
            seen.add(rp)
            uniq.append(p)
    return uniq
