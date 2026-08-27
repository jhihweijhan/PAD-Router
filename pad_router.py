#!/usr/bin/env python3
"""Solve the visible 5x6 PAD board, then optionally play its route over ADB."""

from __future__ import annotations

import argparse
import colorsys
import heapq
import math
import shlex
import struct
import statistics
import subprocess
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Iterable


ROWS, COLS = 5, 6
DIRECTIONS = ((-1, 0), (1, 0), (0, -1), (0, 1))
# Calibrated from the current SM-A1560 screenshot. Raw Android RGBA_8888 is
# little-endian BGRA, hence these values are sampled after swapping R and B.
# Each entry is (hue, saturation, value, maximum distance). These labels are
# fixed; a board cannot create a new normal colour from its own pixels.
ORB_PROTOTYPES = {
    1: (0.66, 0.82, 0.86, 0.30),  # fire
    2: (0.09, 0.82, 0.86, 0.30),  # water
    3: (0.25, 0.82, 0.86, 0.30),  # wood
    4: (0.52, 0.82, 0.86, 0.30),  # light
    5: (0.85, 0.58, 0.70, 0.27),  # dark
    6: (0.77, 0.57, 0.90, 0.27),  # heart
}
NAMES = {1: "fire", 2: "water", 3: "wood", 4: "light", 5: "dark", 6: "heart"}
HAZARDS = {"jammer", "poison", "bomb"}
NORMAL_MIN_MARGIN = 0.035
PLUS_MARKER_MIN = 0.5


@dataclass(frozen=True)
class Orb:
    """A detected orb; enhancement is metadata, never a new match colour."""

    kind: str
    color: int | None = None
    enhanced: bool = False
    visual_class: str | None = None


def orb_match_key(orb: object) -> int | str | None:
    """Return the PAD matching class, keeping + and hazards out of colours."""
    if isinstance(orb, Orb):
        if orb.kind == "normal" and orb.color in NAMES:
            return orb.color
        if orb.kind in HAZARDS:
            return orb.kind
        return None
    if isinstance(orb, int) and orb in NAMES:
        return orb
    return None


def orb_display(orb: object) -> str:
    key = orb_match_key(orb)
    if isinstance(orb, Orb):
        if orb.kind == "normal" and orb.color in NAMES:
            return "火水木光暗心"[orb.color - 1] + ("+" if orb.enhanced else "")
        return {"jammer": "J", "poison": "P", "bomb": "B"}.get(orb.kind, "?")
    return "火水木光暗心"[key - 1] if isinstance(key, int) else "?"


def board_report(board: tuple[tuple[object, ...], ...]) -> str:
    """Summarize detected visual classes and enhancement counts for operators."""
    counts: Counter[str] = Counter()
    enhanced: Counter[str] = Counter()
    for row in board:
        for orb in row:
            if isinstance(orb, Orb) and orb.kind == "normal" and orb.color in NAMES:
                name = NAMES[orb.color]
                counts[name] += 1
                if orb.enhanced:
                    enhanced[name] += 1
            elif isinstance(orb, Orb) and orb.kind in HAZARDS:
                counts[orb.kind] += 1
            elif isinstance((key := orb_match_key(orb)), int):
                counts[NAMES[key]] += 1
            elif orb:
                counts[getattr(orb, "visual_class", None) or "unknown"] += 1
    if not counts:
        return "(no recognized orb classes)"
    return ", ".join(
        f"{name} x{count}" + (f" ({enhanced[name]} enhanced)" if enhanced[name] else "")
        for name, count in sorted(counts.items())
    )


def settle(board: tuple[tuple[object, ...], ...], cascade: bool = True) -> tuple[int, tuple[tuple[object, ...], ...]]:
    """Return combos after gravity; set cascade=False to count only the direct clear."""
    b = [list(row) for row in board]
    # score() calls settle thousands of times during beam search. Cache the
    # semantic keys once per cell instead of re-decoding Orb metadata in every
    # horizontal/vertical/neighbour comparison.
    keys = [[orb_match_key(value) for value in row] for row in b]
    combos = 0
    while True:
        matched = [[False] * COLS for _ in range(ROWS)]
        for r in range(ROWS):
            for c in range(COLS - 2):
                if (keys[r][c] is not None
                        and keys[r][c] == keys[r][c + 1] == keys[r][c + 2]):
                    matched[r][c] = matched[r][c + 1] = matched[r][c + 2] = True
        for r in range(ROWS - 2):
            for c in range(COLS):
                if (keys[r][c] is not None
                        and keys[r][c] == keys[r + 1][c] == keys[r + 2][c]):
                    matched[r][c] = matched[r + 1][c] = matched[r + 2][c] = True
        if not any(map(any, matched)):
            return combos, tuple(map(tuple, b))

        seen = [[False] * COLS for _ in range(ROWS)]
        for r in range(ROWS):
            for c in range(COLS):
                if matched[r][c] and not seen[r][c]:
                    combos += 1
                    color, q = keys[r][c], deque([(r, c)])
                    seen[r][c] = True
                    while q:
                        cr, cc = q.popleft()
                        for dr, dc in DIRECTIONS:
                            nr, nc = cr + dr, cc + dc
                            if (0 <= nr < ROWS and 0 <= nc < COLS and matched[nr][nc]
                                    and not seen[nr][nc] and keys[nr][nc] == color):
                                seen[nr][nc] = True
                                q.append((nr, nc))
        for c in range(COLS):
            kept = [(b[r][c], keys[r][c]) for r in range(ROWS) if not matched[r][c]]
            for r, (value, key) in enumerate([(0, None)] * (ROWS - len(kept)) + kept):
                b[r][c], keys[r][c] = value, key
        if not cascade:
            return combos, tuple(map(tuple, b))


