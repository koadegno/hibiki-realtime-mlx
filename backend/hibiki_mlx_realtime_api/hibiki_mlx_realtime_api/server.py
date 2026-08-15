"""aiohttp server preserving Hibiki's native browser protocol."""

from __future__ import annotations

import asyncio
import logging
import queue
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from aiohttp import WSMsgType, web

from hibiki_mlx_realtime_api.codecs import FRAME_SAMPLES, SAMPLE_RATE
from hibiki_mlx_realtime_api.runtime import RuntimeManager, RuntimeSnapshot
from hibiki_mlx_realtime_api.session import AudioEvent, ErrorEvent, TextEvent

_LOGGER = logging.getLogger(__name__)

OPUS_INPUT_KIND = 1
PCM16_INPUT_KIND = 3
_PCM16_BYTES_PER_SAMPLE = 2


@dataclass(frozen=True, slots=True)
class ServerModules:
    """Late-bound native modules, injectable for Linux tests."""

    sphn: Any

    @classmethod
    def load_default(cls) -> ServerModules:
        import sphn

        return cls(sphn=sphn)


def create_app(
    manager: RuntimeManager,
    *,
    static_dir: Path,
    modules: ServerModules | None = None,
) -> web.Application:
    """Create the HTTP/WebSocket application without blocking on model loading."""
    static_dir = Path(static_dir)
    index_path = static_dir / "index.html"
    if not index_path.is_file():
        raise FileNotFoundError(f"Hibiki frontend index is missing: {index_path}")

    modules = modules or ServerModules.load_default()
    session_lock = asyncio.Lock()
    app = web.Application()

    async def startup(_: web.Application) -> None:
        manager.start_background()

    async def cleanup(_: web.Application) -> None:
        await manager.close()

    async def health(_: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def ready(_: web.Request) -> web.Response:
        return _readiness_response(manager.snapshot)

    async def chat(request: web.Request) -> web.StreamResponse:
        snapshot = manager.snapshot
        if not snapshot.ready:
            return _readiness_response(snapshot)
        return await _handle_chat(request, manager, session_lock, modules)

    async def index(_: web.Request) -> web.FileResponse:
        return web.FileResponse(index_path)

    app.router.add_get("/health", health)
    app.router.add_get("/ready", ready)
    app.router.add_get("/api/chat", chat)
    app.router.add_get("/", index)
    app.router.add_static("/", path=static_dir, name="hibiki-static")
    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)
    return app


async def _handle_chat(
    request: web.Request,
    manager: RuntimeManager,
    session_lock: asyncio.Lock,
    modules: ServerModules,
) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    async with session_lock:
        session = manager.create_session()
        session.start()
        done = asyncio.Event()
        opus_reader = modules.sphn.OpusStreamReader(SAMPLE_RATE)
        opus_writer = modules.sphn.OpusStreamWriter(SAMPLE_RATE)
        await ws.send_bytes(b"\x00")
        _LOGGER.info("accepted Hibiki MLX connection")
        try:
            await asyncio.gather(
                _receive_audio(ws, opus_reader, session, done),
                _send_events(ws, opus_writer, session, done),
            )
        finally:
            done.set()
            session.close()
            _LOGGER.info("Hibiki MLX connection closed")
    return ws


def _pcm16le_to_float32(payload: bytes) -> np.ndarray:
    """Decode little-endian signed PCM16 into normalized float32 samples."""
    if len(payload) % _PCM16_BYTES_PER_SAMPLE:
        raise ValueError("PCM16 payload must contain complete 16-bit samples")
    if not payload:
        return np.empty(0, dtype=np.float32)
    pcm_i16 = np.frombuffer(payload, dtype="<i2")
    return pcm_i16.astype(np.float32) / 32768.0


async def _receive_audio(
    ws: web.WebSocketResponse,
    opus_reader: Any,
    session: Any,
    done: asyncio.Event,
) -> None:
    pending = np.empty(0, dtype=np.float32)
    try:
        async for message in ws:
            if message.type in {WSMsgType.ERROR, WSMsgType.CLOSED, WSMsgType.CLOSE}:
                return
            if message.type is not WSMsgType.BINARY:
                _LOGGER.warning("unexpected websocket message type: %s", message.type)
                continue

            payload = message.data
            if not isinstance(payload, bytes) or not payload:
                continue

            kind = payload[0]
            audio_payload = payload[1:]
            if kind == OPUS_INPUT_KIND:
                pcm = np.asarray(
                    opus_reader.append_bytes(audio_payload),
                    dtype=np.float32,
                ).reshape(-1)
            elif kind == PCM16_INPUT_KIND:
                try:
                    pcm = _pcm16le_to_float32(audio_payload)
                except ValueError as error:
                    _LOGGER.warning("invalid PCM16 websocket payload: %s", error)
                    done.set()
                    await ws.close(code=1003, message=b"invalid PCM16 input")
                    return
            else:
                _LOGGER.warning("unknown Hibiki websocket message kind: %s", kind)
                continue

            if pcm.size == 0:
                continue
            pending = pcm if pending.size == 0 else np.concatenate((pending, pcm))

            while pending.size >= FRAME_SAMPLES:
                frame = np.ascontiguousarray(pending[:FRAME_SAMPLES])
                pending = pending[FRAME_SAMPLES:]
                if session.submit_pcm(frame):
                    continue
                _LOGGER.error(
                    "realtime input queue saturated; closing websocket instead of lagging"
                )
                done.set()
                await ws.close(code=1013, message=b"translation backend overloaded")
                return
    finally:
        done.set()


async def _send_events(
    ws: web.WebSocketResponse,
    opus_writer: Any,
    session: Any,
    done: asyncio.Event,
) -> None:
    while not done.is_set() and not ws.closed:
        try:
            event = session.get_event(timeout=0)
        except queue.Empty:
            await asyncio.sleep(0.001)
            continue

        if isinstance(event, TextEvent):
            await ws.send_bytes(b"\x02" + event.text.encode("utf-8"))
            continue

        if isinstance(event, AudioEvent):
            encoded = opus_writer.append_pcm(event.pcm)
            if encoded:
                await ws.send_bytes(b"\x01" + encoded)
            continue

        if isinstance(event, ErrorEvent):
            _LOGGER.error("realtime session failed: %s", event.error)
            done.set()
            await ws.close(code=1011, message=b"translation worker failed")
            return

        _LOGGER.warning("unknown realtime event: %r", event)


def _readiness_response(snapshot: RuntimeSnapshot) -> web.Response:
    return web.json_response(
        {
            "status": "ready" if snapshot.ready else "not_ready",
            "phase": snapshot.phase.value,
            "ready": snapshot.ready,
            "error": snapshot.error,
        },
        status=200 if snapshot.ready else 503,
    )
