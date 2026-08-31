# PAD-Router: Project Strengthening Opportunities

**Date:** 2026-08-30
**Status:** Research note; no runtime or test code is changed by this note.

## Executive recommendation

PAD-Router is already a useful offline-assisted board router for a narrow but
real workflow: inspect a calibrated Android board, correct recognition when
needed, search a bounded candidate route, and send a guarded ADB gesture with
post-gesture verification. The highest-value strengthening is therefore not a
new solver or model. It is making the existing safety contract temporal and
observable, then exposing the already-implemented offline and manual-planning
paths through the web workspace.

The recommended order is:

1. Add a fresh-frame readiness/preflight gate and require a settled pair of
   frames for verification.
2. Expose PNG import and manual route evaluation in the existing web workspace,
   reusing the controller guards.
3. Add small, local, explainable run/search diagnostics and actionable ADB
   device states.
4. Bound GUI continuous execution and establish a fixed search benchmark before
   changing the heuristic.

This keeps the first improvements within the current standard-library core,
pywebview shell, and injected test seams. It also keeps raw ADB capture as the
execution authority; a video stream can remain a later preview optimization.

## Scope and evidence

The review covered the core router, GUI controller and bridge, webview assets,
tests, README, user guide, architecture/development/context references, and
the existing research notes. Structural discovery used the indexed project
home-karl-orca-projects-PAD-Router at generation 2026-08-30T14:17:53Z
(Tier 2 verification; 1,156 nodes and 5,386 edges; no recorded parse or
coverage gaps in the relied-on paths). Source checks then covered the concrete
seams below; “not present” means not present in those inspected surfaces, not a
claim about files excluded from the repository index.

Key implementation seams:

| Concern | Existing seam | What it establishes |
| --- | --- | --- |
| Recognition | detect_board_pixels, OrbPrototypeModel, BoardInspectionController._detect_with_retries | Fixed HSV/prototype recognition, optional local samples, and fail-closed unknown cells. |
| Search | search_qualifying_route (pad_router.py:1178-1369) | Seeded finite random/beam/shape search with qualifying and diagnostic candidates; no global-optimum guarantee. |
| Execution | play (pad_router.py:1921-2073) and BoardInspectionController.execute_route | ADB gestures, lift/change check, post-gesture comparison, corrective moves, and safe release paths. |
| Workspace | BoardInspectionBridge.command (pad_router_gui.py:2124-2337) | Serialized commands, operation conflict checks, cancellation, generation-based stale-result rejection, snapshots, and bounded console history. |
| Repetition | execute_continuously | Release, recapture, replan, and stop on user request, uncertainty, missing route, execution failure, or verification failure. |
| Tests | test_pad_router_gui.py, test_pad_router_planning.py, test_max_combo_direct.py | Broad recognition, correction, rules, search, bridge concurrency, execution safety, cancellation, and continuous-loop coverage. |

The current full-suite command was started as uv run python -m unittest but
was stopped during a long heuristic-search test to converge on the requested
note. It did not produce a test failure; it produced an interrupt while
test_protection_researches_rejects_manual_route_and_survives_new_source was
still searching. The recommendations below should consequently add focused,
bounded tests rather than infer a clean full-suite result.

## Capability map: present, partial, and genuinely missing

### Present today

- **Recognition:** The core recognizes the supported normal and hazard orb
  classes using fixed prototypes and explicit visual markers. Unknown cells
  remain unknown; a board containing unknown cells is not routeable.
- **Human correction and local learning:** The controller can correct cells,
  persist human/implicit prototype samples, and protect a cell from a route.
  Human labels take precedence over implicit samples.
- **Board sizes:** Core and workspace support the documented 6x5 and 7x6
  boards, subject to calibration for the current display.
- **Rules:** RuleProfile covers condition groups, direct/cascade behavior,
  hazards, external HP/skill-like conditions, protected cells, attempts, seed,
  and route limits. Diagnostics explain failed conditions.
- **Planning:** Manual-route evaluation already exists in the core and
  controller. Automatic search is reproducible with a seed and deliberately
  bounded; it returns a diagnostic candidate when no qualifying candidate is
  found.