def score(board: tuple[tuple[object, ...], ...], cascade: bool = True) -> tuple[float, int]:
    combos, remaining = settle(board, cascade)
    positions: dict[int | str, list[tuple[int, int]]] = {}
    for r, row in enumerate(remaining):
        for c, orb in enumerate(row):
            key = orb_match_key(orb)
            if key is not None:
                positions.setdefault(key, []).append((r, c))
    penalty = sum(
        min(abs(r - rr) + abs(c - cc) for j, (rr, cc) in enumerate(orbs) if i != j)
        for orbs in positions.values() if len(orbs) >= 3
        for i, (r, c) in enumerate(orbs)
    )
    return combos * 1000.0 - penalty * 2.0, combos


@dataclass(order=True)
class Node:
    priority: float
    board: tuple[tuple[object, ...], ...] = field(compare=False)
    cursor: tuple[int, int] = field(compare=False)
    path: tuple[tuple[int, int], ...] = field(compare=False)
    combos: int = field(compare=False)

    @property
    def value(self) -> float:
        return -self.priority


def moved(board: tuple[tuple[object, ...], ...], a: tuple[int, int], b: tuple[int, int]) -> tuple[tuple[object, ...], ...]:
    rows = [list(row) for row in board]
    rows[a[0]][a[1]], rows[b[0]][b[1]] = rows[b[0]][b[1]], rows[a[0]][a[1]]
    return tuple(map(tuple, rows))


def expected_board_after_path(
    board: tuple[tuple[object, ...], ...], path: Iterable[tuple[int, int]]
) -> tuple[tuple[object, ...], ...]:
    """Apply the planned adjacent swaps without changing orb metadata."""
    result = board
    points = list(path)
    for a, b in zip(points, points[1:]):
        result = moved(result, a, b)
    return result


def _board_cell_key(value: object) -> object:
    """Compare matching semantics while retaining unknown visual classes."""
    key = orb_match_key(value)
    if key is not None:
        return key
    if isinstance(value, Orb):
        return (value.kind, value.visual_class)
    return value


def board_mismatch_count(
    actual: tuple[tuple[object, ...], ...], expected: tuple[tuple[object, ...], ...]
) -> int:
    if len(actual) != ROWS or len(expected) != ROWS or any(
        len(row) != COLS for row in (*actual, *expected)
    ):
        raise ValueError("Expected a 5x6 board")
    return sum(
        _board_cell_key(actual[r][c]) != _board_cell_key(expected[r][c])
        for r in range(ROWS) for c in range(COLS)
    )


def corrective_move(
    current: tuple[tuple[object, ...], ...],
    expected: tuple[tuple[object, ...], ...],
    cursor: tuple[int, int],
) -> tuple[int, int] | None:
    """Pick an adjacent move that strictly reduces board mismatches."""
    distance = board_mismatch_count(current, expected)
    candidates = []
    for dr, dc in DIRECTIONS:
        neighbor = cursor[0] + dr, cursor[1] + dc
        if 0 <= neighbor[0] < ROWS and 0 <= neighbor[1] < COLS:
            next_distance = board_mismatch_count(moved(current, cursor, neighbor), expected)
            if next_distance < distance:
                candidates.append((next_distance, neighbor))
    return min(candidates)[1] if candidates else None


def solve(
    board: tuple[tuple[object, ...], ...], beam_width: int, min_steps: int, max_steps: int,
    cascade: bool = True, starts: Iterable[tuple[int, int]] | None = None,
) -> Node:
    if min_steps < 0 or max_steps < min_steps:
        raise ValueError("min_steps must be non-negative and no greater than max_steps")
    initial_score, initial_combos = score(board, cascade)
    start_cells = tuple(starts) if starts is not None else tuple(
        (r, c) for r in range(ROWS) for c in range(COLS)
    )
    if not start_cells or any(not (0 <= r < ROWS and 0 <= c < COLS) for r, c in start_cells):
        raise ValueError("starts must contain board cells")
    beam = [Node(-initial_score, board, start, (start,), initial_combos) for start in start_cells]
    best = beam[0] if min_steps == 0 else None
    for depth in range(1, max_steps + 1):
        candidates: list[Node] = []
        for node in beam:
            previous = node.path[-2] if len(node.path) > 1 else None
            for dr, dc in DIRECTIONS:
                nr, nc = node.cursor[0] + dr, node.cursor[1] + dc
                if not (0 <= nr < ROWS and 0 <= nc < COLS) or (nr, nc) == previous:
                    continue
                next_board = moved(node.board, node.cursor, (nr, nc))
                next_score, combos = score(next_board, cascade)
                candidates.append(Node(-next_score, next_board, (nr, nc), node.path + ((nr, nc),), combos))
        if not candidates:
            break
        beam = heapq.nsmallest(beam_width, candidates)
        if depth >= min_steps and (best is None or beam[0].priority < best.priority):
            best = beam[0]
    return best or beam[0]


