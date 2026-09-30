"""論文 PDF（本文と図が 1 本の PDF）から Figure 領域を切り出す。

Fig*.pdf が別添されていない出版 PDF・PDF 原稿でもパネル文字／パネル分割が動くよう、
キャプション見出し（``Figure 1 |`` / ``Fig. 2.`` / ``Extended Data Figure 3 |`` …）の
直上にある図要素（画像・ベクター・図中の短い文字）を集め、1 図 1 ページの PDF にする。

- 段組み: キャプションと同じ段の図要素を起点に広げる。別キャプションの段に属する要素と
  本文ブロックは取り込まない（1 ページに左右 2 図が載る Letter 型）。全幅の図は、
  キャプションの無い隣の段へ広がる。
- キャプションの上に図が無いときは、キャプションの無い直前ページ（図だけのページ →
  次ページに Legend の型）を使う。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CACHE_DIR = REPO_ROOT / "cache" / "article_figures"
_VERSION = "1"

_CAPTION_RE = re.compile(
    r"^\s*(?:(Extended\s+Data|Supplementa(?:ry|l))\s+)?(?:Fig\.?|Figure)\s*(S?\d+)"
    r"(?!\s*\(Continued\))\s*(?:[|.:]|$)",
    re.IGNORECASE,
)
_MARGIN_TOP = 0.06
_MARGIN_BOTTOM = 0.95
_REACH = 12.0
_GUTTER = 6.0
_MIN_W = 0.15
_MIN_H = 0.08
_MIN_ALONE_AREA = 0.2


@dataclass(frozen=True)
class ArticleFigure:
    key: str  # figure_num_key 互換: "1" / "S2" / "ED3"
    label: str  # "Figure 1" / "Extended Data Figure 3"
    page_index: int
    clip: tuple[float, float, float, float]

    @property
    def filename(self) -> str:
        if self.key.startswith("ED"):
            return f"Extended_Data_Fig{self.key[2:]}.pdf"
        return f"Fig{self.key}.pdf"


@dataclass
class _Text:
    rect: object
    first_line: str
    line_rects: list
    n_lines: int
    n_words: int
    n_chars: int

    @property
    def is_prose(self) -> bool:
        lines = max(self.n_lines, 1)
        return self.n_words >= 8 and (self.n_words / lines >= 4 or self.n_chars / lines >= 22)


def _caption_key(kind: str | None, num: str) -> tuple[str, str]:
    k = (kind or "").lower()
    if k.startswith("extended"):
        return f"ED{num}", f"Extended Data Figure {num}"
    if k.startswith("supplement") or num.upper().startswith("S"):
        n = num.upper().lstrip("S")
        return f"S{n}", f"Supplementary Figure S{n}"
    return num, f"Figure {num}"


def _texts(page) -> list[_Text]:
    import pymupdf

    out: list[_Text] = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        lines = [
            (pymupdf.Rect(ln["bbox"]), t)
            for ln in block.get("lines") or []
            if (t := "".join(s["text"] for s in ln.get("spans") or []).strip())
        ]
        if not lines:
            continue
        joined = " ".join(t for _, t in lines)
        out.append(
            _Text(
                rect=pymupdf.Rect(block["bbox"]),
                first_line=lines[0][1],
                line_rects=[r for r, _ in lines],
                n_lines=len(lines),
                n_words=len(joined.split()),
                n_chars=len(joined),
            )
        )
    return out


def _hoverlap(a, b) -> float:
    return max(0.0, min(a.x1, b.x1) - max(a.x0, b.x0))


class _PageLayout:
    """Captions, text obstacles and artwork rects of one page (running header/footer dropped)."""

    def __init__(self, page) -> None:
        import pymupdf

        rect = page.rect
        self.rect = rect
        top, bottom = rect.y0 + rect.height * _MARGIN_TOP, rect.y0 + rect.height * _MARGIN_BOTTOM
        self.top = top

        def in_margin(r) -> bool:
            return r.y1 <= top or r.y0 >= bottom

        self.captions: list[tuple[str, str, object]] = []
        self.obstacles: list = []
        self.art: list = []
        for t in _texts(page):
            if in_margin(t.rect):
                continue
            m = _CAPTION_RE.match(t.first_line)
            if m:
                key, label = _caption_key(m.group(1), m.group(2))
                self.captions.append((key, label, t.rect))
                self.obstacles.append(t.rect)
            elif t.is_prose:
                self.obstacles.append(t.rect)
            else:
                self.art.extend(t.line_rects)
        for info in page.get_image_info():
            r = pymupdf.Rect(info["bbox"]) & rect
            if r.width > 1 and r.height > 1 and not in_margin(r):
                self.art.append(r)
        page_area = rect.width * rect.height
        for d in page.get_drawings():
            r = pymupdf.Rect(d["rect"])
            if r.width < 0.5:
                r.x1 = r.x0 + 0.5
            if r.height < 0.5:
                r.y1 = r.y0 + 0.5
            r &= rect
            if r.is_empty or in_margin(r):
                continue
            if r.height < 2 and r.width >= 0.8 * rect.width:
                continue  # running rules
            if r.width * r.height >= 0.9 * page_area:
                continue  # page background
            self.art.append(r)

    def band(self, cap) -> tuple[float, float]:
        """Vertical span above ``cap`` up to the nearest text block in its column."""
        upper = self.top
        for o in self.obstacles:
            if o is cap or o.y1 > cap.y0 + 1:
                continue
            if _hoverlap(o, cap) > 0.3 * min(o.width, cap.width):
                upper = max(upper, o.y1)
        return upper, cap.y0


def _figure_clips(layout: _PageLayout) -> dict[str, object]:
    """Caption key -> artwork clip on this page (captions without artwork above are absent)."""
    import pymupdf

    bands = {}
    seeds = {}
    for key, _label, cap in layout.captions:
        upper, lower = layout.band(cap)
        column = pymupdf.Rect(cap.x0, upper, cap.x1, lower)
        own = [
            i
            for i, r in enumerate(layout.art)
            if r.y0 >= upper - 2 and r.y1 <= lower + 2 and _hoverlap(r, column) > 0
        ]
        bands[key] = (upper, lower, column)
        if own:
            seeds[key] = own

    out: dict[str, object] = {}
    for key, own in seeds.items():
        upper, lower, _column = bands[key]
        claimed = [
            bands[k][2] + (-_GUTTER, 0, _GUTTER, 0) for k in seeds if k != key
        ]
        pending = [
            r
            for i, r in enumerate(layout.art)
            if i not in own
            and r.y0 >= upper - 2
            and r.y1 <= lower + 2
            and not any(r.intersects(c) for c in claimed)
        ]
        clip = pymupdf.Rect(layout.art[own[0]])
        for i in own[1:]:
            clip |= layout.art[i]
        grew = True
        while grew:
            zone = clip + (-_REACH, -_REACH, _REACH, _REACH)
            near = [r for r in pending if r.intersects(zone)]
            pending = [r for r in pending if not r.intersects(zone)]
            for r in near:
                clip |= r
            grew = bool(near)
        clip = (clip + (-3, -3, 3, 3)) & layout.rect
        if clip.width >= _MIN_W * layout.rect.width and clip.height >= _MIN_H * layout.rect.height:
            out[key] = clip
    return out


def _art_only_clip(layout: _PageLayout):
    import pymupdf

    if layout.captions or not layout.art:
        return None
    clip = pymupdf.Rect(layout.art[0])
    for r in layout.art[1:]:
        clip |= r
    area = layout.rect.width * layout.rect.height
    return clip if clip.width * clip.height >= _MIN_ALONE_AREA * area else None


def find_article_figures(pdf: Path | str) -> list[ArticleFigure]:
    """Figures located in a paper PDF, in caption order (first caption per key wins)."""
    import pymupdf

    found: dict[str, ArticleFigure] = {}
    with pymupdf.open(str(pdf)) as doc:
        layouts = [_PageLayout(page) for page in doc]
    for i, layout in enumerate(layouts):
        clips = _figure_clips(layout)
        for key, label, _cap in layout.captions:
            if key in found:
                continue
            clip = clips.get(key)
            page_index = i
            if clip is None and i > 0:
                clip = _art_only_clip(layouts[i - 1])
                page_index = i - 1
            if clip is None:
                continue
            found[key] = ArticleFigure(
                key=key,
                label=label,
                page_index=page_index,
                clip=(float(clip.x0), float(clip.y0), float(clip.x1), float(clip.y1)),
            )
    return list(found.values())


def write_figure_pdf(src_doc, fig: ArticleFigure, dest: Path) -> Path:
    """One-page PDF whose page box is the figure clip.

    The source page is placed as a clipped XObject on a page of the clip's size, so
    text of neighbouring columns and the caption falls outside the mediabox and is not
    extracted (panel letters are read from this PDF's text layer).
    """
    import pymupdf

    clip = pymupdf.Rect(fig.clip)
    out = pymupdf.open()
    try:
        page = out.new_page(width=clip.width, height=clip.height)
        page.show_pdf_page(page.rect, src_doc, fig.page_index, clip=clip)
        dest.parent.mkdir(parents=True, exist_ok=True)
        out.save(str(dest), garbage=3, deflate=True)
    finally:
        out.close()
    return dest


def _pdf_digest(pdf: Path) -> str:
    h = hashlib.sha256(_VERSION.encode())
    with pdf.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def export_article_figures(
    pdf: Path | str,
    *,
    cache_dir: Path | None = None,
) -> list[Path]:
    """Write ``Fig1.pdf`` / ``FigS2.pdf`` / ``Extended_Data_Fig3.pdf`` crops for ``pdf``.

    Output lives under ``cache/article_figures/<digest>/`` (reused on re-runs) so HTML
    previews can still render the crops after the verification run finishes.
    """
    import pymupdf

    pdf = Path(pdf)
    root = Path(cache_dir) if cache_dir is not None else DEFAULT_CACHE_DIR
    out_dir = root / _pdf_digest(pdf)
    done = out_dir / ".complete"
    if done.is_file():
        names = done.read_text(encoding="utf-8").split()
        cached = [out_dir / n for n in names]
        if all(p.is_file() for p in cached):
            return cached
    figures = find_article_figures(pdf)
    written: list[Path] = []
    with pymupdf.open(str(pdf)) as src:
        for fig in figures:
            written.append(write_figure_pdf(src, fig, out_dir / fig.filename))
    out_dir.mkdir(parents=True, exist_ok=True)
    done.write_text("".join(p.name + "\n" for p in written), encoding="utf-8")
    return written
