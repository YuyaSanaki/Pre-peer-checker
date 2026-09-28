"""Side-by-side compare previews for image-reuse warnings in the HTML report."""

from __future__ import annotations

import json
from pathlib import Path

from panel_fixtures import page, photo

from pre_peer_checker.report.figure_compare import attach_figure_compares, build_figure_compare
from pre_peer_checker.report.html_report import render_html_report
from pre_peer_checker.warnings import WarningItem, WarningTag


def _panel_warning(tmp_path: Path) -> WarningItem:
    shared = photo(1)
    q = tmp_path / "tmpdir" / "Fig3_p0_x25.png"
    q.parent.mkdir()
    page([photo(2), shared]).save(q)
    c = tmp_path / "source_p5_x171.png"
    page([shared, photo(3), photo(4)], size=(1600, 1200)).save(c)
    box_q = [460, 120, 820, 480]
    box_c = [60, 120, 420, 480]
    return WarningItem(
        tag=WarningTag.IMAGE_REUSE,
        title="過去論文コーパスと同一写真（パネル単位）・出典未記載の疑い",
        location="Fig3_p0_x25.png ↔ source_p5_x171.png",
        reason="パネル単位の特徴点照合で一致 42 点。",
        sources=[str(q), str(c)],
        metadata={
            "pattern_id": "P-IMAGE-REUSE-UNCITED",
            "corpus_match": True,
            "panel": True,
            "panel_a": box_q,
            "panel_b": box_c,
            "panel_matches": [
                {"panel_a": box_q, "panel_b": box_c, "label_a": "左から2枚目",
                 "label_b": "左から1枚目", "matches": 42, "inliers": 40},
            ],
        },
    )


def test_compare_boxes_are_relative_to_each_image(tmp_path: Path):
    w = _panel_warning(tmp_path)
    cmp = build_figure_compare(w)
    assert cmp is not None
    left, right = cmp["sides"]
    assert left["role"].startswith("入力") and right["role"].startswith("参照")
    assert left["image_data_uri"].startswith("data:image/jpeg;base64,")
    assert left["boxes"][0]["left_pct"] == round(100 * 460 / 1400, 3)
    assert right["boxes"][0]["top_pct"] == round(100 * 120 / 1200, 3)
    assert (left["boxes"][0]["key"], right["boxes"][0]["key"]) == ("1A", "1B")
    pair = cmp["pairs"][0]
    assert (pair["box_a"], pair["box_b"]) == ("左から2枚目", "左から1枚目")
    assert pair["matches"] == 42


def test_compare_survives_temp_image_deletion(tmp_path: Path):
    w = _panel_warning(tmp_path)
    assert attach_figure_compares([w]) == 1
    Path(w.sources[0]).unlink()

    html = render_html_report([w], file_summary="t")
    assert 'class="fig-compare"' in html
    assert html.count('class="cmp-box is-focus"') == 2
    assert "入力（原稿）" in html and "参照（過去論文コーパス）" in html
    assert "組 1" in html and "一致 42 点" in html
    assert '<span class="cmp-key">1A</span>左から2枚目' in html
    assert "460,120" not in html
    assert '<span class="src-gone gone-note">' in html
    assert f'href="file://{w.sources[1]}"' in html
    assert "figure_compare" not in json.dumps(w.to_dict(), ensure_ascii=False)


def test_every_warning_gets_compare_and_images_are_embedded_once(tmp_path: Path):
    base = _panel_warning(tmp_path)
    warnings = [
        WarningItem(
            tag=base.tag,
            title=f"{base.title} {i}",
            location=base.location,
            reason=base.reason,
            sources=list(base.sources),
            metadata=dict(base.metadata),
        )
        for i in range(60)
    ]
    assert attach_figure_compares(warnings) == 60
    for s in base.sources:
        Path(s).unlink()

    html = render_html_report(warnings, file_summary="t")
    assert html.count('class="fig-compare"') == 60
    assert html.count("data:image/jpeg;base64,") == 2
    assert html.count("cmp-frame cmpimg-0") == 60


def test_no_compare_without_image_sources():
    w = WarningItem(
        tag=WarningTag.SAMPLE_SIZE,
        title="n mismatch",
        location="Fig 1",
        reason="x",
        sources=["/no/such/file.xlsx"],
    )
    assert build_figure_compare(w) is None
    html = render_html_report([w])
    assert 'class="fig-compare"' not in html
