"""Deterministic raw-PCM replay tools for Hibiki quality experiments."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import wave
from collections.abc import Awaitable, Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

SAMPLE_RATE = 24_000
FRAME_SAMPLES = 1_920
PCM_BYTES_PER_SAMPLE = 2
FRAME_BYTES = FRAME_SAMPLES * PCM_BYTES_PER_SAMPLE
FRAME_SECONDS = FRAME_SAMPLES / SAMPLE_RATE
PCM_INPUT_KIND = 3
DEFAULT_URL = "ws://127.0.0.1:8998/api/chat"


@dataclass(frozen=True, slots=True)
class SourceWav:
    """Canonical source audio used by a reproducible quality experiment."""

    path: Path
    pcm16: bytes
    samples: int
    sha256: str


class ReplayCapture:
    """Collect Hibiki text/audio output from the native binary protocol."""

    def __init__(self, opus_reader: Any) -> None:
        self._opus_reader = opus_reader
        self._text_parts: list[str] = []
        self._audio_parts: list[np.ndarray] = []
        self.handshake_received = False

    @property
    def transcript(self) -> str:
        return "".join(self._text_parts)

    @property
    def translated_pcm(self) -> np.ndarray:
        if not self._audio_parts:
            return np.empty(0, dtype=np.float32)
        return np.ascontiguousarray(np.concatenate(self._audio_parts))

    def consume(self, message: bytes) -> None:
        """Consume one native Hibiki server message strictly."""
        if not message:
            raise ValueError("empty Hibiki server message")

        kind = message[0]
        payload = message[1:]
        if kind == 0:
            self.handshake_received = True
            return
        if kind == 1:
            pcm = np.asarray(self._opus_reader.append_bytes(payload), dtype=np.float32).reshape(-1)
            if pcm.size:
                self._audio_parts.append(np.ascontiguousarray(pcm.copy()))
            return
        if kind == 2:
            self._text_parts.append(payload.decode("utf-8"))
            return
        raise ValueError(f"unknown Hibiki server message kind: {kind}")


def load_source_wav(path: Path) -> SourceWav:
    """Load an exact mono 24 kHz PCM16 WAV without resampling or normalization."""
    path = Path(path)
    with wave.open(str(path), "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        sample_rate = wav_file.getframerate()
        compression = wav_file.getcomptype()
        frame_count = wav_file.getnframes()

        if channels != 1:
            raise ValueError(f"replay WAV must be mono, got {channels} channels")
        if sample_width != PCM_BYTES_PER_SAMPLE:
            raise ValueError(
                f"replay WAV must be 16-bit PCM, got {sample_width * 8}-bit samples"
            )
        if sample_rate != SAMPLE_RATE:
            raise ValueError(f"replay WAV must be 24000 Hz, got {sample_rate} Hz")
        if compression != "NONE":
            raise ValueError(f"replay WAV must be uncompressed PCM, got {compression}")

        pcm16 = wav_file.readframes(frame_count)

    if len(pcm16) % PCM_BYTES_PER_SAMPLE:
        raise ValueError("replay WAV contains an incomplete PCM16 sample")

    return SourceWav(
        path=path,
        pcm16=pcm16,
        samples=len(pcm16) // PCM_BYTES_PER_SAMPLE,
        sha256=hashlib.sha256(pcm16).hexdigest(),
    )


def replay_frames(pcm16: bytes, *, tail_seconds: float) -> Iterator[bytes]:
    """Yield exact 80 ms PCM16 frames followed by a deterministic silence tail."""
    if len(pcm16) % PCM_BYTES_PER_SAMPLE:
        raise ValueError("PCM16 replay payload must contain complete 16-bit samples")
    if tail_seconds < 0:
        raise ValueError("tail_seconds must be >= 0")

    for offset in range(0, len(pcm16), FRAME_BYTES):
        frame = pcm16[offset : offset + FRAME_BYTES]
        if len(frame) < FRAME_BYTES:
            frame += b"\x00" * (FRAME_BYTES - len(frame))
        yield frame

    tail_frames = int(round(tail_seconds / FRAME_SECONDS))
    silence = b"\x00" * FRAME_BYTES
    for _ in range(tail_frames):
        yield silence


async def send_frames_realtime(
    ws: Any,
    frames: Iterable[bytes],
    *,
    clock: Callable[[], float],
    sleep: Callable[[float], Awaitable[None]],
) -> int:
    """Send PCM frames at the native 12.5 Hz cadence instead of flooding the queue."""
    deadline = clock()
    sent = 0
    for frame in frames:
        if len(frame) != FRAME_BYTES:
            raise ValueError(f"expected {FRAME_BYTES} PCM bytes, got {len(frame)}")
        if sent:
            deadline += FRAME_SECONDS
            delay = deadline - clock()
            if delay > 0:
                await sleep(delay)
        await ws.send_bytes(bytes((PCM_INPUT_KIND,)) + frame)
        sent += 1
    return sent


def write_translated_wav(path: Path, pcm: np.ndarray) -> None:
    """Write normalized mono float PCM as canonical 24 kHz PCM16 WAV."""
    samples = np.asarray(pcm, dtype=np.float32).reshape(-1)
    clipped = np.clip(samples, -1.0, 1.0)
    pcm16 = np.where(
        clipped < 0,
        np.rint(clipped * 32768.0),
        np.rint(clipped * 32767.0),
    ).astype("<i2")

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(PCM_BYTES_PER_SAMPLE)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(pcm16.tobytes())


def write_replay_artifacts(
    output_dir: Path,
    *,
    source: SourceWav,
    capture: ReplayCapture,
    metadata: dict[str, Any],
) -> None:
    """Write one self-contained experiment result without overwriting prior runs."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(source.path, output_dir / "source.wav")
    (output_dir / "transcript.txt").write_text(capture.transcript, encoding="utf-8")
    translated_pcm = capture.translated_pcm
    write_translated_wav(output_dir / "translated.wav", translated_pcm)

    manifest = {
        **metadata,
        "protocol": "hibiki-native-pcm16le-kind-3",
        "sample_rate": SAMPLE_RATE,
        "frame_samples": FRAME_SAMPLES,
        "frame_seconds": FRAME_SECONDS,
        "source_pcm_sha256": source.sha256,
        "source_samples": source.samples,
        "source_seconds": source.samples / SAMPLE_RATE,
        "output_samples": int(translated_pcm.size),
        "output_seconds": float(translated_pcm.size / SAMPLE_RATE),
        "transcript_chars": len(capture.transcript),
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


async def _receive_output(ws: Any, capture: ReplayCapture) -> None:
    from aiohttp import WSMsgType

    async for message in ws:
        if message.type == WSMsgType.BINARY:
            capture.consume(bytes(message.data))
        elif message.type == WSMsgType.ERROR:
            raise RuntimeError(f"Hibiki websocket failed: {ws.exception()}")
        elif message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING}:
            return
        else:
            raise RuntimeError(f"unexpected Hibiki websocket message type: {message.type}")


