from __future__ import annotations

import asyncio
import queue
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient, TestServer

from hibiki_mlx_realtime_api.runtime import RuntimePhase, RuntimeSnapshot
from hibiki_mlx_realtime_api.server import ServerModules, create_app
from hibiki_mlx_realtime_api.session import AudioEvent, TextEvent


class FakeSession:
    def __init__(self) -> None:
        self.started = 0
        self.closed = 0
        self.pcm_frames: list[np.ndarray] = []
        self.events: queue.Queue[object] = queue.Queue()

    def start(self) -> None:
        self.started += 1

    def submit_pcm(self, pcm: np.ndarray) -> bool:
        self.pcm_frames.append(pcm.copy())
        self.events.put_nowait(TextEvent(" hello"))
        self.events.put_nowait(AudioEvent(np.ones(1920, dtype=np.float32)))
        return True

    def get_event(self, timeout: float | None = None) -> object:
        return self.events.get(timeout=timeout)

    def close(self) -> None:
        self.closed += 1


class FakeManager:
    def __init__(self, *, ready: bool) -> None:
        self.ready = ready
        self.session = FakeSession()
        self.started = 0
        self.closed = 0

    @property
    def snapshot(self) -> RuntimeSnapshot:
        return RuntimeSnapshot(
            phase=RuntimePhase.READY if self.ready else RuntimePhase.LOADING_MODEL,
            ready=self.ready,
            error=None,
        )

    def start_background(self) -> asyncio.Task[None]:
        self.started += 1
        return asyncio.create_task(asyncio.sleep(0))

    async def close(self) -> None:
        self.closed += 1

    def create_session(self) -> FakeSession:
        return self.session


class FakeOpusReader:
    def __init__(self, sample_rate: int) -> None:
        assert sample_rate == 24_000

    def append_bytes(self, payload: bytes) -> np.ndarray:
        assert payload == b"encoded-input"
        return np.zeros(1920, dtype=np.float32)


class FakeOpusWriter:
    def __init__(self, sample_rate: int) -> None:
        assert sample_rate == 24_000

    def append_pcm(self, pcm: np.ndarray) -> bytes:
        assert pcm.shape == (1920,)
        return b"encoded-output"


def _static_dir(tmp_path: Path) -> Path:
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html>HIBIKI FRONTEND</html>")
    return static


@pytest.mark.asyncio
async def test_health_ready_and_frontend_routes(tmp_path: Path) -> None:
    manager = FakeManager(ready=False)
    app = create_app(manager, static_dir=_static_dir(tmp_path), modules=ServerModules(sphn=None))

    async with TestClient(TestServer(app)) as client:
        health = await client.get("/health")
        ready = await client.get("/ready")
        index = await client.get("/")

        assert health.status == 200
        assert await health.json() == {"status": "ok"}
        assert ready.status == 503
        assert (await ready.json())["phase"] == "loading_model"
        assert "HIBIKI FRONTEND" in await index.text()

    assert manager.started == 1
    assert manager.closed == 1


@pytest.mark.asyncio
async def test_websocket_preserves_native_hibiki_binary_protocol(tmp_path: Path) -> None:
    manager = FakeManager(ready=True)
    sphn = SimpleNamespace(OpusStreamReader=FakeOpusReader, OpusStreamWriter=FakeOpusWriter)
    app = create_app(manager, static_dir=_static_dir(tmp_path), modules=ServerModules(sphn=sphn))

    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/api/chat")
        handshake = await ws.receive(timeout=1.0)
        assert handshake.type is WSMsgType.BINARY
        assert handshake.data == b"\x00"

        await ws.send_bytes(b"\x01encoded-input")
        first = await ws.receive(timeout=1.0)
        second = await ws.receive(timeout=1.0)
        payloads = {first.data, second.data}

        assert b"\x02 hello" in payloads
        assert b"\x01encoded-output" in payloads
        await ws.close()

    assert manager.session.started == 1
    assert manager.session.closed == 1
    assert len(manager.session.pcm_frames) == 1
    assert manager.session.pcm_frames[0].shape == (1920,)


@pytest.mark.asyncio
async def test_websocket_accepts_pcm16le_reference_input(tmp_path: Path) -> None:
    manager = FakeManager(ready=True)
    sphn = SimpleNamespace(OpusStreamReader=FakeOpusReader, OpusStreamWriter=FakeOpusWriter)
    app = create_app(manager, static_dir=_static_dir(tmp_path), modules=ServerModules(sphn=sphn))

    positive = np.full(960, 16384, dtype="<i2")
    negative = np.full(960, -16384, dtype="<i2")

    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/api/chat")
        handshake = await ws.receive(timeout=1.0)
        assert handshake.data == b"\x00"

        await ws.send_bytes(b"\x03" + positive.tobytes())
        await ws.send_bytes(b"\x03" + negative.tobytes())

        first = await ws.receive(timeout=1.0)
        second = await ws.receive(timeout=1.0)
        payloads = {first.data, second.data}
        assert b"\x02 hello" in payloads
        assert b"\x01encoded-output" in payloads
        await ws.close()

    assert len(manager.session.pcm_frames) == 1
    frame = manager.session.pcm_frames[0]
    assert frame.dtype == np.float32
    assert frame.shape == (1920,)
    np.testing.assert_allclose(frame[:960], 0.5, atol=1e-7)
    np.testing.assert_allclose(frame[960:], -0.5, atol=1e-7)


@pytest.mark.asyncio
async def test_websocket_rejects_incomplete_pcm16_sample(tmp_path: Path) -> None:
    manager = FakeManager(ready=True)
    sphn = SimpleNamespace(OpusStreamReader=FakeOpusReader, OpusStreamWriter=FakeOpusWriter)
    app = create_app(manager, static_dir=_static_dir(tmp_path), modules=ServerModules(sphn=sphn))

    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/api/chat")
        handshake = await ws.receive(timeout=1.0)
        assert handshake.data == b"\x00"

        await ws.send_bytes(b"\x03\x01")
        closed = await ws.receive(timeout=1.0)
        assert closed.type in {WSMsgType.CLOSE, WSMsgType.CLOSED}
        assert ws.close_code == 1003

    assert manager.session.pcm_frames == []
