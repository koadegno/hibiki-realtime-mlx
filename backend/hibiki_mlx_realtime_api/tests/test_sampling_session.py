from __future__ import annotations

from types import SimpleNamespace

from hibiki_mlx_realtime_api.codecs import CodecPair
from hibiki_mlx_realtime_api.session import RealtimeSession


class FakeCodec:
    def reset(self) -> None:
        pass


class FakeLoadedModel:
    def __init__(self) -> None:
        self.reset_calls = 0
        self.sampling_seeds: list[int] = []
        self.sampling_profiles: list[str] = []
        self.condition = None
        self.tokenizer = SimpleNamespace(id_to_piece=lambda token: "")
        self.modules = SimpleNamespace(mx=None)

    def reset_state(self) -> None:
        self.reset_calls += 1

    def seed_sampling(self, seed: int) -> None:
        self.sampling_seeds.append(seed)

    def new_generator(self, *, max_steps: int, sampling_profile: str) -> object:
        assert max_steps == 100
        self.sampling_profiles.append(sampling_profile)
        return object()


def test_session_seeds_once_but_keeps_profile_across_generation_reset() -> None:
    loaded = FakeLoadedModel()
    codec = FakeCodec()
    session = RealtimeSession(
        loaded_model=loaded,
        codecs=CodecPair(encoder=codec, decoder=codec, execution="pipelined"),
        queue_capacity=4,
        max_steps=100,
        telemetry_interval_frames=125,
        sampling_profile="greedy",
        sampling_seed=123,
    )

    session._prepare_model_state()
    session._reset_generation()

    assert loaded.reset_calls == 2
    assert loaded.sampling_seeds == [123]
    assert loaded.sampling_profiles == ["greedy", "greedy"]