async def run_replay(
    source_path: Path,
    *,
    output_dir: Path,
    url: str,
    label: str,
    tail_seconds: float,
    settle_seconds: float,
) -> Path:
    """Replay a canonical WAV through a live Hibiki websocket and save the result."""
    if settle_seconds < 0:
        raise ValueError("settle_seconds must be >= 0")

    import sphn
    from aiohttp import ClientSession, WSMsgType

    source = load_source_wav(source_path)
    capture = ReplayCapture(sphn.OpusStreamReader(SAMPLE_RATE))
    loop = asyncio.get_running_loop()

    async with (
        ClientSession() as client,
        client.ws_connect(url, max_msg_size=0) as ws,
    ):
        handshake = await ws.receive(timeout=10.0)
        if handshake.type != WSMsgType.BINARY:
            raise RuntimeError(
                f"expected binary Hibiki handshake, got websocket type {handshake.type}"
            )
        capture.consume(bytes(handshake.data))
        if not capture.handshake_received:
            raise RuntimeError("Hibiki websocket did not send the expected kind-0 handshake")

        receiver = asyncio.create_task(_receive_output(ws, capture))
        try:
            input_frames = await send_frames_realtime(
                ws,
                replay_frames(source.pcm16, tail_seconds=tail_seconds),
                clock=loop.time,
                sleep=asyncio.sleep,
            )
            if settle_seconds:
                await asyncio.sleep(settle_seconds)
        finally:
            await ws.close()
            await receiver

    write_replay_artifacts(
        output_dir,
        source=source,
        capture=capture,
        metadata={
            "label": label,
            "url": url,
            "tail_seconds": tail_seconds,
            "settle_seconds": settle_seconds,
            "input_frames": input_frames,
        },
    )
    return Path(output_dir)


def build_parser() -> argparse.ArgumentParser:
    """Build the deterministic source-replay command line."""
    parser = argparse.ArgumentParser(
        description="Replay an exact 24 kHz mono PCM16 WAV through Hibiki in realtime."
    )
    parser.add_argument("source_wav", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--label", default="replay")
    parser.add_argument("--tail-seconds", type=float, default=6.0)
    parser.add_argument("--settle-seconds", type=float, default=1.0)
    return parser


def main() -> None:
    """CLI entrypoint for repeatable quality experiments."""
    args = build_parser().parse_args()
    output_dir = asyncio.run(
        run_replay(
            args.source_wav,
            output_dir=args.output_dir,
            url=args.url,
            label=args.label,
            tail_seconds=args.tail_seconds,
            settle_seconds=args.settle_seconds,
        )
    )
    print(f"replay artifacts: {output_dir}")


if __name__ == "__main__":
    main()
