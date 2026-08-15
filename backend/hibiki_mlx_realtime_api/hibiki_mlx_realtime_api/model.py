"""Pinned Hibiki-Zero q4 artifact resolution and MLX model loading."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hibiki_mlx_realtime_api.config import RuntimeConfig

_REQUIRED_FILES = (
    "config.json",
    "hibiki.q4.safetensors",
    "mimi-pytorch-e351c8d8@125.safetensors",
    "tokenizer_spm_48k_multi6_2.model",
)


@dataclass(frozen=True, slots=True)
class ModelFiles:
    """Resolved local files for one immutable model snapshot."""

    root: Path
    config: Path
    weights: Path
    mimi: Path
    tokenizer: Path
    revision: str

    @classmethod
    def from_directory(cls, root: str | Path, *, revision: str) -> ModelFiles:
        root = Path(root)
        paths = {name: root / name for name in _REQUIRED_FILES}
        missing = [str(path) for path in paths.values() if not path.is_file()]
        if missing:
            raise FileNotFoundError("missing Hibiki MLX artifacts: " + ", ".join(missing))
        return cls(
            root=root,
            config=paths["config.json"],
            weights=paths["hibiki.q4.safetensors"],
            mimi=paths["mimi-pytorch-e351c8d8@125.safetensors"],
            tokenizer=paths["tokenizer_spm_48k_multi6_2.model"],
            revision=revision,
        )


@dataclass(frozen=True, slots=True)
class ModelModules:
    """Late-bound heavy modules, injectable for Linux unit tests."""

    mx: Any
    nn: Any
    models: Any
    utils: Any
    sentencepiece: Any

    @classmethod
    def load_default(cls) -> ModelModules:
        import mlx.core as mx
        import mlx.nn as nn
        import sentencepiece
        from moshi_mlx import models, utils

        return cls(mx=mx, nn=nn, models=models, utils=utils, sentencepiece=sentencepiece)


@dataclass(slots=True)
class LoadedLanguageModel:
    """One process-wide MLX language model plus session construction helpers."""

    model: Any
    lm_config: Any
    tokenizer: Any
    condition: Any
    modules: ModelModules

    def reset_state(self) -> None:
        for cache in self.model.transformer_cache:
            cache.reset()
        for cache in self.model.depformer_cache:
            cache.reset()
        parallel_head = getattr(self.model, "parallel_head", None)
        if parallel_head is not None:
            parallel_head.reset()

    def new_generator(self, *, max_steps: int, text_temperature: float = 0.4) -> Any:
        return self.modules.models.LmGen(
            model=self.model,
            max_steps=max_steps,
            text_sampler=self.modules.utils.Sampler(top_k=25, temp=text_temperature),
            audio_sampler=self.modules.utils.Sampler(top_k=250, temp=0.8),
            cfg_coef=1.0,
            check=False,
        )


def resolve_model_files(
    config: RuntimeConfig,
    *,
    snapshot_download: Callable[..., str] | None = None,
) -> ModelFiles:
    """Download/cache only the files required by the q4 realtime runtime."""
    if snapshot_download is None:
        from huggingface_hub import snapshot_download as hf_snapshot_download

        snapshot_download = hf_snapshot_download

    root = snapshot_download(
        repo_id=config.model_repo,
        revision=config.model_revision,
        allow_patterns=list(_REQUIRED_FILES),
    )
    return ModelFiles.from_directory(root, revision=config.model_revision)


def _q4_compatible(_path: str, module: Any) -> bool:
    weight = getattr(module, "weight", None)
    return (
        weight is not None
        and hasattr(module, "to_quantized")
        and weight.shape[-1] % 32 == 0
    )


def load_language_model(
    files: ModelFiles,
    *,
    modules: ModelModules | None = None,
) -> LoadedLanguageModel:
    """Build and warm one Hibiki-Zero 3B q4 model on the MLX GPU."""
    modules = modules or ModelModules.load_default()
    mx = modules.mx
    nn = modules.nn

    mx.set_default_device(mx.gpu)
    config_data = json.loads(files.config.read_text())
    lm_config = modules.models.LmConfig.from_config_dict(config_data)
    model = modules.models.Lm(lm_config)
    model.set_dtype(mx.bfloat16)
    nn.quantize(
        model,
        bits=4,
        group_size=32,
        class_predicate=_q4_compatible,
    )
    model.load_weights(str(files.weights), strict=True)
    mx.eval(model.parameters())

    tokenizer = modules.sentencepiece.SentencePieceProcessor(str(files.tokenizer))
    condition = None
    if model.condition_provider is not None:
        condition = model.condition_provider.condition_tensor("description", "very_good")

    model.warmup(condition)
    mx.eval(model.parameters())
    return LoadedLanguageModel(
        model=model,
        lm_config=lm_config,
        tokenizer=tokenizer,
        condition=condition,
        modules=modules,
    )
