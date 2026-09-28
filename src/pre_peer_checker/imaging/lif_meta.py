"""Leica LIF 取得メタ抽出（readlif 不要・UTF-16 XML ヘッダ直読み）."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_MAG_ATTR = re.compile(r'Magnification="(?P<v>[^"]+)"', re.I)
_OBJ_ATTR = re.compile(r'ObjectiveName="(?P<v>[^"]+)"', re.I)
_NA_ATTR = re.compile(r'NumericalAperture="(?P<v>[^"]+)"', re.I)
_OBJ_X = re.compile(r"(?P<mag>\d+(?:\.\d+)?)\s*[x×X]\b")
_DIM = re.compile(
    r'<DimensionDescription\b(?P<body>[^>]+)>',
    re.I,
)
_DIM_FIELD = re.compile(
    r'\b(?P<k>DimID|NumberOfElements|Length|Unit)="(?P<v>[^"]+)"',
    re.I,
)


@dataclass(frozen=True)
class LifAcquisitionMeta:
    path: str
    magnification: float | None = None
    objective_name: str | None = None
    numerical_aperture: float | None = None
    pixel_size_um: float | None = None
    field_of_view_um: float | None = None
    n_pixels: int | None = None
    source: str = "lif_xml"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def as_frame_meta(self) -> dict[str, Any]:
        """Keys understood by ``microscopy_meta.magnification_from_meta``."""
        out: dict[str, Any] = {"format": "lif", "meta_source": self.source}
        if self.magnification is not None:
            out["objective_magnification"] = self.magnification
            out["magnification"] = self.magnification
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


def _decode_lif_xml_header(path: Path, *, max_bytes: int = 4_000_000) -> str:
    """Return UTF-16-LE XML header text embedded in a LIF file (best-effort)."""
    raw = path.read_bytes()[:max_bytes]
    needle = "LMSDataContainerHeader".encode("utf-16-le")
    idx = raw.find(needle)
    if idx < 0:
        # Some exports may store ASCII XML
        idx_ascii = raw.find(b"LMSDataContainerHeader")
        if idx_ascii < 0:
            return ""
        start = raw.rfind(b"<", max(0, idx_ascii - 40), idx_ascii)
        if start < 0:
            start = idx_ascii
        return raw[start : start + max_bytes].decode("utf-8", errors="ignore")
    start = raw.rfind("<".encode("utf-16-le"), max(0, idx - 40), idx)
    if start < 0:
        start = idx
    return raw[start : start + max_bytes].decode("utf-16-le", errors="ignore")


def _first_float(matches: list[str]) -> float | None:
    for raw in matches:
        try:
            return float(str(raw).strip())
        except ValueError:
            continue
    return None


def _mag_from_objective_name(name: str | None) -> float | None:
    if not name:
        return None
    m = _OBJ_X.search(name)
    if not m:
        return None
    try:
        return float(m.group("mag"))
    except ValueError:
        return None


def _xy_pixel_size_um(xml: str) -> tuple[float | None, float | None, int | None]:
    """Return (pixel_size_um, fov_um, n_pixels) from first X/Y dimension."""
    for m in _DIM.finditer(xml):
        fields = {k.lower(): v for k, v in _DIM_FIELD.findall(m.group("body"))}
        dim_id = fields.get("dimid", "")
        # DimID 1/2 are X/Y in Leica LMS XML
        if dim_id not in {"1", "2", "X", "Y", "x", "y"}:
            continue
        try:
            n = int(float(fields.get("numberofelements") or "0"))
            length = float(fields.get("length") or "0")
        except ValueError:
            continue
        if n <= 0 or length <= 0:
            continue
        unit = (fields.get("unit") or "m").lower()
        if unit in {"m", "meter", "metres", "meters"}:
            length_um = length * 1e6
        elif unit in {"µm", "um", "micrometer", "micrometre"}:
            length_um = length
        elif unit in {"mm"}:
            length_um = length * 1e3
        else:
            # Unknown unit — skip rather than invent
            continue
        return length_um / n, length_um, n
    return None, None, None


def parse_lif_acquisition_meta(path: Path | str) -> LifAcquisitionMeta | None:
    """Extract objective magnification / pixel size from a ``.lif`` file."""
    p = Path(path)
    if not p.is_file() or p.suffix.lower() != ".lif":
        return None
    try:
        xml = _decode_lif_xml_header(p)
    except OSError:
        return None
    if not xml or "LMSDataContainerHeader" not in xml:
        return None

    mag = _first_float(_MAG_ATTR.findall(xml))
    obj_names = [s.strip() for s in _OBJ_ATTR.findall(xml) if s.strip()]
    obj = obj_names[0] if obj_names else None
    if mag is None:
        mag = _mag_from_objective_name(obj)
    na = _first_float(_NA_ATTR.findall(xml))
    px, fov, n_pix = _xy_pixel_size_um(xml)
    if mag is None and px is None and obj is None:
        return None
    return LifAcquisitionMeta(
        path=str(p),
        magnification=mag,
        objective_name=obj,
        numerical_aperture=na,
        pixel_size_um=px,
        field_of_view_um=fov,
        n_pixels=n_pix,
    )


def collect_lif_acquisition_meta(
    paths: list[Path | str],
    *,
    max_files: int = 48,
) -> list[LifAcquisitionMeta]:
    out: list[LifAcquisitionMeta] = []
    for raw in paths[:max_files]:
        meta = parse_lif_acquisition_meta(raw)
        if meta is not None:
            out.append(meta)
    return out


def write_minimal_lif_fixture(
    path: Path | str,
    *,
    magnification: float = 40.0,
    objective_name: str = "HC PL APO 40x/0.85 DRY",
    n_pixels: int = 512,
    field_of_view_um: float = 256.0,
) -> Path:
    """Write a tiny UTF-16 LIF-like stub for CI (header only, not a full image)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    length_m = (field_of_view_um * 1e-6)
    xml = (
        f'<LMSDataContainerHeader Version="2">'
        f'<Element Name="synthetic">'
        f"<Data><Image>"
        f'<Attachment Name="HardwareSetting">'
        f'<ATLConfocalSettingDefinition Magnification="{magnification:g}" '
        f'ObjectiveName="{objective_name}" NumericalAperture="0.85"/>'
        f"</Attachment>"
        f"<ImageDescription><Dimensions>"
        f'<DimensionDescription DimID="1" NumberOfElements="{n_pixels}" '
        f'Length="{length_m:.6e}" Unit="m"/>'
        f'<DimensionDescription DimID="2" NumberOfElements="{n_pixels}" '
        f'Length="{length_m:.6e}" Unit="m"/>'
        f"</Dimensions></ImageDescription>"
        f"</Image></Data></Element>"
        f"</LMSDataContainerHeader>"
    )
    # Real LIF uses a short binary preamble; parser scans for UTF-16 header.
    preamble = b"\x70\x00\x00\x00\x20\x04\x00"
    p.write_bytes(preamble + xml.encode("utf-16-le"))
    return p
