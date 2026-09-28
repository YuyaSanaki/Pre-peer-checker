"""Zeiss CZI 取得メタ抽出（pylibCZIrw 不要・UTF-8 XML 直読み）."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_NOM_MAG = re.compile(
    r"<NominalMagnification>\s*(?P<v>[^<]+)\s*</NominalMagnification>",
    re.I,
)
_LENS_NA = re.compile(r"<LensNA>\s*(?P<v>[^<]+)\s*</LensNA>", re.I)
_OBJ_NAME_ATTR = re.compile(
    r'<Objective\b[^>]*\bName="(?P<v>[^"]+)"',
    re.I,
)
_OBJ_NAME_SCALING = re.compile(
    r"<ObjectiveName>\s*(?P<v>[^<]+)\s*</ObjectiveName>",
    re.I,
)
_DIST_XY = re.compile(
    r'<Distance\s+Id="(?P<axis>[XY])"\s*>\s*<Value>\s*(?P<v>[^<]+)\s*</Value>',
    re.I | re.S,
)
_SIZE_X = re.compile(r"<SizeX>\s*(?P<v>[^<]+)\s*</SizeX>", re.I)
_SIZE_Y = re.compile(r"<SizeY>\s*(?P<v>[^<]+)\s*</SizeY>", re.I)
_OBJ_X = re.compile(r"(?P<mag>\d+(?:\.\d+)?)\s*[x×X]\b")


@dataclass(frozen=True)
class CziAcquisitionMeta:
    path: str
    magnification: float | None = None
    objective_name: str | None = None
    numerical_aperture: float | None = None
    pixel_size_um: float | None = None
    field_of_view_um: float | None = None
    n_pixels: int | None = None
    source: str = "czi_xml"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def as_frame_meta(self) -> dict[str, Any]:
        out: dict[str, Any] = {"format": "czi", "meta_source": self.source}
        if self.magnification is not None:
            out["objective_magnification"] = self.magnification
            out["magnification"] = self.magnification
            out["nominal_magnification"] = self.magnification
        if self.objective_name:
            out["objective_name"] = self.objective_name
        if self.numerical_aperture is not None:
            out["numerical_aperture"] = self.numerical_aperture
        if self.pixel_size_um is not None:
            out["pixel_size_um"] = self.pixel_size_um
        if self.field_of_view_um is not None:
            out["field_of_view_um"] = self.field_of_view_um
        if self.n_pixels is not None:
            out["n_pixels"] = self.n_pixels
        return out


def _extract_czi_metadata_xml(path: Path, *, max_bytes: int = 2_000_000) -> str:
    """Pull the ImageDocument / Metadata UTF-8 XML embedded in a ZISRAW CZI."""
    with path.open("rb") as f:
        head = f.read(min(max_bytes, 8_000_000))
    marker = b"<ImageDocument"
    idx = head.find(marker)
    if idx < 0:
        marker = b"<Metadata"
        idx = head.find(marker)
    if idx < 0:
        # Deep search for NominalMagnification then back up
        needle = b"NominalMagnification"
        with path.open("rb") as f:
            pos = 0
            chunk_size = 4_000_000
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    return ""
                j = chunk.find(needle)
                if j >= 0:
                    start = max(0, pos + j - 200_000)
                    f.seek(start)
                    return f.read(max_bytes).decode("utf-8", errors="ignore")
                pos += len(chunk)
        return ""
    # Prefer reading from ImageDocument for up to max_bytes
    with path.open("rb") as f:
        f.seek(idx)
        return f.read(max_bytes).decode("utf-8", errors="ignore")


def _f(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        return float(str(raw).strip())
    except ValueError:
        return None


def _mag_from_name(name: str | None) -> float | None:
    if not name:
        return None
    m = _OBJ_X.search(name)
    if not m:
        return None
    return _f(m.group("mag"))


def parse_czi_acquisition_meta(path: Path | str) -> CziAcquisitionMeta | None:
    """Extract objective magnification / pixel size from a ``.czi`` file."""
    p = Path(path)
    if not p.is_file() or p.suffix.lower() != ".czi":
        return None
    try:
        xml = _extract_czi_metadata_xml(p)
    except OSError:
        return None
    if not xml:
        return None

    # Prefer AutoScaling ObjectiveName (actual used lens) over instrument catalog list
    obj = None
    m_scale = _OBJ_NAME_SCALING.search(xml)
    if m_scale:
        obj = m_scale.group("v").strip()
    mag = None
    m_nom = _NOM_MAG.search(xml)
    if m_nom:
        mag = _f(m_nom.group("v"))
    if mag is None:
        mag = _mag_from_name(obj)
    if mag is None:
        # Fall back to first Objective Name= that includes Nx
        for m in _OBJ_NAME_ATTR.finditer(xml):
            cand = m.group("v").strip()
            hit = _mag_from_name(cand)
            if hit is not None:
                obj = obj or cand
                mag = hit
                break
    if obj is None:
        m0 = _OBJ_NAME_ATTR.search(xml)
        if m0:
            obj = m0.group("v").strip()

    na = None
    m_na = _LENS_NA.search(xml)
    if m_na:
        na = _f(m_na.group("v"))

    px_m = None
    for m in _DIST_XY.finditer(xml):
        if m.group("axis").upper() == "X":
            px_m = _f(m.group("v"))
            break
    if px_m is None:
        m = _DIST_XY.search(xml)
        if m:
            px_m = _f(m.group("v"))
    px_um = px_m * 1e6 if px_m is not None and px_m > 0 else None

    n_pix = None
    m_sx = _SIZE_X.search(xml) or _SIZE_Y.search(xml)
    if m_sx:
        try:
            n_pix = int(float(m_sx.group("v").strip()))
        except ValueError:
            n_pix = None
    fov = None
    if px_um is not None and n_pix is not None and n_pix > 0:
        fov = px_um * n_pix

    if mag is None and px_um is None and obj is None:
        return None
    return CziAcquisitionMeta(
        path=str(p),
        magnification=mag,
        objective_name=obj,
        numerical_aperture=na,
        pixel_size_um=px_um,
        field_of_view_um=fov,
        n_pixels=n_pix,
    )


def collect_czi_acquisition_meta(
    paths: list[Path | str],
    *,
    max_files: int = 48,
) -> list[CziAcquisitionMeta]:
    out: list[CziAcquisitionMeta] = []
    for raw in paths[:max_files]:
        meta = parse_czi_acquisition_meta(raw)
        if meta is not None:
            out.append(meta)
    return out


def write_minimal_czi_fixture(
    path: Path | str,
    *,
    magnification: float = 40.0,
    objective_name: str = "Plan-Apochromat 40x/0.95",
    numerical_aperture: float = 0.95,
    n_pixels: int = 1024,
    pixel_size_um: float = 0.25,
) -> Path:
    """Write a tiny ZISRAW-like stub with UTF-8 metadata XML for CI."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    px_m = pixel_size_um * 1e-6
    xml = (
        '<?xml version="1.0"?>'
        "<ImageDocument>"
        "<Metadata><Information><Image>"
        f"<SizeX>{n_pixels}</SizeX><SizeY>{n_pixels}</SizeY>"
        "<Dimensions><Tracks><Distance Id=\"X\">"
        f"<Value>{px_m:.12e}</Value></Distance>"
        f"<Distance Id=\"Y\"><Value>{px_m:.12e}</Value></Distance>"
        "</Tracks></Dimensions>"
        "<Instrument><Objectives>"
        f'<Objective Id="Objective:1" Name="{objective_name}">'
        f"<LensNA>{numerical_aperture}</LensNA>"
        f"<NominalMagnification>{magnification:g}</NominalMagnification>"
        "</Objective></Objectives></Instrument>"
        "</Image></Information>"
        "<Scaling><AutoScaling>"
        f"<ObjectiveName>{objective_name}</ObjectiveName>"
        "</AutoScaling></Scaling>"
        "</Metadata></ImageDocument>"
    )
    preamble = b"ZISRAWFILE\x00\x00\x00\x00\x00\x00"
    p.write_bytes(preamble + xml.encode("utf-8"))
    return p
