"""Interchangeable streaming Mimi codec backends."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

import numpy as np

from hibiki_mlx_realtime_api.config import CodecBackend

FRAME_SAMPLES = 1920
SAMPLE_RATE = 24_000
CodecExecution = Literal["pipelined", "serial_mlx"]


class StreamingCodec(Protocol):
    """Normalized one-frame Mimi streaming interface."""

    def encode(self, pcm: np.ndarray) -> np.ndarray: ...

    def decode(self, tokens: np.ndarray) -> np.ndarray: ...

    def reset(self) -> None: ...


@dataclass(frozen=True, slots=True)
class CodecModules:
    """Late-bound codec modules, injectable for Linux tests."""

    rustymimi: Any
    mx: Any
    mlx_mimi: Any

    @classmethod
    def load_for(cls, kind: CodecBackend) -> CodecModules:
        if kind == "rust":
            import rustymimi

            return cls(rustymimi=rustymimi, mx=None, mlx_mimi=None)
        if kind == "mlx":
            import mlx.core as mx
            from moshi_mlx.models import mimi as mlx_mimi

            return cls(rustymimi=None, mx=mx, mlx_mimi=mlx_mimi)
        raise ValueError(f"unsupported codec: {kind}")


@dataclass(slots=True)
class CodecPair:
    """Independent encoder and decoder states plus their safe execution topology."""

    encoder: StreamingCodec
    decoder: StreamingCodec
    execution: CodecExecution = "pipelined"

    def warmup(self) -> None:
        """Exercise one complete 80 ms codec frame, then restore clean state."""
        try:
            codes = self.encoder.encode(np.zeros(FRAME_SAMPLES, dtype=np.float32))
            self.decoder.decode(codes)
        finally:
            self.reset()

    def reset(self) -> None:
        self.encoder.reset()
        self.decoder.reset()


class RustMimiCodec:
    """CPU Mimi through the GIL-releasing rustymimi binding."""

    def __init__(self, tokenizer: Any) -> None:
        self._tokenizer = tokenizer

    def encode(self, pcm: np.ndarray) -> np.ndarray:
        pcm = _normalize_pcm(pcm)
        codes = self._tokenizer.encode_step(pcm[None, None, :])
        if codes is None:
            raise RuntimeError("rustymimi produced no codes for a complete 80 ms frame")
        return _codes_to_lm(np.asarray(codes, dtype=np.uint32))

    def decode(self, tokens: np.ndarray) -> np.ndarray:
        tokens = _tokens_to_codec(tokens)
        pcm = self._tokenizer.decode_step(tokens)
        if pcm is None:
            raise RuntimeError("rustymimi produced no PCM for a complete audio-token frame")
        return _codec_pcm_to_frame(np.asarray(pcm, dtype=np.float32))

    def reset(self) -> None:
        self._tokenizer.reset()


class MlxMimiCodec:
    """GPU Mimi using the MLX implementation from the pinned Moshi fork."""

    def __init__(self, model: Any, mx: Any) -> None:
        self._model = model
        self._mx = mx

    def encode(self, pcm: np.ndarray) -> np.ndarray:
        pcm = _normalize_pcm(pcm)
        codes = self._model.encode_step(self._mx.array(pcm[None, None, :]))
        self._mx.eval(codes)
        return _codes_to_lm(np.asarray(codes, dtype=np.uint32))

    def decode(self, tokens: np.ndarray) -> np.ndarray:
        codec_tokens = self._mx.array(_tokens_to_codec(tokens))
        pcm = self._model.decode_step(codec_tokens)
        self._mx.eval(pcm)
        return _codec_pcm_to_frame(np.asarray(pcm, dtype=np.float32))

    def reset(self) -> None:
        self._model.reset_all()


def create_codec_pair(
    kind: CodecBackend,
    *,
    mimi_path: str | Path,
    num_codebooks: int,
    checkpoint_codebooks: int,
    modules: CodecModules | None = None,
) -> CodecPair:
    """Create independent encoder/decoder states for one WebSocket session."""
    if num_codebooks <= 0:
        raise ValueError("num_codebooks must be > 0")
    if checkpoint_codebooks < num_codebooks:
        raise ValueError("checkpoint_codebooks must be >= num_codebooks")
    modules = modules or CodecModules.load_for(kind)
    mimi_path = Path(mimi_path)

    if kind == "rust":
        tokenizer_type = modules.rustymimi.Tokenizer
        encoder = tokenizer_type(str(mimi_path), num_codebooks=num_codebooks)
        decoder = tokenizer_type(str(mimi_path), num_codebooks=num_codebooks)
        return CodecPair(
            RustMimiCodec(encoder),
            RustMimiCodec(decoder),
            execution="pipelined",
        )

    if kind == "mlx":
        mx = modules.mx
        mx.set_default_device(mx.gpu)
        config = modules.mlx_mimi.mimi_202407(checkpoint_codebooks)
        encoder_model = _load_mlx_mimi(
            config,
            mimi_path,
            active_codebooks=num_codebooks,
            modules=modules,
        )
        decoder_model = _load_mlx_mimi(
            config,
            mimi_path,
            active_codebooks=num_codebooks,
            modules=modules,
        )
        return CodecPair(
            MlxMimiCodec(encoder_model, mx),
            MlxMimiCodec(decoder_model, mx),
            execution="serial_mlx",
        )

    raise ValueError(f"unsupported codec: {kind}")


def _load_mlx_mimi(
    config: Any,
    mimi_path: Path,
    *,
    active_codebooks: int,
    modules: CodecModules,
) -> Any:
    model = modules.mlx_mimi.Mimi(config)
    model.load_pytorch_weights(str(mimi_path), strict=True)
    modules.mx.eval(model.parameters())
    _restrict_mlx_mimi_codebooks(model, active_codebooks)
    return model


def _restrict_mlx_mimi_codebooks(model: Any, active_codebooks: int) -> None:
    """Mirror PyTorch Mimi.set_num_codebooks after a strict full-checkpoint load."""
    quantizer = model.quantizer
    checkpoint_codebooks = int(quantizer._nq)
    if not 1 <= active_codebooks <= checkpoint_codebooks:
        raise ValueError(
            f"active Mimi codebooks must be in 1..{checkpoint_codebooks}, got {active_codebooks}"
        )
    if active_codebooks == checkpoint_codebooks:
        return

    rest_layers = quantizer.rvq_rest.vq.layers
    required_rest = active_codebooks - 1
    if len(rest_layers) < required_rest:
        raise ValueError(
            f"Mimi has only {len(rest_layers) + 1} loadable codebooks, "
            f"cannot activate {active_codebooks}"
        )
    quantizer.rvq_rest.vq.layers = rest_layers[:required_rest]
    quantizer._nq = active_codebooks


def _normalize_pcm(pcm: np.ndarray) -> np.ndarray:
    pcm = np.asarray(pcm, dtype=np.float32)
    if pcm.shape != (FRAME_SAMPLES,):
        raise ValueError(f"expected PCM shape ({FRAME_SAMPLES},), got {pcm.shape}")
    return np.ascontiguousarray(pcm)


def _codes_to_lm(codes: np.ndarray) -> np.ndarray:
    if codes.ndim != 3 or codes.shape[0] != 1 or codes.shape[-1] != 1:
        raise ValueError(f"unexpected Mimi code shape: {codes.shape}")
    return np.ascontiguousarray(codes.transpose(0, 2, 1)[0])


def _tokens_to_codec(tokens: np.ndarray) -> np.ndarray:
    tokens = np.asarray(tokens, dtype=np.uint32)
    if tokens.ndim != 2 or tokens.shape[0] != 1:
        raise ValueError(f"expected token shape (1, codebooks), got {tokens.shape}")
    return np.ascontiguousarray(tokens[:, :, None])


def _codec_pcm_to_frame(pcm: np.ndarray) -> np.ndarray:
    if pcm.shape != (1, 1, FRAME_SAMPLES):
        raise ValueError(f"unexpected Mimi PCM shape: {pcm.shape}")
    return np.ascontiguousarray(pcm[0, 0])
