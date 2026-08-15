from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import numpy as np

from hibiki_mlx_realtime_api.codecs import CodecModules, create_codec_pair


class FakeRustTokenizer:
    instances: ClassVar[list[FakeRustTokenizer]] = []

    def __init__(self, path: str, *, num_codebooks: int) -> None:
        self.path = path
        self.num_codebooks = num_codebooks
        self.reset_calls = 0
        self.__class__.instances.append(self)

    def encode_step(self, pcm: np.ndarray) -> np.ndarray:
        assert pcm.shape == (1, 1, 1920)
        return np.arange(self.num_codebooks, dtype=np.uint32).reshape(1, -1, 1)

    def decode_step(self, tokens: np.ndarray) -> np.ndarray:
        assert tokens.shape == (1, self.num_codebooks, 1)
        return np.ones((1, 1, 1920), dtype=np.float32)

    def reset(self) -> None:
        self.reset_calls += 1


class FakeMx:
    gpu = "gpu"

    def __init__(self) -> None:
        self.device = None
        self.eval_calls: list[object] = []

    def set_default_device(self, device: object) -> None:
        self.device = device

    def array(self, value: object) -> np.ndarray:
        return np.asarray(value)

    def eval(self, value: object) -> None:
        self.eval_calls.append(value)


class FakeMimi:
    instances: ClassVar[list[FakeMimi]] = []

    def __init__(self, config: int) -> None:
        self.config = config
        self.loaded: tuple[str, bool] | None = None
        self.reset_calls = 0
        self.quantizer = SimpleNamespace(
            _nq=config,
            rvq_rest=SimpleNamespace(
                vq=SimpleNamespace(layers=[object() for _ in range(config - 1)])
            ),
        )
        self.__class__.instances.append(self)

    def load_pytorch_weights(self, path: str, *, strict: bool) -> None:
        self.loaded = (path, strict)

    def parameters(self) -> str:
        return "mimi-parameters"

    def encode_step(self, pcm: np.ndarray) -> np.ndarray:
        active = self.quantizer._nq
        values = np.arange(1, active + 1, dtype=np.uint32)
        return values.reshape(1, -1, 1)

    def decode_step(self, tokens: np.ndarray) -> np.ndarray:
        assert tokens.shape[1] == self.quantizer._nq
        return np.ones((1, 1, 1920), dtype=np.float32) * 0.5

    def reset_all(self) -> None:
        self.reset_calls += 1


def _rust_pair(tmp_path: Path):
    FakeRustTokenizer.instances.clear()
    mimi_path = tmp_path / "mimi.safetensors"
    mimi_path.write_bytes(b"x")
    modules = CodecModules(
        rustymimi=SimpleNamespace(Tokenizer=FakeRustTokenizer),
        mx=None,
        mlx_mimi=None,
    )
    return create_codec_pair(
        "rust",
        mimi_path=mimi_path,
        num_codebooks=2,
        checkpoint_codebooks=32,
        modules=modules,
    )


def test_rust_codec_pair_has_independent_state_and_normalized_shapes(tmp_path: Path) -> None:
    pair = _rust_pair(tmp_path)
    codes = pair.encoder.encode(np.zeros(1920, dtype=np.float32))
    pcm = pair.decoder.decode(codes)

    assert pair.execution == "pipelined"
    assert len(FakeRustTokenizer.instances) == 2
    assert FakeRustTokenizer.instances[0] is not FakeRustTokenizer.instances[1]
    assert codes.shape == (1, 2)
    assert codes.tolist() == [[0, 1]]
    assert pcm.shape == (1920,)
    pair.reset()
    assert [item.reset_calls for item in FakeRustTokenizer.instances] == [1, 1]


def test_codec_pair_warmup_runs_complete_frame_then_resets(tmp_path: Path) -> None:
    pair = _rust_pair(tmp_path)

    pair.warmup()

    assert [item.reset_calls for item in FakeRustTokenizer.instances] == [1, 1]


def test_mlx_codec_loads_full_checkpoint_strictly_then_uses_only_active_codebooks(
    tmp_path: Path,
) -> None:
    FakeMimi.instances.clear()
    mimi_path = tmp_path / "mimi.safetensors"
    mimi_path.write_bytes(b"x")
    mx = FakeMx()
    modules = CodecModules(
        rustymimi=None,
        mx=mx,
        mlx_mimi=SimpleNamespace(
            mimi_202407=lambda num_codebooks: num_codebooks,
            Mimi=FakeMimi,
        ),
    )

    pair = create_codec_pair(
        "mlx",
        mimi_path=mimi_path,
        num_codebooks=2,
        checkpoint_codebooks=32,
        modules=modules,
    )
    codes = pair.encoder.encode(np.zeros(1920, dtype=np.float32))
    pcm = pair.decoder.decode(codes)

    assert pair.execution == "serial_mlx"
    assert mx.device == "gpu"
    assert len(FakeMimi.instances) == 2
    assert FakeMimi.instances[0] is not FakeMimi.instances[1]
    assert [item.config for item in FakeMimi.instances] == [32, 32]
    assert [item.loaded for item in FakeMimi.instances] == [
        (str(mimi_path), True),
        (str(mimi_path), True),
    ]
    assert [item.quantizer._nq for item in FakeMimi.instances] == [2, 2]
    assert [len(item.quantizer.rvq_rest.vq.layers) for item in FakeMimi.instances] == [1, 1]
    assert codes.tolist() == [[1, 2]]
    assert pcm.shape == (1920,)
    pair.reset()
    assert [item.reset_calls for item in FakeMimi.instances] == [1, 1]