- **Execution safety:** Execution requires a confirmed board, a qualifying
  route, calibration, and a selected serial. Unknown or ineligible routes are
  refused. The hold/lift check, post-gesture verification, corrective-move
  limit, cancellation, and safe-release paths are already implemented.
- **Workspace operations:** Capture, review, correction, rule editing,
  profile import/export, asynchronous search, cancellation, execution, stop,
  debug state, settings persistence, and local web assets are present.
- **Offline core use:** PNG decoding and BoardInspectionController.load_png
  exist, so developers can use a file and the Python API without an Android
  device. This path is not currently exposed in the web workspace.
- **Accessibility basics:** The web surface already uses labels, live regions,
  keyboard-friendly controls, and reduced-motion-aware styling. The next UX
  gains should be explanations and recovery state, not a wholesale visual
  redesign.

### Partial or absent in the user workflow

| Workflow need | Current state | Consequence |
| --- | --- | --- |
| Freshness before a gesture | play captures a selected-cell band for the lift check, but does not compare a fresh full-board frame to the confirmed board immediately before DOWN. | A board that changed after review can pass the logical preconditions while still being the wrong physical board. |
| Temporal stability | _detect_with_retries retries recognition over the same pixel buffer; post-gesture verification performs an immediate detection rather than waiting for a settled pair. | Animation, transition, or a partially revealed board can be interpreted as a stable state. |
| Offline file workflow | load_png exists in the controller, but the bridge actions and UI expose ADB capture only. | Replay, bug reports, demos, and no-device planning require Python-level use or an unimplemented UI path. |
| Manual route workflow | evaluate_manual_route exists in the controller, but there is no route editor or bridge command in the web UI. | A human can inspect an automatic candidate, but cannot conveniently enter and compare their own route. |
| Approval wording | The context and guide describe an explicitly approved route, while the executable action currently relies on confirmed plus execution_eligible; there is no distinct route-approval state or action. | The safety contract is understandable but the product vocabulary and implementation disagree. |
| Search explanation | Progress reports phase and counts, and results expose candidates/diagnostics, but there is no elapsed time, evaluated-state count, stop reason, or best-so-far history. | Users cannot tell whether a result is fast, exhausted, cancelled, or merely heuristic-limited. |
| ADB failure recovery | _list_adb_devices returns only lines whose state is exactly device; unauthorized, offline, and command errors collapse into “no available device.” | The user has to guess whether to unlock/authorize, reconnect, fix ADB, or choose another serial. |
| Continuous execution limits | The CLI has --round-limit; the GUI continuous controller loops until a stop or unsafe condition and has no visible round/time budget. | The useful batch workflow is available, but unattended duration is harder to control than it should be. |
| Reproducible evidence | The console is bounded and snapshots expose current state, but there is no user-facing run report containing source identity, options, timings, recognition decisions, and verification. | A failure is difficult to replay or compare across devices and releases. |
| Calibration reuse | Calibration is intentionally not in the generic workspace settings because it is display/device-specific; automatic inference runs again for a new source. | Correctness is safer than stale persistence, but repeated users pay setup cost. |
| Stream capture | The existing research describes H.264/scrcpy as a possible preview-only optimization; it is not shipped. | This is a deliberate dependency and safety boundary, not a missing prerequisite for current ADB use. |

## Ranked recommendations

The ranking weighs user value, implementation feasibility, new dependencies,
and the effect of a mistake. “Low dependency” means it can reuse current
capture, recognition, controller, bridge, and test doubles without adding a
Python package or cloud service.