@dataclass(frozen=True)
class Grid:
    left: int = 0
    top: int = 1380
    cell: int = 180

    def point(self, row: int, col: int) -> tuple[int, int]:
        return self.left + self.cell * col + self.cell // 2, self.top + self.cell * row + self.cell // 2


@dataclass(frozen=True)
class CellFeatures:
    hue: float
    saturation: float
    value: float
    dark: float
    white: float
    orange: float
    purple: float
    blue: float
    green: float
    plus: float


def _hue_distance(a: float, b: float) -> float:
    distance = abs(a - b)
    return min(distance, 1.0 - distance)


def _palette_hue(samples: list[tuple[float, float, float]]) -> float:
    """Return a saturation-weighted circular mode, ignoring colour outliers."""
    if not samples:
        return 0.0
    bins = 36
    weights = [0.0] * bins
    for hue, saturation, _value in samples:
        weights[min(bins - 1, int(hue * bins))] += saturation
    mode = max(range(bins), key=weights.__getitem__)
    center = (mode + 0.5) / bins
    selected = [sample for sample in samples if _hue_distance(sample[0], center) <= 1.5 / bins]
    selected = selected or samples
    sin_sum = sum(math.sin(hue * math.tau) * saturation for hue, saturation, _ in selected)
    cos_sum = sum(math.cos(hue * math.tau) * saturation for hue, saturation, _ in selected)
    return (math.atan2(sin_sum, cos_sum) / math.tau) % 1


def _cell_features(
    width: int, height: int, pixels: bytes, point: tuple[int, int], cell: int = 180
) -> CellFeatures:
    """Extract robust palette and separate icon features from one BGRA cell."""
    x, y = point
    radius = min(55, max(12, round(cell * 0.42)))
    samples: list[tuple[int, int, float, float, float]] = []
    marker_samples: list[tuple[int, int, float, float, float]] = []
    for dy in range(-radius, radius + 1, 5):
        for dx in range(-radius, radius + 1, 5):
            sx, sy = x + dx, y + dy
            if not (0 <= sx < width and 0 <= sy < height):
                raise ValueError("Grid is outside the screenshot; pass --left, --top, and --cell after calibration")
            blue, green, red, _ = pixels[(sy * width + sx) * 4:(sy * width + sx) * 4 + 4]
            hue, saturation, value = colorsys.rgb_to_hsv(red / 255, green / 255, blue / 255)
            samples.append((dx, dy, hue, saturation, value))
            marker_hue, marker_saturation, marker_value = colorsys.rgb_to_hsv(
                blue / 255, green / 255, red / 255
            )
            marker_samples.append((dx, dy, marker_hue, marker_saturation, marker_value))

    # Palette uses a central annulus: the centre icon and outer rim are both
    # unstable. The lower-right marker is intentionally outside this sample.
    inner = max(4, round(cell * 0.16))
    outer = max(inner + 4, round(cell * 0.35))
    marker_start = max(20, round(cell * 0.15))
    palette = [
        (hue, saturation, value)
        for dx, dy, hue, saturation, value in samples
        if inner <= math.hypot(dx, dy) <= outer
        and not (dx >= marker_start and dy >= marker_start)
        and saturation >= 0.12 and value >= 0.08
    ]
    hue = _palette_hue(palette)
    saturation = statistics.median(sample[1] for sample in palette) if palette else 0.0
    value = statistics.median(sample[2] for sample in palette) if palette else 0.0

    # Hazard metrics intentionally retain the broad crop so their distinctive
    # glyphs remain separate from normal-colour palette classification.
    dark = sum(sample[4] < 0.24 for sample in samples) / len(samples)
    white = sum(sample[3] < 0.20 and sample[4] > 0.72 for sample in samples) / len(samples)
    orange = sum(0.035 < sample[2] < 0.14 and sample[3] > 0.45 and sample[4] > 0.45 for sample in samples) / len(samples)
    purple = sum(0.72 < sample[2] < 0.97 and sample[3] > 0.35 for sample in samples) / len(samples)
    blue = sum(0.52 < sample[2] < 0.72 and sample[3] > 0.35 for sample in samples) / len(samples)
    green = sum(0.25 < sample[2] < 0.48 and sample[3] > 0.35 for sample in samples) / len(samples)

    # The actual yellow '+' is right/below the orb centre. Test for its colour
    # in the marker box instead of treating ordinary white rim highlights as a
    # cross. Android's raw screenshot order is RGB here, unlike the legacy
    # calibrated palette path above.
    marker = [sample for sample in marker_samples
              if round(cell * 0.14) <= sample[0] <= round(cell * 0.42)
              and round(cell * 0.08) <= sample[1] <= round(cell * 0.36)]
    def yellow(sample: tuple[int, int, float, float, float]) -> bool:
        _dx, _dy, hue, saturation, value = sample
        return 0.10 < hue < 0.20 and saturation > 0.55 and value > 0.70

    marker_yellow = sum(yellow(sample) for sample in marker)
    all_yellow = sum(yellow(sample) for sample in marker_samples)
    plus = 1.0 if marker_yellow >= 8 and all_yellow <= marker_yellow * 3 else 0.0
    return CellFeatures(hue, saturation, value, dark, white, orange, purple, blue, green, max(0.0, plus))


