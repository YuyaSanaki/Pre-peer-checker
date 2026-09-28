"""Train / cache stub — NOT a required LoRA path for the product.

Product verification is deterministic patterns + optional few-shot Legend prompts.
This entrypoint is for local DINOv2 embedding caches and threshold sweeps.
LoRA is only considered if rule/prompt Legend extraction fails broadly on public text.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local cache / threshold stub (LoRA not required)")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("/data/output/train_stub_report.json"),
    )
    args = parser.parse_args(argv)
    report = {
        "status": "ok",
        "message": (
            "Phase 2 train stub: use for DINOv2 cache / threshold sweeps. "
            "LoRA is not part of the default product path."
        ),
        "lora_required": False,
        "next": [
            "Build optional embedding cache under /data/models",
            "Sweep duplicate-scan thresholds on synthetic fixtures",
            "Only revisit LoRA if legend_extract rules+prompts fail on diverse public legends",
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
