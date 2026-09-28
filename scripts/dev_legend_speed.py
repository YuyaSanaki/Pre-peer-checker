#!/usr/bin/env python3
"""Legend LLM の速度と抽出結果を JSON 生成方式ごとに比較する（Mac MLX / Linux CUDA）.

同じ Figure チャンクを 3 方式で抽出し、1 Figure あたりの時間と、
規則とマージした後のパネル (panel, n, groups) が一致するかを表示する::

    python scripts/dev_legend_speed.py path/to/MyCase --max-figures 2

方式:
  outlines-percall  従来経路（Figure ごとに Outlines のスキーマを再コンパイル）
  outlines-cached   現行経路（照合 1 回につき 1 回だけコンパイル）
  free+coerce       自由生成 → 後段でスキーマに整形（Outlines 不使用）

原稿の本文は表示しない（Figure 番号・件数・時間のみ）。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


def _pick_docx(target: Path) -> list[Path]:
    from pre_peer_checker.pipeline.orchestrator import _select_docx_for_legend

    if target.is_file():
        return [target]
    docx = sorted(
        p for p in target.rglob("*.docx") if not p.name.startswith(("~$", "."))
    )
    return _select_docx_for_legend(docx)


def _panel_key(items) -> set[tuple[str, int | None, tuple[str, ...]]]:
    return {
        (p.panel.upper(), p.n, tuple(str(g) for g in (p.groups or [])))
        for p in items.panels
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("target", type=Path, help="症例フォルダ（manuscript/ を含む）または .docx")
    ap.add_argument("--max-figures", type=int, default=2)
    ap.add_argument("--prefer", default="auto", help="auto|mlx|cuda|transformers")
    ap.add_argument("--profile", default=None, help="registry profile id")
    ap.add_argument("--model", default=None, help="model id override")
    ap.add_argument(
        "--modes",
        default="outlines-percall,outlines-cached,free+coerce",
        help="comma-separated subset of the three modes",
    )
    args = ap.parse_args(argv)

    from pre_peer_checker.llm import json_mode as jm
    from pre_peer_checker.llm.backend import select_backend
    from pre_peer_checker.llm.legend_extract import extract_check_items_from_chunk
    from pre_peer_checker.llm.legend_schema import legend_json_schema
    from pre_peer_checker.parsers.figure_chunks import build_figure_chunks_from_docx

    docx = _pick_docx(args.target.expanduser().resolve())
    if not docx:
        print("no .docx found", file=sys.stderr)
        return 2
    chunks = [c for d in docx for c in build_figure_chunks_from_docx(d)]
    chunks = chunks[: max(1, args.max_figures)]
    if not chunks:
        print("no figure chunks found in the manuscript", file=sys.stderr)
        return 2

    backend = select_backend(args.prefer, model_id=args.model, profile_id=args.profile)
    if backend is None:
        print("no LLM backend available", file=sys.stderr)
        return 2
    info = backend.info()
    t = time.perf_counter()
    backend.load()
    load_s = time.perf_counter() - t
    backend.generate("Hello", max_tokens=8)  # warm up kernels before timing
    print(f"backend={info.name} model={info.model_id} device={info.device}")
    print(f"outlines installed={jm.outlines_available()}  model load={load_s:.1f}s")
    print(f"docx={len(docx)}  figures measured={len(chunks)}\n")

    schema = legend_json_schema()
    name = info.name

    def gen_percall(prompt: str) -> str:
        fn = jm._try_outlines_mlxlm if name == "mlx" else jm._try_outlines_transformers
        out = fn(backend._model, backend._tokenizer, prompt, schema, max_tokens=1024)
        if out is None:
            raise RuntimeError("outlines per-call path unavailable")
        return out

    def gen_cached(prompt: str) -> str:
        out = jm._generate_with_cached_outlines(
            backend, name, prompt, schema, max_tokens=1024
        )
        if out is None:
            raise RuntimeError("outlines cached path unavailable")
        return out

    def gen_free(prompt: str) -> str:
        return backend.generate(prompt, max_tokens=1024)

    all_modes = {
        "outlines-percall": gen_percall,
        "outlines-cached": gen_cached,
        "free+coerce": gen_free,
    }
    modes = [m.strip() for m in args.modes.split(",") if m.strip() in all_modes]

    if "outlines-cached" in modes and jm.outlines_available():
        import json

        t = time.perf_counter()
        key = (name, id(backend._model), json.dumps(schema, sort_keys=True))
        backend.__dict__.setdefault("_outlines_generators", {})[key] = (
            jm._build_outlines_generator(name, backend._model, backend._tokenizer, schema)
        )
        print(
            f"outlines schema compile={time.perf_counter() - t:.1f}s "
            "(paid once per run by outlines-cached, once per Figure by outlines-percall)\n"
        )

    tok = getattr(backend, "_tokenizer", None)

    def n_tokens(text: str) -> int:
        try:
            return len(tok.encode(text))
        except Exception:  # noqa: BLE001
            return -1

    results: dict[str, list[tuple[float, set, str, int]]] = {m: [] for m in modes}
    prompt_tok: dict[str, int] = {}
    for mode in modes:
        fn = all_modes[mode]
        for ch in chunks:
            errors: list[Exception] = []
            outputs: list[str] = []

            def wrapped(
                prompt: str, _fn=fn, _errors=errors, _outputs=outputs, _fid=ch.figure_id
            ) -> str:
                prompt_tok.setdefault(_fid, n_tokens(prompt))
                try:
                    out = _fn(prompt)
                except Exception as exc:
                    _errors.append(exc)
                    raise
                _outputs.append(out)
                return out

            t = time.perf_counter()
            item = extract_check_items_from_chunk(ch, llm_generate=wrapped, prefer_llm=True)
            elapsed = time.perf_counter() - t
            if errors:
                status = "unavailable: " + str(errors[0])
            elif item.extractor == "rules":
                status = "LLM output unparsable -> rules only"
            else:
                status = "ok"
            out_tok = sum(n_tokens(o) for o in outputs) if outputs else 0
            results[mode].append((elapsed, _panel_key(item), status, out_tok))

    ok_modes = [m for m in modes if all(r[2] == "ok" for r in results[m])]
    ref_mode = ok_modes[0] if ok_modes else modes[0]
    print("prompt tokens per figure (prefill): " + ", ".join(
        f"{fid}={n}" for fid, n in prompt_tok.items()
    ))
    print("out_tok near 1024 = generation hit max_tokens (runaway)\n")
    print(f"{'mode':18} {'figure':12} {'sec':>7}  out_tok  panels  same_as_{ref_mode}  status")
    for mode in modes:
        for (sec, panels, status, out_tok), ch, ref in zip(
            results[mode], chunks, results[ref_mode]
        ):
            if status.startswith("unavailable"):
                print(f"{mode:18} {ch.figure_id[:12]:12} {'-':>7}  {'-':>7}  {'-':>6}  {'-':>12}  {status}")
                continue
            same = "yes" if panels == ref[1] else "NO"
            print(
                f"{mode:18} {ch.figure_id[:12]:12} {sec:7.1f}  {out_tok:7}  "
                f"{len(panels):6}  {same:>12}  {status}"
            )
    print()
    for mode in modes:
        rows = [r for r in results[mode] if not r[2].startswith("unavailable")]
        if rows:
            mean_s = sum(r[0] for r in rows) / len(rows)
            mean_t = sum(r[3] for r in rows) / len(rows)
            tps = mean_t / mean_s if mean_s > 0 else 0.0
            print(f"{mode:18} mean {mean_s:6.1f} s/figure  {mean_t:6.0f} tok/figure  {tps:5.1f} tok/s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
