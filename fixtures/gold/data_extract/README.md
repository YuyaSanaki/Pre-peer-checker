# Legend n ↔ data n gold (data extract benchmark)

One folder per slot of `input/data extract/<slot>/` (manuscript + data). The
filled `legend_data_gold.json` files carry real file paths and sample sizes, so
they are gitignored; only this README and `legend_data_gold.example.json` are
tracked.

## Item

| field | meaning |
|---|---|
| `figure`, `panel`, `group` | legend key (`Figure 2`, `Extended Data Figure 8`; empty group = whole panel) |
| `legend_n` or `legend_n_min`/`legend_n_max` | n stated by the legend (`n ≥ 2` → min only; `n = 2 or 3` → both) |
| `n_source` | `legend` (default) or `figure` (n printed inside the figure, e.g. survival curves) |
| `extract_aliases` | other (panel, group) keys an extractor may produce for the same statement (legend letter typos) |
| `data` | `null` when no table holds the panel's values; else `file` (relative to `<slot>/data`), `alt_files`, `sheet`, a count rule and `n` |
| `verdict` | `match` · `mismatch` · `data_missing` · `not_comparable` (rows are not samples) · `uncertain` |
| `panel_kind` | `plot`, `image`, `survival_curve`, `heatmap`, `correlation`, `flow`, `track`, `blot` |
| `review_status` | `draft` (mapped by the developer) or `confirmed` (checked by the authors) |

Count rules: `rows` (rows whose `group_col` equals `group_value`), `sum:<col>`
(count tables), `km_cohort` (Kaplan–Meier cohort rebuilt from a percent-survival
column; a lower bound, see `n_is_lower_bound`), `manual` (read off the sheet; `n`
may be a per-group mapping).

## Scripts

```bash
# re-derive every data n from the files (fails on drift)
python scripts/dev_legend_gold_check.py "input/data extract"
# score the tool against the gold
python scripts/dev_data_link_eval.py "input/data extract"
```
