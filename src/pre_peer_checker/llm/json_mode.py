"""Legend JSON スキーマ強制生成（6A）.

優先順:
  1. Outlines + MLX（Mac 配布）
  2. Outlines + transformers（CUDA / Linux）
  3. 自由生成 → ``coerce_legend_dict`` / ``parse_legend_llm_response``（既存）

Outlines 未導入時も製品は動く。Mac 検証は ``scripts/dev_legend_json_mode_verify.py``。
"""

from __future__ import annotations

import json
from typing import Any

from pre_peer_checker.llm.legend_schema import legend_json_schema


def outlines_available() -> bool:
    try:
        import outlines  # noqa: F401

        return True
    except Exception:
        return False


def _schema_for_outlines(schema: dict[str, Any] | None = None) -> dict[str, Any]:
    return schema or legend_json_schema()


def _try_outlines_mlxlm(
    model: Any,
    tokenizer: Any,
    prompt: str,
    schema: dict[str, Any],
    *,
    max_tokens: int,
) -> str | None:
    """Outlines MLX 経路（API 変遷に耐える）."""
    try:
        import outlines
    except Exception:
        return None

    # Newer: outlines.from_mlxlm + JsonSchema / dict output_type
    try:
        from outlines.types import JsonSchema  # type: ignore

        om = outlines.from_mlxlm(model, tokenizer)  # type: ignore[attr-defined]
        out_type = JsonSchema(schema)
        result = om(prompt, output_type=out_type, max_tokens=max_tokens)
        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    except Exception:
        pass

    try:
        om = outlines.from_mlxlm(model, tokenizer)  # type: ignore[attr-defined]
        result = om(prompt, output_type=schema, max_tokens=max_tokens)
        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    except Exception:
        pass

    # Older: outlines.models.mlxlm + generate.json
    try:
        from outlines.models.mlxlm import MLXLM  # type: ignore
        import outlines.generate as ogen  # type: ignore

        om = MLXLM(model, tokenizer)
        generator = ogen.json(om, schema)
        return str(generator(prompt, max_tokens=max_tokens))
    except Exception:
        return None


def _try_outlines_transformers(
    model: Any,
    tokenizer: Any,
    prompt: str,
    schema: dict[str, Any],
    *,
    max_tokens: int,
) -> str | None:
    try:
        import outlines
    except Exception:
        return None

    try:
        from outlines.types import JsonSchema  # type: ignore

        om = outlines.from_transformers(model, tokenizer)  # type: ignore[attr-defined]
        out_type = JsonSchema(schema)
        result = om(prompt, output_type=out_type, max_new_tokens=max_tokens, do_sample=False)
        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    except Exception:
        pass

    try:
        om = outlines.from_transformers(model, tokenizer)  # type: ignore[attr-defined]
        result = om(prompt, output_type=schema, max_new_tokens=max_tokens, do_sample=False)
        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    except Exception:
        pass

    try:
        from outlines.models.transformers import Transformers  # type: ignore
        import outlines.generate as ogen  # type: ignore

        om = Transformers(model, tokenizer)
        generator = ogen.json(om, schema)
        return str(generator(prompt, max_tokens=max_tokens))
    except Exception:
        return None


def _build_outlines_generator(
    kind: str,
    model: Any,
    tokenizer: Any,
    schema: dict[str, Any],
) -> Any | None:
    """Compile the schema once; returns ``fn(prompt, max_tokens)`` or None.

    ``model(prompt, output_type=...)`` builds a fresh Generator — and recompiles
    the JSON-schema automaton — on every call. Building it once per run gives
    the same output without paying that cost for every Figure.
    """
    try:
        import outlines
    except Exception:
        return None

    try:
        from outlines.types import JsonSchema  # type: ignore

        wrap = outlines.from_mlxlm if kind == "mlx" else outlines.from_transformers  # type: ignore[attr-defined]
        gen = outlines.Generator(wrap(model, tokenizer), JsonSchema(schema))  # type: ignore[attr-defined]
        # Kwargs go straight to mlx_lm.generate (greedy by default) / HF generate
        # (would otherwise sample per the model's generation_config).
        if kind == "mlx":
            return lambda prompt, max_tokens: gen(prompt, max_tokens=max_tokens)
        return lambda prompt, max_tokens: gen(
            prompt, max_new_tokens=max_tokens, do_sample=False
        )
    except Exception:
        pass

    try:
        import outlines.generate as ogen  # type: ignore

        if kind == "mlx":
            from outlines.models.mlxlm import MLXLM  # type: ignore

            om = MLXLM(model, tokenizer)
        else:
            from outlines.models.transformers import Transformers  # type: ignore

            om = Transformers(model, tokenizer)
        gen = ogen.json(om, schema)
        return lambda prompt, max_tokens: gen(prompt, max_tokens=max_tokens)
    except Exception:
        return None


