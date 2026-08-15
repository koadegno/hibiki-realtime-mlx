import hashlib
import json
import wave
from importlib.util import find_spec
from pathlib import Path

import numpy as np
import pytest

import hibiki_mlx_realtime_api.replay as replay


class FakeOpusReader:
    def __init__(self) -> None:
        self.payloads: list[bytes] = []

    def append_bytes(self, payload: bytes) -> np.ndarray:
        self.payloads.append(payload)
        return np.array([0.25, -0.5], dtype=np.float32)


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[bytes] = []

    async def send_bytes(self, payload: bytes) -> None:
        self.messages.append(payload)


def _write_wav(
    path: Path,
    pcm16: bytes,
    *,
    sample_rate: int = 24_000,
    channels: int = 1,
    sample_width: int = 2,
) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm16)


def test_deterministic_pcm_replay_module_is_packaged() -> None:
    assert find_spec("hibiki_mlx_realtime_api.replay") is not None


def test_load_source_wav_preserves_exact_pcm16_and_hash(tmp_path: Path) -> None:
    pcm16 = bytes(range(64)) * 8
    path = tmp_path / "source.wav"
    _write_wav(path, pcm16)

    source = replay.load_source_wav(path)

    assert source.pcm16 == pcm16
    assert source.samples == len(pcm16) // 2
    assert source.sha256 == hashlib.sha256(pcm16).hexdigest()


@pytest.mark.parametrize(
    ("sample_rate", "channels", "sample_width", "expected"),
    [
        (48_000, 1, 2, "24000 Hz"),
        (24_000, 2, 2, "mono"),
        (24_000, 1, 1, "16-bit"),
    ],
)
def test_load_source_wav_rejects_noncanonical_audio(
    tmp_path: Path,
    sample_rate: int,
    channels: int,
    sample_width: int,
    expected: str,
) -> None:
    path = tmp_path / "bad.wav"
    frame_bytes = channels * sample_width
    _write_wav(
        path,
        b"\x00" * frame_bytes * 32,
        sample_rate=sample_rate,
        channels=channels,
        sample_width=sample_width,
    )

    with pytest.raises(ValueError, match=expected):
        replay.load_source_wav(path)


def test_replay_frames_pad_last_source_frame_then_append_exact_silence() -> None:
    frame_bytes = 1920 * 2
    pcm16 = b"\x01\x00" * 1921

    frames = list(replay.replay_frames(pcm16, tail_seconds=0.16))

    assert len(frames) == 4
    assert all(len(frame) == frame_bytes for frame in frames)
    assert frames[0] == pcm16[:frame_bytes]
    assert frames[1][:2] == b"\x01\x00"
    assert frames[1][2:] == b"\x00" * (frame_bytes - 2)
    assert frames[2] == b"\x00" * frame_bytes
    assert frames[3] == b"\x00" * frame_bytes


def test_replay_capture_preserves_protocol_text_and_decodes_audio() -> None:
    reader = FakeOpusReader()
    capture = replay.ReplayCapture(reader)

    capture.consume(b"\x00")
    capture.consume(b"\x02 hello")
    capture.consume(b"\x01opus-a")
    capture.consume(b"\x02 world")
    capture.consume(b"\x01opus-b")

    assert capture.handshake_received is True
    assert capture.transcript == " hello world"
    assert reader.payloads == [b"opus-a", b"opus-b"]
    np.testing.assert_array_equal(
        capture.translated_pcm,
        np.array([0.25, -0.5, 0.25, -0.5], dtype=np.float32),
    )


def test_replay_capture_rejects_unknown_server_message_kind() -> None:
    capture = replay.ReplayCapture(FakeOpusReader())

    with pytest.raises(ValueError, match="unknown Hibiki server message kind"):
        capture.consume(b"\x07oops")


def test_write_translated_wav_is_mono_24k_pcm16(tmp_path: Path) -> None:
    path = tmp_path / "translated.wav"
    replay.write_translated_wav(path, np.array([-1.0, -0.5, 0.0, 0.5, 1.0], dtype=np.float32))

    with wave.open(str(path), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 24_000
        pcm16 = np.frombuffer(wav_file.readframes(5), dtype="<i2")

    np.testing.assert_array_equal(pcm16, np.array([-32768, -16384, 0, 16384, 32767]))


@pytest.mark.asyncio
async def test_send_frames_realtime_prefixes_pcm_kind_and_paces_after_first_frame() -> None:
    ws = FakeWebSocket()
    frames = [b"a" * replay.FRAME_BYTES, b"b" * replay.FRAME_BYTES]
    clock_values = iter((10.0, 10.01))
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    sent = await replay.send_frames_realtime(
        ws,
        frames,
        clock=lambda: next(clock_values),
        sleep=fake_sleep,
    )

    assert sent == 2
    assert ws.messages == [b"\x03" + frames[0], b"\x03" + frames[1]]
    assert sleeps == pytest.approx([0.07])


def test_write_replay_artifacts_records_source_identity_and_outputs(tmp_path: Path) -> None:
    source_path = tmp_path / "input.wav"
    _write_wav(source_path, b"\x01\x00" * 1920)
    source = replay.load_source_wav(source_path)
    capture = replay.ReplayCapture(FakeOpusReader())
    capture.consume(b"\x00")
    capture.consume(b"\x02 translated text")
    capture.consume(b"\x01opus")
    output_dir = tmp_path / "run"

    replay.write_replay_artifacts(
        output_dir,
        source=source,
        capture=capture,
        metadata={
            "label": "adaptive-reset-pcm",
            "url": "ws://127.0.0.1:8998/api/chat",
            "tail_seconds": 6.0,
            "input_frames": 76,
        },
    )

    assert (output_dir / "source.wav").read_bytes() == source_path.read_bytes()
    assert (output_dir / "transcript.txt").read_text() == " translated text"
    assert (output_dir / "translated.wav").is_file()
    manifest = json.loads((output_dir / "manifest.json").read_text())
    assert manifest["label"] == "adaptive-reset-pcm"
    assert manifest["source_pcm_sha256"] == source.sha256
    assert manifest["source_samples"] == 1920
    assert manifest["output_samples"] == 2


def test_replay_cli_defaults_to_local_server_and_six_second_tail(tmp_path: Path) -> None:
    args = replay.build_parser().parse_args(
        [
            str(tmp_path / "source.wav"),
            "--output-dir",
            str(tmp_path / "run"),
        ]
    )

    assert args.url == "ws://127.0.0.1:8998/api/chat"
    assert args.tail_seconds == 6.0
    assert args.settle_seconds == 1.0
