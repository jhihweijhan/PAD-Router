# Wi-Fi continuous screen streaming feasibility for PAD Router

**Scope:** assess whether a persistent Android screen stream can reduce capture and
recognition latency without increasing device, host, network, or image-quality
risk enough to make PAD Router less reliable.

**Recommendation (2026-08-30):** run a bounded H.264 pilot, but do not replace
`adb exec-out screencap` in the safety-critical gesture path yet. Use a stream
first for preview and candidate recognition; keep fresh ADB captures for
pre-gesture, hold, and post-gesture verification until freshness, recognition,
and game-performance gates pass on the target phone. A full-screen, high-FPS
stream is likely to reduce capture wait while adding sustained encoder and
transport load. A cropped, low-FPS hardware H.264 stream is the only option
worth piloting now.

Take the lossless raw-crop win first. The board occupies full-width rows
`1380..2279` of the raw framebuffer, so it is already one contiguous byte range;
transferring only those rows measured `1.38–1.46 s` against `1.8–3.0 s` for the
full frame. That is three lines in `screenshot()`, zero recognition risk, and it
is the baseline any stream must beat — not the `2.5 s` full-frame number.

This document is research only. It does not change the Python or JavaScript
runtime. Claims are separated into **source fact**, **local observation**,
**inference**, and **measurement still required**.

## Decision in one page

| Option | Latency | Device/host/network cost | Image and safety risk | Scope | Decision |
| --- | --- | --- | --- | --- | --- |
| On-demand raw ADB screenshot | High and variable in this setup | Cost is bursty; no always-on encoder | Lossless RGBA; existing safety semantics | Already implemented | Keep as fallback and safety authority |
| Cropped raw ADB screenshot | `1.38–1.46 s` measured, versus `1.8–3.0 s` full frame | Same bursty cost, 2.6x fewer wire bytes | Lossless; identical safety semantics | Three lines in `screenshot()` | **Do this first; it is the real baseline** |
| MJPEG server over Wi-Fi | Latest-frame retrieval can be fast after startup; jitter depends on the server and Wi-Fi | High bandwidth; JPEG encoding and decoding are continuous; host must parse multipart JPEG | JPEG artifacts can change HSV/glyph features; frame freshness must be proven | Requires an Android stream app or helper | Do not make it the first implementation |
| H.264 stream through scrcpy/ADB | Potentially one frame interval plus transport/decode instead of a new full screenshot command | Hardware encoding is available on the target, but continuous load is real; H.264 greatly reduces wire bytes | Lossy temporal artifacts, dropped/stale frames, and decoder queue can produce a visually plausible but unsafe old frame | Requires a frame-consumption adapter and a decoder/sink | **Pilot, with crop, low FPS, no playback buffer, and raw safety fallback** |
| Custom MediaProjection + MediaCodec service | Full control over crop, timestamps, and protocol | Highest engineering and lifecycle cost | Can be made explicit, but permissions, service death, and resize handling are new failure modes | Adds an Android component absent from this repository | Defer until a scrcpy-based pilot proves the need |

### Direct answer

- **Delay:** likely better for screen refresh and planning, but measure against
  the cropped baseline, not the full-frame one. The bottleneck is specifically
  Wi-Fi *transport* — `0.37–0.41 s` of device capture against `1.57–2.56 s` of
  transfer — not the display read and not pixel classification. Streaming cannot
  remove route movement time or any raw ADB verification retained for safety.
- **Load:** likely worse if the stream is always-on at full `1080x2340`, high FPS,
  and default settings. Hardware H.264 and a board crop can make the added load
  acceptable, but only a controlled A/B run can establish that.
- **Quality:** raw ADB is lossless. H.264/MJPEG can make human viewing smoother
  while making cell recognition worse. The acceptance metric must be board-cell
  accuracy and `unknown`/mismatch behavior, not visual smoothness.
- **Best tradeoff:** stream the smallest board region at 15–30 FPS, keep only the
  newest decoded frame, and use the stream as an optional accelerator. Any stale,
  missing, unstable, or unrecognized frame must stop or fall back; it must never
  silently become an executable board.

## Current PAD Router path

The relevant flow is:

```text
ADB exec-out screencap (raw RGBA)
  -> BoardInspectionController
  -> calibration and HSV/local-feature recognition
  -> unknown/manual review
  -> confirmed board and route evaluation
  -> play(): baseline screenshot, DOWN, hold screenshot, MOVE sequence,
     post-gesture screenshot, release
```

