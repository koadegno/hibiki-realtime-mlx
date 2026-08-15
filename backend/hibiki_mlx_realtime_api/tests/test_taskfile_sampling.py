from pathlib import Path

TASKFILE = Path(__file__).parents[3] / "Taskfile.yml"


def test_taskfile_exposes_named_stage2_sampling_servers() -> None:
    taskfile = TASKFILE.read_text()

    for name in (
        "hibiki-mlx:serve:rust:adaptive-reset:mlx-current:",
        "hibiki-mlx:serve:rust:adaptive-reset:kyutai-reference:",
        "hibiki-mlx:serve:rust:adaptive-reset:greedy:",
    ):
        assert name in taskfile

    assert '--sampling-profile "${HIBIKI_SAMPLING_PROFILE:-mlx-current}"' in taskfile
    assert '--sampling-seed "${HIBIKI_SAMPLING_SEED:-299792458}"' in taskfile
    assert "HIBIKI_SAMPLING_PROFILE=historical-cold-0.2" in taskfile
    assert "HIBIKI_TEXT_TEMPERATURE" not in taskfile
    assert "--text-temperature" not in taskfile
