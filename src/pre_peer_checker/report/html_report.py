"""HTML レポート生成（タグ別フィルタ付き・パネル↔Legend 連動ハイライト）."""

from __future__ import annotations

import html
import re
from collections import Counter, OrderedDict
from datetime import datetime
from pathlib import Path

from jinja2 import Template

from pre_peer_checker.warnings import WarningItem, WarningTag

# n=53 (B) / 12 (O) / n=12 (E), 12 (F)
_PANEL_N_SPAN_RE = re.compile(
    r"(?:n\s*=\s*)?(\d+)\s*\(\s*([A-Za-z]\d?)\s*\)",
    re.IGNORECASE,
)


def find_panel_n_spans(
    legend: str,
    panel: str,
    n: int | None = None,
) -> list[tuple[int, int]]:
    """Locate sample-size phrases for a panel inside a figure legend."""
    if not legend or not panel:
        return []
    panel_u = panel.upper()
    hits: list[tuple[int, int]] = []
    for m in _PANEL_N_SPAN_RE.finditer(legend):
        if m.group(2).upper() != panel_u:
            continue
        if n is not None and int(m.group(1)) != int(n):
            continue
        hits.append((m.start(), m.end()))
    if hits:
        return hits
    # n matched but letter case/form differed: retry without n filter
    if n is not None:
        for m in _PANEL_N_SPAN_RE.finditer(legend):
            if m.group(2).upper() == panel_u:
                hits.append((m.start(), m.end()))
        if hits:
            return hits
    return []


def annotate_legend_html(legend: str, rows: list[dict]) -> str:
    """Escape legend text and wrap panel-n phrases in <mark data-panel=…>."""
    if not legend:
        return ""
    marks: list[tuple[int, int, str]] = []
    for r in rows:
        panel = str(r.get("panel") or "").upper()
        if not panel:
            continue
        ms = r.get("manuscript") if isinstance(r.get("manuscript"), dict) else {}
        n = ms.get("n") if isinstance(ms, dict) else None
        for start, end in find_panel_n_spans(legend, panel, n):
            marks.append((start, end, panel))

    # Prefer longer spans first when choosing non-overlapping coverage
    marks.sort(key=lambda t: (t[0], -(t[1] - t[0]), t[2]))
    chosen: list[tuple[int, int, str]] = []
    for start, end, panel in marks:
        if any(not (end <= c0 or start >= c1) for c0, c1, _ in chosen):
            continue
        chosen.append((start, end, panel))
    chosen.sort(key=lambda t: t[0])

    parts: list[str] = []
    pos = 0
    for start, end, panel in chosen:
        if start > pos:
            parts.append(html.escape(legend[pos:start]))
        parts.append(
            f'<mark class="leg-hl" data-panel="{html.escape(panel)}">'
            f"{html.escape(legend[start:end])}</mark>"
        )
        pos = end
    parts.append(html.escape(legend[pos:]))
    return "".join(parts)


def _normalize_fig_key(label: str) -> str:
    s = (label or "").strip().lower()
    s = s.replace("supplementary figure", "figs").replace("supplementary fig.", "figs")
    s = s.replace("figure", "fig").replace("fig.", "fig")
    s = re.sub(r"\s+", "", s)
    return s


def _legend_for_figure(figure: str, legends_by_id: dict[str, str]) -> str:
    if figure in legends_by_id:
        return legends_by_id[figure] or ""
    key = _normalize_fig_key(figure)
    for fid, text in legends_by_id.items():
        if _normalize_fig_key(fid) == key:
            return text or ""
    return ""


def _preview_for_figure(figure: str, previews_by_id: dict[str, dict]) -> dict | None:
    if figure in previews_by_id:
        return previews_by_id[figure]
    key = _normalize_fig_key(figure)
    for fid, prev in previews_by_id.items():
        if _normalize_fig_key(fid) == key:
            return prev
    return None


def ensure_figure_previews(coverage: dict | None) -> dict | None:
    """Embed JPEG previews when coverage only has PDF source paths."""
    if not coverage:
        return coverage
    existing = coverage.get("figure_previews")
    if isinstance(existing, list) and existing:
        return coverage
    sources = coverage.get("figure_preview_sources") or []
    if not sources:
        return coverage
    from pre_peer_checker.report.figure_previews import build_figure_previews

    paths = [Path(p) for p in sources if p]
    try:
        previews = build_figure_previews(paths)
    except Exception:
        previews = []
    out = dict(coverage)
    out["figure_previews"] = previews
    return out


