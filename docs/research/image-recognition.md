# PAD 5x6 basic-orb recognition research

**Scope.** This is an implementation recommendation only; `pad_router.py` was
not changed.  Sources were retrieved with Firecrawl on 2026-08-27.  The
external technical sources below are primary documentation unless a source is
explicitly labelled otherwise.

## Current failure and decision

`detect_board_pixels` currently samples a square extending 55 pixels in every
direction, averages HSV across all chromatic pixels, greedily clusters the
resulting `CellFeatures`, then applies a fixed calibrated-hue cutoff of 0.10.
That combines orb body, edge shading, shine, icon, and any enhanced marker in
one mean.  A normal orb therefore becomes `unknown` when its averaged hue is
pulled beyond the calibration cutoff; the reported board pattern is consistent
with that failure rather than evidence of a seventh normal class.

**Recommendation: retain the standard-library-only implementation, replace the
feature with a robust masked HSV patch statistic, and classify directly against
six persisted prototypes.**  Do not add OpenCV or a ML package for this fixed,
aligned 30-cell board.  Keep hazards as the existing separate path.  This is
the smallest design that removes the two unstable steps: pixel averaging across
the whole cell and an unlabeled, first-seen greedy cluster center.

The present graph-backed code inspection found `detect_board_pixels`
(`pad_router.py:387-426`) called from `detect_board` and `self_check` (and
transitively main/play).  Its support routines define the current full-square
sampling (`_cell_features`, 271-308), feature distance (322-326), hazard
classification (311-319), and fixed hue mapping (329-335).  Index generation
was `2026-08-27T13:11:36Z`; `pad_router.py` had no recorded coverage gap.  That
coverage signal is best-effort, not proof of source completeness.

## Evidence and approach comparison

| Approach | What the sources establish | Fit here | Decision |
| --- | --- | --- | --- |
| HSV/Lab segmentation plus a robust patch statistic | OpenCV documents BGR↔HSV and BGR↔Lab conversion; Python's `colorsys.rgb_to_hsv` provides the already-used HSV conversion. HSV hue is circular, so it needs circular handling around red. | Strong. The grid is known and the six basic types are primarily colour-distinguished. A central icon, specular highlight, rim, and enhanced `+` are outliers that a mask plus median/mode avoids. | **Use HSV now.** Lab is a useful later A/B feature, but requires adding a conversion implementation or a dependency with no demonstrated need. |
| Reference-template normalized cross-correlation (NCC) | OpenCV's `matchTemplate` compares a template with overlapping image regions and documents normalized correlation methods, including `TM_CCOEFF_NORMED`; its own multiple-object example thresholds a score. | Useful for board/cell locating or a fixed-device fallback. Per-orb templates need variants for every normal orb, enhanced overlay, animation, scale, game art/theme, and device rendering. It is less tolerant of the visual variation that caused the report. | Do not make this the basic-orb classifier. Consider it only as an optional board locator or a last-resort, device-specific confidence fallback. |
| Per-board adaptive clustering, anchored by labelled prototypes | Clustering can group visual variants but produces no names by itself. The current version also permits fewer than six current-board clusters, which is correct for absent colours but makes class naming depend on a separately fixed calibration. | Better than raw greedy clustering only if anchors constrain it. A board may omit classes, and a greedy first representative is sensitive to cell order/outliers; neither supplies a robust calibration. | Do not use free clustering as the primary decision. Optionally estimate one small **global** colour offset from only high-confidence prototype matches after corpus tests show a systematic device/display shift. Never require all six classes. |
| Lightweight learned classifier: k-NN / linear model | scikit-learn documents `KNeighborsClassifier` training, nearest-neighbor queries, and probability prediction; it also warns that equal-distance conflicting labels depend on training order. | A labelled screenshot corpus could support k-NN on the same robust features. A six-prototype nearest-distance rule is effectively the simpler, deterministic 1-NN-centroid version. | Skip ML and its dependency now. Revisit k-NN only if a held-out corpus shows overlapping distributions after the robust prototype design, with a reproducible accuracy/confidence target. |

Primary sources:

