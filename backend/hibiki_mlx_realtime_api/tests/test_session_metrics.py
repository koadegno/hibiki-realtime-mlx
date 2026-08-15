from hibiki_mlx_realtime_api.session import SessionMetrics


def test_pipeline_metrics_expose_encode_lm_and_decode_percentiles() -> None:
    metrics = SessionMetrics()
    metrics.encode_ms.extend((10.0, 20.0, 30.0))
    metrics.lm_ms.extend((20.0, 30.0, 40.0))
    metrics.decode_ms.extend((30.0, 40.0, 50.0))

    assert metrics.encode_p50_ms == 20.0
    assert metrics.encode_p95_ms == 29.0
    assert metrics.lm_p50_ms == 30.0
    assert metrics.lm_p95_ms == 39.0
    assert metrics.decode_p50_ms == 40.0
    assert metrics.decode_p95_ms == 49.0
