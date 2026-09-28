"""顕微鏡ファイル／sidecar／LIF・CZI XML メタ × Legend — P-SCALE-MAG-INCONSISTENT."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Protocol

from pre_peer_checker.engine.scale_mag import MagClaim, extract_mag_claims
from pre_peer_checker.imaging.czi_meta import (
    CziAcquisitionMeta,
    collect_czi_acquisition_meta,
)
from pre_peer_checker.imaging.lif_meta import (
    LifAcquisitionMeta,
    collect_lif_acquisition_meta,
)
from pre_peer_checker.warnings import WarningItem, WarningTag

_MAG_KEYS = (
    "objective_magnification",
    "magnification",
    "mag",
    "objective_mag",
    "nominal_magnification",
)


class _AcqMeta(Protocol):
    path: str
    field_of_view_um: float | None
    pixel_size_um: float | None
    n_pixels: int | None

    def as_frame_meta(self) -> dict[str, Any]: ...


def load_sidecar_meta(path: Path | str) -> dict[str, Any]:
    """Read ``stem.meta.json`` or ``name.json`` next to an image/LIF/CZI."""
    p = Path(path)
    candidates = [
        p.with_suffix(p.suffix + ".meta.json"),
        p.with_name(p.stem + ".meta.json"),
        p.with_suffix(".meta.json"),
    ]
    for c in candidates:
        if not c.is_file():
            continue
        try:
            data = json.loads(c.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict):
            return data
    return {}


def magnification_from_meta(meta: dict[str, Any] | None) -> float | None:
    if not meta:
        return None
    for key in _MAG_KEYS:
        if key not in meta:
            continue
        raw = meta[key]
        if isinstance(raw, (int, float)):
            return float(raw)
        if isinstance(raw, str):
            m = re.search(r"(\d+(?:\.\d+)?)", raw)
            if m:
                return float(m.group(1))
    obj = meta.get("objective_name")
    if isinstance(obj, str):
        m = re.search(r"(\d+(?:\.\d+)?)\s*[x×X]\b", obj)
        if m:
            return float(m.group(1))
    for nest_key in ("acquisition", "instrument", "optics"):
        nested = meta.get(nest_key)
        if isinstance(nested, dict):
            hit = magnification_from_meta(nested)
            if hit is not None:
                return hit
    return None


def collect_image_meta_rows(
    image_paths: list[Path | str],
    *,
    frame_meta_by_source: dict[str, dict[str, Any]] | None = None,
    lif_meta: list[LifAcquisitionMeta] | None = None,
    czi_meta: list[CziAcquisitionMeta] | None = None,
) -> list[tuple[Path, dict[str, Any], float]]:
    """Return (path, meta, magnification) for paths that expose a numeric mag."""
    frame_meta_by_source = dict(frame_meta_by_source or {})
    for acq in list(lif_meta or []) + list(czi_meta or []):
        frame_meta_by_source[acq.path] = {
            **frame_meta_by_source.get(acq.path, {}),
            **acq.as_frame_meta(),
        }
        try:
            frame_meta_by_source[str(Path(acq.path).resolve())] = frame_meta_by_source[
                acq.path
            ]
        except OSError:
            pass

    out: list[tuple[Path, dict[str, Any], float]] = []
    seen: set[str] = set()
    for raw in image_paths:
        path = Path(raw)
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        meta: dict[str, Any] = {}
        src_key = str(path.resolve()) if path.exists() else str(path)
        if src_key in frame_meta_by_source:
            meta.update(frame_meta_by_source[src_key])
        elif str(path) in frame_meta_by_source:
            meta.update(frame_meta_by_source[str(path)])
        suf = path.suffix.lower()
        if suf == ".lif" and "objective_magnification" not in meta:
            from pre_peer_checker.imaging.lif_meta import parse_lif_acquisition_meta

            parsed = next(
                (x for x in (lif_meta or []) if Path(x.path) == path),
                None,
            ) or parse_lif_acquisition_meta(path)
            if parsed is not None:
                meta.update(parsed.as_frame_meta())
        elif suf == ".czi" and "objective_magnification" not in meta:
            from pre_peer_checker.imaging.czi_meta import parse_czi_acquisition_meta

            parsed_c = next(
                (x for x in (czi_meta or []) if Path(x.path) == path),
                None,
            ) or parse_czi_acquisition_meta(path)
            if parsed_c is not None:
                meta.update(parsed_c.as_frame_meta())
        meta.update(load_sidecar_meta(path))
        mag = magnification_from_meta(meta)
        if mag is None:
            continue
        out.append((path, meta, mag))
    return out


def _scale_bar_vs_fov_warnings(
    claims: list[MagClaim],
    acq_metas: list[_AcqMeta],
) -> list[WarningItem]:
    """Scale-bar length that exceeds acquisition FOV is inconsistent."""
    scale_claims = [c for c in claims if c.unit == "scale_um"]
    if not scale_claims:
        return []
    warnings: list[WarningItem] = []
    seen: set[str] = set()
    for am in acq_metas:
        fov = am.field_of_view_um
        if fov is None or fov <= 0:
            continue
        fmt = Path(am.path).suffix.lower().lstrip(".") or "microscopy"
        for claim in scale_claims:
            if claim.value <= fov * 1.05:
                continue
            key = f"{am.path}::{claim.value}"
            if key in seen:
                continue
            seen.add(key)
            warnings.append(
                WarningItem(
                    tag=WarningTag.CONFIG_MISMATCH,
                    title="スケールバー記載が顕微鏡視野より大きい",
                    location=(
                        f"{Path(am.path).name} FOV≈{fov:.4g}µm ↔ Legend {claim.value:g}µm"
                    ),
                    reason=(
                        f"{fmt.upper()} の視野幅は約 {fov:.4g} µm"
                        + (
                            f"（pixel={am.pixel_size_um:.4g} µm, n={am.n_pixels}）"
                            if am.pixel_size_um and am.n_pixels
                            else ""
                        )
                        + f" なのに、Legend に「{claim.span}」"
                        f"（{claim.value:g} µm）があります。"
                        "スケールバーと取得条件の突合を確認してください。"
                    ),
                    sources=[am.path],
                    metadata={
                        "pattern_id": "P-SCALE-MAG-INCONSISTENT",
                        "grounding": f"{fmt}_fov_vs_scale_bar",
                        "field_of_view_um": fov,
                        "pixel_size_um": am.pixel_size_um,
                        "legend_scale_um": claim.value,
                        "legend_span": claim.span,
                    },
                )
            )
    return warnings


def warnings_from_microscopy_meta(
    texts: list[str],
    image_paths: list[Path | str],
    *,
    frame_meta_by_source: dict[str, dict[str, Any]] | None = None,
    lif_meta: list[LifAcquisitionMeta] | None = None,
    czi_meta: list[CziAcquisitionMeta] | None = None,
    rel_tol: float = 0.12,
) -> list[WarningItem]:
    """File/LIF/CZI/sidecar magnification (and FOV vs scale bar) vs Legend claims."""
    claims = extract_mag_claims(texts)
    paths = [Path(p) for p in image_paths]
    lif_paths = [p for p in paths if p.suffix.lower() == ".lif"]
    czi_paths = [p for p in paths if p.suffix.lower() == ".czi"]
    parsed_lif = list(lif_meta or [])
    parsed_czi = list(czi_meta or [])
    if lif_paths and not parsed_lif:
        parsed_lif = collect_lif_acquisition_meta(lif_paths)
    if czi_paths and not parsed_czi:
        parsed_czi = collect_czi_acquisition_meta(czi_paths)

    warnings: list[WarningItem] = []
    mag_claims = [c for c in claims if c.unit == "magnification"]
    rows = collect_image_meta_rows(
        paths,
        frame_meta_by_source=frame_meta_by_source,
        lif_meta=parsed_lif,
        czi_meta=parsed_czi,
    )
    seen: set[str] = set()
    for path, meta, file_mag in rows:
        for claim in mag_claims:
            if abs(file_mag - claim.value) / max(file_mag, claim.value, 1.0) <= rel_tol:
                continue
            key = f"{path.resolve()}::{claim.value}"
            if key in seen:
                continue
            seen.add(key)
            obj = meta.get("objective_name")
            suf = path.suffix.lower()
            if suf == ".lif":
                grounding = "lif_xml"
            elif suf == ".czi":
                grounding = "czi_xml"
            else:
                grounding = "microscopy_meta"
            warnings.append(
                WarningItem(
                    tag=WarningTag.CONFIG_MISMATCH,
                    title="顕微鏡メタの倍率と Legend 記載が不一致",
                    location=f"{path.name} meta={file_mag:g}× ↔ Legend {claim.value:g}×",
                    reason=(
                        f"顕微鏡メタの倍率が {file_mag:g}×"
                        + (f"（{obj}）" if obj else "")
                        + f" なのに、Legend／Methods には「{claim.span}」"
                        f"（{claim.value:g}×）があります。"
                        "取得条件と記載の突合を確認してください。"
                    ),
                    sources=[str(path)],
                    metadata={
                        "pattern_id": "P-SCALE-MAG-INCONSISTENT",
                        "grounding": grounding,
                        "file_magnification": file_mag,
                        "legend_magnification": claim.value,
                        "legend_span": claim.span,
                        "objective_name": obj,
                        "pixel_size_um": meta.get("pixel_size_um"),
                        "field_of_view_um": meta.get("field_of_view_um"),
                    },
                )
            )

    warnings.extend(
        _scale_bar_vs_fov_warnings(claims, list(parsed_lif) + list(parsed_czi))
    )
    return warnings