def _hazard_kind(features: CellFeatures) -> str | None:
    """Recognize the distinctive glyph/palette of the three board hazards."""
    if features.orange >= 0.025 and features.dark >= 0.18 and features.green >= 0.06:
        return "bomb"
    # Hearts are also bright purple in this capture path.  A poison skull has
    # a distinctly larger white glyph; keep normal hearts out of this branch.
    if features.white >= 0.18 and features.purple >= 0.08:
        return "poison"
    # Live jammer glyphs are dark with only a small blue-eye region.
    if features.dark >= 0.25 and features.blue >= 0.06 and features.white < 0.20:
        return "jammer"
    return None


def _normal_color(features: CellFeatures) -> int | None:
    if features.saturation < 0.20 or features.value < 0.08:
        return None
    distances = sorted(
        (1.4 * _hue_distance(features.hue, prototype[0])
         + 0.45 * abs(features.saturation - prototype[1])
         + 0.75 * abs(features.value - prototype[2]), color, prototype[3])
        for color, prototype in ORB_PROTOTYPES.items()
    )
    best, color, limit = distances[0]
    runner_up = distances[1][0]
    return color if best <= limit and runner_up - best >= NORMAL_MIN_MARGIN else None


def screenshot(serial: str) -> tuple[int, int, bytes]:
    raw = subprocess.check_output(["adb", "-s", serial, "exec-out", "screencap"])
    width, height, pixel_format, _color_space = struct.unpack_from("<IIII", raw)
    if pixel_format != 1 or len(raw) != 16 + width * height * 4:
        raise RuntimeError("Expected an RGBA_8888 screenshot from adb screencap")
    return width, height, raw[16:]


def board_brightness(width: int, height: int, pixels: bytes, grid: Grid) -> float:
    """Return mean cell-center luminance (0-255) for the visible board."""
    luminance = []
    for r in range(ROWS):
        for c in range(COLS):
            x, y = grid.point(r, c)
            if not (0 <= x < width and 0 <= y < height):
                raise ValueError("Grid is outside the screenshot; pass --left, --top, and --cell after calibration")
            blue, green, red, _ = pixels[(y * width + x) * 4:(y * width + x) * 4 + 4]
            luminance.append(0.2126 * red + 0.7152 * green + 0.0722 * blue)
    return sum(luminance) / len(luminance)


def cell_visual_change(
    before: tuple[int, int, bytes], after: tuple[int, int, bytes], point: tuple[int, int]
) -> float:
    """Return mean RGB change around one cell center between two screenshots."""
    before_width, before_height, before_pixels = before
    after_width, after_height, after_pixels = after
    if (before_width, before_height) != (after_width, after_height):
        raise ValueError("Baseline and held screenshots have different dimensions")
    width, height = before_width, before_height
    x, y = point
    deltas = []
    for dy in (-30, -15, 0, 15, 30):
        for dx in (-30, -15, 0, 15, 30):
            sx, sy = x + dx, y + dy
            if not (0 <= sx < width and 0 <= sy < height):
                raise ValueError("Grid is outside the screenshot; pass --left, --top, and --cell after calibration")
            offset = (sy * width + sx) * 4
            deltas.append(sum(abs(before_pixels[offset + channel] - after_pixels[offset + channel])
                              for channel in range(3)) / 3)
    return sum(deltas) / len(deltas)


def ready(serial: str, grid: Grid, threshold: float) -> tuple[bool, float]:
    width, height, pixels = screenshot(serial)
    brightness = board_brightness(width, height, pixels, grid)
    return brightness >= threshold, brightness