Current code evidence:

- `pad_router.py:screenshot()` invokes `adb -s SERIAL exec-out screencap`, checks
  the RGBA header and exact `width * height * 4` payload, then returns the raw
  pixels.
- `BoardInspectionController.capture_device()` captures once and recognition
  retries reuse the same pixels; retries do not silently recapture a changing
  screen.
- `pad_router.py:play()` captures a baseline, verifies the initial hold with a
  second capture, executes the route, and captures again before release for
  board verification. Correction moves can add more captures.
- The architecture explicitly rejects unknown boards and preserves safe release
  and post-gesture verification. A stream integration must preserve these
  invariants.

### Local observations on the target setup

These are measurements from the current target device and repository harness,
not general Android guarantees. The capture-decomposition rows were taken on
2026-08-30 against `192.168.5.150:5555`, three runs each, with an uncontrolled
screen scene — raw payload size is content-independent, but the PNG size and
encode time are not:

| Measurement | Observation | Interpretation |
| --- | ---: | --- |
| Device | Samsung SM-A1560, Android API 36 / release 16 | Target is new enough for current Android MediaProjection rules |
| Physical display | `1080x2340` | Full-screen streaming has a large source surface |
| Raw frame payload | `10,108,800` bytes | One RGBA frame is about 9.64 MiB before command overhead |
| `adb exec-out screencap` | about `1.8–3.0 s` per call in repeated runs | Current capture/transport dominates local detection |
| Device-side `screencap` alone | `0.37–0.41 s`, captured to `/dev/null` on device | Only ~15–20% of the call; the display read is not the bottleneck |
| Wi-Fi transfer of one raw frame | `1.57–2.56 s` via `exec-out cat` of a pre-captured file | ~80% of the call is moving 10.1 MB over Wi-Fi; ~35–50 Mbps effective |
| `adb exec-out screencap -p` (PNG) | `2.42–2.79 s`, `2,834,883` bytes | **Slower than raw despite 3.6x fewer bytes**: device-side PNG encode costs ~1.5–2 s. Rejected |
| Cropped raw board rows, `900x1080` | `1.38–1.46 s`, `3,888,000` bytes | Lossless 1.6x win with no decoder, no dependency, no new failure mode |
| Local pixel detection | about `0.047 s` | Replacing the detector is not the first latency lever |
| PNG encoding for UI snapshot | about `0.527 s` | Separate presentation cost; not the raw ADB capture cost |
| Safe no-motion play harness | about `5.756 s`, with three screenshots | Replacing all three captures could matter, but only if safety remains equivalent |
| 80-point route at 40 ms move delay | about `3.354 s` | Streaming cannot remove intentional gesture timing |
| Default calibrated board geometry | `1080x900` board region on the `1080x2340` screen | The board is about 38.5% of full-screen pixels; cropping is a meaningful load lever |
| Hardware encoder query | `c2.mtk.avc.encoder` (H.264 hardware) and `c2.mtk.hevc.encoder` present | H.264 pilot is technically available on this phone |

One `adb shell top -b -n 1 -m 8` snapshot while an existing scrcpy session was
active showed the game process at 115% CPU, the scrcpy server at 36.3%,
`surfaceflinger` at 36.3%, the vendor AVC codec process at 30.3%, and
`mediaswcodec` at 24.2% on an eight-logical-CPU device. This is an **observed
concurrent snapshot, not a causal A/B measurement**: stream settings, audio,
current game scene, thermal state, and other processes were not controlled. It
is enough to treat continuous streaming load as a first-class risk, not enough
to reject H.264 before an isolated run.

### Cheaper lossless levers, measured before streaming

The observation table above decomposes the capture call, and the decomposition
changes which lever comes first:

- **Device-side capture is not the problem.** `screencap` to `/dev/null` on the
  phone runs in `0.37–0.41 s`. Everything else is wire time.
- **Device-side PNG is a trap.** `screencap -p` cuts the payload to `2.83 MB` but
  measured `2.42–2.79 s`, *slower* than raw, because the phone spends ~1.5–2 s
  encoding. Compressing on the device only helps if the encoder is hardware.
