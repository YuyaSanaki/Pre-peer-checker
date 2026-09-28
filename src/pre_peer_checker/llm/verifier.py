"""ローカル LLM/VLM（読む本線）と Legend JSON（規則フォールバック）."""

from __future__ import annotations

from typing import Any

from pre_peer_checker.llm.backend import (
    BackendInfo,
    CallableBackend,
    LocalLLMBackend,
    MLXBackend,
    TransformersBackend,
    probe_backends,
    select_backend,
)
from pre_peer_checker.llm.legend_extract import (
    extract_check_items_from_chunk,
    extract_legend_json_hybrid,
    extract_legends_json_from_docx,
    extract_legends_with_backend,
    legend_json_to_panel_ns,
    legends_any_citation,
    legends_to_artifact,
    merge_panel_ns,
    structured_to_legend_json,
)
from pre_peer_checker.llm.legend_schema import (
    FIGURE_CHUNK_SYSTEM_PROMPT,
    LEGEND_JSON_SCHEMA,
    LegendFigureJSON,
    build_figure_chunk_llm_prompt,
    build_legend_llm_prompt,
    detect_citation,
    legend_json_schema,
    parse_legend_llm_response,
    validate_legend_dict,
)

SYSTEM_PROMPT = """あなたは論文のデータ整合性を検証する専門家です。Figure Legendの記述と、生データから再計算した統計値を照合し、不整合があれば細分類タグ付きのWarningを出力してください。

細分類タグ:
- Warning [データ取り違え]
- Warning [実験データとの不一致]
- Warning [サンプルサイズ記載誤記]
- Warning [統計手法の不整合]
- Warning [表記揺れ・参照不整合]
"""


def build_verify_prompt(legend_text: str, calculated_stats: dict[str, Any] | str) -> str:
    return f"""<|im_start|>system
{SYSTEM_PROMPT}
<|im_end|>
<|im_start|>user
【Figure Legend 記載内容】
{legend_text}

【生データ再計算結果】
{calculated_stats}

照合結果を日本語で出力してください。
<|im_end|>
<|im_start|>assistant
"""


class LocalManuscriptVerifier:
    """Local LLM verifier — MLX on Mac, transformers on CUDA/CPU when available."""

    def __init__(
        self,
        model_path: str | None = None,
        *,
        prefer: str = "auto",
        backend: LocalLLMBackend | None = None,
    ):
        self.prefer = prefer
        self.model_path = model_path
        self._backend = backend

    def _ensure(self) -> LocalLLMBackend:
        if self._backend is None:
            self._backend = select_backend(self.prefer, model_id=self.model_path)
            if self._backend is None:
                raise RuntimeError(
                    "No local LLM backend (install mlx extras on Mac or transformers+torch on CUDA)"
                )
        return self._backend

    def load(self) -> None:
        be = self._ensure()
        if hasattr(be, "load"):
            be.load()  # type: ignore[attr-defined]

    def verify_consistency(self, legend_text: str, calculated_stats: dict[str, Any] | str) -> str:
        be = self._ensure()
        return be.generate(build_verify_prompt(legend_text, calculated_stats), max_tokens=512)

    def extract_legend_json(self, legend_text: str, figure_hint: str | None = None) -> LegendFigureJSON:
        be = self._ensure()
        return extract_legend_json_hybrid(
            legend_text,
            figure_hint=figure_hint,
            llm_generate=lambda p: be.generate(p, max_tokens=768),
            prefer_llm=True,
        )

    def backend_info(self) -> BackendInfo | None:
        try:
            return self._ensure().info()
        except RuntimeError:
            return None


__all__ = [
    "SYSTEM_PROMPT",
    "FIGURE_CHUNK_SYSTEM_PROMPT",
    "build_verify_prompt",
    "LocalManuscriptVerifier",
    "LocalLLMBackend",
    "MLXBackend",
    "TransformersBackend",
    "CallableBackend",
    "BackendInfo",
    "probe_backends",
    "select_backend",
    "LEGEND_JSON_SCHEMA",
    "LegendFigureJSON",
    "build_legend_llm_prompt",
    "build_figure_chunk_llm_prompt",
    "detect_citation",
    "legend_json_schema",
    "parse_legend_llm_response",
    "validate_legend_dict",
    "extract_legend_json_hybrid",
    "extract_check_items_from_chunk",
    "extract_legends_json_from_docx",
    "extract_legends_with_backend",
    "legend_json_to_panel_ns",
    "merge_panel_ns",
    "legends_any_citation",
    "legends_to_artifact",
    "structured_to_legend_json",
]
