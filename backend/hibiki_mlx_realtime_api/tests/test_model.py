from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from hibiki_mlx_realtime_api.config import RuntimeConfig
from hibiki_mlx_realtime_api.model import (
    ModelFiles,
    ModelModules,
    load_language_model,
    resolve_model_files,
)


class FakeCache:
    def __init__(self) -> None:
        self.reset_calls = 0

    def reset(self) -> None:
        self.reset_calls += 1


class FakeConditionProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def condition_tensor(self, name: str, value: str) -> str:
        self.calls.append((name, value))
        return "condition"


class FakeModel:
    def __init__(self) -> None:
        self.dtype = None
        self.loaded: tuple[str, bool] | None = None
        self.warmup_arg = None
        self.condition_provider = FakeConditionProvider()
        self.transformer_cache = [FakeCache(), FakeCache()]
        self.depformer_cache = [FakeCache()]

    def set_dtype(self, dtype: object) -> None:
        self.dtype = dtype

    def load_weights(self, path: str, *, strict: bool) -> None:
        self.loaded = (path, strict)

    def parameters(self) -> str:
        return "parameters"

    def warmup(self, condition: object) -> None:
        self.warmup_arg = condition


class FakeMx:
    gpu = "gpu"
    bfloat16 = "bf16"

    def __init__(self) -> None:
        self.device = None
        self.evaluated: list[object] = []

    def set_default_device(self, device: object) -> None:
        self.device = device

    def eval(self, value: object) -> None:
        self.evaluated.append(value)


class FakeNn:
    def __init__(self) -> None:
        self.quantize_calls: list[dict[str, object]] = []

    def quantize(self, model: object, **kwargs: object) -> None:
        self.quantize_calls.append({"model": model, **kwargs})


class FakeSampler:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class FakeLmGen:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


def _write_model_files(root: Path) -> ModelFiles:
    (root / "config.json").write_text(json.dumps({"dep_q": 16, "n_q": 32}))
    for name in (
        "hibiki.q4.safetensors",
        "mimi-pytorch-e351c8d8@125.safetensors",
        "tokenizer_spm_48k_multi6_2.model",
    ):
        (root / name).write_bytes(b"x")
    return ModelFiles.from_directory(root, revision="deadbeef")


def test_resolve_model_files_pins_revision_and_required_artifacts(tmp_path: Path) -> None:
    _write_model_files(tmp_path)
    calls: list[dict[str, object]] = []

    def fake_snapshot_download(**kwargs: object) -> str:
        calls.append(kwargs)
        return str(tmp_path)

    config = RuntimeConfig(model_revision="deadbeef")
    files = resolve_model_files(config, snapshot_download=fake_snapshot_download)

    assert files.root == tmp_path
    assert files.weights.name == "hibiki.q4.safetensors"
    assert files.mimi.name == "mimi-pytorch-e351c8d8@125.safetensors"
    assert files.tokenizer.name == "tokenizer_spm_48k_multi6_2.model"
    assert calls == [
        {
            "repo_id": "huybik/hibiki-zero-3b-mlx-q4",
            "revision": "deadbeef",
            "allow_patterns": [
                "config.json",
                "hibiki.q4.safetensors",
                "mimi-pytorch-e351c8d8@125.safetensors",
                "tokenizer_spm_48k_multi6_2.model",
            ],
        }
    ]


def test_load_language_model_uses_gpu_q4_strict_weights_and_hibiki_samplers(tmp_path: Path) -> None:
    files = _write_model_files(tmp_path)
    fake_model = FakeModel()
    mx = FakeMx()
    nn = FakeNn()
    config_calls: list[dict[str, object]] = []

    class FakeLmConfig:
        @staticmethod
        def from_config_dict(data: dict[str, object]) -> str:
            config_calls.append(data)
            return "lm-config"

    models = SimpleNamespace(
        LmConfig=FakeLmConfig,
        Lm=lambda config: fake_model,
        LmGen=FakeLmGen,
    )
    modules = ModelModules(
        mx=mx,
        nn=nn,
        models=models,
        utils=SimpleNamespace(Sampler=FakeSampler),
        sentencepiece=SimpleNamespace(SentencePieceProcessor=lambda path: ("tokenizer", path)),
    )

    loaded = load_language_model(files, modules=modules)
    generator = loaded.new_generator(max_steps=500)

    assert mx.device == "gpu"
    assert fake_model.dtype == "bf16"
    assert nn.quantize_calls[0]["bits"] == 4
    assert nn.quantize_calls[0]["group_size"] == 32
    assert fake_model.loaded == (str(files.weights), True)
    assert fake_model.condition_provider.calls == [("description", "very_good")]
    assert fake_model.warmup_arg == "condition"
    assert mx.evaluated == ["parameters", "parameters"]
    assert config_calls == [{"dep_q": 16, "n_q": 32}]
    assert generator.kwargs["model"] is fake_model
    assert generator.kwargs["max_steps"] == 500
    assert generator.kwargs["text_sampler"].kwargs == {"top_k": 25, "temp": 0.4}
    assert generator.kwargs["audio_sampler"].kwargs == {"top_k": 250, "temp": 0.8}
    assert generator.kwargs["cfg_coef"] == 1.0
    assert generator.kwargs["check"] is False

    loaded.reset_state()
    assert [cache.reset_calls for cache in fake_model.transformer_cache] == [1, 1]
    assert [cache.reset_calls for cache in fake_model.depformer_cache] == [1]