def build_n_matrix_sections(coverage: dict | None) -> list[dict]:
    """Group n_matrix rows by figure and attach Fig preview + annotated legend."""
    if not coverage:
        return []
    rows = coverage.get("n_matrix") or []
    if not rows:
        return []
    legends_by_id: dict[str, str] = {}
    for ch in coverage.get("figure_chunks") or []:
        if not isinstance(ch, dict):
            continue
        fid = str(ch.get("figure_id") or "").strip()
        if fid:
            legends_by_id[fid] = str(ch.get("legend") or "")

    from pre_peer_checker.report.figure_previews import figure_previews_by_id

    previews_by_id = figure_previews_by_id(
        [p for p in (coverage.get("figure_previews") or []) if isinstance(p, dict)]
    )

    by_fig: OrderedDict[str, list[dict]] = OrderedDict()
    for r in rows:
        if not isinstance(r, dict):
            continue
        fig = str(r.get("figure") or "?")
        by_fig.setdefault(fig, []).append(r)

    sections: list[dict] = []
    for fig, fig_rows in by_fig.items():
        legend = _legend_for_figure(fig, legends_by_id)
        preview = _preview_for_figure(fig, previews_by_id)
        sections.append(
            {
                "figure": fig,
                "rows": fig_rows,
                "legend": legend,
                "legend_html": annotate_legend_html(legend, fig_rows) if legend else "",
                "has_legend": bool(legend.strip()) if legend else False,
                "preview": preview,
                "has_preview": bool(preview and preview.get("image_data_uri")),
            }
        )
    return sections


