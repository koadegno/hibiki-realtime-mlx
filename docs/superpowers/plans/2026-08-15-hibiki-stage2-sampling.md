# Hibiki Stage 2 Sampling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add reproducible named Hibiki text-sampling profiles and a raw-PCM Stage 2 replay workflow that records the active profile and seed in every artifact.

**Architecture:** Sampling policy becomes process configuration resolved from one profile table. A fresh WebSocket seeds MLX once, then every generator created inside that session uses the same named profile without reseeding on silence resets. `/ready`, logs, Taskfile commands, and browser replay artifacts expose the resolved experiment identity.

**Tech Stack:** Python 3.12, MLX 0.26, pinned `moshi_mlx`, aiohttp, pytest, vanilla browser JavaScript, Go Task, GitHub Actions.

## Global Constraints

- Canonical Stage 2 input is the exact 24 kHz mono PCM16 WAV whose PCM SHA-256 is `22f929d3860d39c3a0f5acb888a96e3748987a899aa74e48d93df5b59f66e8e3`.
- Stage 2 uses raw PCM only; the deferred Opus `1013` replay robustness bug is out of scope.
- Candidate text profiles are exactly `mlx-current` (`temp=0.4`, `top_k=25`), `kyutai-reference` (`temp=0.8`, `top_k=250`), and `greedy` (`temp=0.0`, argmax).
- `historical-cold-0.2` remains available only for reproducibility (`temp=0.2`, `top_k=25`).
- Audio sampling stays `temp=0.8`, `top_k=250` for every profile.
- Default sampling seed is `299792458`.
- Seed once per fresh WebSocket session; never reseed when `adaptive-reset` creates a replacement generator inside that session.
- Preserve Rust Mimi + q4 MLX as the Stage 2 runtime baseline.
- Do not change silence thresholds, q4 weights, Mimi, confidence logic, or audio-sampler policy.
- Public repository: no secrets, tokens, private repository references, or credential-dependent CI.

---

### Task 1: Named sampling configuration and CLI

**Files:**
- Modify: `backend/hibiki_mlx_realtime_api/hibiki_mlx_realtime_api/config.py`
- Modify: `backend/hibiki_mlx_realtime_api/hibiki_mlx_realtime_api/__main__.py`
- Modify: `backend/hibiki_mlx_realtime_api/tests/test_config.py`
- Modify: `backend/hibiki_mlx_realtime_api/tests/test_cli.py`

**Interfaces:**
- Produces: `SamplingProfile`, `SamplingSettings`, `resolve_sampling_profile(profile)`, `RuntimeConfig.sampling_profile`, `RuntimeConfig.sampling_seed`.
- Consumed by: Tasks 2-5.

- [ ] **Step 1: Write failing profile/config tests**

Add assertions equivalent to:

```python
from hibiki_mlx_realtime_api.config import RuntimeConfig, resolve_sampling_profile


def test_sampling_profiles_resolve_exact_settings() -> None:
    current = resolve_sampling_profile("mlx-current")
    reference = resolve_sampling_profile("kyutai-reference")
    greedy = resolve_sampling_profile("greedy")

    assert (current.text_temperature, current.text_top_k) == (0.4, 25)
    assert (reference.text_temperature, reference.text_top_k) == (0.8, 250)
    assert (greedy.text_temperature, greedy.text_top_k) == (0.0, 250)
    for profile in (current, reference, greedy):
        assert (profile.audio_temperature, profile.audio_top_k) == (0.8, 250)


def test_runtime_defaults_to_named_sampling_profile_and_reference_seed() -> None:
    config = RuntimeConfig()
    assert config.sampling_profile == "mlx-current"
    assert config.sampling_seed == 299792458


def test_runtime_rejects_bad_sampling_configuration() -> None:
    with pytest.raises(ValueError, match="sampling_profile"):
        RuntimeConfig(sampling_profile="magic")
    with pytest.raises(ValueError, match="sampling_seed"):
        RuntimeConfig(sampling_seed=-1)
```

Update CLI tests to require:

```python
config = config_from_args(
    build_parser().parse_args(
        ["--sampling-profile", "greedy", "--sampling-seed", "123"]
    )
)
assert config.sampling_profile == "greedy"
assert config.sampling_seed == 123
```

and remove expectations for the old free-form `text_temperature` field.

