# Chunk 1 Review — Companion Stop, Lease Loss, and Stale Provider Results

Date: 2026-11-14
Base commit: `639ffe0`
Scope: `host/companion/service.py`, `host/companion/body.py`, `tests/test_companion.py`
Evidence class: in-process `SimulatorBody` (`host/cammy_simulator.py`), deterministic fake providers (`host/companion/fakes.py`), and ephemeral `127.0.0.1` gateway fixture (`LoopbackGatewayTests`). No camera, ADB, TF, device, or hardware audio/gimbal/face evidence.

## 1. Concrete Findings

**Code defects found at this boundary: None.**

The existing `CompanionService` implementation in `host/companion/service.py` enforces two independent guards across stop, emergency stop, lease loss, local expiry, disconnect, and restart/reacquisition:

1. **Transition teardown (`stop()`, `emergency_stop()`, `_halt()`):**
   - Increments `_generation` (`service.py:201, 215, 426`) and calls `_reset_work()` (`service.py:409-413`).
   - `_reset_work()` calls `_interrupt('reset')` (incrementing `_epoch`, retiring `_agent_job` and `_synth_job` via `_retire()`, clearing `_actions`, clearing `_speech_until`, and marking any in-flight utterance `interrupted=True`), calls `_retire_sensing()` (incrementing `_sense_epoch` and retiring `_sense_job`), clears `_inputs`, and sets `phase = IDLE`.
   - `_settle()` clears `_lease_deadline`, `face`, `gaze`, and `_face_wanted`, and transitions `run_state` to `STOPPED` or `HALTED`.
   - When `run_state` is `STOPPED` or `HALTED`, `tick()` only runs `_drain_retired()` (`service.py:272-273`); no step or `_sync_face()` body call is executed.

2. **Result acceptance and body action dispatch (`_drain_retired()`, `_step_transcript()`, `_step_agent()`, `_step_output()`, `_sync_face()`):**
   - Retired jobs that ignore `cancel()` (`ignore_cancel=True`) are polled in `_drain_retired()` (`service.py:371-381`), removed upon completion, and counted in `stale_results_rejected` with a `stale-result-rejected` event without reaching transcript, agent, or output dispatch.
   - Even if a job handle or queued action escaped retirement (white-box second layer), `_step_transcript()` checks `pending.token != (self._generation, self._sense_epoch)` (`service.py:502`), `_step_agent()` checks `pending.token != self._token()` (`service.py:532`), and `_step_output()` checks both `pending.token != self._token()` (`service.py:598`) and `token != self._token()` (`service.py:610`).
   - Because stale results return before modifying `self.phase` or `self._actions`, `_sync_face()` sees no phase change (`IDLE` / `LISTENING`) and emits no new `face`, `look`, or `speak` body calls.

3. **Ownership and cleanup truthfulness:**
   - Normal `stop()` sends owner-scoped `POST /v1/session/release` only when `run_state in (RUNNING, PAUSED)` (`service.py:200-206`).
   - Lease conflict (`409` or `state['owner'] != agent`), local deadline expiry (`now >= _lease_deadline`), and disconnect (`unavailable`) call `_halt(...)` with `body_stop_sent: False`, never sending global `POST /v1/stop` or interfering with another owner (`service.py:425-435, 445-470`).
   - `emergency_stop()` explicitly sends global `POST /v1/stop` (`service.py:212-223`).
   - Both `stop()` and `emergency_stop()` report `hardware_cleanup_confirmed: False`, and `cancel()` requests on providers are tracked separately from `cancellations_acknowledged`.

## 2. Coverage Gaps Closed (`tests/test_companion.py`)

Added two focused regression tests in `LifecycleTests` (bringing `tests.test_companion` from 29 to 31 tests):

- `test_emergency_stop_and_delayed_synthesizer_and_queued_work_cleared`:
  - Exercises `emergency_stop()` while a synthesizer job (`_synth_job`), a sensing transcriber job (`_sense_job`), queued body actions (`_actions`), and queued user inputs (`_inputs`) are all simultaneously pending, with `ignore_cancel=True` on both synthesizer and transcriber.
  - Verifies queues clear, `stop.requested` is sent once, `hardware_cleanup_confirmed` is `False`, delayed synthesizer/transcriber completions while `STOPPED` emit zero body events and increment `stale_results_rejected`, and a delayed synthesizer completion arriving after `stop()` + `start()` reacquisition is likewise rejected without triggering speech, gaze, or face actions.
- `test_lease_conflict_and_expiry_while_speaking_or_pending_reject_delayed_results`:
  - Exercises lease takeover by another owner (`other-owner`) mid-speaking while multi-chunk speech actions and a follow-up input are queued; verifies transition to `(HALTED, 'lease-lost')`, queue clearing, `interrupted=True` on the in-flight utterance, zero interference with `other-owner`, and no global `/v1/stop`.
  - Exercises local lease deadline expiry while a slow `ignore_cancel=True` agent job is pending; verifies transition to `(HALTED, 'lease-expired')` without renewal and rejection of the late agent result with zero body actions.

## 3. Changed Paths

- `tests/test_companion.py`
- `docs/work-packages/CHUNK1-COMPANION-STOP.md`
- `private/ownership-program/chunk1/checkpoint.json` (ignored checkpoint)

(`host/companion/service.py`, `host/companion/body.py`, and `docs/COMPANION-SERVICE.md` required no edits.)

## 4. Reproducible Verification

```sh
cd <projects>/Cammy-OS
python3 -m unittest -v tests.test_companion
PYTHONPATH=host python3 -m companion --quiet
```

Outcome: `Ran 31 tests in 1.236s — OK` (all in-process simulator or ephemeral loopback fixture; nothing left running).

## 5. Remaining Limits

- **Fake/simulator evidence only:** All 31 tests run against `Simulator` and scripted fake providers (`ManualClock`, `ScriptedAgent`, `ScriptedTranscriber`, `EstimatedTextSpeech`). They do not prove physical audio playback completion, physical speaker/microphone cutoff, or native hardware cleanup.
- **No per-utterance recall in v1:** As documented in `service.py:402-404`, interrupting or stopping while an utterance chunk has already been dispatched to `/v1/speak` prevents subsequent chunks from being sent, but v1 has no per-utterance cancel RPC beyond owner lease release (`/v1/session/release`, which calls `stop_body()` in the simulator) or global `/v1/stop`.
- **Single-threaded / lock-serialized polling:** Providers are polled synchronously inside `tick()` under `self._lock`; real asynchronous provider adapters must continue to surface results exclusively through nonblocking `job.poll()` handles.

## 6. Suggested Progress-Note Text

> Chunk 1 companion stop/lease-loss review complete: verified `host/companion/service.py` transitions for `stop()`, `emergency_stop()`, lease conflict, local lease expiry, disconnect, and restart/reacquisition. Confirmed generation/epoch invalidation and retirement queues prevent delayed or `ignore_cancel` agent, transcriber, and synthesizer results from dispatching speech, gaze, or face actions, while preserving owner-scoped release vs global `/v1/stop` semantics and `hardware_cleanup_confirmed: False`. Added 2 regression tests in `tests/test_companion.py` (31/31 passing, simulator/loopback fixture evidence only).