def detect_board_pixels(width: int, height: int, pixels: bytes, grid: Grid) -> tuple[tuple[Orb, ...], ...]:
    """Recognize a board from screenshot pixels, retaining hazards and '+'."""
    features = [_cell_features(width, height, pixels, grid.point(r, c), grid.cell)
                for r in range(ROWS) for c in range(COLS)]
    hazards = [_hazard_kind(item) for item in features]
    board: list[tuple[Orb, ...]] = []
    for row in range(ROWS):
        detected: list[Orb] = []
        for col in range(COLS):
            index = row * COLS + col
            hazard = hazards[index]
            if hazard:
                detected.append(Orb(hazard, visual_class=hazard))
                continue
            color = _normal_color(features[index])
            detected.append(Orb("normal" if color is not None else "unknown", color,
                                features[index].plus >= PLUS_MARKER_MIN if color is not None else False,
                                NAMES[color] if color is not None else "unknown"))
        board.append(tuple(detected))
    return tuple(board)


def detect_board(serial: str, grid: Grid) -> tuple[tuple[Orb, ...], ...]:
    width, height, pixels = screenshot(serial)
    return detect_board_pixels(width, height, pixels, grid)


def _board_is_routeable(board: tuple[tuple[object, ...], ...]) -> bool:
    """A screenshot with any unknown cell is never safe to route."""
    return all(orb_match_key(orb) is not None for row in board for orb in row)


def _board_is_blind(board: tuple[tuple[object, ...], ...]) -> bool:
    """A fully obscured board has no usable class until it is swept."""
    unknowns = sum(isinstance(orb, Orb) and orb.kind == "unknown" for row in board for orb in row)
    # Black-orb overlays can leave a few bright glyph fragments that resemble
    # known orbs; --blind-scan is explicit opt-in, so 80% unknown is decisive.
    return unknowns >= ROWS * COLS * 0.8


def parse_board(text: str) -> tuple[tuple[int, ...], ...]:
    digits = [int(char) for char in text if char.isdigit()]
    if len(digits) != ROWS * COLS or not all(1 <= color <= 6 for color in digits):
        raise ValueError("--board needs exactly 30 digits, using colours 1 through 6")
    return tuple(tuple(digits[r * COLS:(r + 1) * COLS]) for r in range(ROWS))


def send_motion(serial: str, action: str, point: tuple[int, int]) -> None:
    subprocess.run(["adb", "-s", serial, "shell", "input", "motionevent", action,
                    str(point[0]), str(point[1])], check=True)


def blind_scan_path() -> tuple[tuple[int, int], ...]:
    """One continuous serpentine pass over all 30 cells."""
    return tuple((row, col if row % 2 == 0 else COLS - 1 - col)
                 for row in range(ROWS) for col in range(COLS))


def send_moves(serial: str, points: Iterable[tuple[int, int]], delay: float) -> None:
    commands = [f"input motionevent MOVE {x} {y}; sleep {delay}" for x, y in points]
    if commands:
        script = "; ".join(commands)
        subprocess.run(["adb", "-s", serial, "shell", f"sh -c {shlex.quote(script)}"], check=True)


def play(
    serial: str,
    path: Iterable[tuple[int, int]] | None,
    grid: Grid,
    delay: float,
    hold_delay: float = 0.15,
    lift_threshold: float = 12.0,
    expected_board: tuple[tuple[object, ...], ...] | None = None,
    max_corrections: int = 2,
    blind_scan: bool = False,
    scan_delay: float = 0.01,
    scan_capture_delay: float = 0.12,
    beam_width: int = 80,
    min_steps: int = 5,
    max_steps: int = 25,
    cascade: bool = True,
) -> bool:
    path = tuple(path or ())
    scan = blind_scan_path() if blind_scan else ()
    if blind_scan:
        path = scan
    points = [grid.point(r, c) for r, c in path]
    if not points:
        raise ValueError("Cannot play an empty path")
    if expected_board is None and not blind_scan:
        raise ValueError("expected_board is required for safe play verification")
    if expected_board is not None and not _board_is_routeable(expected_board):
        raise ValueError("Cannot play an uncertain board")
    if (delay < 0 or hold_delay < 0 or lift_threshold <= 0 or max_corrections < 0
            or scan_delay < 0 or scan_capture_delay < 0):
        raise ValueError("delay and hold delay must be non-negative; lift threshold must be positive; max corrections must be non-negative")

    baseline = screenshot(serial)
    send_motion(serial, "DOWN", points[0])
    down = True
    cursor = points[0]
    cursor_cell = path[0]
    try:
        time.sleep(hold_delay)
        held = screenshot(serial)
        change = cell_visual_change(baseline, held, points[0])
        if change < lift_threshold:
            print(f"Start orb hold not verified (cell RGB change {change:.1f} < {lift_threshold:.1f})")
            return False

        if blind_scan:
            send_moves(serial, points[1:], scan_delay)
            cursor, cursor_cell = points[-1], path[-1]
            time.sleep(scan_capture_delay)
            expected_board = detect_board(serial, grid)
            if not _board_is_routeable(expected_board):
                print("Blind scan did not reveal a complete board; releasing")
                return False
            solution = solve(expected_board, beam_width, min_steps, max_steps, cascade,
                             starts=(cursor_cell,))
            path = solution.path
            points = [grid.point(r, c) for r, c in path]
            print(f"Blind scan revealed board; continuing with {solution.combos} combos")

        send_moves(serial, points[1:], delay)
        if len(points) > 1:
            cursor, cursor_cell = points[-1], path[-1]

        expected = expected_board_after_path(expected_board, path)
        try:
            current = detect_board(serial, grid)
            if not _board_is_routeable(current):
                print("Final board verification uncertain; releasing without correction")
                return False
            mismatches = board_mismatch_count(current, expected)
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"Final board verification failed: {exc}")
            return False

        corrections = 0
        while mismatches and corrections < max_corrections:
            target = corrective_move(current, expected, cursor_cell)
            if target is None:
                print(f"Final board mismatch ({mismatches} cells); no safe correction found")
                return False
            send_motion(serial, "MOVE", grid.point(*target))
            cursor = grid.point(*target)
            cursor_cell = target
            corrections += 1
            time.sleep(delay)
            try:
                current = detect_board(serial, grid)
                if not _board_is_routeable(current):
                    print("Board correction verification uncertain; releasing")
                    return False
                mismatches = board_mismatch_count(current, expected)
            except (OSError, RuntimeError, ValueError) as exc:
                print(f"Board correction verification failed: {exc}")
                return False

        if mismatches:
            print(f"Final board mismatch ({mismatches} cells) after {corrections} corrections")
            return False
        send_motion(serial, "UP", cursor)
        down = False
        print(f"Final board verified ({corrections} corrections)")
        return True
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Gesture failed before final board verification: {exc}")
        return False
    finally:
        if down:
            try:
                send_motion(serial, "UP", cursor)
            except (OSError, subprocess.CalledProcessError) as exc:
                print(f"Emergency release failed: {exc}")