- [ ] **Step 2: Run CI and verify RED**

Expected: pytest fails because named sampling types/fields/resolver and CLI options do not exist yet.

- [ ] **Step 3: Implement one profile table in `config.py`**

Use this shape:

```python
SamplingProfile = Literal[
    "mlx-current",
    "kyutai-reference",
    "greedy",
    "historical-cold-0.2",
]


@dataclass(frozen=True, slots=True)
class SamplingSettings:
    text_temperature: float
    text_top_k: int
    audio_temperature: float = 0.8
    audio_top_k: int = 250


_SAMPLING_PROFILES: dict[SamplingProfile, SamplingSettings] = {
    "mlx-current": SamplingSettings(0.4, 25),
    "kyutai-reference": SamplingSettings(0.8, 250),
    "greedy": SamplingSettings(0.0, 250),
    "historical-cold-0.2": SamplingSettings(0.2, 25),
}


def resolve_sampling_profile(profile: SamplingProfile) -> SamplingSettings:
    try:
        return _SAMPLING_PROFILES[profile]
    except KeyError as exc:
        raise ValueError(f"unsupported sampling_profile: {profile}") from exc
```

Replace `RuntimeConfig.text_temperature` with:

```python
sampling_profile: SamplingProfile = "mlx-current"
sampling_seed: int = 299792458
```

Validate the profile through `resolve_sampling_profile()` and require `0 <= sampling_seed <= 0xFFFFFFFF`.

- [ ] **Step 4: Replace CLI free-form temperature with named profile/seed**

In `__main__.py`, add:

```python
parser.add_argument(
    "--sampling-profile",
    choices=("mlx-current", "kyutai-reference", "greedy", "historical-cold-0.2"),
    default=defaults.sampling_profile,
)
parser.add_argument("--sampling-seed", type=int, default=defaults.sampling_seed)
```

and pass both into `RuntimeConfig` from `config_from_args()`.

- [ ] **Step 5: Verify Task 1 tests GREEN**

Run through GitHub Actions; expected config/CLI tests and existing tests touching defaults to pass after corresponding expectations are updated.

- [ ] **Step 6: Commit Task 1 implementation**

Commit message: `feat: add named Hibiki sampling profiles`.

---

### Task 2: Profile-specific generator and MLX session seeding

**Files:**
- Modify: `backend/hibiki_mlx_realtime_api/hibiki_mlx_realtime_api/model.py`
- Modify: `backend/hibiki_mlx_realtime_api/hibiki_mlx_realtime_api/session.py`
- Modify: `backend/hibiki_mlx_realtime_api/tests/test_model.py`
- Modify: `backend/hibiki_mlx_realtime_api/tests/test_session.py`

**Interfaces:**
- Consumes: `SamplingProfile`, `resolve_sampling_profile()`.
- Produces: `LoadedLanguageModel.seed_sampling(seed: int)`, `LoadedLanguageModel.new_generator(max_steps: int, sampling_profile: SamplingProfile)` and `RealtimeSession(..., sampling_profile, sampling_seed)`.

- [ ] **Step 1: Write failing model tests**

Extend `FakeMx` with a fake random object:

```python
class FakeRandom:
    def __init__(self) -> None:
        self.seeds: list[int] = []

    def seed(self, value: int) -> None:
        self.seeds.append(value)
```

Require:

```python
loaded.seed_sampling(123)
assert mx.random.seeds == [123]

reference = loaded.new_generator(max_steps=500, sampling_profile="kyutai-reference")
assert reference.kwargs["text_sampler"].kwargs == {"top_k": 250, "temp": 0.8}
assert reference.kwargs["audio_sampler"].kwargs == {"top_k": 250, "temp": 0.8}

greedy = loaded.new_generator(max_steps=500, sampling_profile="greedy")
assert greedy.kwargs["text_sampler"].kwargs == {"top_k": 250, "temp": 0.0}
```

- [ ] **Step 2: Write failing session seed-once test**

Make `FakeLoadedModel` record `sampling_profiles` and `sampling_seeds`. Instantiate an adaptive-reset session with `sampling_profile="greedy"` and `sampling_seed=123`. Drive it through one park/resume that creates a second generator. Assert:

```python
assert loaded.sampling_seeds == [123]
assert loaded.sampling_profiles == ["greedy", "greedy"]
assert len(loaded.generators) == 2
```

This proves fresh-session seed happens once while generation reset keeps the profile without reseeding.

- [ ] **Step 3: Run CI and verify RED**

Expected: tests fail because model/session seeding and profile parameters do not exist.

- [ ] **Step 4: Implement generator profile resolution**

In `LoadedLanguageModel`:

```python
def seed_sampling(self, seed: int) -> None:
    self.modules.mx.random.seed(seed)


def new_generator(self, *, max_steps: int, sampling_profile: SamplingProfile) -> Any:
    settings = resolve_sampling_profile(sampling_profile)
    return self.modules.models.LmGen(
        model=self.model,
        max_steps=max_steps,
        text_sampler=self.modules.utils.Sampler(
            top_k=settings.text_top_k,
            temp=settings.text_temperature,
        ),
        audio_sampler=self.modules.utils.Sampler(
            top_k=settings.audio_top_k,
            temp=settings.audio_temperature,
        ),
        cfg_coef=1.0,
        check=False,
    )
```

- [ ] **Step 5: Seed once in `RealtimeSession`**

Add constructor fields `sampling_profile` and `sampling_seed`. Make `_prepare_model_state()` perform:

```python
self._loaded_model.reset_state()
self._loaded_model.seed_sampling(self._sampling_seed)
self._generator = self._new_generator()
```

Make `_new_generator()` call `new_generator(max_steps=..., sampling_profile=self._sampling_profile)`.

Keep `_reset_generation()` as:

```python
self._loaded_model.reset_state()
self._generator = self._new_generator()
```

with **no** call to `seed_sampling()`.

For `serial_mlx`, call `_prepare_model_state()` instead of separately duplicating initial model reset/generator construction so the same seed-once invariant applies to both execution paths.

- [ ] **Step 6: Verify Task 2 GREEN**

Expected: model and session tests pass, including adaptive-reset seed-once regression.

- [ ] **Step 7: Commit Task 2 implementation**

Commit message: `feat: seed sampling per realtime session`.

---

### Task 3: Runtime propagation, resolved logs, and readiness metadata

**Files:**
- Modify: `backend/hibiki_mlx_realtime_api/hibiki_mlx_realtime_api/runtime.py`
- Modify: `backend/hibiki_mlx_realtime_api/hibiki_mlx_realtime_api/server.py`
- Modify: `backend/hibiki_mlx_realtime_api/tests/test_runtime.py`
- Modify: `backend/hibiki_mlx_realtime_api/tests/test_server.py`

**Interfaces:**
- Consumes: `RuntimeConfig.sampling_profile`, `RuntimeConfig.sampling_seed`, `resolve_sampling_profile()`.
- Produces: `RuntimeManager.experiment_metadata: dict[str, object]`; `/ready` additive sampling fields.

- [ ] **Step 1: Write failing runtime propagation test**

Instantiate `RuntimeConfig(sampling_profile="greedy", sampling_seed=123)` and assert the session factory receives:

```python
assert session_kwargs["sampling_profile"] == "greedy"
assert session_kwargs["sampling_seed"] == 123
```

Require manager metadata:

```python
assert manager.experiment_metadata == {
    "sampling_profile": "greedy",
    "sampling_seed": 123,
    "text_temperature": 0.0,
    "text_top_k": 250,
    "audio_temperature": 0.8,
    "audio_top_k": 250,
}
```

- [ ] **Step 2: Write failing `/ready` metadata test**

Give `FakeManager` the same `experiment_metadata` property and assert a ready response includes `sampling_profile`, `sampling_seed`, and resolved sampler values.

- [ ] **Step 3: Run CI and verify RED**

Expected: runtime/server tests fail because metadata and session kwargs are absent.

- [ ] **Step 4: Implement runtime metadata and propagation**

Add a module logger and a property equivalent to:

```python
@property
def experiment_metadata(self) -> dict[str, object]:
    settings = resolve_sampling_profile(self.config.sampling_profile)
    return {
        "sampling_profile": self.config.sampling_profile,
        "sampling_seed": self.config.sampling_seed,
        "text_temperature": settings.text_temperature,
        "text_top_k": settings.text_top_k,
        "audio_temperature": settings.audio_temperature,
        "audio_top_k": settings.audio_top_k,
    }
```

