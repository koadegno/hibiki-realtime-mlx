from pathlib import Path

STATIC_DIR = Path(__file__).parents[1] / "hibiki_mlx_realtime_api" / "static"


def test_pcm_quality_lab_assets_are_packaged() -> None:
    html_path = STATIC_DIR / "quality-lab.html"
    script_path = STATIC_DIR / "quality-lab.js"
    worklet_path = STATIC_DIR / "quality-input-processor.js"

    assert html_path.is_file()
    assert script_path.is_file()
    assert worklet_path.is_file()

    html = html_path.read_text()
    script = script_path.read_text()
    worklet = worklet_path.read_text()

    assert "RAW PCM16LE" in html
    assert 'src="/quality-lab.js"' in html
    assert "Download source WAV" in html
    assert "PCM_INPUT_KIND = 3" in script
    assert "echoCancellation: true" in script
    assert "noiseSuppression: false" in script
    assert "autoGainControl: true" in script
    assert "channelCount: 1" in script
    assert "inputAudioContext = new AudioContext({ sampleRate: SAMPLE_RATE })" in script
    assert "outputAudioContext = new AudioContext()" in script
    assert "outputBufferSampleRate: outputAudioContext.sampleRate" in script
    assert "(960 * outputAudioContext.sampleRate) / SAMPLE_RATE" in script
    assert "registerProcessor(\"quality-input-processor\"" in worklet
    assert "1920" in worklet