_REPORT_TEMPLATE = Template(
    """<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Paper Data Verification Report</title>
  <style>
    :root {
      --bg: #f4f5f7;
      --card: #ffffff;
      --text: #1f2937;
      --muted: #6b7280;
      --line: #e5e7eb;
      --hl: #fde68a;
      --hl-strong: #f59e0b;
      --row-active: #fffbeb;
    }
    * { box-sizing: border-box; }
    body {
      font-family: "IBM Plex Sans", "Hiragino Sans", "Noto Sans JP", sans-serif;
      margin: 0; padding: 28px clamp(16px, 4vw, 48px) 64px;
      background: var(--bg); color: var(--text);
    }
    h1 { font-size: 1.6rem; margin: 0 0 8px; letter-spacing: -0.02em; }
    .meta { color: var(--muted); margin-bottom: 20px; }
    .toolbar {
      display: flex; flex-wrap: wrap; gap: 8px; align-items: center;
      margin-bottom: 18px; position: sticky; top: 0; z-index: 5;
      background: color-mix(in srgb, var(--bg) 92%, white);
      padding: 10px 0; backdrop-filter: blur(6px);
    }
    .chip {
      border: 1px solid var(--line); background: white; color: var(--text);
      border-radius: 999px; padding: 6px 12px; font-size: 13px; cursor: pointer;
    }
    .chip.active { background: #111827; color: white; border-color: #111827; }
    .chip .count { opacity: 0.75; margin-left: 4px; }
    #search {
      flex: 1 1 180px; min-width: 160px; max-width: 320px;
      border: 1px solid var(--line); border-radius: 8px; padding: 8px 12px; font-size: 14px;
    }
    .card {
      background: var(--card); border: 1px solid var(--line);
      border-radius: 10px; padding: 18px 20px; margin-bottom: 14px;
    }
    .card.hidden { display: none; }
    .warning-badge {
      display: inline-block; padding: 4px 10px; border-radius: 4px;
      font-weight: 600; font-size: 12px; margin-bottom: 8px;
    }
    .badge-mismatch { background: #fee2e2; color: #b91c1c; border: 1px solid #fca5a5; }
    .badge-sample { background: #fef3c7; color: #b45309; border: 1px solid #fcd34d; }
    .badge-image { background: #e0e7ff; color: #3730a3; border: 1px solid #a5b4fc; }
    .badge-info { background: #e5e7eb; color: #374151; border: 1px solid #d1d5db; }
    .demote-pill {
      display: inline-block; margin-left: 8px; padding: 2px 8px;
      border-radius: 999px; font-size: 11px; font-weight: 600;
      background: #f3f4f6; color: #4b5563; border: 1px solid #d1d5db;
      vertical-align: middle;
    }
    .warning-card.is-demoted {
      border-left: 3px solid #9ca3af;
      background: color-mix(in srgb, var(--card) 92%, #e5e7eb);
    }
    .n-authority-note {
      font-size: 0.85rem; color: var(--muted); margin: 0 0 0.75rem;
    }
    .pattern { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; color: var(--muted); }
    .sources { font-size: 13px; color: var(--muted); word-break: break-all; }
    .src-link { color: #1d4ed8; text-decoration: none; }
    .src-link:hover { text-decoration: underline; }
    .code-snippet {
      background: #111827; color: #e5e7eb; padding: 10px; border-radius: 6px;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; white-space: pre-wrap;
    }
    .ok { color: #15803d; }
    .empty-filter { display: none; color: var(--muted); }
    .empty-filter.show { display: block; }
    .coverage-table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
    .coverage-table th, .coverage-table td {
      text-align: left; padding: 0.4rem 0.35rem; border-bottom: 1px solid var(--line);
      vertical-align: top;
    }
    .n-matrix { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
    .n-matrix th, .n-matrix td {
      text-align: left; padding: 0.45rem 0.4rem; border-bottom: 1px solid var(--line);
      vertical-align: top; max-width: 14rem;
    }
    .n-matrix th { color: var(--muted); font-weight: 600; white-space: nowrap; }
    .n-matrix tr.mismatch { background: #fff7ed; }
    .n-matrix tr[data-panel] { cursor: pointer; }
    .n-matrix tr[data-panel]:hover,
    .n-matrix tr[data-panel].is-active { background: var(--row-active); }
    .n-matrix tr.mismatch.is-active,
    .n-matrix tr.mismatch:hover { background: #ffedd5; }
    .n-matrix .cell-file { display: block; color: var(--muted); font-size: 0.75rem; word-break: break-all; }
    .extractor-badge {
      display: inline-block; font-size: 0.72rem; font-weight: 600;
      padding: 2px 7px; border-radius: 6px; border: 1px solid var(--line);
      background: #f3f4f6; color: #374151; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    }
    .extractor-badge.hybrid-llm, .extractor-badge.llm-rules {
      background: #ede9fe; border-color: #c4b5fd; color: #5b21b6;
    }
    .extractor-badge.llm { background: #e0e7ff; border-color: #a5b4fc; color: #3730a3; }
    .extractor-badge.rules { background: #f3f4f6; color: #4b5563; }
    .info-card {
      border-left: 3px solid #60a5fa;
      background: color-mix(in srgb, var(--card) 90%, #dbeafe);
    }
    .info-card h3 { font-size: 0.95rem; margin: 0.35rem 0; }
    .info-pill {
      display: inline-block; font-size: 0.72rem; font-weight: 600;
      padding: 2px 8px; border-radius: 999px; background: #dbeafe; color: #1e40af;
      margin-bottom: 0.35rem;
    }
    .link-badge {
      display: inline-block; font-size: 0.68rem; font-weight: 600;
      letter-spacing: 0.02em; padding: 1px 6px; border-radius: 999px;
      margin-left: 4px; vertical-align: middle; border: 1px solid var(--line);
      background: #f3f4f6; color: #374151;
    }
    .link-badge.tier1 { background: #dcfce7; border-color: #86efac; color: #166534; }
    .link-badge.tier2 { background: #dbeafe; border-color: #93c5fd; color: #1e40af; }
    .link-badge.tier3 { background: #fef3c7; border-color: #fcd34d; color: #92400e; }
    .link-badge.soft { background: #f3e8ff; border-color: #d8b4fe; color: #6b21a8; }
    .link-badge.digitized { background: #e0e7ff; border-color: #a5b4fc; color: #3730a3; }
    .link-badge.unlinked, .link-badge.none { background: #fee2e2; border-color: #fca5a5; color: #991b1b; }
    .link-badge.data_missing { background: #ffedd5; border-color: #fdba74; color: #9a3412; }
    .link-reason {
      display: block; font-size: 0.72rem; color: var(--muted); margin-top: 2px;
      max-width: 28ch; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    .fig-section { margin-top: 1.6rem; padding-top: 0.35rem; border-top: 1px solid var(--line); }
    .fig-section:first-of-type { margin-top: 0.5rem; padding-top: 0; border-top: none; }
    .fig-section h3 {
      font-size: 0.98rem; margin: 0 0 0.55rem; letter-spacing: -0.01em;
    }
    .fig-preview-wrap { margin-bottom: 0.75rem; }
    .fig-preview-label {
      font-size: 0.78rem; font-weight: 600; color: var(--muted);
      margin-bottom: 0.35rem; letter-spacing: 0.02em;
    }
    .fig-preview {
      position: relative; display: inline-block; max-width: 100%;
      border: 1px solid var(--line); border-radius: 8px; overflow: hidden;
      background: #fff; vertical-align: top;
    }
    .fig-preview img {
      display: block; max-width: 100%; height: auto; user-select: none;
    }
    .panel-hotspot {
      position: absolute; border: 1.5px solid transparent; border-radius: 3px;
      background: transparent; cursor: pointer; padding: 0; margin: 0;
      transition: background 0.12s ease, border-color 0.12s ease, box-shadow 0.12s ease;
    }
    .panel-hotspot:hover,
    .panel-hotspot.is-active {
      background: color-mix(in srgb, var(--hl) 28%, transparent);
      border-color: var(--hl-strong);
      box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--hl-strong) 50%, white);
    }
    .panel-hotspot .hotspot-tag {
      position: absolute; top: 2px; left: 2px;
      font-size: 10px; font-weight: 700; line-height: 1;
      color: #92400e; background: color-mix(in srgb, var(--hl) 85%, white);
      border-radius: 2px; padding: 1px 3px; opacity: 0;
      transition: opacity 0.12s ease; pointer-events: none;
    }
    .panel-hotspot:hover .hotspot-tag,
    .panel-hotspot.is-active .hotspot-tag { opacity: 1; }
    .matrix-block { margin-top: 0.15rem; }
    .matrix-block .matrix-label,
    .legend-block .legend-label {
      font-size: 0.78rem; font-weight: 600; color: var(--muted);
      margin-bottom: 0.4rem; letter-spacing: 0.02em;
    }
    .legend-block {
      margin-top: 0.75rem; padding: 0.75rem 0.9rem;
      border: 1px solid var(--line); border-radius: 8px; background: #fafafa;
    }
    .legend-body {
      font-size: 0.86rem; line-height: 1.55; white-space: pre-wrap;
      color: var(--text);
    }
    mark.leg-hl {
      background: color-mix(in srgb, var(--hl) 35%, transparent);
      color: inherit; padding: 0 0.12em; border-radius: 2px;
      border-bottom: 1.5px dotted color-mix(in srgb, var(--hl-strong) 55%, transparent);
      cursor: pointer; transition: background 0.12s ease, box-shadow 0.12s ease;
    }
    mark.leg-hl.is-active {
      background: var(--hl);
      box-shadow: 0 0 0 2px color-mix(in srgb, var(--hl-strong) 70%, white);
      border-bottom-style: solid;
    }
    .status-ran { color: #15803d; font-weight: 600; }
    .status-skipped { color: var(--muted); }
    .note { color: var(--muted); font-size: 0.88rem; margin: 0.35rem 0 0; }
    .llm-box {
      border: 1px solid var(--line); border-radius: 8px; padding: 0.65rem 0.85rem;
      margin-bottom: 0.85rem; background: #f9fafb; font-size: 0.9rem;
    }
    .llm-box.warn { background: #fff7ed; border-color: #fdba74; }
    .llm-box.ok { background: #ecfdf5; border-color: #6ee7b7; }
  </style>
</head>
<body>
  <h1>論文データ照合結果レポート</h1>
  <p class="meta">解析日時: {{ timestamp }} · 対象: {{ file_summary }} · Warning {{ warnings|length }} 件</p>

  {% if coverage %}
  <div class="card">
    <h2 style="font-size:1.05rem;margin:0 0 0.75rem;">照合カバレッジ（何を見たか）</h2>
    {% if coverage.legend_llm_status %}
    <div class="llm-box {% if coverage.legend_llm_status.status == 'active' %}ok{% elif coverage.legend_llm_status.status == 'unavailable' %}warn{% endif %}">
      <strong>Legend LLM:</strong> {{ coverage.legend_llm_status.message }}
    </div>
    {% endif %}
    {% if coverage.file_counts %}
    <p><strong>読込ファイル:</strong>
      {% for k, v in coverage.file_counts.items() %}{{ k }}={{ v }}{% if not loop.last %}, {% endif %}{% endfor %}
    </p>
    {% endif %}
    {% if coverage.extracted_zips %}
    <p><strong>展開 ZIP:</strong> {{ coverage.extracted_zips|length }} 件</p>
    {% endif %}
    {% if coverage.docx_used_for_legend %}
    <p><strong>Legend 用 Word:</strong>
      {% for p in coverage.docx_used_for_legend[:6] %}{{ p.split('/')[-1] }}{% if not loop.last %}, {% endif %}{% endfor %}
    </p>
    {% endif %}
    <table class="coverage-table">
      <thead><tr><th>状態</th><th>チェック</th><th>詳細</th></tr></thead>
      <tbody>
      {% for c in coverage.checks %}
        <tr>
          <td class="{% if c.status == 'ran' %}status-ran{% else %}status-skipped{% endif %}">
            {% if c.status == 'ran' %}実施{% else %}スキップ{% endif %}
          </td>
          <td>{{ c.name }}</td>
          <td>{{ c.detail }}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
    {% for note in coverage.notes or [] %}
    <p class="note">{{ note }}</p>
    {% endfor %}
  </div>
  {% endif %}

  {% if n_matrix_sections %}
  <div class="card" id="n-matrix-card">
    <h2 style="font-size:1.05rem;margin:0 0 0.5rem;">パネル n 対照表</h2>
    <p class="note" style="margin-bottom:0.35rem;">
      各 Figure について <strong>Fig → 対応表 → Legend</strong> の順に並べています。
      Fig 上のパネル領域または対応表の行にカーソルを合わせると、Legend 内の対応するサンプルサイズ表記がハイライトされます（逆方向も連動）。
      行がオレンジのものは、取得できた n のあいだに不一致があります。
    </p>
    <p class="n-authority-note">
      <strong>正の n</strong>は「実験データ」列の有効行数です。ggplot の可視ドット数は使いません。
      Legend 列は記載値、グラフ作図列は残渣／数字化の参照です。
      紐付け根拠は <span class="link-badge tier1">tier1</span>
      <span class="link-badge tier2">tier2</span>
      <span class="link-badge tier3">tier3</span>
      <span class="link-badge soft">soft</span> バッジ（指紋／統計／候補キー／軟紐付け）です。
    </p>
    {% for sec in n_matrix_sections %}
    <section class="fig-section" data-figure="{{ sec.figure|e }}">
      <h3>{{ sec.figure }}</h3>

      {% if sec.has_preview %}
      <div class="fig-preview-wrap">
        <div class="fig-preview-label">Fig（{{ sec.preview.source_name }} · p{{ sec.preview.page }}）</div>
        <div class="fig-preview" data-figure="{{ sec.figure|e }}">
          <img src="{{ sec.preview.image_data_uri }}" alt="{{ sec.figure|e }}" loading="lazy">
          {% for hp in sec.preview.panels %}
          <button type="button" class="panel-hotspot"
                  data-figure="{{ sec.figure|e }}"
                  data-panel="{{ hp.panel|e }}"
                  style="left:{{ hp.left_pct }}%;top:{{ hp.top_pct }}%;width:{{ hp.width_pct }}%;height:{{ hp.height_pct }}%;"
                  aria-label="Panel {{ hp.panel|e }}">
            <span class="hotspot-tag">{{ hp.panel }}</span>
          </button>
          {% endfor %}
        </div>
      </div>
      {% else %}
      <p class="note">出版 Figure PDF のプレビューがありません。</p>
      {% endif %}

      <div class="matrix-block">
        <div class="matrix-label">対応表 <span style="font-weight:400;color:var(--muted)">（各セルに参照ファイル名。ホバーでフルパス）</span></div>
        <div style="overflow-x:auto;">
        <table class="n-matrix">
          <thead>
            <tr>
              <th>Panel</th>
              <th>原稿 (Legend)</th>
              <th>実験データ</th>
              <th>グラフ作図</th>
              <th>統計解析</th>
              <th>抽出</th>
            </tr>
          </thead>
          <tbody>
          {% for r in sec.rows %}
            <tr class="{% if r.mismatch %}mismatch{% endif %}"
                data-figure="{{ sec.figure|e }}"
                data-panel="{{ r.panel|e }}">
              <td><strong>{% if r.group %}{{ r.panel }} [{{ r.group }}]{% else %}{{ r.panel }}{% endif %}</strong></td>
              <td>
                {% if r.manuscript.n is not none %}n={{ r.manuscript.n }}{% else %}—{% endif %}
                {% if r.manuscript.file %}
                <span class="cell-file" title="{{ r.manuscript.file|e }}">{{ r.manuscript.file_display or r.manuscript.file.split('/')[-1] }}</span>
                {% endif %}
                {% if r.manuscript.detail %}<span class="cell-file">{{ r.manuscript.detail }}</span>{% endif %}
              </td>
              <td>
                {% if r.data.n is not none %}n={{ r.data.n }}{% else %}—{% endif %}
                {% if r.data.link_tier %}
                <span class="link-badge {{ r.data.link_tier }}" title="{{ r.data.link_status|e }}">{{ r.data.link_tier }}</span>
                {% elif r.data.link_status %}
                <span class="link-badge {{ r.data.link_status }}">{{ r.data.link_status }}</span>
                {% endif %}
                {% if r.data.file %}
                <a class="cell-file src-link" href="file://{{ r.data.file }}" title="{{ r.data.file|e }}">{{ r.data.file_display or r.data.file.split('/')[-1] }}</a>
                {% endif %}
                {% if r.data.detail %}<span class="link-reason" title="{{ r.data.detail|e }}">{{ r.data.detail }}</span>{% endif %}
              </td>
              <td>
                {% if r.plot.n is not none %}n={{ r.plot.n }}{% else %}—{% endif %}
                {% if r.plot.link_tier %}
                <span class="link-badge {{ r.plot.link_tier }}" title="{{ r.plot.link_status|e }}">{{ r.plot.link_tier }}</span>
                {% elif r.plot.link_status %}
                <span class="link-badge {{ r.plot.link_status }}">{{ r.plot.link_status }}</span>
                {% endif %}
                {% if r.plot.file %}
                <a class="cell-file src-link" href="file://{{ r.plot.file }}" title="{{ r.plot.file|e }}">{{ r.plot.file_display or r.plot.file.split('/')[-1] }}</a>
                {% endif %}
                {% if r.plot.detail %}<span class="link-reason" title="{{ r.plot.detail|e }}">{{ r.plot.detail }}</span>{% endif %}
              </td>
              <td>
                {% if r.stats.n is not none %}n={{ r.stats.n }}{% else %}—{% endif %}
                {% if r.stats.file %}
                <a class="cell-file src-link" href="file://{{ r.stats.file }}" title="{{ r.stats.file|e }}">{{ r.stats.file_display or r.stats.file.split('/')[-1] }}</a>
                {% endif %}
                {% if r.stats.detail %}<span class="cell-file">{{ r.stats.detail }}</span>{% endif %}
              </td>
              <td>
                {% if r.extractor %}
                <span class="extractor-badge {{ r.extractor|replace('+','-')|e }}">{{ r.extractor }}</span>
                {% else %}—{% endif %}
              </td>
            </tr>
          {% endfor %}
          </tbody>
        </table>
        </div>
      </div>

      {% if sec.has_legend %}
      <div class="legend-block" data-figure="{{ sec.figure|e }}">
        <div class="legend-label">Legend</div>
        <div class="legend-body">{{ sec.legend_html | safe }}</div>
      </div>
      {% else %}
      <p class="note">この Figure の Legend テキストはありません。</p>
      {% endif %}
    </section>
    {% endfor %}
  </div>
  {% endif %}

  {% if coverage and coverage.cited_image_matches %}
  <div class="card" id="cited-image-card">
    <h2 style="font-size:1.05rem;margin:0 0 0.5rem;">画像再利用（出典あり・情報）</h2>
    <p class="note" style="margin-bottom:0.5rem;">
      Legend に reproduced/adapted from 等の出典があるため重大 Warning は抑制しています。
      コーパス一致は確認用に残します。
    </p>
    {% for m in coverage.cited_image_matches %}
    <div class="card info-card" style="margin:0.5rem 0;padding:0.75rem 1rem;">
      <span class="info-pill">出典あり一致</span>
      <h3>{{ (m.a or '')|e }} ↔ {{ (m.b or '')|e }}</h3>
      <p class="sources">
        {% if m.similarity is defined and m.similarity is not none %}類似度 {{ m.similarity }}
        {% elif m.score is defined and m.score is not none %}NCC {{ m.score }}
        {% else %}一致{% endif %}
        {% if m.a %}<br><a class="src-link" href="file://{{ m.a }}">{{ m.a }}</a>{% endif %}
        {% if m.b %}<br><a class="src-link" href="file://{{ m.b }}">{{ m.b }}</a>{% endif %}
      </p>
    </div>
    {% endfor %}
  </div>
  {% endif %}

  {% if coverage and coverage.citation_evidence_reviews %}
  <div class="card" id="citation-evidence-card">
    <h2 style="font-size:1.05rem;margin:0 0 0.5rem;">引用根拠レビュー（情報）</h2>
    <p class="note" style="margin-bottom:0.5rem;">
      ユーザー提供の引用先 PDF から取得した根拠候補です。
      LLM／検索スコアだけでは Warning を確定しません（数値・極性などの決定論矛盾のみ Warning）。
    </p>
    {% for r in coverage.citation_evidence_reviews %}
    <div class="card info-card" style="margin:0.5rem 0;padding:0.75rem 1rem;">
      <span class="info-pill">{{ r.status or 'reviewed' }}</span>
      <h3>{{ (r.span or '')|e }}</h3>
      <p class="sources">
        cite_keys: {{ (r.cite_keys or [])|join(', ') }}
        {% if r.linked_keys %} · linked: {{ r.linked_keys|join(', ') }}{% endif %}
      </p>
      {% for p in r.passages or [] %}
      <p class="note" style="margin:0.35rem 0;">
        score={{ p.score }} — {{ (p.text or '')|e }}
        {% if p.pdf_path %}<br><a class="src-link" href="file://{{ p.pdf_path }}">{{ p.pdf_path }}</a>{% endif %}
      </p>
      {% endfor %}
    </div>
    {% endfor %}
  </div>
  {% endif %}

  {% if warnings %}
  <div class="toolbar" id="filters">
    <button type="button" class="chip active" data-filter="__all__">すべて<span class="count">{{ warnings|length }}</span></button>
    {% for tag, count in tag_counts %}
    <button type="button" class="chip" data-filter="{{ tag }}">{{ tag }}<span class="count">{{ count }}</span></button>
    {% endfor %}
    {% if demoted_count %}
    <button type="button" class="chip" data-filter="__demoted__">降格・情報<span class="count">{{ demoted_count }}</span></button>
    {% endif %}
    <input id="search" type="search" placeholder="タイトル・理由・pattern_id を検索" aria-label="検索">
  </div>
  {% endif %}

  {% if not warnings %}
  <div class="card"><p class="ok">検出された Warning はありません（上のカバレッジでスキップ理由も確認してください）。</p></div>
  {% endif %}

  <p class="empty-filter" id="empty-filter">条件に一致する Warning がありません。</p>

  {% for w in warnings %}
  <div class="card warning-card{% if w.demoted %} is-demoted{% endif %}"
       data-tag="{{ w.tag }}"
       data-demoted="{{ '1' if w.demoted else '0' }}"
       data-search="{{ w.search_text|e }}">
    <div class="warning-badge {{ w.badge_class }}">{{ w.tag }}</div>
    {% if w.demoted %}<span class="demote-pill">降格・情報</span>{% endif %}
    {% if w.metadata and w.metadata.pattern_id %}
    <div class="pattern">{{ w.metadata.pattern_id }}{% if w.metadata.also_pattern %} · {{ w.metadata.also_pattern }}{% endif %}{% if w.metadata.n_authority %} · 正のn={{ w.metadata.n_authority }}{% endif %}</div>
    {% endif %}
    <h3>{{ w.title }}</h3>
    <p><strong>該当箇所:</strong> {{ w.location }}</p>
    <p><strong>検出理由:</strong> {{ w.reason }}</p>
    {% if w.sources %}
    <p class="sources"><strong>根拠:</strong>
      {% for s in w.sources %}
        <a class="src-link" href="file://{{ s }}">{{ s }}</a>{% if not loop.last %} · {% endif %}
      {% endfor %}
    </p>
    {% endif %}
    {% if w.code_snippet %}
    <div class="code-snippet">{{ w.code_snippet }}</div>
    {% endif %}
  </div>
  {% endfor %}

  <script>
    (function () {
      const chips = Array.from(document.querySelectorAll('.chip[data-filter]'));
      const cards = Array.from(document.querySelectorAll('.warning-card'));
      const search = document.getElementById('search');
      const empty = document.getElementById('empty-filter');
      let activeTag = '__all__';

      function apply() {
        const q = (search && search.value || '').trim().toLowerCase();
        let shown = 0;
        cards.forEach(card => {
          const tagOk = activeTag === '__all__'
            || (activeTag === '__demoted__' ? card.dataset.demoted === '1' : card.dataset.tag === activeTag);
          const text = (card.dataset.search || '').toLowerCase();
          const qOk = !q || text.includes(q);
          const on = tagOk && qOk;
          card.classList.toggle('hidden', !on);
          if (on) shown += 1;
        });
        if (empty) empty.classList.toggle('show', cards.length > 0 && shown === 0);
      }

      chips.forEach(chip => {
        chip.addEventListener('click', () => {
          activeTag = chip.dataset.filter;
          chips.forEach(c => c.classList.toggle('active', c === chip));
          apply();
        });
      });
      if (search) search.addEventListener('input', apply);

      /* Fig hotspot ↔ table row ↔ legend sample-size highlight */
      const matrixCard = document.getElementById('n-matrix-card');
      if (matrixCard) {
        function clearActive(scope) {
          scope.querySelectorAll(
            'tr.is-active, mark.leg-hl.is-active, .panel-hotspot.is-active'
          ).forEach(el => el.classList.remove('is-active'));
        }
        function activate(figure, panel, scrollTarget) {
          const section = matrixCard.querySelector(
            '.fig-section[data-figure="' + CSS.escape(figure) + '"]'
          );
          if (!section) return;
          clearActive(section);
          section.querySelectorAll(
            'tr[data-panel="' + CSS.escape(panel) + '"]'
          ).forEach(tr => tr.classList.add('is-active'));
          section.querySelectorAll(
            '.panel-hotspot[data-panel="' + CSS.escape(panel) + '"]'
          ).forEach(h => h.classList.add('is-active'));
          const marks = section.querySelectorAll(
            'mark.leg-hl[data-panel="' + CSS.escape(panel) + '"]'
          );
          marks.forEach(m => m.classList.add('is-active'));
          if (scrollTarget === 'legend' && marks.length) {
            marks[0].scrollIntoView({ block: 'nearest', behavior: 'smooth' });
          } else if (scrollTarget === 'row') {
            const row = section.querySelector('tr[data-panel="' + CSS.escape(panel) + '"]');
            if (row) row.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
          }
        }
        function bindHover(els, scrollTarget) {
          els.forEach(el => {
            el.addEventListener('mouseenter', () => {
              const figure = el.dataset.figure
                || (el.closest('[data-figure]') && el.closest('[data-figure]').dataset.figure);
              const panel = el.dataset.panel;
              if (figure && panel) activate(figure, panel, scrollTarget);
            });
            el.addEventListener('mouseleave', () => {
              const section = el.closest('.fig-section');
              if (section) clearActive(section);
            });
            el.addEventListener('focus', () => {
              const figure = el.dataset.figure
                || (el.closest('[data-figure]') && el.closest('[data-figure]').dataset.figure);
              const panel = el.dataset.panel;
              if (figure && panel) activate(figure, panel, scrollTarget);
            });
          });
        }
        const rows = matrixCard.querySelectorAll('tr[data-panel]');
        rows.forEach(tr => tr.setAttribute('tabindex', '0'));
        bindHover(rows, 'legend');
        bindHover(matrixCard.querySelectorAll('.panel-hotspot'), 'legend');
        bindHover(matrixCard.querySelectorAll('mark.leg-hl'), 'row');
      }
    })();
  </script>
</body>
</html>
"""
)