Pass `sampling_profile` and `sampling_seed` to every `RealtimeSession`. Log the complete metadata when the runtime becomes ready and when a session is created.

- [ ] **Step 5: Make `/ready` additive**

Change the readiness response helper to accept metadata and merge it into the JSON body without changing `status`, `phase`, `ready`, `error`, or HTTP status semantics.

- [ ] **Step 6: Verify Task 3 GREEN**

Expected: runtime/server tests pass and existing readiness consumers remain compatible.

- [ ] **Step 7: Commit Task 3 implementation**

Commit message: `feat: expose sampling experiment metadata`.

---

### Task 4: Stage 2 browser artifact identity

**Files:**
- Modify: `backend/hibiki_mlx_realtime_api/hibiki_mlx_realtime_api/static/transport-replay.html`
- Modify: `backend/hibiki_mlx_realtime_api/hibiki_mlx_realtime_api/static/transport-replay.js`
- Modify: `backend/hibiki_mlx_realtime_api/tests/test_transport_replay.py`

**Interfaces:**
- Consumes: `GET /ready` sampling metadata.
- Produces: profile-specific raw-PCM artifact labels and manifest fields.

- [ ] **Step 1: Write failing browser contract test**

Require the packaged page/script to contain runtime identity fields and a readiness fetch before replay:

```python
assert 'id="samplingProfile"' in html
assert 'id="samplingSeed"' in html
assert 'fetch("/ready"' in script
assert "sampling_profile" in script
assert "sampling_seed" in script
assert "stage2-${runtimeMetadata.sampling_profile}-pcm" in script
```

- [ ] **Step 2: Run CI and verify RED**

Expected: transport replay asset test fails because Stage 2 metadata is not displayed/captured yet.

- [ ] **Step 3: Add runtime identity UI**

Add read-only rows under experiment identity:

```html
<dt>Sampling profile</dt><dd id="samplingProfile">—</dd>
<dt>Sampling seed</dt><dd id="samplingSeed">—</dd>
```

- [ ] **Step 4: Fetch and validate readiness metadata before WebSocket creation**

Add:

```javascript
async function fetchRuntimeMetadata() {
  const response = await fetch("/ready", { cache: "no-store" });
  const payload = await response.json();
  if (!response.ok || payload.ready !== true) {
    throw new Error(`Hibiki runtime is not ready: ${payload.phase || response.status}`);
  }
  if (!payload.sampling_profile || !Number.isInteger(payload.sampling_seed)) {
    throw new Error("Hibiki /ready is missing Stage 2 sampling metadata");
  }
  return payload;
}
```

Call it before `openWebSocket()`, display profile/seed, and preserve the returned object for the manifest.

- [ ] **Step 5: Label and enrich artifacts**

For raw PCM set:

```javascript
manifest.label = `stage2-${runtimeMetadata.sampling_profile}-pcm`;
```

For Opus keep a Stage 1-oriented label so Stage 2 does not silently include Opus. Add to every manifest:

```javascript
manifest.sampling_profile = runtimeMetadata.sampling_profile;
manifest.sampling_seed = runtimeMetadata.sampling_seed;
manifest.text_temperature = runtimeMetadata.text_temperature;
manifest.text_top_k = runtimeMetadata.text_top_k;
manifest.audio_temperature = runtimeMetadata.audio_temperature;
manifest.audio_top_k = runtimeMetadata.audio_top_k;
```

- [ ] **Step 6: Verify browser tests GREEN**

Expected: pytest asset test, `node --check`, and deterministic Node transport tests pass.

- [ ] **Step 7: Commit Task 4 implementation**

Commit message: `feat: label Stage 2 replay artifacts by sampling profile`.

---

### Task 5: Taskfile experiment commands and historical compatibility

**Files:**
- Modify: `Taskfile.yml`
- Modify: `backend/hibiki_mlx_realtime_api/tests/test_cli.py` or add `backend/hibiki_mlx_realtime_api/tests/test_taskfile_sampling.py`

**Interfaces:**
- Produces user-facing commands for the three Stage 2 candidate servers.

- [ ] **Step 1: Write failing Taskfile contract test**

Read the repository `Taskfile.yml` and require all three task names:

