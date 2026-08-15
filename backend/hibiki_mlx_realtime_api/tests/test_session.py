from __future__ import annotations

import queue
import threading
import time
from contextlib import suppress
from types import SimpleNamespace

import numpy as np

from hibiki_mlx_realtime_api.codecs import CodecPair
from hibiki_mlx_realtime_api.session import AudioEvent, RealtimeSession, TextEvent


class FakeCodec:
    def __init__(self, thread_names: list[str] | None = None) -> None:
        self.reset_calls = 0
        self.encode_calls = 0
        self.decode_calls = 0
        self.thread_names = thread_names

    def encode(self, pcm: np.ndarray) -> np.ndarray:
        assert pcm.shape == (1920,)
        self.encode_calls += 1
        if self.thread_names is not None:
            self.thread_names.append(threading.current_thread().name)
        return np.array([[10, 20]], dtype=np.uint32)

    def decode(self, tokens: np.ndarray) -> np.ndarray:
        assert tokens.shape == (1, 2)
        self.decode_calls += 1
        if self.thread_names is not None:
            self.thread_names.append(threading.current_thread().name)
        return np.full(1920, 0.25, dtype=np.float32)

    def reset(self) -> None:
        self.reset_calls += 1


class FakeToken:
    def __init__(self, value: int) -> None:
        self.value = value

    def item(self) -> int:
        return self.value


class FakeGenerator:
    def __init__(
        self,
        thread_names: list[str] | None = None,
        tokens: list[int] | None = None,
        audio_delay_frames: int = 0,
    ) -> None:
        self.steps: list[np.ndarray] = []
        self.thread_names = thread_names
        self.tokens = list(tokens or [42])
        self.audio_delay_frames = audio_delay_frames
        self.audio_calls = 0

    def step(self, codes: np.ndarray, condition: object) -> list[FakeToken]:
        assert condition == "condition"
        if self.thread_names is not None:
            self.thread_names.append(threading.current_thread().name)
        self.steps.append(codes)
        token = self.tokens.pop(0) if self.tokens else 42
        return [FakeToken(token)]

    def last_audio_tokens(self) -> np.ndarray | None:
        self.audio_calls += 1
        if self.audio_calls <= self.audio_delay_frames:
            return None
        return np.array([[7, 8]], dtype=np.uint32)


class FakeLoadedModel:
    def __init__(
        self,
        thread_names: list[str] | None = None,
        generator_tokens: list[list[int]] | None = None,
        generator_audio_delays: list[int] | None = None,
    ) -> None:
        self._thread_names = thread_names
        self._generator_tokens = list(generator_tokens or [[42]])
        self._generator_audio_delays = list(generator_audio_delays or [0])
        self.generators: list[FakeGenerator] = []
        self.generator_temperatures: list[float] = []
        self.condition = "condition"
        self.tokenizer = SimpleNamespace(
            id_to_piece=lambda token: "</s>" if token == 2 else "▁hello"
        )
        self.modules = SimpleNamespace(mx=SimpleNamespace(array=np.asarray))
        self.reset_calls = 0

    @property
    def generator(self) -> FakeGenerator:
        return self.generators[-1]

    def reset_state(self) -> None:
        self.reset_calls += 1

    def new_generator(self, *, max_steps: int, text_temperature: float = 0.4) -> FakeGenerator:
        assert max_steps == 100
        tokens = self._generator_tokens.pop(0) if self._generator_tokens else [42]
        audio_delay = self._generator_audio_delays.pop(0) if self._generator_audio_delays else 0
        generator = FakeGenerator(
            self._thread_names,
            tokens=tokens,
            audio_delay_frames=audio_delay,
        )
        self.generator_temperatures.append(text_temperature)
        self.generators.append(generator)
        return generator


def _next_events(session: RealtimeSession, count: int) -> list[object]:
    deadline = time.monotonic() + 2.0
    events: list[object] = []
    while len(events) < count and time.monotonic() < deadline:
        with suppress(queue.Empty):
            events.append(session.get_event(timeout=0.05))
    return events


def _drain_events(session: RealtimeSession) -> list[object]:
    events: list[object] = []
    while True:
        try:
            events.append(session.get_event(timeout=0.05))
        except queue.Empty:
            return events


def _wait_for_frames(session: RealtimeSession, frames: int) -> None:
    deadline = time.monotonic() + 2.0
    while session.metrics.frames < frames and time.monotonic() < deadline:
        time.sleep(0.01)
    assert session.metrics.frames == frames


def _wait_for_input_frames(session: RealtimeSession, frames: int) -> None:
    deadline = time.monotonic() + 2.0
    while session.metrics.input_frames < frames and time.monotonic() < deadline:
        time.sleep(0.01)
    assert session.metrics.input_frames == frames


def _audio_events(events: list[object]) -> list[AudioEvent]:
    return [event for event in events if isinstance(event, AudioEvent)]


