"""Bounded realtime execution for one Hibiki-Zero MLX session."""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from hibiki_mlx_realtime_api.codecs import FRAME_SAMPLES, CodecExecution, CodecPair
from hibiki_mlx_realtime_api.config import SilenceMode

_LOGGER = logging.getLogger(__name__)
_FRAME_BUDGET_MS = 80.0
_FRAME_RATE = 1000.0 / _FRAME_BUDGET_MS
_ZERO_PCM = np.zeros(FRAME_SAMPLES, dtype=np.float32)
_TEXT_PAD_TOKENS = frozenset((0, 3))
_TEXT_CONTROL_TOKENS = frozenset((0, 2, 3))


@dataclass(frozen=True, slots=True)
class TextEvent:
    """One translated text token/piece ready for the WebSocket."""

    text: str


@dataclass(frozen=True, slots=True)
class AudioEvent:
    """One translated 80 ms PCM frame ready for Opus."""

    pcm: np.ndarray


@dataclass(frozen=True, slots=True)
class ErrorEvent:
    """Terminal worker failure surfaced to the WebSocket owner."""

    error: BaseException


@dataclass(frozen=True, slots=True)
class _InputFrame:
    pcm: np.ndarray
    rms: float
    source_silent: bool
    source_speech: bool


@dataclass(frozen=True, slots=True)
class _EncodedFrame:
    codes: np.ndarray
    rms: float
    source_silent: bool
    source_speech: bool


@dataclass(frozen=True, slots=True)
class _DecodeJob:
    tokens: np.ndarray | None
    silence: bool = False
    reset: bool = False


@dataclass(slots=True)
class SessionMetrics:
    """Bounded rolling realtime metrics for one session."""

    execution: CodecExecution = "pipelined"
    input_frames: int = 0
    frames: int = 0
    overloads: int = 0
    parks: int = 0
    resumes: int = 0
    resets: int = 0
    parked_frames: int = 0
    latest_input_rms: float = 0.0
    encode_ms: deque[float] = field(default_factory=lambda: deque(maxlen=512))
    lm_ms: deque[float] = field(default_factory=lambda: deque(maxlen=512))
    decode_ms: deque[float] = field(default_factory=lambda: deque(maxlen=512))
    total_ms: deque[float] = field(default_factory=lambda: deque(maxlen=512))
    started_at: float = field(default_factory=time.perf_counter)

    @property
    def rtf(self) -> float:
        """Return sustained compute RTF for the selected execution topology."""
        if self.execution == "serial_mlx":
            return _mean(self.total_ms) / _FRAME_BUDGET_MS
        stage_means = [
            _mean(self.encode_ms),
            _mean(self.lm_ms),
            _mean(self.decode_ms),
        ]
        return max(stage_means, default=0.0) / _FRAME_BUDGET_MS

    @property
    def encode_p50_ms(self) -> float:
        return _percentile(self.encode_ms, 50)

    @property
    def encode_p95_ms(self) -> float:
        return _percentile(self.encode_ms, 95)

    @property
    def lm_p50_ms(self) -> float:
        return _percentile(self.lm_ms, 50)

    @property
    def lm_p95_ms(self) -> float:
        return _percentile(self.lm_ms, 95)

    @property
    def decode_p50_ms(self) -> float:
        return _percentile(self.decode_ms, 50)

    @property
    def decode_p95_ms(self) -> float:
        return _percentile(self.decode_ms, 95)


