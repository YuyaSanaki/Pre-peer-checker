"""画像処理: 顕微鏡ローダと重複スキャン."""

from pre_peer_checker.imaging.duplicate_scan import (
    DinoDuplicateScanner,
    ImagePairMatch,
    scan_image_duplicates,
    scan_image_duplicates_auto,
)
from pre_peer_checker.imaging.lightglue_match import (
    MIN_MATCHES_BY_FEATURES,
    PreciseMatchResult,
    default_min_matches,
    lightglue_available,
    verify_image_pair,
)
from pre_peer_checker.imaging.corpus_scan import CorpusScanResult, scan_against_corpus
from pre_peer_checker.imaging.microscopy import (
    ImageFrame,
    export_frames_as_png,
    extract_czi_scenes,
    extract_lif_series,
    load_microscope_image,
    load_raster_image,
    try_load_frames,
)
from pre_peer_checker.imaging.microscopy_scan import (
    MicroscopyScanResult,
    scan_microscopy_duplicates,
    select_image_paths,
)

__all__ = [
    "DinoDuplicateScanner",
    "ImagePairMatch",
    "scan_image_duplicates",
    "scan_image_duplicates_auto",
    "PreciseMatchResult",
    "MIN_MATCHES_BY_FEATURES",
    "default_min_matches",
    "lightglue_available",
    "verify_image_pair",
    "CorpusScanResult",
    "scan_against_corpus",
    "ImageFrame",
    "extract_czi_scenes",
    "extract_lif_series",
    "load_microscope_image",
    "load_raster_image",
    "try_load_frames",
    "export_frames_as_png",
    "MicroscopyScanResult",
    "scan_microscopy_duplicates",
    "select_image_paths",
]