```text
hibiki-mlx:serve:rust:adaptive-reset:mlx-current
hibiki-mlx:serve:rust:adaptive-reset:kyutai-reference
hibiki-mlx:serve:rust:adaptive-reset:greedy
```

Require the base adaptive-reset command to pass both `--sampling-profile` and `--sampling-seed`, and require the historical cold task to use `historical-cold-0.2` rather than `--text-temperature`.

- [ ] **Step 2: Run CI and verify RED**

Expected: new Taskfile contract fails on existing free-form temperature commands.

- [ ] **Step 3: Update runtime tasks**

Replace Stage 2-capable free-form temperature usage with:

```bash
--sampling-profile "${HIBIKI_SAMPLING_PROFILE:-mlx-current}" \
--sampling-seed "${HIBIKI_SAMPLING_SEED:-299792458}"
```

Apply the same named default to relevant Rust/MLX serve paths so all runtime starts have explicit sampling identity.

- [ ] **Step 4: Add three convenience tasks**

Each task sets `HIBIKI_SAMPLING_PROFILE` and delegates to `hibiki-mlx:serve:rust:adaptive-reset`:

```yaml
hibiki-mlx:serve:rust:adaptive-reset:greedy:
  env:
    HIBIKI_SAMPLING_PROFILE: greedy
  cmds:
    - task: hibiki-mlx:serve:rust:adaptive-reset
```

Create equivalent `mlx-current` and `kyutai-reference` tasks.

Map `hibiki-mlx:serve:rust:adaptive-reset-cold` to `historical-cold-0.2` and the standard seed.

- [ ] **Step 5: Update `hibiki-mlx:info`**

Print default sampling profile and seed next to model/codec information.

- [ ] **Step 6: Verify Taskfile parsing and tests GREEN**

Expected: Go Task parse succeeds and no task references `HIBIKI_TEXT_TEMPERATURE` for candidate Stage 2 runs.

- [ ] **Step 7: Commit Task 5 implementation**

Commit message: `feat: add Stage 2 sampling server tasks`.

---

### Task 6: Record Stage 1 decision and Stage 2 readiness

**Files:**
- Modify: `docs/hibiki-quality-roadmap.md`
- Modify: `docs/hibiki-experiment-journal.md`

**Interfaces:**
- Documents the evidence basis and exact physical M4 experiment to run next.

- [ ] **Step 1: Update Stage 1 status**

Record that same-WAV PCM/Opus artifacts were obtained, both preserve the opening girl/son distinction, neither transport is consistently superior, and transport is not currently supported as the dominant lexical-loss explanation. Record the remaining `1013` issue as deferred robustness work, not a Stage 2 blocker.

- [ ] **Step 2: Update Stage 2 status**

Set Stage 2 to `IMPLEMENTED — WAITING FOR M4 PROFILE REPLAYS` and document the exact candidate profiles, fixed audio sampler, fixed seed, raw-PCM-only rule, and requirement for two greedy runs.

- [ ] **Step 3: Append journal entry**

Record the Stage 1B interpretation and the implementation choices from this spec without claiming unmeasured M4 quality results.

- [ ] **Step 4: Commit documentation**

Commit message: `docs: advance quality roadmap to Stage 2`.

---

### Task 7: Full verification before M4 handoff

**Files:**
- No new production files unless verification exposes a defect.

- [ ] **Step 1: Run full GitHub Actions workflow on final HEAD**

Require success for:

```text
uv lock --check
Ruff
pytest
browser JavaScript syntax
browser deterministic transport tests
CLI smoke
Taskfile parse
```

- [ ] **Step 2: Inspect final branch diff**

Confirm changes are limited to sampling config/model/session/runtime/readiness/replay identity/tasks/tests/docs. Confirm no model weights, secrets, tokens, private repository references, or unrelated runtime changes were added.

- [ ] **Step 3: Verify final branch SHA and prepare M4 commands**

The handoff must include:

```text
task hibiki-mlx:serve:rust:adaptive-reset:mlx-current
task hibiki-mlx:serve:rust:adaptive-reset:kyutai-reference
task hibiki-mlx:serve:rust:adaptive-reset:greedy
```

and instruct the user to run `greedy` twice with the same canonical WAV in Raw PCM16LE mode, six-second tail, downloading each manifest/transcript/translated WAV plus server logs.
