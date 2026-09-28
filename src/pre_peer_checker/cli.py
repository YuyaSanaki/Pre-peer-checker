"""CLI エントリポイント."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pre_peer_checker.pipeline.orchestrator import run_verification


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pre-peer-checker",
        description="論文投稿前データ照合AI（ローカル検証）",
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        help="検証対象のファイルまたはディレクトリ",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="outputs/report.html",
        help="HTML レポート出力先 (default: outputs/report.html)",
    )
    parser.add_argument(
        "--json",
        dest="json_path",
        default=None,
        help="Warning 一覧を JSON でも出力する場合のパス",
    )
    parser.add_argument(
        "--corpus",
        action="append",
        default=None,
        help="過去論文画像コーパス（H3）。複数回指定可",
    )
    parser.add_argument(
        "--cited-papers",
        action="append",
        default=None,
        help="引用先論文 PDF またはそのディレクトリ（文献メタ＋引用整合）。複数回指定可",
    )
    parser.add_argument(
        "--legend-llm",
        action="store_true",
        help="Figチャンク（Legend/Results/Methods）からチェック項目 JSON を MLX/CUDA LLM で補助抽出（未導入時は規則のみ）",
    )
    parser.add_argument(
        "--legend-llm-prefer",
        default="auto",
        choices=["auto", "mlx", "cuda", "transformers", "none"],
        help="LLM バックエンド選択（default: auto = MLX→transformers）",
    )
    parser.add_argument(
        "--legend-llm-model",
        default=None,
        help="モデル ID 上書き（例: mlx-community/Qwen2.5-7B-Instruct-4bit）",
    )
    parser.add_argument(
        "--llm-profile",
        default=None,
        help="Text LLM プロファイル ID（llm/model_registry.yaml。例: qwen2.5-7b-mlx, qwen2.5-7b-hf）",
    )
    parser.add_argument(
        "--vlm-profile",
        default=None,
        help="Vision プロファイル ID（Fig 接地用。例: qwen2.5-vl-7b, qwen2.5-vl-32b）",
    )
    parser.add_argument(
        "--vlm-assist",
        action="store_true",
        help="ベクターパネル分割が空の出版 Fig に VLM パネル地図を補助（mlx-vlm / transformers-VL）",
    )
    parser.add_argument(
        "--vlm-prefer",
        default="auto",
        choices=["auto", "mlx", "cuda", "transformers", "none"],
        help="VLM バックエンド選択（default: auto = mlx-vlm→transformers CUDA）",
    )
    parser.add_argument(
        "--list-llm-profiles",
        action="store_true",
        help="利用可能な LLM/VLM プロファイルを表示して終了",
    )
    args = parser.parse_args(argv)

    if args.list_llm_profiles:
        from pre_peer_checker.llm.registry import list_profiles, load_registry

        _, default_llm, default_vlm = load_registry()
        print(f"default_llm_profile: {default_llm}")
        print(f"default_vlm_profile: {default_vlm}")
        print("\n[text]")
        for p in list_profiles("text"):
            print(
                f"  {p.id:24} {p.label}  ({p.model_id}, prefer={p.prefer}, "
                f"~{p.recommended_vram_gb or '?'}GB)"
            )
        print("\n[vision]")
        for p in list_profiles("vision"):
            print(
                f"  {p.id:24} {p.label}  ({p.model_id}, prefer={p.prefer}, "
                f"~{p.recommended_vram_gb or '?'}GB)"
            )
        return 0

    if not args.inputs:
        parser.error("inputs が必要です（または --list-llm-profiles）")

    result = run_verification(
        args.inputs,
        corpus=args.corpus,
        cited_papers=args.cited_papers,
        legend_llm=args.legend_llm,
        legend_llm_prefer=args.legend_llm_prefer,
        legend_llm_model=args.legend_llm_model,
        legend_llm_profile=args.llm_profile,
        vlm_profile=args.vlm_profile,
        vlm_assist=bool(args.vlm_assist),
        vlm_prefer=args.vlm_prefer,
    )
    out = result.write_report(args.output)
    print(f"Report written: {out}")
    print(f"Warnings: {len(result.warnings)}")
    for w in result.warnings:
        print(f"  - {w.tag.value}: {w.title}")

    if args.json_path:
        path = Path(args.json_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "warnings": [w.to_dict() for w in result.warnings],
                    "n_warnings": len(result.warnings),
                    "artifacts": {
                        k: result.artifacts[k]
                        for k in (
                            "legend_json",
                            "figure_chunks",
                            "legend_citation_mentioned",
                            "corpus_present",
                            "corpus_scan",
                            "pdf_image_scan_method",
                            "microscopy_scan",
                        )
                        if k in result.artifacts
                    },
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        print(f"JSON written: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
