"""Cross-profile LLM/VLM bakeoff plan and optional dry-run resolution.

Usage:
  python -m pre_peer_checker.eval.model_bakeoff --list
  python -m pre_peer_checker.eval.model_bakeoff \\
    --profiles qwen2.5-7b-mlx,qwen2.5-7b-hf,qwen2.5-32b-hf -o outputs/metrics/model_bakeoff.json
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pre_peer_checker.llm.backend import probe_backends, select_backend
from pre_peer_checker.llm.registry import (
    get_profile,
    list_profiles,
    load_registry,
    resolve_model,
)


def build_bakeoff_plan(
    profile_ids: list[str] | None = None,
) -> dict[str, Any]:
    profiles, default_llm, default_vlm = load_registry()
    if profile_ids:
        selected = []
        for pid in profile_ids:
            p = profiles.get(pid)
            if p is None:
                selected.append(
                    {
                        "profile_id": pid,
                        "error": "unknown profile",
                    }
                )
            else:
                selected.append(p.to_dict())
    else:
        selected = [p.to_dict() for p in list_profiles()]

    probed = [b.__dict__ for b in probe_backends()]
    rows: list[dict[str, Any]] = []
    for item in selected:
        if item.get("error"):
            rows.append({**item, "backend_available": False})
            continue
        pid = item["id"]
        role = item.get("role") or "text"
        resolved = resolve_model(role=role, profile_id=pid)  # type: ignore[arg-type]
        backend_ok = False
        backend_info: dict[str, Any] | None = None
        if role == "text":
            try:
                be = select_backend("auto", profile_id=pid)
                if be is not None:
                    backend_ok = True
                    backend_info = be.info().__dict__
            except Exception as exc:  # noqa: BLE001 — mlx import can abort on some hosts
                backend_info = {"name": "probe-error", "detail": str(exc)}
        else:
            try:
                from pre_peer_checker.llm.vlm_backend import select_vlm_backend

                vbe = select_vlm_backend("auto", profile_id=pid)
                if vbe is not None:
                    backend_ok = True
                    backend_info = vbe.info().__dict__
                else:
                    backend_info = {
                        "name": "vlm-unavailable",
                        "detail": "install .[vlm-mlx] or .[vlm-cuda]",
                    }
            except Exception as exc:  # noqa: BLE001
                backend_info = {"name": "vlm-probe-error", "detail": str(exc)}
        rows.append(
            {
                "profile": item,
                "resolved": resolved.to_dict(),
                "backend_available": backend_ok,
                "backend": backend_info,
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "default_llm_profile": default_llm,
        "default_vlm_profile": default_vlm,
        "backends_probed": probed,
        "n_profiles": len(rows),
        "rows": rows,
        "notes": [
            "This report resolves profiles and probes text/VLM backends; it does not load 30B+ weights.",
            "Text extraction bakeoff: scripts/dev_legend_llm_bakeoff.py or "
            "scripts/dev_legend_json_gold_eval.py --prefer cuda --profile <id>.",
            "VLM panel-map verify: scripts/dev_vlm_panel_map_verify.py --synthetic --require-vlm.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LLM/VLM profile bakeoff planner")
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print profiles and exit",
    )
    parser.add_argument(
        "--profiles",
        default=None,
        help="Comma-separated profile IDs (default: all)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="outputs/metrics/model_bakeoff.json",
        help="JSON output path",
    )
    args = parser.parse_args(argv)

    if args.list:
        _, default_llm, default_vlm = load_registry()
        print(f"defaults: llm={default_llm} vlm={default_vlm}")
        for role in ("text", "vision"):
            print(f"\n[{role}]")
            for p in list_profiles(role):  # type: ignore[arg-type]
                print(f"  {p.id:24} {p.label}")
        return 0

    ids = None
    if args.profiles:
        ids = [x.strip() for x in args.profiles.split(",") if x.strip()]
        for pid in ids:
            if get_profile(pid) is None:
                print(f"warning: unknown profile {pid!r}")

    plan = build_bakeoff_plan(ids)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {out} ({plan['n_profiles']} profiles)")
    for row in plan["rows"]:
        pid = (row.get("profile") or {}).get("id") or row.get("profile_id")
        ok = row.get("backend_available")
        print(f"  {'OK' if ok else '--'}  {pid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
