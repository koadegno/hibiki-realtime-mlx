from pathlib import Path

STATIC_DIR = Path(__file__).parents[1] / "hibiki_mlx_realtime_api" / "static"


def test_stage1b_transport_replay_assets_are_packaged() -> None:
    core_path = STATIC_DIR / "transport-replay-core.js"
    html_path = STATIC_DIR / "transport-replay.html"
    script_path = STATIC_DIR / "transport-replay.js"

    assert core_path.is_file()
    assert html_path.is_file()
    assert script_path.is_file()

    html = html_path.read_text()
    script = script_path.read_text()

    assert html.index('src="/transport-replay-core.js"') < html.index(
        'src="/transport-replay.js"'
    )
    assert 'value="pcm"' in html
    assert 'value="opus"' in html
    assert 'id="sourceFile"' in html
    assert 'id="tailSeconds"' in html
    assert 'value="6"' in html
    assert 'id="run"' in html
    assert 'id="downloadSource"' in html
    assert 'id="downloadTranscript"' in html
    assert 'id="downloadTranslated"' in html
    assert 'id="downloadManifest"' in html

    assert 'new Worker("/encoderWorker.min.js")' in script
    assert 'new Worker("/decoderWorker.min.js")' in script
    assert "new WebSocket" in script
    assert "crypto.subtle.digest" in script
    assert 'command: "getHeaderPages"' in script
    assert 'command: "encode"' in script
    assert 'command: "done"' in script
    assert "...core.OFFICIAL_ENCODER_CONFIG" in script
    assert "PCM_INPUT_KIND = 3" in script
    assert "OPUS_INPUT_KIND = 1" in script
    assert "performance.now()" in script
    assert ".terminate()" in script
    assert ".close(1000" in script
    assert "async function runTransport" in script


def test_stage1b_opus_is_preencoded_then_pages_are_paced_to_the_websocket() -> None:
    script = (STATIC_DIR / "transport-replay.js").read_text()

    assert "async function preencodeOfficialOpus" in script
    assert "audioPages" in script
    assert "headerPages" in script
    assert "OPUS_PAGE_INTERVAL_MS" in script
    assert "await preencodeOfficialOpus" in script
    assert "await sendPreencodedOpus" in script

    preencode_start = script.index("async function preencodeOfficialOpus")
    preencode_end = script.index("async function sendPreencodedOpus")
    preencode_body = script[preencode_start:preencode_end]

    # The encoder worker may produce pages in bursts. It must never write those
    # pages straight to the websocket; replay pacing owns all websocket sends.
    assert "ws.send(" not in preencode_body
