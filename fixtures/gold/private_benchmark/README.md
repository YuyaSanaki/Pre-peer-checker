# Private benchmark case (local only)

Filled `case_manifest.json` / `gold_warnings.json` are **gitignored** — they point at private manuscript paths and real-data-derived facts (n, panel mapping) that could identify the underlying study.

**Committed here**
- `case_manifest.example.json` / `gold_warnings.example.json` — schema templates (no private paths / real n)

**Local setup**
```bash
cp case_manifest.example.json case_manifest.json
cp gold_warnings.example.json gold_warnings.json
# edit paths/n to match your private input/ + check_reference/
```

Public/CI regression uses `fixtures/gold/demo/` (synthetic) and `fixtures/patterns/` (generic rules).