def test_session_streams_text_and_audio_through_bounded_pipeline() -> None:
    loaded = FakeLoadedModel()
    encoder = FakeCodec()
    decoder = FakeCodec()
    session = RealtimeSession(
        loaded_model=loaded,
        codecs=CodecPair(encoder=encoder, decoder=decoder, execution="pipelined"),
        queue_capacity=4,
        max_steps=100,
        telemetry_interval_frames=125,
    )

    session.start()
    try:
        assert session.submit_pcm(np.zeros(1920, dtype=np.float32)) is True
        events = _next_events(session, 2)
    finally:
        session.close()

    assert loaded.reset_calls == 1
    assert encoder.reset_calls == 1
    assert decoder.reset_calls == 1
    assert any(isinstance(event, TextEvent) and event.text == " hello" for event in events)
    audio = next(event for event in events if isinstance(event, AudioEvent))
    assert audio.pcm.shape == (1920,)
    assert session.metrics.frames == 1
    assert session.metrics.overloads == 0


def test_all_mlx_session_runs_encode_lm_decode_on_one_metal_thread() -> None:
    thread_names: list[str] = []
    session = RealtimeSession(
        loaded_model=FakeLoadedModel(thread_names),
        codecs=CodecPair(
            encoder=FakeCodec(thread_names),
            decoder=FakeCodec(thread_names),
            execution="serial_mlx",
        ),
        queue_capacity=4,
        max_steps=100,
        telemetry_interval_frames=125,
    )

    session.start()
    try:
        assert session.submit_pcm(np.zeros(1920, dtype=np.float32)) is True
        events = _next_events(session, 2)
    finally:
        session.close()

    assert len(events) == 2
    assert thread_names == ["hibiki-mlx-serial"] * 3
    assert len(session.metrics.total_ms) == 1


def test_submit_pcm_refuses_new_audio_when_input_queue_is_full() -> None:
    session = RealtimeSession(
        loaded_model=FakeLoadedModel(),
        codecs=CodecPair(
            encoder=FakeCodec(),
            decoder=FakeCodec(),
            execution="pipelined",
        ),
        queue_capacity=2,
        max_steps=100,
        telemetry_interval_frames=125,
    )

    assert session.submit_pcm(np.zeros(1920, dtype=np.float32)) is True
    assert session.submit_pcm(np.zeros(1920, dtype=np.float32)) is True
    assert session.submit_pcm(np.zeros(1920, dtype=np.float32)) is False
    assert session.metrics.overloads == 1


def test_session_close_is_idempotent() -> None:
    session = RealtimeSession(
        loaded_model=FakeLoadedModel(),
        codecs=CodecPair(
            encoder=FakeCodec(),
            decoder=FakeCodec(),
            execution="pipelined",
        ),
        queue_capacity=2,
        max_steps=100,
        telemetry_interval_frames=125,
    )

    session.start()
    session.close()
    session.close()

    assert session.closed is True


def test_realtime_pipeline_keeps_advancing_during_long_silence() -> None:
    frame_count = 21
    loaded = FakeLoadedModel(generator_tokens=[[42] + [0] * (frame_count - 1)])
    session = RealtimeSession(
        loaded_model=loaded,
        codecs=CodecPair(
            encoder=FakeCodec(),
            decoder=FakeCodec(),
            execution="pipelined",
        ),
        queue_capacity=32,
        max_steps=100,
        telemetry_interval_frames=125,
    )

    session.start()
    try:
        assert session.submit_pcm(np.ones(1920, dtype=np.float32) * 0.1) is True
        for _ in range(frame_count - 1):
            assert session.submit_pcm(np.zeros(1920, dtype=np.float32)) is True
        _wait_for_frames(session, frame_count)
        events = _next_events(session, frame_count + 1)
    finally:
        session.close()

    audio_events = _audio_events(events)
    assert len(audio_events) == frame_count
    assert loaded.reset_calls == 1


def test_eos_control_token_is_not_streamed_to_frontend() -> None:
    loaded = FakeLoadedModel(generator_tokens=[[2]])
    session = RealtimeSession(
        loaded_model=loaded,
        codecs=CodecPair(
            encoder=FakeCodec(),
            decoder=FakeCodec(),
            execution="pipelined",
        ),
        queue_capacity=4,
        max_steps=100,
        telemetry_interval_frames=125,
    )

    session.start()
    try:
        assert session.submit_pcm(np.ones(1920, dtype=np.float32) * 0.1) is True
        _wait_for_frames(session, 1)
        events = _drain_events(session)
    finally:
        session.close()

    assert len(_audio_events(events)) == 1
    assert not any(isinstance(event, TextEvent) for event in events)