- **A lossless crop is free.** With the default geometry (`--top 1380`,
  `--cell 180`, `ROWS=5`) the board is rows `1380..2279` at full width. Raw
  framebuffer rows are contiguous, so the board is one byte range —
  `16 + 1380*1080*4` through `+ 900*1080*4`:

  ```sh
  adb exec-out 'screencap | tail -c +5961617 | head -c 3888000'
  ```

  Measured `1.38–1.46 s`. Every sampling window stays inside the crop: cell
  centres start at `y=1470` and `_cell_features` reaches at most `radius=55`,
  `cell_visual_change` at most `±30`.

A stream's realistic floor is ~50–100 ms per frame, so it is still roughly 10x
better than the crop — but 10x, not the 20x implied by comparing against the
uncropped `2.5 s`. Phase 0 should quote the improvement over the crop.

## Why a stream can reduce delay

The approximate critical paths are:

```text
on-demand ADB:
  process/startup + display read + full raw transfer + parse + detect

persistent stream:
  continuous display capture + encode + network + decode + newest-frame lookup + detect
```

The stream pays capture/encode continuously, but removes the need to start a
new screenshot command and transfer another full raw frame at the moment the
controller requests one. With three raw frames, the current measured payload
alone is about `30,326,400` bytes per play — `11,664,000` bytes if cropped to the
board rows. A configured 4 Mbps encoded stream
transmits about 0.5 MB/s while active; that arithmetic predicts lower wire
volume, not a guaranteed lower end-to-end latency.

The stream only wins if its queue is bounded. A decoder queue that preserves all
frames can make the application inspect an old frame while the UI looks smooth.
The consumer should drop old frames and expose at least:

- source/decoded monotonic timestamp;
- sequence number;
- dimensions and crop/orientation;
- decode time and frame age;
- dropped-frame and reconnect counters;
- a health state that cannot be mistaken for a valid `Screenshot`.

For safety, a frame that is merely the latest *received* frame is not necessarily
a fresh display observation. scrcpy's documented default behavior also produces
frames when the surface changes, so a static screen may not generate a new packet
at every requested instant. This is another reason to keep an explicit ADB
capture around the gesture boundary until the stream protocol has a tested
freshness handshake or periodic-repeat policy.

This is not a hypothetical. `play()` verifies the initial hold by comparing the
baseline and held captures with `cell_visual_change(...) >= lift_threshold`
(default `12.0`). If both reads resolve to the same stale decoded frame — the
normal case for a settled board that produced no new packet — the change is ~0
and `play()` reports `hold_not_verified` and returns `False`. The failure is
safe, but it means a stream without a freshness handshake is not merely risky in
the gesture path, it is *unusable* there: it would abort every play. Treat that
as a precondition on Phase 3, not a tuning problem.

## Load and quality tradeoffs

### Device

- H.264 hardware encoding avoids the worst software-encoder path, but it still
  consumes encoder, compositor, memory-bandwidth, and power budget.
- Full-screen high-FPS capture competes with the game and can increase frame time,
  thermal throttling, battery drain, and animation instability.
- That load is second-order, not just a comfort cost: throttling slows the game's
  own orb-fall and cascade animation, which shifts the moment at which `play()`'s
  post-gesture capture sees a settled board. The stream's load can therefore
  corrupt the very frame the stream is being asked to verify. Any A/B run must
  record game frame-time *and* settle-time drift, not only host-side latency.
- Cropping to the board before encoding and limiting FPS reduce the source work.
  The default board region is only about 38.5% of the full display area. This
  ratio is a geometry bound, not a measured bitrate reduction.
- Continuous streaming is more expensive during idle periods than on-demand
  capture. Stop it when no preview/planning task needs it, or use a low-rate idle
  mode.

### Host

The current project has only `pywebview==5.4` beyond the standard library. It
has no H.264 or MJPEG frame decoder dependency. Consuming scrcpy through a CLI
sink or FFmpeg can be a disposable prototype, but embedding a decoder or
parsing scrcpy's internal protocol is a separate integration decision. scrcpy's
own documentation calls the client/server protocol internal and requires matching
versions; depending directly on private packet details would create avoidable
upgrade risk.

A host decoder can also compete with the GUI worker and snapshot path. The
controller must not block the webview event loop while waiting for a frame.

### Network

Raw ADB over Wi-Fi transfers large bursty frames. H.264 gives a bounded configured
bitrate and usually uses much less wire volume, but packet loss becomes frame
loss or decoder recovery rather than a single slow request. A stream must prefer
the newest complete frame, bound the queue to one frame, and reconnect without
reusing an old frame as current.

