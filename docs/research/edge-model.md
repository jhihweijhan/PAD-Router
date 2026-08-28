# Edge and local-model research for PAD orb recognition

**Recommendation:** do not add an “edge-calculation-level AI model” to the
current recognizer.  Keep the calibrated HSV/prototype classifier recommended
in [the image-recognition research](image-recognition.md); add edge/contour evidence only as a
measured, optional *reject/structure* feature after its corpus is available.
For a genuinely new orb visual, the safe outcome is `unknown` and no drag—not
an automatic new label.  A trained model can learn a later labelled class, but
edges alone cannot name an unseen class or make closed-set classification safe.

This is research only: `pad_router.py` and the existing reports were not
changed.  Source retrieval used Firecrawl and Context7 on 2026-08-27.  The
Firecrawl `firecrawl_research_search_papers` endpoint was unavailable (HTTP
404); its normal page-scrape endpoint succeeded for the cited OpenCV,
TensorFlow, ONNX Runtime, and arXiv pages.  Context7 successfully resolved and
queried current OpenCV 4.13, TensorFlow, and ONNX Runtime documentation.

## What this adds to the existing HSV recommendation

The existing report correctly treats the fixed, calibrated 5x6 board as a
colour-first problem: robust masked HSV features, labelled prototypes, an
absolute distance limit, runner-up margin, and an `unknown` result.  Current
code now reflects that direction: `_cell_features` uses central annular HSV
palette features while retaining broad glyph metrics and a separate lower-right
yellow marker; `detect_board_pixels` checks hazards first, applies six HSV
prototypes, and makes low-confidence normal cells `unknown`.