class RealtimeSession:
    """Own fresh streaming state and bounded worker queues for one client."""

    def __init__(
        self,
        *,
        loaded_model: Any,
        codecs: CodecPair,
        queue_capacity: int,
        max_steps: int,
        telemetry_interval_frames: int,
        silence_mode: SilenceMode = "none",
        silence_rms_threshold: float = 0.002,
        speech_rms_threshold: float = 0.006,
        silence_min_seconds: float = 4.0,
        silence_max_seconds: float = 8.0,
        silence_pad_frames: int = 12,
        text_temperature: float = 0.4,
    ) -> None:
        if queue_capacity < 2:
            raise ValueError("queue_capacity must be >= 2")
        if silence_mode not in {"none", "hold", "reset", "adaptive-reset"}:
            raise ValueError(f"unsupported silence_mode: {silence_mode}")
        if silence_rms_threshold < 0:
            raise ValueError("silence_rms_threshold must be >= 0")
        if speech_rms_threshold <= silence_rms_threshold:
            raise ValueError("speech_rms_threshold must be > silence_rms_threshold")
        if silence_min_seconds <= 0:
            raise ValueError("silence_min_seconds must be > 0")
        if silence_max_seconds < silence_min_seconds:
            raise ValueError("silence_max_seconds must be >= silence_min_seconds")
        if silence_pad_frames <= 0:
            raise ValueError("silence_pad_frames must be > 0")
        if text_temperature < 0:
            raise ValueError("text_temperature must be >= 0")

        self._loaded_model = loaded_model
        self._codecs = codecs
        self._max_steps = max_steps
        self._telemetry_interval_frames = telemetry_interval_frames
        self._silence_mode = silence_mode
        self._silence_rms_threshold = silence_rms_threshold
        self._speech_rms_threshold = speech_rms_threshold
        self._silence_min_frames = max(1, int(round(silence_min_seconds * _FRAME_RATE)))
        self._silence_max_frames = max(1, int(round(silence_max_seconds * _FRAME_RATE)))
        self._silence_pad_frames = silence_pad_frames
        self._text_temperature = text_temperature

        self._input_q: queue.Queue[_InputFrame] = queue.Queue(maxsize=queue_capacity)
        self._encoded_q: queue.Queue[_EncodedFrame] = queue.Queue(maxsize=queue_capacity)
        self._decode_q: queue.Queue[_DecodeJob] = queue.Queue(maxsize=queue_capacity)
        self._events: queue.Queue[TextEvent | AudioEvent | ErrorEvent] = queue.Queue(
            maxsize=max(8, queue_capacity * 4)
        )
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._generator: Any = None
        self._started = False
        self._closed = False
        self._error: BaseException | None = None
        self._metrics_lock = threading.Lock()

        # The silence state machine is owned by the LM/serial worker. The encoder
        # always advances through source silence, so a parked LM can resume from a
        # codec stream that still reflects the actual microphone timeline.
        self._parked = False
        self._source_silence_frames = 0
        self._output_pad_run = 0
        self._fill_clock_after_reset = False
        self.metrics = SessionMetrics(execution=codecs.execution)

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def error(self) -> BaseException | None:
        return self._error

    @property
    def parked(self) -> bool:
        return self._parked

    def start(self) -> None:
        if self._started:
            return
        if self._closed:
            raise RuntimeError("cannot restart a closed realtime session")

        self.metrics.started_at = time.perf_counter()
        if self._codecs.execution == "serial_mlx":
            self._threads = [self._make_thread("hibiki-mlx-serial", self._serial_mlx_loop)]
        else:
            self._codecs.reset()
            self._threads = [
                self._make_thread("hibiki-mimi-encode", self._encoder_loop),
                self._make_thread("hibiki-mlx-lm", self._lm_loop),
                self._make_thread("hibiki-mimi-decode", self._decoder_loop),
            ]
        self._started = True
        for thread in self._threads:
            thread.start()

    def submit_pcm(self, pcm: np.ndarray) -> bool:
        """Queue one frame without blocking; return False on overload."""
        pcm = np.asarray(pcm, dtype=np.float32)
        if pcm.shape != (FRAME_SAMPLES,):
            raise ValueError(f"expected PCM shape ({FRAME_SAMPLES},), got {pcm.shape}")
        pcm = np.ascontiguousarray(pcm.copy())
        rms = _rms(pcm)
        frame = _InputFrame(
            pcm=pcm,
            rms=rms,
            source_silent=rms <= self._silence_rms_threshold,
            source_speech=rms >= self._speech_rms_threshold,
        )
        try:
            self._input_q.put_nowait(frame)
        except queue.Full:
            with self._metrics_lock:
                self.metrics.overloads += 1
            return False
        return True

    def get_event(self, timeout: float | None = None) -> TextEvent | AudioEvent | ErrorEvent:
        return self._events.get(timeout=timeout)

    def close(self) -> None:
        if self._closed:
            return
        self._stop.set()
        current = threading.current_thread()
        for thread in self._threads:
            if thread is not current:
                thread.join(timeout=1.0)
        self._closed = True

    def _make_thread(self, name: str, target: Any) -> threading.Thread:
        return threading.Thread(
            name=name,
            target=self._guard_worker,
            args=(target,),
            daemon=True,
        )

    def _guard_worker(self, target: Any) -> None:
        try:
            target()
        except BaseException as exc:  # worker boundary must surface every terminal failure
            self._error = exc
            self._stop.set()
            _LOGGER.exception("realtime worker failed: %s", threading.current_thread().name)
            self._offer_event(ErrorEvent(exc))

    def _new_generator(self) -> Any:
        return self._loaded_model.new_generator(
            max_steps=self._max_steps,
            text_temperature=self._text_temperature,
        )

    def _prepare_model_state(self) -> None:
        """Create all session-local MLX state on the thread that will execute the LM."""
        self._loaded_model.reset_state()
        self._generator = self._new_generator()

    def _reset_generation(self) -> None:
        self._loaded_model.reset_state()
        self._generator = self._new_generator()
        self._fill_clock_after_reset = True
        with self._metrics_lock:
            self.metrics.resets += 1

    def _serial_mlx_loop(self) -> None:
        """Run Mimi encode, Hibiki LM and Mimi decode on one Metal submission thread."""
        self._loaded_model.reset_state()
        self._codecs.reset()
        self._generator = self._new_generator()
        mx = self._loaded_model.modules.mx

        while not self._stop.is_set():
            try:
                frame = self._input_q.get(timeout=0.05)
            except queue.Empty:
                continue

            total_started = time.perf_counter()
            encode_started = time.perf_counter()
            codes = self._codecs.encoder.encode(frame.pcm)
            encode_ms = (time.perf_counter() - encode_started) * 1000.0
            input_count = self._record_input(frame.rms, encode_ms)

            skip_lm, reset_generation = self._before_generation(frame)
            if skip_lm:
                self._put_event(AudioEvent(_ZERO_PCM.copy()))
                total_ms = (time.perf_counter() - total_started) * 1000.0
                with self._metrics_lock:
                    self.metrics.total_ms.append(total_ms)
                self._maybe_log_metrics(input_count)
                continue

            if reset_generation:
                self._reset_generation()
                self._codecs.decoder.reset()

            token_id, audio_tokens, _lm_ms, _frame_count = self._infer(codes, mx)
            self._emit_text(token_id)
            self._after_generation(frame, token_id)

            decode_ms = 0.0
            if audio_tokens is not None:
                self._fill_clock_after_reset = False
                decode_started = time.perf_counter()
                translated_pcm = self._codecs.decoder.decode(audio_tokens)
                decode_ms = (time.perf_counter() - decode_started) * 1000.0
                if not self._put_event(AudioEvent(np.ascontiguousarray(translated_pcm))):
                    return
            elif self._fill_clock_after_reset:
                if not self._put_event(AudioEvent(_ZERO_PCM.copy())):
                    return

            total_ms = (time.perf_counter() - total_started) * 1000.0
            with self._metrics_lock:
                self.metrics.decode_ms.append(decode_ms)
                self.metrics.total_ms.append(total_ms)
            self._maybe_log_metrics(input_count)

    def _encoder_loop(self) -> None:
        while not self._stop.is_set():
            try:
                frame = self._input_q.get(timeout=0.05)
            except queue.Empty:
                continue
            started = time.perf_counter()
            codes = self._codecs.encoder.encode(frame.pcm)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            input_count = self._record_input(frame.rms, elapsed_ms)
            encoded = _EncodedFrame(
                codes=np.ascontiguousarray(codes),
                rms=frame.rms,
                source_silent=frame.source_silent,
                source_speech=frame.source_speech,
            )
            if not self._put_bounded(self._encoded_q, encoded):
                return
            self._maybe_log_metrics(input_count)

    def _lm_loop(self) -> None:
        self._prepare_model_state()
        mx = self._loaded_model.modules.mx
        while not self._stop.is_set():
            try:
                frame = self._encoded_q.get(timeout=0.05)
            except queue.Empty:
                continue

            skip_lm, reset_generation = self._before_generation(frame)
            if skip_lm:
                if not self._put_bounded(self._decode_q, _DecodeJob(tokens=None, silence=True)):
                    return
                continue

            decoder_reset = False
            if reset_generation:
                self._reset_generation()
                decoder_reset = True

            token_id, audio_tokens, _lm_ms, _frame_count = self._infer(frame.codes, mx)
            self._emit_text(token_id)
            self._after_generation(frame, token_id)

            if audio_tokens is not None:
                self._fill_clock_after_reset = False
                job = _DecodeJob(tokens=audio_tokens, reset=decoder_reset)
                if not self._put_bounded(self._decode_q, job):
                    return
            elif decoder_reset or self._fill_clock_after_reset:
                job = _DecodeJob(tokens=None, silence=True, reset=decoder_reset)
                if not self._put_bounded(self._decode_q, job):
                    return

    def _before_generation(self, frame: _InputFrame | _EncodedFrame) -> tuple[bool, bool]:
        """Return (skip_lm, reset_generation) for one source frame."""
        if self._silence_mode == "none":
            return False, False

        if self._parked:
            if not frame.source_speech:
                with self._metrics_lock:
                    self.metrics.parked_frames += 1
                return True, False

            self._parked = False
            self._source_silence_frames = 0
            self._output_pad_run = 0
            reset_generation = self._silence_mode in {"reset", "adaptive-reset"}
            with self._metrics_lock:
                self.metrics.resumes += 1
            _LOGGER.info(
                "source speech resumed after silence park mode=%s reset_generation=%s rms=%.6f",
                self._silence_mode,
                reset_generation,
                frame.rms,
            )
            return False, reset_generation

        if frame.source_silent:
            self._source_silence_frames += 1
        else:
            # The hysteresis band is deliberately conservative: uncertain low-level
            # audio prevents us from declaring an utterance boundary, while it is not
            # strong enough to wake an already parked session.
            self._source_silence_frames = 0
            self._output_pad_run = 0
        return False, False

    def _after_generation(self, frame: _InputFrame | _EncodedFrame, token_id: int) -> None:
        if self._silence_mode == "none" or self._parked:
            return

        if not frame.source_silent:
            self._output_pad_run = 0
            return

        if token_id in _TEXT_PAD_TOKENS:
            self._output_pad_run += 1
        else:
            self._output_pad_run = 0

        reason: str | None = None
        if self._silence_mode == "adaptive-reset":
            if (
                self._source_silence_frames >= self._silence_min_frames
                and self._output_pad_run >= self._silence_pad_frames
            ):
                reason = "translated-tail-pad"
            elif self._source_silence_frames >= self._silence_max_frames:
                reason = "hard-silence-cap"
        elif self._source_silence_frames >= self._silence_max_frames:
            reason = "fixed-silence-cap"

        if reason is not None:
            self._parked = True
            with self._metrics_lock:
                self.metrics.parks += 1
            _LOGGER.info(
                "parking Hibiki generation mode=%s reason=%s source_silence_s=%.2f "
                "pad_run=%d rms=%.6f; output clock continues with silence",
                self._silence_mode,
                reason,
                self._source_silence_frames / _FRAME_RATE,
                self._output_pad_run,
                frame.rms,
            )

    def _infer(self, codes: np.ndarray, mx: Any) -> tuple[int, np.ndarray | None, float, int]:
        started = time.perf_counter()
        text_token = self._generator.step(mx.array(codes), self._loaded_model.condition)
        token_id = int(text_token[0].item())
        audio_tokens = self._generator.last_audio_tokens()
        audio_numpy = None
        if audio_tokens is not None:
            audio_numpy = np.ascontiguousarray(np.asarray(audio_tokens, dtype=np.uint32))
        lm_ms = (time.perf_counter() - started) * 1000.0

        with self._metrics_lock:
            self.metrics.frames += 1
            self.metrics.lm_ms.append(lm_ms)
            frame_count = self.metrics.frames
        return token_id, audio_numpy, lm_ms, frame_count

    def _emit_text(self, token_id: int) -> None:
        # SentencePiece EOS is a control signal, not user-visible transcript. The
        # upstream realtime server accidentally streams token 2 because its EOS
        # branch is unreachable; filtering it also removes literal </s> from the UI.
        if token_id in _TEXT_CONTROL_TOKENS:
            return
        piece = self._loaded_model.tokenizer.id_to_piece(token_id).replace("▁", " ")
        if piece:
            self._put_event(TextEvent(piece))

    def _decoder_loop(self) -> None:
        while not self._stop.is_set():
            try:
                job = self._decode_q.get(timeout=0.05)
            except queue.Empty:
                continue

            if job.reset:
                self._codecs.decoder.reset()

            if job.tokens is None:
                if not self._put_event(AudioEvent(_ZERO_PCM.copy())):
                    return
                continue

            started = time.perf_counter()
            pcm = self._codecs.decoder.decode(job.tokens)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            with self._metrics_lock:
                self.metrics.decode_ms.append(elapsed_ms)
            if job.silence:
                pcm = _ZERO_PCM
            if not self._put_event(AudioEvent(np.ascontiguousarray(pcm))):
                return

    def _record_input(self, rms: float, encode_ms: float) -> int:
        with self._metrics_lock:
            self.metrics.input_frames += 1
            self.metrics.latest_input_rms = rms
            self.metrics.encode_ms.append(encode_ms)
            return self.metrics.input_frames

    def _maybe_log_metrics(self, input_frames: int) -> None:
        if input_frames % self._telemetry_interval_frames == 0:
            self._log_metrics()

    def _put_event(self, event: TextEvent | AudioEvent | ErrorEvent) -> bool:
        return self._put_bounded(self._events, event)

    def _offer_event(self, event: TextEvent | AudioEvent | ErrorEvent) -> None:
        with suppress(queue.Full):
            self._events.put_nowait(event)

    def _put_bounded(self, target: queue.Queue[Any], value: Any) -> bool:
        while not self._stop.is_set():
            try:
                target.put(value, timeout=0.05)
                return True
            except queue.Full:
                continue
        return False

    def _log_metrics(self) -> None:
        with self._metrics_lock:
            input_frames = self.metrics.input_frames
            lm_frames = self.metrics.frames
            rtf = self.metrics.rtf
            encode_p50 = self.metrics.encode_p50_ms
            encode_p95 = self.metrics.encode_p95_ms
            lm_p50 = self.metrics.lm_p50_ms
            lm_p95 = self.metrics.lm_p95_ms
            decode_p50 = self.metrics.decode_p50_ms
            decode_p95 = self.metrics.decode_p95_ms
            overloads = self.metrics.overloads
            parks = self.metrics.parks
            resumes = self.metrics.resumes
            resets = self.metrics.resets
            rms = self.metrics.latest_input_rms
            queues = (self._input_q.qsize(), self._encoded_q.qsize(), self._decode_q.qsize())
        _LOGGER.info(
            "realtime input_frames=%d lm_frames=%d rtf=%.3f "
            "encode_p50_ms=%.1f encode_p95_ms=%.1f "
            "lm_p50_ms=%.1f lm_p95_ms=%.1f "
            "decode_p50_ms=%.1f decode_p95_ms=%.1f "
            "rms=%.6f silence_s=%.2f parked=%s parks=%d resumes=%d resets=%d "
            "queues=%d/%d/%d overloads=%d execution=%s mode=%s",
            input_frames,
            lm_frames,
            rtf,
            encode_p50,
            encode_p95,
            lm_p50,
            lm_p95,
            decode_p50,
            decode_p95,
            rms,
            self._source_silence_frames / _FRAME_RATE,
            self._parked,
            parks,
            resumes,
            resets,
            *queues,
            overloads,
            self._codecs.execution,
            self._silence_mode,
        )


def _rms(pcm: np.ndarray) -> float:
    samples = np.asarray(pcm, dtype=np.float64)
    return float(np.sqrt(np.mean(samples * samples)))


def _mean(values: deque[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _percentile(values: deque[float], percentile: int) -> float:
    if not values:
        return 0.0
    return float(np.percentile(np.fromiter(values, dtype=np.float64), percentile))