| Rank | Smallest useful slice | User value | Feasibility / dependency cost | Safety posture |
| --- | --- | --- | --- | --- |
| P0 | **Fresh-frame safety gate.** Add one bounded capture/readiness seam that obtains fresh frames, waits for a stable pair, and compares the pre-gesture board identity with the confirmed board before DOWN. Reuse it after the gesture before verification/correction. | Very high: prevents acting on a stale review and reduces false mismatch corrections. | Medium; reuses ADB capture and recognition, but needs explicit injected frame sequences and timing policy. No new dependency. | Fail closed on unknown, changed board, changed dimensions/orientation, timeout, or disagreement. Never drag on a stale/unstable pair. |
| P0 | **PNG import in the web workspace.** Add a local file-selection path to the existing controller/bridge, with bounded PNG size/dimensions and the same calibration/unknown guards as ADB capture. | High for no-device use, bug reproduction, demos, and deterministic review. | Low-medium; the controller already has load_png; use the native pywebview file-dialog/API or a bounded local handoff, not an upload service. | File import must never bypass confirmation or make a route executable automatically. Keep source pixels immutable. |
| P1 | **Manual route editor.** Add click/tap-to-append, adjacency validation, undo/clear, and “evaluate route” using evaluate_manual_route. Keep execution behind the existing route eligibility and device checks. | High for experts who already know the intended drag and for comparing human and automatic candidates. | Medium; mostly UI state plus one bridge action, with the core evaluator already present. | Manual input is evidence for evaluation, not permission to execute. A nonqualifying or protected-cell route remains blocked. |
| P1 | **Actionable ADB states.** Preserve device, offline, unauthorized, and command failure separately in the device snapshot, and show the shortest recovery instruction. Keep serial selection explicit. | High: removes the most common setup ambiguity. | Low; parse the existing adb devices output and keep the current executor. No dependency. | Do not treat any non-device state as executable. A fresh serial/preflight check still wins immediately before input. |
| P1 | **Minimal run/search observability.** Add elapsed durations, capture count, recognition retry/stability outcome, ADB command phase, search stop reason, evaluated/unique state counts, and best-so-far candidate metrics to the existing debug snapshot and console. Offer a local “copy diagnostics” or JSON report. | High for debugging, support, and tuning; also makes safety refusals explainable. | Low-medium; counters and time.monotonic() fit existing snapshots. A full replay bundle can wait. | Default to metadata and hashes; save screenshots only on explicit user request. Never transmit data or add a service. |
| P1 | **Versioned recognition benchmark.** Build the raw, labeled, session-separated screenshot corpus already called for by the recognition research; report cell accuracy, per-class recall, unknown rate, high-confidence wrong labels, and full-board acceptance. | Very high: it is the only reliable way to know whether prototype learning, a heuristic change, or a future model actually improves recognition. | Medium data work, low runtime dependency; use the current decoder/recognizer and `uv` test tooling first. | Keep zero unsafe drags as a hard gate and split sessions/devices so near-duplicate frames cannot inflate results. |
| P1 | **Bound GUI continuous execution.** Reuse the existing loop but require a visible maximum round count or time budget, show the current/remaining count, and keep stop-on-uncertain/failure behavior. | Medium-high for repeated turns and unattended farming sessions. | Low-medium; controller parameter and UI setting, no algorithm change. The CLI already establishes the concept with --round-limit. | Default to a finite run. Stop on preflight change, disconnect, unknown, verification failure, or budget exhaustion. |
| P2 | **Fixed search benchmark before search changes.** Create a small checked-in board/profile corpus and record seeded route, duration, qualifying rate, and diagnostic quality. Only then adjust tie-breaks, deduplication, or separate sampling and beam budgets. | Medium: improves confidence rather than adding a user-visible feature. | Low; uses existing unittest and deterministic seed. No new solver/dependency. | Keep the finite heuristic contract explicit; do not promise global optimality. |
| P2 | **Validated calibration profiles.** If real usage shows repeated calibration friction, persist calibration keyed by serial, display dimensions, orientation, and board size; validate it against a fresh frame before use and discard on mismatch. | Medium for a small set of stable devices; low for one-off users. | Medium; persistence and invalidation rules are easy to get subtly wrong. | Never reuse a profile without a current geometry/content check. Do not put calibration into generic settings merely for convenience. |
| P3 | **Preview-only stream experiment.** Prototype a bounded newest-frame H.264/scrcpy preview for faster review on high-latency ADB links. Raw ADB remains the authority for preflight, gesture safety, and final verification. | Potentially medium-high for Wi-Fi capture, but uncertain until measured on target devices. | High relative to current scope: process lifecycle, codec, orientation, frame age, fallback, and platform integration. | No stream frame may authorize execution until freshness, sequence, age, quality, and fallback gates are proven. |

### Why these are the highest-leverage changes