Edge/contour data can improve *validation* where appearance differs while
shape remains diagnostic—for example, rejecting a cell whose interior glyph or
silhouette cannot plausibly be a normal orb, or distinguishing a known
hazard-like glyph.  It is not a replacement for palette information: normal
orbs share a near-circular silhouette, and Canny/contour results depend on
intensity thresholds, anti-aliasing, animation, highlights, and crop alignment.
OpenCV documents Canny as a multi-stage, thresholded intensity-gradient
procedure, including noise reduction and hysteresis; it therefore requires
device/corpus calibration rather than being a semantic detector.
[OpenCV Canny documentation](https://docs.opencv.org/4.x/da/d22/tutorial_py_canny.html)

## Decision table

| Option | Can classify now | Handles a labelled future type | Safely handles an unlabelled/unseen type | Cost in this ADB/Python flow | Decision |
| --- | --- | --- | --- | --- | --- |
| Existing masked HSV prototypes + confidence gate | Yes, for six calibrated colours | Yes, add examples/prototype and revalidate | Rejects it as `unknown`; cannot name it | Negligible, offline, no dependency | **Keep as primary.** |
| Canny/contour/Hu descriptors | Only if shape/glyph differs materially | Possibly, with per-type labelled thresholds | Can flag out-of-distribution shape, but cannot name it | Low CPU; requires OpenCV or equivalent code and threshold calibration | **Optional secondary reject feature only.** |
| Fixed templates / NCC | Yes, only for tightly matched art/device variants | Yes, but template variants grow with visual states | Can reject weak matches; cannot identify a new class | Low inference, high template maintenance | **Use only for a narrow fallback/locator experiment.** |
| Edge + colour hybrid | Yes, colour names plus shape sanity check | Yes, after labels and held-out validation | Rejects only when either signal is uncertain | Low–moderate; extra calibration | **Best classical fallback if HSV fails a measured gate.** |
| Small local CNN/embedding + prototype distance | Not without a labelled corpus | Yes, few labelled examples per type can form a new prototype | Only with explicit distance/unknown calibration; never from top-1 alone | Offline inference is feasible, but training, packaging, and calibration dominate | **Defer until corpus proves HSV+hybrid inadequate.** |
| Closed-set classifier / “edge AI” alone | May force a known label | Requires retraining or a new class prototype | **No.** Closed-set outputs do not make unknowns safe | Unjustified added complexity | **Do not use.** |

## Classical contours and templates

OpenCV exposes `findContours` on a binary image, moments on an image or
contour, seven Hu invariants, and `matchShapes` comparisons based on Hu
invariants.  Hu invariants are designed to be invariant to translation, scale,
and rotation (the seventh changes sign under reflection), which makes them
reasonable compact evidence for a deliberately segmented glyph or silhouette.
They do **not** establish object identity, segmentation quality, colour, or
robustness to the game changing its art.
[OpenCV shape API](https://docs.opencv.org/4.x/d3/dc0/group__imgproc__shape.html)

For this board, an annular colour crop leaves mostly the same circle for normal
orbs; use a contour only on a separately masked central glyph or thresholded
silhouette.  Measure simple features—edge density, contour area/circularity,
Hu distance to known normal/hazard references—and use them to *veto* a close
HSV match when the feature is outside its held-out range.  Do not invent a
normal colour from a contour score.  This avoids duplicating the existing HSV
classifier and preserves its useful explicit `unknown` state.

Template matching is also comparison, not discovery.  OpenCV describes
`matchTemplate` as sliding a template over overlapping image regions and
returning a comparison map; its own multiple-object example requires a score
threshold.  It is appropriate only when a template, scale, art, and state are
known.  Enhanced overlays, animations, display scaling, and a new orb each
need new templates, so NCC should not be the general 30-cell classifier.
[OpenCV template-matching tutorial](https://docs.opencv.org/4.x/d4/dc6/tutorial_py_template_matching.html)

**Smallest classical experiment, only if needed:** on the already grid-aligned
cell crop, retain HSV as the label signal and record two or three contour
measurements beside it.  Accept only when HSV's existing absolute-limit and
runner-up-margin checks pass *and* the contour is within the labelled normal
range; otherwise return `unknown`.  A correct test is false acceptance on
held-out hazards/new art, not merely edge-detection screenshots.

## Local learned models, embeddings, and open-set behaviour

A compact classifier or embedding model can run locally: TensorFlow Lite's
interpreter loads a model and invokes it locally, and integer quantization can
reduce model size and inference cost but requires a representative calibration
dataset.  ONNX Runtime also supplies a Python CPU package and an
`InferenceSession` API.  These runtime facts make local inference possible;
they are not evidence that a model is warranted here.
[TensorFlow Lite integer quantization](https://www.tensorflow.org/lite/performance/post_training_integer_quant)
[ONNX Runtime Python quickstart](https://onnxruntime.ai/docs/get-started/with-python.html)

An embedding/prototype design is the least complex learned option if the
classical gate fails: train a small crop encoder on labelled cell images, keep
one or more embeddings per visual class, and assign a class only below a
validated distance threshold and with a validated nearest/second-nearest
margin.  Prototypical Networks demonstrate the underlying pattern—classify in
a learned metric space by distance to prototypes, including few-shot settings.
That supports adding a *labelled* future orb with a few representative
captures; it does not support assigning a semantic name from no examples.
[Prototypical Networks for Few-shot Learning](https://arxiv.org/abs/1703.05175)

Open-set handling must be explicit.  A conventional classifier is closed set:
it selects among its trained labels even for an unrelated image.  OpenMax was
proposed precisely to estimate unknown probability from penultimate-layer
activations and to reduce those forced errors, rather than trusting softmax
thresholding alone.  For this safety-critical action path, use the simpler
operational equivalent first: calibrated class-distance limit + runner-up
margin + known-unknown validation set + temporal agreement.  A learned
open-set method is a later experiment, not a licence to route an uncertain
board.
[Towards Open Set Deep Networks](https://arxiv.org/abs/1511.06233)

## Data, calibration, and safety gate

Capture raw `adb exec-out screencap` frames and label all 30 cells, visual
class, colour (where applicable), hazards, plus state, grid calibration,
device/display mode, and whether the board is settled.  Split by screenshot
session/device/theme—not random cells from the same screenshot—so the held-out
set contains real rendering variation.  Include six basic colours, enhanced
variants, hazards, animations/transitions, absent-colour boards, close
dark/heart cases, changed art, and deliberate non-board/unknown crops.

For each candidate feature/model, save raw crop hashes and the prediction,
best/second score, threshold version, and decision reason.  Calibrate
thresholds only on the training/calibration split.  Treat future orb captures
as initially `unknown`; after human labelling, add them as a versioned class
and re-run the whole held-out and known-unknown suite.  Never auto-label new
art from an edge or nearest-neighbour result.

The routing contract must stay simple:

1. Capture only after the board is visually stable; acquire two independent
   frames.
2. Require every one of 30 cells to have an accepted class/state in both
   frames, with the same board result.  Any `unknown`, disagreement, failed
   grid bound, hazard ambiguity, or model/runtime error means **no drag**.
3. Log the rejected frame and scores; recapture once, then return control to
   the user rather than relaxing thresholds.
4. After a drag, reacquire before the next decision; never assume the prior
   board remains valid through animation/cascade.

This is intentionally stricter than “best prediction.”  It turns model
uncertainty into a harmless no-op, which is the only acceptable default for an
ADB drag executor.

## Smallest phased recommendation and acceptance criteria

| Phase | Smallest deliverable | Go/no-go measurement |
| --- | --- | --- |
| 0 — baseline | Label a session-separated raw-screen corpus and run the existing HSV/prototype recognizer against it. | No high-confidence wrong normal label; record per-class recall, normal-as-unknown rate, and full-board acceptance rate. |
| 1 — safety | Enforce two settled-frame identical-board acceptance and `unknown => no drag`; retain current hazards/+ paths. | Across the held-out corpus and replay, **zero drags** are emitted for any unknown, disagreement, malformed crop, or injected recognizer failure. |
| 2 — classical hybrid (conditional) | Add the smallest contour/glyph veto feature only if Phase 0 has repeatable false accepts that HSV cannot separate. | It eliminates the targeted false accepts without increasing held-out normal-orb unknowns or introducing a high-confidence wrong label. |
| 3 — local embedding (conditional) | Benchmark a small quantized local crop encoder against the frozen corpus and hybrid baseline. | It materially improves the predefined error metric, meets the Android/ADB end-to-end latency budget measured on target hardware, remains offline, and still has zero unsafe drag decisions. |
| 4 — future visual class | Human-label new captures; add an embedding/prototype class or a narrowly scoped template only after validation. | New-class recall meets the agreed target and all previous classes/known-unknowns retain zero unsafe drag decisions. |

No model should be selected from an unmeasured latency claim.  In this workflow
ADB screencap/transport and the two-frame stability wait may dominate a tiny
local crop inference, so time the full capture → classify → gate path on the
target device before adding a runtime.  The smallest path is therefore to ship
no new AI dependency now, preserve HSV as the label source, and make the
existing `unknown` state an absolute route blocker.  Add contours only for a
specific measured failure; add a local embedding only when the labelled corpus
shows that HSV plus the narrow hybrid cannot meet the safety gate.