- [OpenCV: Changing Colorspaces](https://docs.opencv.org/4.x/df/d9d/tutorial_py_colorspaces.html) — documented BGR↔HSV conversion and colour-threshold workflow.
- [OpenCV: Color conversions](https://docs.opencv.org/4.x/de/d25/imgproc_color_conversions.html) — documented colour-conversion reference, including Lab conversions.
- [Python `colorsys`](https://docs.python.org/3/library/colorsys.html) — standard-library RGB↔HSV functions, with each component in 0–1.
- [OpenCV: Template Matching](https://docs.opencv.org/4.x/d4/dc6/tutorial_py_template_matching.html) and [its `matchTemplate` API](https://docs.opencv.org/4.x/df/dfb/group__imgproc__object.html) — sliding template comparison, normalized correlation options, and score thresholding.
- [scikit-learn `KNeighborsClassifier`](https://scikit-learn.org/stable/modules/generated/sklearn.neighbors.KNeighborsClassifier.html) — supervised nearest-neighbour alternative and its tie caveat.

The recommendation from those sources is an engineering inference for this
specific, pre-aligned PAD board, not a claim that the sources prescribe a PAD
classifier.

## Minimum robust design

### 1. Crop and mask

For every calibrated grid centre, sample an orb-local crop rather than the
whole cell:

- Use a circular/annular body mask at about `0.30–0.70 * cell_size` radius.
  Exclude the rim/background outside it and the centre icon inside it.  Derive
  these radii from the corpus rather than hard-code them as universal.
- Exclude the lower-right enhanced-marker rectangle from the **palette** mask;
  retain its current dedicated `+` detector or make a separate small mask for
  it.
- Discard low-saturation and near-black pixels before colour summarisation.
  This removes black glyph/background pixels without pretending a dark orb is
  black; the remaining value statistic distinguishes dark and heart when hue
  is close.

### 2. Feature and prototypes

Use `colorsys.rgb_to_hsv` on retained pixels.  Record a compact, reproducible
feature: weighted circular hue-bin mode (or circular median) plus median
saturation and median value.  Weight hue contributions by saturation; do not
take an ordinary arithmetic mean of hue.  A hue histogram avoids the 0/1 red
seam and is robust to isolated shine/icon pixels.

Persist one labelled prototype per basic orb: `(hue, saturation, value)` plus
an allowed distance/quantile learned from calibration examples.  Classify each
cell by the nearest prototype using circular hue distance and scaled S/V
distance.  The output is one of the six basic types only when it passes the
class's absolute limit; otherwise it remains an explicit low-confidence
`unknown`.  The root rule is direct labelled prototype comparison, not
clustering.  A standard-library JSON file is sufficient for prototypes and
thresholds; no new package is needed.

Initial calibration should collect at least 15 visually varied, manually
labelled cells per basic type from the target Android device.  Store the robust
prototype as per-component median (circular for hue) and set each absolute
limit to a held-out/leave-one-out high percentile of its own class distance,
then verify it against nearest-other-class separation.  The close
heart/dark-purple pair must have separately verified S/V and hue margins.

### 3. Confidence and temporal stabilization

Return `(label, best_distance, runner_up_distance, confidence_reason)`
internally.  Accept only if both apply:

1. `best_distance <= class_limit`; and
2. `runner_up_distance - best_distance >= margin`.

This prevents a barely-nearest class from becoming a confident solver input.
For an idle board, capture two or three screenshots after the game animation
has settled.  Accept a cell only when a high-confidence label agrees in a
majority (or two sequential frames agree); otherwise recapture once and report
the cell/feature/distance rather than route a board with guessed colours.
Do not temporally blend frames during a drag or cascade, because that creates a
nonexistent board.

### 4. Bounded adaptive correction (not the first patch)

If corpus results reveal a repeatable display-wide hue shift, calculate one
bounded global circular hue offset from high-confidence direct matches and
rerun the direct comparison.  Cap the offset based on validation; never infer
a correction from low-confidence cells, never create labels from clusters, and
never assume all six types occur.  This is a calibration refinement, not a
replacement classifier.

## Offline corpus and acceptance test

Add a versioned, offline corpus before changing thresholds:

```text
tests/fixtures/pad_orbs/
  manifest.json                 # screenshot filename, device/display metadata, 30 labels
  raw/<shot-id>.rgba            # original adb RGBA_8888 bytes plus width/height
  expected/<shot-id>.json       # grid, basic label, + flag, hazards where present
```

Use raw `adb exec-out screencap` captures, not lossy/composited screen photos.
Include a calibration split and a held-out split; never set a class limit from
the held-out split.  The initial corpus should contain at least 20 stationary
screenshots (600 cell labels), deliberately covering each normal type,
enhanced variants, boards missing one or more types, close dark/heart cases,
different board positions, brightness/display modes, and normal PAD visual
effects.  Preserve hazard examples to ensure the normal-mask change does not
weaken their existing detection.

The one offline assertion should decode every held-out screenshot and compare
all 30 ground-truth basic labels (and the existing hazard/+ expectations).  It
must report: per-class recall, total cell accuracy, unknown rate for genuine
normal cells, and any high-confidence wrong label.  Proposed release gate:
zero high-confidence basic-orb mislabels and no unexplained normal-orb
unknowns on the held-out corpus; otherwise leave the board unrouted and expand
the calibration/corpus instead of widening a global cutoff.

## Implementation order

1. Capture and label the offline corpus; inspect per-class robust-feature
   distributions, especially heart versus dark.
2. Implement only the local masked robust HSV feature and persisted direct
   prototypes; retain hazard and `+` code paths.
3. Add the corpus assertion and tune class limits/margins against the
   calibration split, then run held-out verification.
4. Add the two-frame idle-board agreement gate.  Only if held-out results show
   a measured global shift should the bounded adaptive offset be considered.

Skipped: OpenCV/NCC templates, free per-board clustering, and ML.  Add one only
when the corpus demonstrates that the direct, robust standard-library
classifier cannot meet the release gate.