The first item closes a physical-world race at the shared execution seam. It
does not require better recognition; it prevents a known-good recognition
result from being applied to a different or unsettled board. The next two
items close a product-surface gap by exposing capabilities already implemented
in the controller, so they add little conceptual surface area. Observability
then makes the safety and heuristic behavior diagnosable instead of requiring
users to infer it from a short console.

The ranking intentionally does **not** lead with a neural model, OpenCV, MCTS,
remote service, or stream capture. Those would increase dependencies and
failure modes before the project has the corpus and measurements needed to
justify them.

## Practical usage scenarios

| Scenario | Readiness today | Strengthening that makes it dependable |
| --- | --- | --- |
| Offline analysis of a saved board | Core/controller path is present; web UI exposure is missing. | PNG import, deterministic source identity, manual route editor, and local diagnostics. |
| Assisted play on one Android device over USB | Present: capture, calibrate, review, search, guarded execute, and verify. | Fresh preflight/stability gate and actionable device status. |
| Assisted play over ADB Wi-Fi | Present in principle through ADB capture, but latency and transient connectivity are more visible. | Freshness/timeout reporting first; only then consider preview stream. |
| Hazard-aware or protected-cell planning | Present through RuleProfile, hazard policy, and protected cells. | Better result explanation and a manual route comparison surface. |
| 6x5 and 7x6 board workflows | Present in core/UI/tests. | Calibration validation keyed to board size; no separate solver needed. |
| Repeated turns in a session | Present in CLI and GUI continuous execution, with several unsafe conditions stopping the loop. | Finite GUI round/time budget and per-round evidence. |
| Human-designed route inspection | Core evaluator is present; UI editing is absent. | Small manual route editor; keep automatic search as an alternative. |
| Regression/support investigation | Fixed tests and local prototype persistence help, but no run artifact exists. | Copyable JSON diagnostics and optional explicit screenshot capture. |
| New visual classes or unseen hazards | Correctly rejected as unknown. | Add labeled corpus and acceptance tests before adding a class; do not guess. |

## AI recognition model: will it increase accuracy?

The current “learning model” is not a neural network. It is a conservative
hybrid of fixed HSV/texture rules and nearest-prototype samples learned from
human corrections. That can help repeated device/theme-specific ambiguities,
but the repository currently has no held-out corpus proving a general accuracy
gain. A prototype that recognizes its own recent samples is not sufficient
evidence because adjacent frames from one session are highly correlated.

The correct next step is measurement, not a heavier model:

1. Collect at least 20 stationary screenshots / 600 labeled cells across
   separate sessions, devices, brightness levels, normal/enhanced/hazard orbs,
   animation, absent-color cases, and dark/heart ambiguity.
2. Freeze train/tune/test splits by session or device and compare fixed rules,
   prototype-assisted recognition, and any proposed change on identical data.
3. Track exact-cell accuracy, per-class recall, unknown rate, high-confidence
   wrong labels, and full-board acceptance. A reduction in unknowns is not a
   win if confident wrong labels increase.
4. Consider a small learned embedding/CNN only if the frozen benchmark shows a
   repeatable failure that calibrated HSV/prototypes cannot solve.

So the answer is conditional: local prototypes probably improve familiar
visual variants, but there is not yet evidence that “AI learning” raises the
project's overall recognition accuracy. The benchmark is the feature that
makes that claim testable.

## Gameplay and application direction

The best product position is an **offline board laboratory and route coach**,
not a second PAD battle simulator and not an opaque auto-play bot. Official PAD
material describes puzzle, team, attributes, skills, and dungeon state as
interacting systems; this repository currently represents the board-local
puzzle well but does not model credible damage, HP, teams, enemies, skills, or
random skyfall.

The smallest engaging loop reuses the existing deterministic evaluator:

1. **Explainable coaching card:** show direct match groups, cascade rounds,
   condition evidence, hazard/protected-cell decisions, route length, and why
   a bounded candidate qualifies or fails. Never label it globally optimal.
2. **Manual route versus solver:** let the player draw one route, evaluate it
   through `evaluate_manual_route`, and compare direct/total combos, cascades,
   conditions, hazards, and steps with the automatic candidate.