def self_check() -> None:
    board = ((1, 1, 1, 2, 2, 2), (2, 3, 4, 5, 6, 1),
             (3, 4, 5, 6, 1, 2), (4, 5, 6, 1, 2, 3),
             (5, 6, 1, 2, 3, 4))
    combos, cleared = settle(board)
    assert combos == 2 and all(not value for row in cleared[:1] for value in row)
    cascade_board = ((3, 2, 2, 3, 2, 1), (1, 2, 1, 3, 3, 1),
                     (2, 3, 1, 2, 3, 3), (3, 1, 1, 2, 3, 4),
                     (2, 4, 2, 4, 4, 3))
    assert settle(cascade_board, cascade=False)[0] == 2 < settle(cascade_board)[0]
    assert solve(board, beam_width=4, min_steps=0, max_steps=1).combos >= 2
    assert len(solve(board, beam_width=4, min_steps=2, max_steps=3).path) - 1 >= 2
    forced_start = solve(board, beam_width=4, min_steps=1, max_steps=2, starts=((4, 5),))
    assert forced_start.path[0] == (4, 5)
    scan = blind_scan_path()
    assert len(scan) == ROWS * COLS and len(set(scan)) == ROWS * COLS
    assert all(abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1 for a, b in zip(scan, scan[1:]))
    mostly_blind = ((Orb("unknown"),) * COLS,) * 4 + ((Orb("normal", 4),) * COLS,)
    assert _board_is_blind(mostly_blind)
    assert parse_board("123456" * 5)[4][5] == 6
    # Every normal class is labelled by a persisted prototype, including the
    # close dark/heart pair. A barely-nearest colour remains unknown.
    prototype_features = [
        CellFeatures(hue, saturation, value, 0, 0, 0, 0, 0, 0, 0)
        for hue, saturation, value, _limit in ORB_PROTOTYPES.values()
    ]
    assert [_normal_color(item) for item in prototype_features] == list(ORB_PROTOTYPES)
    heart_feature = CellFeatures(0.768, 0.57, 0.90, 0, 0, 0, 0, 0, 0, 0)
    dark_feature = CellFeatures(0.858, 0.58, 0.70, 0, 0, 0, 0, 0, 0, 0)
    assert _normal_color(heart_feature) == 6 and _normal_color(dark_feature) == 5
    assert _normal_color(CellFeatures(0.735, 0.70, 0.86, 0, 0, 0, 0, 0, 0, 0)) is None
    # Regression values from the live board: hearts must remain normal while
    # the dark jammer with small blue eyes remains a matchable jammer.
    assert _hazard_kind(CellFeatures(0, 0, 0, 0, 0.161, 0, 0.626, 0.006, 0, 0)) is None
    assert _hazard_kind(CellFeatures(0, 0, 0, 0.397, 0.006, 0.210, 0.002, 0.081, 0, 0)) == "jammer"
    assert _hazard_kind(CellFeatures(0, 0, 0, 0.461, 0.008, 0.202, 0.004, 0.069, 0, 0)) == "jammer"
    assert settle(((Orb("jammer"),) * 3 + (0,) * 3,) + ((0,) * COLS,) * (ROWS - 1))[0] == 1
    synthetic = parse_board("123456" * 5)
    expected = expected_board_after_path(synthetic, ((0, 0), (0, 1)))
    current = moved(expected, (0, 1), (0, 2))
    assert board_mismatch_count(current, expected) == 2
    correction = corrective_move(current, expected, (0, 1))
    assert correction == (0, 2)
    assert board_mismatch_count(moved(current, (0, 1), correction), expected) == 0
    pixels = bytes([100, 100, 100, 255] * 30)
    assert board_brightness(6, 5, pixels, Grid(top=0, cell=1)) == 100
    width = height = 100
    baseline = bytes([0, 0, 0, 255] * (width * height))
    held = bytearray(baseline)
    offset = (50 * width + 50) * 4
    held[offset:offset + 3] = bytes([60, 60, 60])
    assert cell_visual_change((width, height, baseline), (width, height, bytes(held)), (50, 50)) > 0
    assert cell_visual_change((width, height, baseline), (width, height, baseline), (50, 50)) == 0

    # Synthetic visual board: all six normal prototypes (one enhanced), one
    # shaded/outlier normal, and the three distinct hazards.
    grid = Grid(left=60, top=60, cell=120)
    width, height = 780, 700
    pixels = bytearray([0, 0, 0, 255] * (width * height))

    def prototype_rgb(color: int) -> tuple[int, int, int]:
        hue, saturation, value, _limit = ORB_PROTOTYPES[color]
        return tuple(round(channel * 255) for channel in colorsys.hsv_to_rgb(hue, saturation, value))

    def paint(row: int, col: int, rgb: tuple[int, int, int]) -> None:
        x, y = grid.point(row, col)
        blue, green, red = rgb[2], rgb[1], rgb[0]
        for dy in range(-55, 56):
            for dx in range(-55, 56):
                offset = ((y + dy) * width + x + dx) * 4
                pixels[offset:offset + 4] = bytes((blue, green, red, 255))

    def patch(row: int, col: int, bounds: tuple[int, int, int, int], rgb: tuple[int, int, int]) -> None:
        x, y = grid.point(row, col)
        left, top, right, bottom = bounds
        blue, green, red = rgb[2], rgb[1], rgb[0]
        for dy in range(top, bottom + 1, 5):
            for dx in range(left, right + 1, 5):
                offset = ((y + dy) * width + x + dx) * 4
                pixels[offset:offset + 4] = bytes((blue, green, red, 255))

    normal_board = ((1, 2, 3, 4, 5, 6), (6, 5, 4, 3, 2, 1))
    for row, values in enumerate(normal_board):
        for col, color in enumerate(values):
            paint(row, col, prototype_rgb(color))
    patch(0, 0, (25, 15, 75, 65), (0, 255, 255))  # native-RGBA yellow fire+
    patch(0, 1, (-45, -10, -25, 10), (220, 40, 100))  # shading/outlier on water
    for col in range(6):
        paint(2, col, (5, 10, 45))  # jammer
        paint(3, col, (150, 20, 180))  # poison base
        patch(3, col, (-20, -20, 20, 20), (245, 245, 245))  # skull glyph
        paint(4, col, (5, 50, 30))  # bomb body
        patch(4, col, (-15, -50, 15, -25), (255, 130, 20))  # fuse
    detected = detect_board_pixels(width, height, bytes(pixels), grid)
    assert [detected[0][col].color for col in range(COLS)] == [1, 2, 3, 4, 5, 6]
    assert [detected[1][col].color for col in range(COLS)] == [6, 5, 4, 3, 2, 1]
    assert detected[0][0].kind == detected[0][1].kind == "normal"
    assert detected[0][0].enhanced
    assert not detected[0][1].enhanced
    assert orb_match_key(detected[0][0]) == orb_match_key(detected[1][5])
    enhanced_board = tuple(tuple(Orb("normal", value, enhanced=(r == 0 and c == 1))
                                 for c, value in enumerate(row))
                           for r, row in enumerate(board))
    assert settle(enhanced_board)[0] == 2
    assert [detected[row][0].kind for row in range(2, 5)] == ["jammer", "poison", "bomb"]
    report = board_report(detected)
    assert all(report.count(name) == 1 for name in ("jammer", "poison", "bomb"))
    assert "unknown" not in report
    assert not _board_is_routeable(((Orb("unknown", visual_class="unknown"),) * COLS,) * ROWS)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial", default="R5CX42SQRBR")
    parser.add_argument("--board", help="30 digits, row-major: 1 fire, 2 water, 3 wood, 4 light, 5 dark, 6 heart")
    parser.add_argument("--beam-width", type=int, default=80)
    parser.add_argument("--min-steps", type=int, default=5,
                        help="Minimum drag steps before a solution can be selected")
    parser.add_argument("--max-steps", type=int, default=25)
    parser.add_argument("--direct-only", action="store_true",
                        help="Score only matches made directly by the final board, not gravity cascades")
    parser.add_argument("--left", type=int, default=0)
    parser.add_argument("--top", type=int, default=1380)
    parser.add_argument("--cell", type=int, default=180)
    parser.add_argument("--move-delay", "--delay", dest="move_delay", type=float, default=0.04,
                        help="Seconds between MOVE events (default: 0.04; --delay is retained as an alias)")
    parser.add_argument("--hold-delay", type=float, default=0.15,
                        help="Seconds to wait after DOWN before checking the selected orb")
    parser.add_argument("--loop", action="store_true", help="Keep waiting for and solving each visible board")
    parser.add_argument("--poll-interval", type=float, default=1.0, help="Seconds between readiness checks")
    parser.add_argument("--brightness-threshold", type=float, default=40.0,
                        help="Minimum mean board luminance (0-255) before solving")
    parser.add_argument("--lift-threshold", type=float, default=12.0,
                        help="Minimum mean RGB change in the selected cell after DOWN")
    parser.add_argument("--max-corrections", type=int, default=2,
                        help="Maximum conservative MOVE corrections before releasing")
    parser.add_argument("--blind-scan", action="store_true",
                        help="When all 30 cells are hidden, hold and sweep once before routing")
    parser.add_argument("--scan-delay", type=float, default=0.01,
                        help="Seconds between blind-scan cells while held")
    parser.add_argument("--scan-capture-delay", type=float, default=0.12,
                        help="Seconds to wait for revealed cells before recognition")
    parser.add_argument("--round-limit", type=int, default=0, help="Stop after this many rounds; 0 means unlimited")
    parser.add_argument("--play", action="store_true", help="Actually send the gesture; default only prints it")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        print("self-check passed")
        return
    if (args.min_steps < 0 or args.max_steps < args.min_steps or args.round_limit < 0
            or args.poll_interval < 0 or args.brightness_threshold < 0
            or args.move_delay < 0 or args.hold_delay < 0 or args.lift_threshold <= 0
            or args.max_corrections < 0 or args.scan_delay < 0 or args.scan_capture_delay < 0):
        parser.error("--min-steps must be within 0..--max-steps; timings, thresholds, and max corrections must be non-negative")
    grid = Grid(args.left, args.top, args.cell)
    rounds = 0
    while True:
        if args.board:
            board = parse_board(args.board)
        else:
            is_ready, brightness = ready(args.serial, grid, args.brightness_threshold)
            if not is_ready and not (args.play and args.blind_scan):
                print(f"Board dim ({brightness:.1f}); waiting...")
                time.sleep(args.poll_interval)
                continue
            board = detect_board(args.serial, grid)
        if args.play and args.blind_scan and _board_is_blind(board):
            print("Fully hidden board; holding and sweeping before recognition.")
            rounds += 1
            if not play(args.serial, None, grid, args.move_delay, args.hold_delay,
                        args.lift_threshold, None, args.max_corrections, blind_scan=True,
                        scan_delay=args.scan_delay, scan_capture_delay=args.scan_capture_delay,
                        beam_width=args.beam_width, min_steps=args.min_steps,
                        max_steps=args.max_steps, cascade=not args.direct_only):
                print("Releasing and returning to readiness polling.")
                if args.board or not args.loop:
                    return
                time.sleep(args.poll_interval)
                continue
            print("Gesture sent")
            if not args.loop or (args.round_limit and rounds >= args.round_limit):
                break
            time.sleep(args.poll_interval)
            continue
        if args.play and not _board_is_routeable(board):
            print("Detected board:")
            print("\n".join(" ".join(orb_display(orb) for orb in row) for row in board))
            print("Classes:", board_report(board))
            print("Uncertain board; refusing to route. Retry after the board settles.")
            if args.board or not args.loop:
                return
            time.sleep(args.poll_interval)
            continue
        solution = solve(board, args.beam_width, args.min_steps, args.max_steps,
                         cascade=not args.direct_only)
        rounds += 1
        print("Detected board:")
        print("\n".join(" ".join(orb_display(orb) for orb in row) for row in board))
        print("Classes:", board_report(board))
        print(f"Combos: {solution.combos}; score: {solution.value:.1f}; steps: {len(solution.path) - 1}")
        print("Path:", " -> ".join(f"({r + 1},{c + 1})" for r, c in solution.path))
        if args.play:
            if not play(args.serial, solution.path, grid, args.move_delay, args.hold_delay,
                        args.lift_threshold, board, args.max_corrections):
                print("Releasing and returning to readiness polling.")
                if args.board:
                    return
                time.sleep(args.poll_interval)
                continue
            print("Gesture sent")
        else:
            print("Dry run. Add --play to send this gesture.")
        if not args.loop or (args.round_limit and rounds >= args.round_limit):
            break
        time.sleep(args.poll_interval)


if __name__ == "__main__":
    main()