def render_html_report(
    warnings: list[WarningItem],
    *,
    file_summary: str = "",
    timestamp: datetime | None = None,
    coverage: dict | None = None,
) -> str:
    ts = (timestamp or datetime.now()).strftime("%Y-%m-%d %H:%M")
    dicts = []
    for w in warnings:
        d = w.to_dict()
        meta = d.get("metadata") or {}
        search_bits = [
            d.get("tag", ""),
            d.get("title", ""),
            d.get("location", ""),
            d.get("reason", ""),
            str(meta.get("pattern_id", "")),
            str(meta.get("also_pattern", "")),
            " ".join(d.get("sources") or []),
        ]
        d["search_text"] = " ".join(search_bits)
        if d.get("demoted"):
            d["search_text"] += " 降格 情報 demoted"
        dicts.append(d)
    counts = Counter(w.tag.value for w in warnings)
    tag_order = {t.value: i for i, t in enumerate(WarningTag)}
    tag_counts = sorted(counts.items(), key=lambda kv: (tag_order.get(kv[0], 99), kv[0]))
    demoted_count = sum(1 for d in dicts if d.get("demoted"))
    cov = ensure_figure_previews(coverage)
    n_matrix_sections = build_n_matrix_sections(cov)
    return _REPORT_TEMPLATE.render(
        warnings=dicts,
        timestamp=ts,
        file_summary=file_summary or "(未指定)",
        tag_counts=tag_counts,
        demoted_count=demoted_count,
        coverage=cov,
        n_matrix_sections=n_matrix_sections,
    )


def write_html_report(
    warnings: list[WarningItem],
    out_path: Path | str,
    *,
    file_summary: str = "",
    coverage: dict | None = None,
) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    html_text = render_html_report(warnings, file_summary=file_summary, coverage=coverage)
    out_path.write_text(html_text, encoding="utf-8")
    return out_path