3. **Static practice cards:** local board + rule profile + optional seed/max
   steps for combo, color, shape, hazard, protected-cell, and direct-versus-
   cascade drills. Official Ordeal dungeons use fixed puzzle tasks within turn
   limits, so this is recognizable PAD-style training without inventing a
   server or reward economy.
4. **Optional local replay/journal:** save board symbols, rule profile, seed,
   route, result, and verification—not raw screenshots by default—for personal
   comparison and reproducible bug reports.
5. **Later only:** a small set of bounded route alternatives showing trade-offs
   such as more direct combos, fewer steps, or safer hazard handling.

Before expanding these modes, verify and close the repository's open rule
issues [#6 (direct-match maximum-combo semantics)](https://github.com/jhihweijhan/PAD-Router/issues/6)
and [#7 (untyped 4/L/cross selection)](https://github.com/jhihweijhan/PAD-Router/issues/7).
Those rules are the scoring foundation for coaching and challenges.

Explicit gameplay boundaries:

- Do not build a damage/team/leader/dungeon simulator until those states are
  actual product inputs with authoritative tests.
- Do not predict random skyfall from the current finite board.
- Do not add badges, progression, a daily network service, or leaderboards
  before the explain/compare loop proves useful.
- Do not target live competitive/PvP automation. The official 4-Player Mode
  page warns that suspicious data manipulation or unauthorized programs can
  lead to restrictions or permanent suspension; keep coaching, challenges,
  and replay offline/read-only and retain explicit execution confirmation.

Primary PAD context:

- [Official PAD Hong Kong/Taiwan overview](https://pad.gungho.jp/hktw/pad/)
- [Official tutorial overview](https://www.puzzleanddragons.us/single-post/tutorial-overview)
- [Official Ordeal Dungeon example](https://www.puzzleanddragons.us/single-post/ordeal-dungeon-of-japanese-gods-arrives-241107)
- [Official 4-Player Mode (PvP) and prohibited-activity warning](https://www.puzzleanddragons.us/single-post/4-player-mode-pvp-arrives-231115)

## Skills worth installing

The project already has computer-vision/OpenCV, senior computer vision,
documentation, README, research, TDD, code review, Android emulator QA,
Android performance, and game UI/playtest capabilities. Installing duplicates
will not improve recognition accuracy. The bounded recommendations are:

| Timing | Skill | Why / condition | Install command |
| --- | --- | --- | --- |
| Use now if manual QA is part of the workflow | [mattpocock/skills `qa`](https://skills.sh/mattpocock/skills/qa) | Turns exploratory observations into reproducible issues, matching this repository's GitHub Issue workflow. Marketplace snapshot: 204.8K installs, 240.9K repository stars, all displayed audits pass. | `npx skills add mattpocock/skills@qa -g -y` |
| Conditional | [anthropics/skills `webapp-testing`](https://skills.sh/anthropics/skills/webapp-testing) | Strong browser-layer testing once a local static/HTTP harness and fake bridge exist. It does not replace native pywebview/GTK or real ADB smoke tests. Snapshot: 145.7K installs, 172.5K stars, all displayed audits pass. | `npx skills add anthropics/skills@webapp-testing -g -y` |
| Conditional on a measured Python search bottleneck | [wshobson/agents `python-performance-optimization`](https://skills.sh/wshobson/agents/python-performance-optimization) | Useful for profiling the bounded heuristic/search core after a benchmark identifies the slow case; Android performance tooling does not replace Python profiling. Snapshot: 32.1K installs, 39.2K stars, all displayed audits pass. | `npx skills add wshobson/agents@python-performance-optimization -g -y` |

Do not install an ML/CLIP skill, another Android/ADB skill, a generic game-
design skill, or Microsoft Playwright CLI now. They are respectively premature,
duplicates/scope mismatches, aimed at a separate game product, or—in the
Playwright CLI marketplace snapshot—show a Snyk failure. A Ruff-fix skill also
adds no value until Ruff itself is an explicit project tool.

## 30-day / 90-day / later roadmap

### First 30 days: close safety and explainability gaps

1. Specify and implement the bounded fresh-frame seam: readiness, two settled
   frames, board semantic identity, display geometry/orientation, timeout, and
   fail-closed reasons.
2. Add focused tests for stable frames, changed frames, unknown frames,
   rotation/size changes, timeout, pre-gesture mismatch, and animation during
   post-gesture verification. Use injected capture adapters; do not depend on a
   phone in CI.
3. Expose local PNG import through the existing controller/bridge and make the
   source name, dimensions, calibration, and unknown cells visible in the
   current review surface.
4. Preserve ADB device states and add basic capture/search/execution elapsed
   times plus a local JSON diagnostics copy action.
5. Freeze a small board/profile corpus and record the current seeded search
   behavior before tuning it.

The 30-day exit gate is simple: a stale or unsettled frame cannot cause input;
a saved PNG can be reviewed without ADB; a device setup failure explains its
next action; and a support report can identify the source, rules, seed, route,
and verification status without collecting data remotely.

### By 90 days: complete the human and bounded-session workflows

1. Add the manual route editor with undo/clear, adjacency checks, route
   evaluation, overlay, expected-board preview, and the same execution guard.
2. Add a visible finite round/time budget to GUI continuous execution and show
   per-round capture, route, verification, and stop reason.
3. Expand search telemetry with evaluated/unique state counts and stop reason;
   use the benchmark to decide whether to separate random-attempt and beam
   budgets or adjust tie-breaks.
4. If repeated device usage justifies it, add validated calibration profiles
   keyed by serial/resolution/orientation/board size.
5. Run a measured preview-only stream pilot on the target Android/Wi-Fi setup,
   with raw ADB fallback and no stream-authoritative execution.

### Later, only with evidence or a product mandate

- Make the preview stream production-ready only if frame age, orientation,
  quality, reconnection, and fallback behavior meet the safety acceptance
  tests.
- Add new orb classes or learned recognition only with a labeled, versioned
  corpus, open-set/unknown tests, and a measured false-positive budget.
- Consider broader platform support only after the supported Linux/GTK workflow
  has concrete demand and a maintained input/capture abstraction.
- Consider deeper search only if the fixed corpus demonstrates a meaningful
  failure rate or latency problem that the current heuristic cannot address.

## Explicit non-goals

These are deliberately out of the strengthening plan:

- No neural network, automatic unseen-class guessing, or mandatory OpenCV/ML
  dependency. Unknown remains a safe state until evidence supports a new model.
- No claim of globally optimal routes, exhaustive search, MCTS, or a general
  game solver. The finite heuristic and its limits should remain visible.
- No full team/HP/skill/dungeon/skyfall simulation or a monster database. The
  current rule profile is the useful boundary for board-local planning.
- No cloud service, HTTP upload endpoint, remote execution service, telemetry
  backend, or automatic screenshot sharing. Diagnostics stay local and opt-in.
- No cross-platform/mobile rewrite, multi-device orchestration, or always-on
  screen-stream architecture without a product requirement and a separate
  platform plan.
- No unlimited unattended GUI execution by default. Continuous mode must have
  a finite user-visible budget before it is treated as a dependable workflow.
- No stream frame may replace raw ADB as the safety authority until the stated
  freshness and fallback gates are demonstrated.

## Primary-source references

The repository sources and existing research notes are the primary evidence for
current PAD-Router behavior. The external references below support only the
platform constraints used in the recommendations:

- [Android Debug Bridge (adb), Android Developers](https://developer.android.com/tools/adb) — describes the client/server/device model, adb devices status reporting, targeted commands, wireless debugging, and direct adb exec-out screencap capture.
- [Media projection, Android Developers](https://developer.android.com/media/grow/media-projection) — documents user consent per capture session, Android 14 foreground-service requirements, size changes, and session-stop/resource recovery; these are why a custom capture helper is a later project rather than a small current dependency.
- [scrcpy video configuration, Genymobile](https://github.com/Genymobile/scrcpy/blob/master/doc/video.md) — documents bounded size, frame-rate, codec, orientation, crop, and buffering controls, as well as variable frame production; these support a measured preview-only pilot, not stream-authoritative execution.

The local research notes remain the source of measured PAD-Router-specific
claims, including ADB screenshot timing, recognition behavior, search tradeoffs,
and the existing stream-feasibility constraints:

- docs/research/image-recognition.md
- docs/research/combo-search.md
- docs/research/wifi-stream-feasibility.md
- docs/research/edge-model.md