Existing wireless ADB is already an authenticated control boundary. Do not add an
unauthenticated HTTP MJPEG endpoint bound to all LAN interfaces. Prefer ADB
forwarding/loopback or an explicitly authenticated and encrypted endpoint.

### Recognition quality

The recognizer uses raw BGRA pixels, calibrated cell coordinates, HSV palette
features, glyph/marker features, and an `unknown` outcome. Compression can alter
saturation, value, edge contrast, and small glyphs—especially locked/enhanced
markers, hazards, highlights, and transition frames. H.264's temporal prediction
can also preserve a plausible old region during motion.

The exposure is uneven, and the safe half is the half that already fails closed:

| Feature | Where | Compression exposure |
| --- | --- | --- |
| Normal colour | `_normal_color`, hue-dominant (weight `1.4`) over a ~500-sample median | Most robust; a bad frame yields `None` → `unknown` → `_board_is_routeable` rejects. Degrades to *slower*, not *wrong* |
| `'+'` marker | `_cell_features`, ~40–50 samples in the marker box needing `hue 0.10–0.20, s>0.55, v>0.70`, count `>= max(3, 8%)` | Highest risk. 4:2:0 chroma subsampling plus ringing on a small high-contrast glyph. **No `unknown` fallback** — a flipped `'+'` silently changes the expected board and surfaces only as a post-gesture mismatch |
| dark(5) vs heart(6) | `_normal_color` tiebreak via `center_hue` and `center_pattern` | Prototype hues differ by only `0.08`; the tiebreak reads a 25-point luminance grid and its edge magnitudes, which is exactly what deblocking smooths away |
| Hazards | `_hazard_kind` on per-pixel *fractions* (`bomb` needs `orange >= 0.025`, ~13 samples) | Fraction-of-pixels thresholds are far more fragile than medians; a missed hazard usually degrades to `unknown` (safe), a false positive does not |

The baseline is already tight: the comment in `_normal_color` records that the
`'+'` flash swings saturation and value by up to `0.25` **between two lossless
captures of the same board**. There is no headroom to spend on codec noise.

Worse, the timing is adversarial. All three of `play()`'s captures land during
finger drag or immediately after a cascade — peak motion, where H.264 quality is
lowest and board correctness matters most.

Therefore:

1. compare decoded frames with near-time raw ADB frames, not screenshots viewed by
   a human;
2. measure per-cell exact accuracy and `unknown` rate for every supported visual
   state;
3. require two independent settled frames where the existing safety policy calls
   for them;
4. never turn a stream frame into `Confirmed Board` or an executable route merely
   because it is decodable.

## Implementation choices

### 1. Keep raw ADB as the reference

This is the correctness baseline: lossless pixels, current retry semantics, and
no new decoder or Android component. Optimize around it rather than deleting it.
It remains the fallback when a stream disconnects, is stale, or produces an
uncertain board.

Optimizing it is cheap and unfinished: cropping the raw transfer to the board's
contiguous rows is lossless and already measured at `1.38–1.46 s` versus
`1.8–3.0 s`. Land that before spending effort on a codec.

### 2. MJPEG

MJPEG is conceptually simple: an HTTP multipart stream holds JPEG frames and the
client can select the latest complete image. A historical Appium Pro experiment
reported materially faster screenshot retrieval from an Android MJPEG server than
repeated ADB screenshots, but its numbers are old, host-specific, and not a
benchmark for this phone. Treat it as directional evidence only.

The costs are continuous JPEG encoding, larger bandwidth at useful quality,
multipart parsing, and lossy artifacts. A ready-made screen-stream app also adds
installation, permission, lifecycle, and trust-surface work. It is useful as a
quick latency prototype, not the preferred production path.

### 3. H.264 through scrcpy/ADB

This is the preferred pilot:

- the target exposes a vendor hardware H.264 encoder;
- scrcpy uses a device-side server, separate video/control sockets, and H.264 by
  default;
- its documented default is no video buffering, while optional buffering trades
  latency for jitter smoothing;
- its documented controls include maximum size, bitrate, FPS, codec, and crop;
- H.264 is documented as lower latency than H.265 for this use.