def test_hold_mode_freezes_generation_after_long_silence_but_keeps_audio_clock() -> None:
    loaded = FakeLoadedModel(generator_tokens=[[42, 0, 0, 42]])
    encoder = FakeCodec()
    decoder = FakeCodec()
    session = RealtimeSession(
        loaded_model=loaded,
        codecs=CodecPair(encoder=encoder, decoder=decoder, execution="pipelined"),
        queue_capacity=16,
        max_steps=100,
        telemetry_interval_frames=125,
        silence_mode="hold",
        silence_rms_threshold=0.01,
        speech_rms_threshold=0.05,
        silence_min_seconds=0.08,
        silence_max_seconds=0.16,
    )

    frames = [
        np.ones(1920, dtype=np.float32) * 0.1,
        np.zeros(1920, dtype=np.float32),
        np.zeros(1920, dtype=np.float32),
        np.zeros(1920, dtype=np.float32),
        np.zeros(1920, dtype=np.float32),
        np.ones(1920, dtype=np.float32) * 0.1,
    ]

    session.start()
    try:
        for frame in frames:
            assert session.submit_pcm(frame) is True
        _wait_for_input_frames(session, len(frames))
        deadline = time.monotonic() + 2.0
        while len(loaded.generator.steps) < 4 and time.monotonic() < deadline:
            time.sleep(0.01)
        events = _drain_events(session)
    finally:
        session.close()

    assert len(loaded.generator.steps) == 4
    assert loaded.reset_calls == 1
    assert encoder.encode_calls == len(frames)
    assert decoder.reset_calls == 1
    audio = _audio_events(events)
    assert len(audio) == len(frames)
    assert sum(bool(np.allclose(event.pcm, 0.0)) for event in audio) == 2
    assert session.metrics.parks == 1
    assert session.metrics.resumes == 1


def test_reset_mode_starts_fresh_generation_on_resume_without_stopping_audio_clock() -> None:
    loaded = FakeLoadedModel(
        generator_tokens=[[42, 0, 0], [42]],
        generator_audio_delays=[0, 1],
    )
    encoder = FakeCodec()
    decoder = FakeCodec()
    session = RealtimeSession(
        loaded_model=loaded,
        codecs=CodecPair(encoder=encoder, decoder=decoder, execution="pipelined"),
        queue_capacity=16,
        max_steps=100,
        telemetry_interval_frames=125,
        silence_mode="reset",
        silence_rms_threshold=0.01,
        speech_rms_threshold=0.05,
        silence_min_seconds=0.08,
        silence_max_seconds=0.16,
    )

    frames = [
        np.ones(1920, dtype=np.float32) * 0.1,
        np.zeros(1920, dtype=np.float32),
        np.zeros(1920, dtype=np.float32),
        np.zeros(1920, dtype=np.float32),
        np.zeros(1920, dtype=np.float32),
        np.ones(1920, dtype=np.float32) * 0.1,
    ]

    session.start()
    try:
        for frame in frames:
            assert session.submit_pcm(frame) is True
        _wait_for_input_frames(session, len(frames))
        deadline = time.monotonic() + 2.0
        while len(loaded.generators) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        events = _drain_events(session)
    finally:
        session.close()

    assert [len(generator.steps) for generator in loaded.generators] == [3, 1]
    assert loaded.reset_calls == 2
    assert encoder.reset_calls == 1
    assert decoder.reset_calls == 2
    audio = _audio_events(events)
    assert len(audio) == len(frames)
    assert np.allclose(audio[-1].pcm, 0.0)
    assert session.metrics.parks == 1
    assert session.metrics.resumes == 1
    assert session.metrics.resets == 1


def test_adaptive_reset_parks_after_reference_style_pad_run() -> None:
    loaded = FakeLoadedModel(generator_tokens=[[42, 0, 0], [42]])
    session = RealtimeSession(
        loaded_model=loaded,
        codecs=CodecPair(
            encoder=FakeCodec(),
            decoder=FakeCodec(),
            execution="pipelined",
        ),
        queue_capacity=16,
        max_steps=100,
        telemetry_interval_frames=125,
        silence_mode="adaptive-reset",
        silence_rms_threshold=0.01,
        speech_rms_threshold=0.05,
        silence_min_seconds=0.08,
        silence_max_seconds=1.0,
        silence_pad_frames=2,
    )

    frames = [
        np.ones(1920, dtype=np.float32) * 0.1,
        np.zeros(1920, dtype=np.float32),
        np.zeros(1920, dtype=np.float32),
        np.zeros(1920, dtype=np.float32),
        np.ones(1920, dtype=np.float32) * 0.1,
    ]

    session.start()
    try:
        for frame in frames:
            assert session.submit_pcm(frame) is True
        _wait_for_input_frames(session, len(frames))
        deadline = time.monotonic() + 2.0
        while len(loaded.generators) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        events = _drain_events(session)
    finally:
        session.close()

    assert [len(generator.steps) for generator in loaded.generators] == [3, 1]
    assert len(_audio_events(events)) == len(frames)
    assert session.metrics.parks == 1
    assert session.metrics.resets == 1
