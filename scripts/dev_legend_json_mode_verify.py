#!/usr/bin/env python3
"""Legend JSON mode 検証（Mac MLX / Linux CUDA）.

Mac（配布経路）::

    pip install -e ".[mlx]"
    pip install -e ".[llm-json]"   # outlines
    python scripts/dev_legend_json_mode_verify.py --prefer mlx

Linux CUDA::

    pip install -e ".[llm-cuda]"
    pip install -e ".[llm-json]"
    python scripts/dev_legend_json_mode_verify.py --prefer cuda --profile qwen2.5-7b-hf

成功条件:
  - backend が active
  - ``json_mode`` が ``outlines-mlx`` または ``outlines-transformers``
    （Outlines 未導入時は ``free+coerce`` でフォールバックし exit 0 だが WARN）
  - 返却 JSON が ``parse_legend_llm_response`` でパースできる
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


SAMPLE_LEGEND = (
    "Figure 1. Clone size quantification. "
    "n=12 (F) and n=17 (N). Welch's t-test, p<0.01."
)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--prefer", default="auto", help="auto|mlx|cuda|transformers")
    p.add_argument("--profile", default=None, help="registry profile id")
    p.add_argument("--model", default=None, help="model id override")
    p.add_argument(
        "--require-outlines",
        action="store_true",
        help="fail if json_mode is free+coerce (Mac 受入用)",
    )
    p.add_argument("-o", "--out", type=Path, default=None)
    args = p.parse_args(argv)

    from pre_peer_checker.llm.backend import select_backend
    from pre_peer_checker.llm.json_mode import json_mode_probe, structured_legend_generate
    from pre_peer_checker.llm.legend_schema import (
        build_legend_llm_prompt,
        parse_legend_llm_response,
    )

    probe = json_mode_probe()
    print("=== probe ===")
    print(json.dumps(probe, ensure_ascii=False, indent=2))

    backend = select_backend(
        args.prefer, model_id=args.model, profile_id=args.profile
    )
    if backend is None:
        print("FAIL: no backend (install .[mlx] or .[llm-cuda])", file=sys.stderr)
        return 2

    info = backend.info()
    print("=== backend ===")
    print(json.dumps(info.__dict__, ensure_ascii=False, indent=2))

    prompt = build_legend_llm_prompt(SAMPLE_LEGEND, figure_hint="Figure 1")
    text, meta = structured_legend_generate(backend, prompt, max_tokens=512)
    print("=== json_mode ===")
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    print("=== raw (truncated) ===")
    print((text or "")[:800])

    parsed = parse_legend_llm_response(text or "")
    ok_parse = parsed is not None and bool(parsed.panels)
    print("=== parse ===")
    print(
        json.dumps(
            {
                "ok": ok_parse,
                "figure": getattr(parsed, "figure", None),
                "n_panels": len(parsed.panels) if parsed else 0,
                "extractor": getattr(parsed, "extractor", None),
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    report = {
        "probe": probe,
        "backend": info.__dict__,
        "json_mode_meta": meta,
        "parse_ok": ok_parse,
        "raw_preview": (text or "")[:1200],
    }
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"wrote {args.out}")

    mode = str(meta.get("json_mode") or "")
    if args.require_outlines and not mode.startswith("outlines"):
        print(
            f"FAIL: require-outlines but json_mode={mode!r}. "
            "Install: pip install -e '.[llm-json]'",
            file=sys.stderr,
        )
        return 3
    if not mode.startswith("outlines"):
        print(
            f"WARN: json_mode={mode!r} (Outlines 未使用。Mac 受入は --require-outlines で確認)",
            file=sys.stderr,
        )
    if not ok_parse:
        print("FAIL: could not parse legend JSON", file=sys.stderr)
        return 4
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