Start with a board crop and no audio, not the default full-screen desktop stream.
A disposable consumer can use a scrcpy sink/FFmpeg process to prove timing and
quality before the repository grows a decoder dependency. Do not parse the
private scrcpy protocol as a permanent API without accepting version coupling.

### 4. Custom MediaProjection + MediaCodec

A custom Android helper can emit exactly the crop, timestamp, and packet format
needed by PAD Router. It is not a small Python-only change. On Android 14 and
later, each MediaProjection session requires user consent; apps targeting Android
14+ need foreground-service and `mediaProjection` declarations; the service must
handle projection stop, screen lock, rotation, resize, and resource release. The
target is API 36, so these are current constraints, not legacy edge cases. Build
this only if the scrcpy pilot cannot expose the required frame semantics.

## Measurement plan

### Baseline and scenarios

Run every case on the same phone, game scene, display orientation, Wi-Fi AP, and
battery/thermal state. Repeat each scenario enough to report p50/p95/p99 rather
than one favorable run.

| Case | Transport/settings | Purpose |
| --- | --- | --- |
| A0 | Current raw ADB, full frame, no stream | Historical reference |
| A0c | Cropped raw ADB board rows, no stream | **The real baseline.** Lossless, already measured at `1.38–1.46 s`; every stream case must beat this, not A0 |
| A1 | Raw ADB plus scrcpy preview | Isolate the cost of an always-on stream without changing safety capture |
| H1 | H.264, hardware encoder, board crop, 15 FPS, low bitrate | Lowest-cost pilot |
| H2 | H.264, board crop, 30 FPS, medium bitrate | Quality/latency tradeoff |
| H3 | H.264, full screen, 30 FPS | Upper-bound cost; likely rejection case |
| M1 | MJPEG only if readily available | Compare simplicity and JPEG quality, not assume it wins |

Use static settled boards, animated orb transitions, highlights, locked/enhanced
orbs, hazards, dim/bright scenes, orientation changes, and Wi-Fi disturbance.
Reuse the existing labelled recognition corpus and add time-aligned raw frames
for the stream cases.

### Required measurements

| Axis | Measurement | Suggested gate |
| --- | --- | --- |
| Capture latency | request-to-usable-frame p50/p95/p99; source-to-decode frame age | Must materially beat A0c (the cropped baseline); report tails, not only mean |
| End-to-end with fallback | latency of the *whole* request including a raw-ADB retry when the stream frame is stale, unknown, or disagrees; measured fallback rate | Must beat A0c on the mean too. Fallbacks are not independent of scene difficulty — animation, flash, and hazards are both the most-likely-unknown and the most-likely-compressed-badly cases, so a stream can be faster at p50 and slower on average |
| Queue health | queue depth, dropped frames, out-of-order frames, reconnect time | Queue bounded to one; stale frames are invalid |
| Recognition | exact cell accuracy, `unknown` rate, hazard/marker errors, settled-frame agreement | No incorrect executable board; no regression against A0 on held-out scenes |
| Device performance | game frame-time p50/p95, game CPU, encoder/compositor CPU, memory, temperature, battery drain | Reject visible quality loss or sustained frame-time regression; start with a 5% p95 regression budget |
| Host performance | decoder CPU, PAD Router RSS, GUI event latency, decode failures | Must not block the bridge/webview or starve route workers |
| Network | actual Mbps, jitter, packet loss, ADB/control responsiveness | No unbounded backlog; test AP distance and interference |
| Safety | stale/disconnected/unknown/mismatched frame behavior, release on every error | Any health failure stops/falls back; never executes from stale data |

The 5% frame-time budget is a pilot starting point, not a universal Android
quality standard. Tighten it if the game visibly degrades or if the target has a
stricter gameplay requirement.

### Controlled A/B procedure

1. Record A0 with the stream stopped: raw capture timing, recognition result,
   route eligibility, and game frame-time/temperature.
2. Start one stream configuration; wait for a healthy decoded frame; discard warmup
   samples; record the same scenarios.
3. Pair stream frames with a near-time raw ADB reference. Compare cell labels and
   unknowns, not only image hashes.
4. Run at least a 10-minute active session and a 10-minute idle/settled session;
   record thermal drift and game frame-time tails.
5. Inject or observe Wi-Fi jitter/disconnects. Confirm the controller rejects stale
   frames and either falls back to ADB or stops safely.
