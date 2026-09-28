"""出版 Figure 多パネル間の点列同一性 → データ取り違え Warning。"""

from __future__ import annotations

from pathlib import Path

from pre_peer_checker.parsers.pdf_panel_plots import (
    extract_multipanel_figure_pdf,
    find_identical_panel_pairs,
    find_identical_panels_across_pages,
)
from pre_peer_checker.report.figure_previews import (
    _infer_supp_figure_id,
    figure_id_from_label,
)
from pre_peer_checker.warnings import WarningItem, WarningTag


def warnings_from_figure_panel_identity(
    figure_pdfs: list[Path],
    *,
    min_groups: int = 2,
    min_score: float = 0.98,
) -> tuple[list[WarningItem], list[dict]]:
    """Scan publication figure PDFs for cross-panel identical ggplot clouds.

    Works without Excel/Rplot residue — vector markers on the PDF alone.
    Includes same-page distant panels and cross-page pairs.
    """
    warnings: list[WarningItem] = []
    arts: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()

    for pdf in figure_pdfs:
        try:
            pages = extract_multipanel_figure_pdf(pdf, max_pages=12)
        except Exception:
            continue
        for page in pages:
            arts.append(
                {
                    "path": str(page.path),
                    "page": page.page_index,
                    "figure": page.figure_label,
                    "figure_id": (
                        figure_id_from_label(page.figure_label)
                        or _infer_supp_figure_id(page.path, page.page_index)
                    ),
                    "panels": [
                        {
                            "panel": p.panel,
                            "n_markers": p.n_markers,
                            "n_groups": len(p.groups),
                            "group_ns": [g.n for g in p.groups],
                        }
                        for p in page.panels
                    ],
                }
            )
            for a, b, scores in find_identical_panel_pairs(
                page, min_groups=min_groups, min_score=min_score
            ):
                letters = tuple(sorted((a.panel, b.panel)))
                key = (str(page.path.resolve()), f"p{page.page_index}", letters[0], letters[1])
                if key in seen:
                    continue
                seen.add(key)
                fig = page.figure_label or page.path.name
                warnings.append(
                    WarningItem(
                        tag=WarningTag.DATA_SWAP,
                        title=f"{fig} panels {a.panel}/{b.panel}: 点列が実質同一",
                        location=f"{page.path.name} p{page.page_index} {a.panel}↔{b.panel}",
                        reason=(
                            f"パネル間で {len(scores)} 群の点列が一致"
                            f"（score={', '.join(f'{s:.3f}' for s in scores)}）。"
                            "別条件のはずの定量パネルが同一データ由来の可能性があります。"
                        ),
                        sources=[str(page.path)],
                        metadata={
                            "pattern_id": "P-DATA-SWAP-CROSS-CONDITION",
                            "figure": fig,
                            "panel_a": a.panel,
                            "panel_b": b.panel,
                            "page": page.page_index,
                            "scores": scores,
                            "source": "pdf_panels_only",
                        },
                    )
                )

        for a, b, scores, pa, pb in find_identical_panels_across_pages(
            pages, min_groups=min_groups, min_score=min_score
        ):
            letters = tuple(sorted((a.panel, b.panel)))
            key = (
                str(pa.path.resolve()),
                f"p{pa.page_index}-p{pb.page_index}",
                letters[0],
                letters[1],
            )
            if key in seen:
                continue
            seen.add(key)
            fig = pa.figure_label or pb.figure_label or pa.path.name
            warnings.append(
                WarningItem(
                    tag=WarningTag.DATA_SWAP,
                    title=f"{fig} panels {a.panel}/{b.panel}: ページ横断で点列が実質同一",
                    location=(
                        f"{pa.path.name} p{pa.page_index}:{a.panel} ↔ "
                        f"p{pb.page_index}:{b.panel}"
                    ),
                    reason=(
                        f"ページ横断で {len(scores)} 群の点列が一致"
                        f"（score={', '.join(f'{s:.3f}' for s in scores)}）。"
                        "Excel/Rplot 残渣が無くても PDF 点列のみで検知。"
                    ),
                    sources=[str(pa.path)],
                    metadata={
                        "pattern_id": "P-DATA-SWAP-CROSS-CONDITION",
                        "figure": fig,
                        "panel_a": a.panel,
                        "panel_b": b.panel,
                        "page_a": pa.page_index,
                        "page_b": pb.page_index,
                        "scores": scores,
                        "source": "pdf_panels_cross_page",
                    },
                )
            )
    return warnings, arts


def is_publication_figure_pdf(path: Path) -> bool:
    name = path.name.lower()
    if name.startswith("rplot"):
        return False
    if "editorial" in name:
        return False
    return any(k in name for k in ("fig", "figure")) and path.suffix.lower() == ".pdf"