def _generate_with_cached_outlines(
    backend: Any,
    kind: str,
    prompt: str,
    schema: dict[str, Any],
    *,
    max_tokens: int,
) -> str | None:
    """Generate via a generator cached on ``backend`` (freed with the backend)."""
    model = getattr(backend, "_model", None)
    tokenizer = getattr(backend, "_tokenizer", None)
    if model is None:
        return None
    cache: dict[tuple[str, int, str], Any] = backend.__dict__.setdefault(
        "_outlines_generators", {}
    )
    key = (kind, id(model), json.dumps(schema, sort_keys=True))
    if key not in cache:
        cache[key] = _build_outlines_generator(kind, model, tokenizer, schema)
    gen = cache[key]
    if gen is None:
        return None
    try:
        result = gen(prompt, max_tokens)
    except Exception:
        return None
    return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)


def structured_legend_generate(
    backend: Any,
    prompt: str,
    *,
    max_tokens: int = 1024,
    schema: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Backend 向けスキーマ強制生成。戻り値: (text, meta).

    meta.json_mode:
      - outlines-mlx
      - outlines-transformers
      - free+coerce（Outlines 無し／失敗時。後段 parse が coerce）
    """
    schema = _schema_for_outlines(schema)
    meta: dict[str, Any] = {
        "json_mode": "free+coerce",
        "outlines_available": outlines_available(),
        "schema_title": schema.get("title"),
    }

    name = ""
    try:
        name = str(backend.info().name)
    except Exception:
        name = type(backend).__name__.lower()

    # Ensure model loaded for structured path
    if hasattr(backend, "load"):
        try:
            backend.load()
        except Exception as exc:
            meta["load_error"] = str(exc)
            text = backend.generate(prompt, max_tokens=max_tokens)
            meta["json_mode"] = "free+coerce"
            return text, meta

    if name in {"mlx", "transformers"} and getattr(backend, "_model", None) is not None:
        text = _generate_with_cached_outlines(
            backend, name, prompt, schema, max_tokens=max_tokens
        )
        if text is not None:
            meta["json_mode"] = f"outlines-{name}"
            return text, meta

    if name == "mlx" and getattr(backend, "_model", None) is not None:
        text = _try_outlines_mlxlm(
            backend._model,
            backend._tokenizer,
            prompt,
            schema,
            max_tokens=max_tokens,
        )
        if text is not None:
            meta["json_mode"] = "outlines-mlx"
            return text, meta

    if name == "transformers" and getattr(backend, "_model", None) is not None:
        text = _try_outlines_transformers(
            backend._model,
            backend._tokenizer,
            prompt,
            schema,
            max_tokens=max_tokens,
        )
        if text is not None:
            meta["json_mode"] = "outlines-transformers"
            return text, meta

    text = backend.generate(prompt, max_tokens=max_tokens)
    meta["json_mode"] = "free+coerce"
    return text, meta


def json_mode_probe() -> dict[str, Any]:
    """依存とバックエンド可否の短い診断（Mac 検証用）."""
    from pre_peer_checker.llm.backend import probe_backends

    return {
        "outlines_available": outlines_available(),
        "backends": [b.__dict__ for b in probe_backends()],
        "schema_title": legend_json_schema().get("title"),
        "mac_verify_hint": (
            "pip install -e '.[mlx]' && pip install 'outlines>=0.1' && "
            "python scripts/dev_legend_json_mode_verify.py --prefer mlx"
        ),
        "cuda_verify_hint": (
            "pip install -e '.[llm-cuda]' && pip install 'outlines>=0.1' && "
            "python scripts/dev_legend_json_mode_verify.py --prefer cuda "
            "--profile qwen2.5-7b-hf"
        ),
    }