6. Repeat the best configuration after stopping/restarting the stream and after
   device rotation/app transition. A stream that only works from a warm process is
   not production-ready.

## Staged adoption plan

### Phase 0 — land the lossless crop, then instrument

Two steps, in order:

1. Crop `screenshot()` to the board's contiguous framebuffer rows. Lossless,
   three lines, no new dependency, measured 1.6x. This is not part of the stream
   experiment; it is the win that exists regardless of how the pilot turns out.
2. Add a disposable benchmark that records the metrics above for A0c and an
   external scrcpy consumer. Do not alter `play()` or its raw verification.

This phase answers whether the target still has a useful latency margin *after*
the free win is taken.

### Phase 1 — preview-only H.264 pilot

Run hardware H.264 with no audio, no playback buffer, board crop, and a bounded
newest-frame queue. Feed only the UI preview or non-authoritative candidate
recognition. Mark every frame with age/sequence/health. Keep `capture_device()`'s
raw ADB path and keep `play()` unchanged.

### Phase 2 — stream-assisted planning

If recognition and load gates pass, allow the stream to reduce the wait for
planning/preview. A stream disconnect, stale frame, dimension change, orientation
change, unknown cell, or disagreement returns to raw ADB/manual review. No silent
fallback may preserve an old `Confirmed Board` as current.

### Phase 3 — blocked, not merely deferred

Do not schedule this. `play()`'s hold check compares two captures taken around
the DOWN event; a surface-change-driven stream returns the same stale frame for
both on a settled board, so `cell_visual_change` reads ~0 and every play aborts
with `hold_not_verified`. Phase 3 is therefore blocked on a stream protocol that
can prove per-request frame freshness — a handshake or a periodic-repeat policy —
not on tuning bitrate or FPS.

Even once unblocked, the post-gesture raw verification should remain until the
stream has demonstrated equivalent safety under dropped frames, rotation,
cascade animation, and reconnects, with two-settled-frame agreement and the
`'+'`/dark-heart/hazard accuracy gates from the recognition table met on
held-out scenes.

## Go / no-go conclusion

**Do first, independent of the pilot:** crop the raw ADB transfer to the board
rows. Lossless, three lines, measured `1.38–1.46 s` against `1.8–3.0 s`. Device
PNG (`screencap -p`) is rejected — measured *slower* than raw at `2.42–2.79 s`.

**Go:** H.264/scrcpy is technically feasible on the target and is worth a
preview-only pilot. The hardware encoder and the remaining ~1.4 s capture cost
make a latency win plausible, though the honest headroom against the cropped
baseline is ~10x, not the ~20x the uncropped number suggests.

**Blocked, not deferred:** replacing `play()`'s captures needs a freshness
handshake, because a surface-change-driven stream makes the hold check abort on
every settled board. That is a protocol precondition, not a tuning exercise.

**No-go for immediate full replacement:** the target already showed substantial
concurrent game/compositor/codec load during an active scrcpy session, albeit in a
confounded snapshot. The current recognizer is sensitive to raw pixel features,
and the safety path deliberately verifies fresh state. Full-screen persistent
streaming without crop, queue bounds, frame-age checks, and A/B evidence could
make the UI feel faster while increasing game degradation or executing from stale
state.

The boring safe choice is therefore hybrid: **H.264 stream for optional preview and
planning; raw ADB for authoritative safety until measured evidence proves the
stream equivalent.**

## Primary sources

- [Android Debug Bridge (ADB), Android Developers](https://developer.android.com/tools/adb)
- [Media projection, Android Developers](https://developer.android.com/media/grow/media-projection)
- [MediaCodec API reference, Android Developers](https://developer.android.com/reference/android/media/MediaCodec)
- [Media codecs and hardware acceleration, Android Developers](https://developer.android.com/media/optimize/performance/codec)
- [scrcpy video configuration](https://github.com/Genymobile/scrcpy/blob/master/doc/video.md)
- [scrcpy developer architecture and protocol](https://github.com/Genymobile/scrcpy/blob/master/doc/develop.md)
- [Android screencap source](https://android.googlesource.com/platform/frameworks/base/+/master/cmds/screencap/screencap.cpp)

Secondary directional reference:

- [Appium Pro: Speeding Up Android Screenshots With MJPEG Servers](https://www.appiumpro.com/editions/83-speeding-up-android-screenshots-with-mjpeg-servers)
