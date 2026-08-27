#!/usr/bin/env python3
"""Native desktop board inspection for PAD Router.

The controller is deliberately independent of tkinter.  It owns the small
workflow the GUI needs while recognition and device capture remain injected
adapters around the existing PAD Router functions.
"""

from __future__ import annotations

import base64
import binascii
import struct
import zlib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterable

from pad_router import (
    COLS,
    HAZARDS,
    NAMES,
    ROWS,
    Grid,
    Orb,
    RouteEvaluation,
    RouteSearchOptions,
    RouteSearchResult,
    RuleProfile,
    detect_board_pixels,
    evaluate_manual_route,
    load_rule_profile,
    orb_display,
    orb_match_key,
    search_qualifying_route,
    save_rule_profile,
    screenshot,
)


Screenshot = tuple[int, int, bytes]
Board = tuple[tuple[object, ...], ...]
Detector = Callable[[int, int, bytes, Grid], Board]
Capture = Callable[[str], Screenshot]


@dataclass(frozen=True)
class BoardCalibration:
    """Top-left pixel and cell size for a Standard Board."""

    left: int = 0
    top: int = 1380
    cell: int = 180

    def validate(self, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            raise ValueError("Screenshot dimensions must be positive")
        if self.left < 0 or self.top < 0 or self.cell <= 0:
            raise ValueError("Calibration coordinates must be non-negative and cell must be positive")
        if self.left + COLS * self.cell > width or self.top + ROWS * self.cell > height:
            raise ValueError("Calibration must keep the Standard Board inside the screenshot")

    def to_grid(self) -> Grid:
        return Grid(self.left, self.top, self.cell)


def infer_calibration(width: int, height: int) -> BoardCalibration:
    """Choose the legacy calibration when it fits, otherwise center a board."""

    legacy = BoardCalibration()
    try:
        legacy.validate(width, height)
    except ValueError:
        cell = min(width // COLS, height // ROWS)
        if cell <= 0:
            raise ValueError("Screenshot is too small for a Standard Board")
        legacy = BoardCalibration((width - COLS * cell) // 2,
                                  (height - ROWS * cell) // 2, cell)
        legacy.validate(width, height)
    return legacy


def _paeth(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    left_distance = abs(estimate - left)
    above_distance = abs(estimate - above)
    upper_left_distance = abs(estimate - upper_left)
    if left_distance <= above_distance and left_distance <= upper_left_distance:
        return left
    if above_distance <= upper_left_distance:
        return above
    return upper_left


def _unfilter_png(raw: bytes, width: int, height: int, row_bytes: int, bytes_per_pixel: int) -> bytes:
    expected = height * (row_bytes + 1)
    if len(raw) != expected:
        raise ValueError("PNG pixel data has an unexpected size")
    rows: list[bytes] = []
    offset = 0
    for _ in range(height):
        filter_type = raw[offset]
        offset += 1
        current = bytearray(raw[offset:offset + row_bytes])
        offset += row_bytes
        previous = rows[-1] if rows else b"\x00" * row_bytes
        if filter_type == 1:
            for index in range(row_bytes):
                current[index] = (current[index] + (current[index - bytes_per_pixel] if index >= bytes_per_pixel else 0)) & 255
        elif filter_type == 2:
            for index in range(row_bytes):
                current[index] = (current[index] + previous[index]) & 255
        elif filter_type == 3:
            for index in range(row_bytes):
                left = current[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
                current[index] = (current[index] + ((left + previous[index]) // 2)) & 255
        elif filter_type == 4:
            for index in range(row_bytes):
                left = current[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
                current[index] = (current[index] + _paeth(left, previous[index], previous[index - bytes_per_pixel] if index >= bytes_per_pixel else 0)) & 255
        elif filter_type != 0:
            raise ValueError(f"Unsupported PNG filter {filter_type}")
        rows.append(bytes(current))
    return b"".join(rows)


def decode_png(path: str | Path) -> Screenshot:
    """Decode an 8-bit, non-interlaced PNG into the existing BGRA format."""

    data = Path(path).read_bytes()
    signature = b"\x89PNG\r\n\x1a\n"
    if not data.startswith(signature):
        raise ValueError("Expected a PNG image")
    width = height = bit_depth = color_type = interlace = None
    palette: bytes | None = None
    transparency: bytes | None = None
    compressed = bytearray()
    offset = len(signature)
    while offset + 12 <= len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        kind = data[offset + 4:offset + 8]
        payload_start = offset + 8
        payload_end = payload_start + length
        if payload_end + 4 > len(data):
            raise ValueError("Truncated PNG chunk")
        payload = data[payload_start:payload_end]
        checksum = struct.unpack_from(">I", data, payload_end)[0]
        if binascii.crc32(kind + payload) & 0xFFFFFFFF != checksum:
            raise ValueError("PNG chunk checksum mismatch")
        if kind == b"IHDR":
            if length != 13:
                raise ValueError("Invalid PNG header")
            width, height, bit_depth, color_type, _compression, _filter, interlace = struct.unpack(
                ">IIBBBBB", payload
            )
        elif kind == b"PLTE":
            palette = payload
        elif kind == b"tRNS":
            transparency = payload
        elif kind == b"IDAT":
            compressed.extend(payload)
        elif kind == b"IEND":
            break
        offset = payload_end + 4
    if width is None or height is None or bit_depth != 8 or interlace != 0:
        raise ValueError("Only 8-bit, non-interlaced PNG images are supported")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
    if channels is None:
        raise ValueError("Unsupported PNG colour type")
    if color_type == 3 and (palette is None or len(palette) % 3):
        raise ValueError("Indexed PNG is missing a valid palette")
    raw = zlib.decompress(bytes(compressed))
    row_bytes = width * channels
    pixels = _unfilter_png(raw, width, height, row_bytes, channels)
    result = bytearray(width * height * 4)
    for index in range(width * height):
        source = index * channels
        if color_type == 6:
            red, green, blue, alpha = pixels[source:source + 4]
        elif color_type == 2:
            red, green, blue = pixels[source:source + 3]
            alpha = 255
            if transparency and len(transparency) == 6:
                transparent = tuple(struct.unpack(">H", transparency[i:i + 2])[0] for i in (0, 2, 4))
                alpha = 0 if (red, green, blue) == transparent else 255
        elif color_type == 4:
            red = green = blue = pixels[source]
            alpha = pixels[source + 1]
        elif color_type == 0:
            red = green = blue = pixels[source]
            alpha = 255
        else:
            palette_index = pixels[source]
            palette_offset = palette_index * 3
            if palette_offset + 3 > len(palette or b""):
                raise ValueError("PNG palette index is out of range")
            red, green, blue = (palette or b"")[palette_offset:palette_offset + 3]
            alpha = transparency[palette_index] if transparency and palette_index < len(transparency) else 255
        result[index * 4:index * 4 + 4] = bytes((blue, green, red, alpha))
    return width, height, bytes(result)


def _board_shape(board: Board) -> None:
    if len(board) != ROWS or any(len(row) != COLS for row in board):
        raise ValueError("Recognizer must return a 5x6 board")


def _uncertain_cells(board: Board) -> tuple[tuple[int, int], ...]:
    return tuple((row, col) for row in range(ROWS) for col in range(COLS)
                 if orb_match_key(board[row][col]) is None)


def _coerce_orb(value: object) -> Orb:
    if isinstance(value, Orb):
        if value.kind == "normal" and value.color not in NAMES:
            raise ValueError("Normal Orbs need a colour from 1 through 6")
        if value.kind not in HAZARDS and value.kind != "normal":
            raise ValueError(f"Unsupported Orb type: {value.kind}")
        return value
    if isinstance(value, int):
        if value not in NAMES:
            raise ValueError("Orb colour must be an integer from 1 through 6")
        return Orb("normal", value)
    if not isinstance(value, str):
        raise ValueError("Cell correction must be an Orb, colour number, or Orb name")
    text = value.strip().lower().replace(" ", "_")
    enhanced = text.endswith("+")
    if enhanced:
        text = text[:-1]
    locked = text.endswith("*")
    if locked:
        text = text[:-1]
    names = {name: color for color, name in NAMES.items()}
    if text in names:
        return Orb("normal", names[text], enhanced=enhanced, locked=locked)
    if text in HAZARDS:
        return Orb(text, visual_class=text)
    raise ValueError(f"Unsupported Orb type: {value}")


@dataclass(frozen=True)
class BoardInspectionState:
    source_name: str | None = None
    width: int | None = None
    height: int | None = None
    pixels: bytes | None = None
    calibration: BoardCalibration | None = None
    detected_board: Board | None = None
    board: Board | None = None
    confirmed_board: Board | None = None
    confirmed: bool = False
    uncertain_cells: tuple[tuple[int, int], ...] = ()
    overlay: tuple[dict[str, object], ...] = ()
    status: str = "No source loaded"
    rule_profile: RuleProfile | None = None
    route_evaluation: RouteEvaluation | None = None
    route_approved: bool = False
    route_search: RouteSearchResult | None = None
    route_overlay: tuple[dict[str, object], ...] = ()

    @property
    def source_path(self) -> str | None:
        return self.source_name

    @property
    def editable_board(self) -> Board | None:
        return self.board


class BoardInspectionController:
    """Public board inspection seam used by the GUI and standard-library tests."""

    def __init__(self, detector: Detector = detect_board_pixels, capture: Capture = screenshot):
        self._detector = detector
        self._capture = capture
        self.state = BoardInspectionState()

    def _with_source(self, source: Screenshot, source_name: str) -> BoardInspectionState:
        width, height, pixels = source
        if width <= 0 or height <= 0 or len(pixels) != width * height * 4:
            raise ValueError("Screenshot must contain width*height BGRA pixels")
        calibration = infer_calibration(width, height)
        detected = self._detector(width, height, pixels, calibration.to_grid())
        _board_shape(detected)
        return self._replace_source(source_name, source, calibration, detected)

    def _replace_source(self, source_name: str, source: Screenshot,
                        calibration: BoardCalibration, detected: Board) -> BoardInspectionState:
        uncertain = _uncertain_cells(detected)
        status = (f"Loaded {source_name}; manual correction required for {len(uncertain)} cell(s)"
                  if uncertain else f"Loaded {source_name}; review and confirm the Board")
        state = BoardInspectionState(source_name, source[0], source[1], source[2], calibration,
                                     detected, detected, None, False, uncertain, (), status)
        if self.state.rule_profile is not None:
            state = replace(state, rule_profile=self.state.rule_profile)
        self.state = self._with_overlay(state)
        return self.state

    def _with_overlay(self, state: BoardInspectionState) -> BoardInspectionState:
        if state.board is None or state.calibration is None:
            return state
        grid = state.calibration.to_grid()
        source_board = state.detected_board or state.board
        overlay = tuple({"cell": (row, col), "x": grid.point(row, col)[0],
                         "y": grid.point(row, col)[1], "label": orb_display(source_board[row][col]),
                         "uncertain": orb_match_key(source_board[row][col]) is None}
                         for row in range(ROWS) for col in range(COLS))
        return replace(state, overlay=overlay)

    def _route_overlay(self, result: RouteEvaluation | None) -> tuple[dict[str, object], ...]:
        if result is None or self.state.calibration is None:
            return ()
        grid = self.state.calibration.to_grid()
        return tuple({"cell": cell, "step": index, "x": grid.point(*cell)[0], "y": grid.point(*cell)[1]}
                     for index, cell in enumerate(result.route, 1))

    def load_png(self, path: str | Path) -> BoardInspectionState:
        if Path(path).suffix.lower() != ".png":
            raise ValueError("Only PNG images are supported")
        return self._with_source(decode_png(path), str(path))

    def capture_device(self, serial: str) -> BoardInspectionState:
        serial = serial.strip()
        if not serial:
            raise ValueError("A device serial is required")
        return self._with_source(self._capture(serial), serial)

    def set_calibration(self, calibration: BoardCalibration) -> BoardInspectionState:
        if self.state.pixels is None or self.state.width is None or self.state.height is None:
            raise ValueError("Load an image or capture a device before calibrating")
        if not isinstance(calibration, BoardCalibration):
            raise TypeError("calibration must be a BoardCalibration")
        calibration.validate(self.state.width, self.state.height)
        detected = self._detector(self.state.width, self.state.height, self.state.pixels,
                                  calibration.to_grid())
        _board_shape(detected)
        return self._replace_source(self.state.source_name or "source",
                                    (self.state.width, self.state.height, self.state.pixels),
                                    calibration, detected)

    def calibrate(self, left: BoardCalibration | int, top: int | None = None,
                  cell: int | None = None) -> BoardInspectionState:
        if isinstance(left, BoardCalibration):
            if top is not None or cell is not None:
                raise TypeError("Do not combine a BoardCalibration with coordinate arguments")
            calibration = left
        elif top is not None and cell is not None:
            calibration = BoardCalibration(left, top, cell)
        else:
            raise TypeError("calibrate needs a BoardCalibration or left, top, and cell")
        return self.set_calibration(calibration)

    def correct_cell(self, row: int, col: int, value: object) -> BoardInspectionState:
        if self.state.board is None:
            raise ValueError("Load an image or capture a device before correcting a cell")
        if not (0 <= row < ROWS and 0 <= col < COLS):
            raise ValueError("Cell must be inside the 5x6 Standard Board")
        board = [list(items) for items in self.state.board]
        board[row][col] = _coerce_orb(value)
        updated = replace(self.state, board=tuple(map(tuple, board)), confirmed_board=None,
                          confirmed=False, uncertain_cells=_uncertain_cells(tuple(map(tuple, board))),
                          overlay=(), route_evaluation=None, route_approved=False,
                          route_search=None, route_overlay=(),
                          status="Cell corrected; review and confirm the Board")
        self.state = self._with_overlay(updated)
        return self.state

    def confirm_board(self) -> Board:
        if self.state.board is None:
            raise ValueError("No Board is loaded")
        if self.state.uncertain_cells:
            raise ValueError("Uncertain cells require manual correction before confirmation")
        self.state = replace(self.state, confirmed_board=self.state.board, confirmed=True,
                             uncertain_cells=(), route_evaluation=None, route_approved=False,
                             route_search=None, route_overlay=(),
                             status="Board confirmed")
        return self.state.board

    def set_rule_profile(self, profile: RuleProfile) -> BoardInspectionState:
        if not isinstance(profile, RuleProfile):
            raise TypeError("profile must be a RuleProfile")
        self.state = replace(self.state, rule_profile=profile, route_evaluation=None,
                             route_approved=False, route_search=None, route_overlay=(),
                             status=f"Rule Profile applied: {profile.name}")
        return self.state

    def save_rule_profile(self, path: str | Path, profile: RuleProfile | None = None) -> None:
        profile = profile or self.state.rule_profile
        if profile is None:
            raise ValueError("No Rule Profile is applied")
        save_rule_profile(profile, path)

    def load_rule_profile(self, path: str | Path) -> BoardInspectionState:
        return self.set_rule_profile(load_rule_profile(path))

    def evaluate_manual_route(self, path: Iterable[tuple[int, int]],
                               profile: RuleProfile | None = None) -> RouteEvaluation:
        board = self.state.confirmed_board or self.state.board
        if board is None:
            raise ValueError("Load and confirm a Board before evaluating a Route")
        profile = profile or self.state.rule_profile
        if profile is None:
            raise ValueError("Apply a Rule Profile before evaluating a Route")
        result = evaluate_manual_route(board, path, profile, confirmed=self.state.confirmed)
        self.state = replace(self.state, route_evaluation=result, route_approved=False,
                             route_search=None, route_overlay=self._route_overlay(result),
                             status=result.diagnostic)
        return result

    def search_qualifying_route(self, options: RouteSearchOptions | None = None) -> RouteSearchResult:
        board = self.state.confirmed_board or self.state.board
        if board is None:
            raise ValueError("Load and confirm a Board before searching for a Route")
        profile = self.state.rule_profile
        if profile is None:
            raise ValueError("Apply a Rule Profile before searching for a Route")
        result = search_qualifying_route(board, profile, options, confirmed=self.state.confirmed)
        candidate = result.candidate
        self.state = replace(self.state, route_search=result, route_evaluation=candidate,
                             route_approved=False, route_overlay=self._route_overlay(candidate),
                             status=result.diagnostic)
        return result

    def search_route(self, options: RouteSearchOptions | None = None) -> RouteSearchResult:
        return self.search_qualifying_route(options)

    def approve_route(self, explicit_confirmation: bool = False) -> RouteEvaluation:
        result = self.state.route_evaluation
        if result is None or not result.execution_eligible:
            raise ValueError("Only a qualifying Route on a confirmed Board can be approved")
        if not explicit_confirmation:
            raise ValueError("Explicit Route confirmation is required")
        self.state = replace(self.state, route_approved=True, status="Route approved")
        return result


def _photo_from_screenshot(screenshot_data: Screenshot, tk_module):
    width, height, pixels = screenshot_data
    rgb = bytearray(width * height * 3)
    for index in range(width * height):
        blue, green, red = pixels[index * 4:index * 4 + 3]
        rgb[index * 3:index * 3 + 3] = bytes((red, green, blue))
    ppm = f"P6\n{width} {height}\n255\n".encode() + bytes(rgb)
    return tk_module.PhotoImage(data=base64.b64encode(ppm).decode("ascii"))


class BoardInspectionApp:
    """Small tkinter view for the controller's board inspection workflow."""

    def __init__(self, root=None, controller: BoardInspectionController | None = None):
        try:
            import tkinter as tk
            from tkinter import ttk
        except ModuleNotFoundError as exc:
            raise RuntimeError("The Python tkinter module is required to open the desktop GUI") from exc

        self.tk = tk
        self.ttk = ttk
        self.root = root or tk.Tk()
        self.root.title("PAD Router — Board Inspection")
        self.controller = controller or BoardInspectionController()
        self._photo = None
        self._selected_cell: tuple[int, int] | None = None
        self._manual_route: list[tuple[int, int]] = []
        self._dragging_route = False
        self._profile: RuleProfile | None = self.controller.state.rule_profile
        self._selected_orb = tk.StringVar(value="fire")
        self._enhanced = tk.BooleanVar()
        self._locked = tk.BooleanVar()
        self._serial = tk.StringVar()
        self._left = tk.StringVar()
        self._top = tk.StringVar()
        self._cell = tk.StringVar()
        self._selected_label = tk.StringVar(value="No cell selected")
        self._profile_name = tk.StringVar(value="manual")
        self._hazard_policy = tk.StringVar(value="avoid")
        self._search_attempts = tk.StringVar(value="100")
        self._search_seed = tk.StringVar(value="0")
        self._profile_label = tk.StringVar(value="No Rule Profile")
        self._evaluation = tk.StringVar(value="No Route evaluated")
        self._status = tk.StringVar(value=self.controller.state.status)
        self._build()

    def _build(self):
        tk, ttk = self.tk, self.ttk
        controls = ttk.Frame(self.root, padding=8)
        controls.pack(fill="x")
        ttk.Button(controls, text="Open PNG", command=self.open_png).pack(side="left")
        ttk.Label(controls, text="Serial:").pack(side="left", padx=(12, 2))
        ttk.Entry(controls, width=18, textvariable=self._serial).pack(side="left")
        ttk.Button(controls, text="Capture", command=self.capture_device).pack(side="left", padx=4)
        ttk.Label(controls, text="Calibration left/top/cell:").pack(side="left", padx=(12, 2))
        for variable, width in ((self._left, 5), (self._top, 6), (self._cell, 5)):
            ttk.Entry(controls, width=width, textvariable=variable).pack(side="left", padx=1)
        ttk.Button(controls, text="Apply", command=self.apply_calibration).pack(side="left", padx=4)

        body = ttk.Frame(self.root, padding=(8, 0, 8, 8))
        body.pack(fill="both", expand=True)
        source_frame = ttk.LabelFrame(body, text="Source and detection overlay", padding=4)
        source_frame.pack(side="left", fill="both", expand=True)
        self.source = tk.Canvas(source_frame, width=650, height=700, background="#202020")
        source_scroll = ttk.Scrollbar(source_frame, orient="vertical", command=self.source.yview)
        self.source.configure(yscrollcommand=source_scroll.set)
        self.source.pack(side="left", fill="both", expand=True)
        source_scroll.pack(side="right", fill="y")

        board_frame = ttk.LabelFrame(body, text="Editable Board", padding=8)
        board_frame.pack(side="right", fill="y")
        self.board = tk.Canvas(board_frame, width=390, height=330, background="#111111", highlightthickness=0)
        self.board.pack()
        self.board.bind("<ButtonPress-1>", self.route_press)
        self.board.bind("<B1-Motion>", self.route_motion)
        self.board.bind("<ButtonRelease-1>", self.route_release)
        ttk.Label(board_frame, textvariable=self._selected_label).pack(pady=(8, 2))
        ttk.Label(board_frame, text="Correction:").pack(anchor="w")
        options = list(NAMES.values()) + sorted(HAZARDS)
        ttk.Combobox(board_frame, textvariable=self._selected_orb, values=options,
                     state="readonly", width=18).pack(fill="x")
        ttk.Checkbutton(board_frame, text="Enhanced", variable=self._enhanced).pack(anchor="w")
        ttk.Checkbutton(board_frame, text="Locked", variable=self._locked).pack(anchor="w")
        ttk.Button(board_frame, text="Correct selected cell", command=self.correct_selected).pack(fill="x", pady=4)
        ttk.Button(board_frame, text="Confirm Board", command=self.confirm_board).pack(fill="x")
        profile_frame = ttk.LabelFrame(board_frame, text="Rule Profile", padding=6)
        profile_frame.pack(fill="x", pady=(10, 0))
        ttk.Label(profile_frame, text="Name:").pack(anchor="w")
        ttk.Entry(profile_frame, textvariable=self._profile_name).pack(fill="x")
        ttk.Label(profile_frame, text="Hazard policy:").pack(anchor="w", pady=(4, 0))
        ttk.Combobox(profile_frame, textvariable=self._hazard_policy, values=("avoid", "allow"),
                     state="readonly", width=12).pack(fill="x")
        search_controls = ttk.Frame(profile_frame)
        search_controls.pack(fill="x", pady=(4, 0))
        ttk.Label(search_controls, text="Attempts:").pack(side="left")
        ttk.Entry(search_controls, textvariable=self._search_attempts, width=7).pack(side="left", padx=(2, 6))
        ttk.Label(search_controls, text="Seed:").pack(side="left")
        ttk.Entry(search_controls, textvariable=self._search_seed, width=7).pack(side="left", padx=2)
        ttk.Button(search_controls, text="Search", command=self.search_route).pack(side="right")
        ttk.Label(profile_frame, textvariable=self._profile_label, wraplength=340).pack(anchor="w", pady=4)
        profile_buttons = ttk.Frame(profile_frame)
        profile_buttons.pack(fill="x")
        ttk.Button(profile_buttons, text="Create", command=self.create_profile).pack(side="left", expand=True, fill="x")
        ttk.Button(profile_buttons, text="Apply", command=self.apply_profile).pack(side="left", expand=True, fill="x", padx=2)
        ttk.Button(profile_buttons, text="Load JSON", command=self.load_profile).pack(side="left", expand=True, fill="x")
        ttk.Button(profile_buttons, text="Save JSON", command=self.save_profile).pack(side="left", expand=True, fill="x", padx=(2, 0))
        ttk.Label(board_frame, textvariable=self._evaluation, wraplength=370, justify="left").pack(anchor="w", pady=(10, 0))
        ttk.Label(self.root, textvariable=self._status, anchor="w", relief="sunken").pack(fill="x", side="bottom")

    def _show_error(self, message: str):
        from tkinter import messagebox
        messagebox.showerror("PAD Router", message, parent=self.root)

    @staticmethod
    def _format_evaluation(result: RouteEvaluation | None) -> str:
        if result is None:
            return "No Route evaluated"
        matches = ", ".join(
            f"{NAMES.get(match.key, match.key)}×{len(match.cells)}"
            for match in result.resolved_matches
        ) or "none"
        groups = ", ".join(
            f"G{item.index} {'pass' if item.satisfied else 'fail'}"
            for item in result.group_results
        ) or "none"
        conditions = ", ".join(
            f"{item.identifier}={'pass' if item.satisfied else 'fail'}"
            for item in result.condition_results
        ) or "none"
        return (f"Matches: {matches} | Cascades: {result.cascades} | Combos: {result.combo_count}\n"
                f"Groups: {groups}\nConditions: {conditions}\n"
                f"Hazard: {result.hazard_outcome} | Status: {result.diagnostic_status} | "
                f"Qualifying: {'yes' if result.qualifying else 'no'} | "
                f"Execution: {'yes' if result.execution_eligible else 'no'}\n{result.diagnostic}")

    def _display(self, state: BoardInspectionState):
        self._status.set(state.status)
        if state.rule_profile is not None:
            self._profile = state.rule_profile
            self._profile_name.set(state.rule_profile.name)
            self._hazard_policy.set(state.rule_profile.hazard_policy)
            self._profile_label.set(f"Current: {state.rule_profile.name}")
        result = state.route_evaluation
        self._evaluation.set(self._format_evaluation(result))
        if state.calibration:
            self._left.set(str(state.calibration.left))
            self._top.set(str(state.calibration.top))
            self._cell.set(str(state.calibration.cell))
        self.source.delete("all")
        if state.width and state.height and state.pixels:
            self._photo = _photo_from_screenshot((state.width, state.height, state.pixels), self.tk)
            self.source.create_image(0, 0, image=self._photo, anchor="nw")
            radius = max(8, min(36, (state.calibration.cell // 3) if state.calibration else 20))
            for item in state.overlay:
                x, y = item["x"], item["y"]
                uncertain = item["uncertain"]
                self.source.create_oval(x - radius, y - radius, x + radius, y + radius,
                                        outline="#ff4444" if uncertain else "#61dafb", width=3)
                self.source.create_text(x, y, text=item["label"], fill="white", font=("TkDefaultFont", 12, "bold"))
            route_points = tuple((item["x"], item["y"]) for item in state.route_overlay)
            if route_points:
                coords = tuple(value for point in route_points for value in point)
                if len(coords) > 2:
                    self.source.create_line(*coords, fill="#ffcc33", width=6,
                                             capstyle="round", joinstyle="round")
                for item, (x, y) in zip(state.route_overlay, route_points):
                    self.source.create_oval(x - 12, y - 12, x + 12, y + 12,
                                            outline="#ffcc33", width=2)
                    self.source.create_text(x, y, text=str(item["step"]), fill="white",
                                            font=("TkDefaultFont", 10, "bold"))
            self.source.configure(scrollregion=(0, 0, state.width, state.height))
        self.board.delete("all")
        if state.board is None:
            return
        size = 60
        colors = {"fire": "#d85245", "water": "#4b93db", "wood": "#58a85c",
                  "light": "#e6d45c", "dark": "#8058a8", "heart": "#e783ab",
                  "jammer": "#4a4a54", "poison": "#834c9e", "mortal_poison": "#632078", "bomb": "#9b6334"}
        for row in range(ROWS):
            for col in range(COLS):
                orb = state.board[row][col]
                x0, y0 = col * size, row * size
                color = colors.get(getattr(orb, "kind", ""), "#555555")
                if self._selected_cell == (row, col):
                    self.board.create_rectangle(x0 + 1, y0 + 1, x0 + size - 1, y0 + size - 1, outline="#ffffff", width=3)
                self.board.create_oval(x0 + 5, y0 + 5, x0 + size - 5, y0 + size - 5,
                                       fill=color, outline="#ff4444" if (row, col) in state.uncertain_cells else "#dddddd", width=2)
                self.board.create_text(x0 + size // 2, y0 + size // 2, text=orb_display(orb), fill="white",
                                       font=("TkDefaultFont", 14, "bold"))
        route = tuple(item["cell"] for item in state.route_overlay) or tuple(self._manual_route)
        if route:
            points = tuple((col * size + size // 2, row * size + size // 2)
                           for row, col in route)
            coords = tuple(value for point in points for value in point)
            if len(coords) > 2:
                self.board.create_line(*coords, fill="#ffcc33" if state.route_overlay else "#00e5ff", width=6,
                                       capstyle="round", joinstyle="round")
            for index, (x, y) in enumerate(points, 1):
                self.board.create_oval(x - 12, y - 12, x + 12, y + 12,
                                       outline="#ffcc33" if state.route_overlay else "#ffffff", width=2)
                self.board.create_text(x, y, text=str(index), fill="white",
                                       font=("TkDefaultFont", 10, "bold"))

    def _apply(self, action):
        try:
            self._display(action())
        except (OSError, RuntimeError, ValueError, TypeError, zlib.error) as exc:
            self._show_error(str(exc))

    def open_png(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(parent=self.root, filetypes=(("PNG image", "*.png"),))
        if path:
            self._manual_route.clear()
            self._apply(lambda: self.controller.load_png(path))

    def capture_device(self):
        self._manual_route.clear()
        self._apply(lambda: self.controller.capture_device(self._serial.get()))

    def apply_calibration(self):
        self._apply(lambda: self.controller.set_calibration(BoardCalibration(
            int(self._left.get()), int(self._top.get()), int(self._cell.get()))))

    def _cell_at(self, event) -> tuple[int, int] | None:
        cell = (event.y // 60, event.x // 60)
        return cell if 0 <= cell[0] < ROWS and 0 <= cell[1] < COLS else None

    def select_cell(self, event):
        cell = self._cell_at(event)
        if cell is not None:
            self._selected_cell = cell
            self._selected_label.set(f"Cell {cell[0] + 1},{cell[1] + 1}")
            self._display(self.controller.state)
        return "break"

    def route_press(self, event):
        cell = self._cell_at(event)
        if cell is None:
            return "break"
        self._selected_cell = cell
        self._selected_label.set(f"Cell {cell[0] + 1},{cell[1] + 1}")
        self._manual_route[:] = [cell]
        self._dragging_route = True
        self._display(self.controller.state)
        return "break"

    def route_motion(self, event):
        if not self._dragging_route:
            return "break"
        cell = self._cell_at(event)
        if cell is None or cell in self._manual_route:
            return "break"
        previous = self._manual_route[-1]
        if abs(previous[0] - cell[0]) + abs(previous[1] - cell[1]) != 1:
            return "break"
        self._manual_route.append(cell)
        self._display(self.controller.state)
        return "break"

    def route_release(self, event):
        if not self._dragging_route:
            return "break"
        self._dragging_route = False
        state = self.controller.state
        if state.board is not None and state.rule_profile is not None:
            self._apply(lambda: self.controller.evaluate_manual_route(tuple(self._manual_route)))
        else:
            self._display(state)
        return "break"

    def correct_selected(self):
        if self._selected_cell is None:
            self._show_error("Select a Board cell first")
            return
        value = self._selected_orb.get()
        if value in NAMES.values():
            value += "+" if self._enhanced.get() else ""
            value += "*" if self._locked.get() else ""
        self._manual_route.clear()
        self._apply(lambda: self.controller.correct_cell(*self._selected_cell, value))

    def confirm_board(self):
        try:
            self.controller.confirm_board()
            self._manual_route.clear()
            self._display(self.controller.state)
        except ValueError as exc:
            self._show_error(str(exc))

    def create_profile(self):
        try:
            self._profile = RuleProfile(self._profile_name.get(), hazard_policy=self._hazard_policy.get())
            self._profile_label.set(f"Created: {self._profile.name}")
        except (ValueError, TypeError) as exc:
            self._show_error(str(exc))

    def apply_profile(self):
        if self._profile is None:
            self._show_error("Create or load a Rule Profile first")
            return
        self._apply(lambda: self.controller.set_rule_profile(self._profile))

    def search_route(self):
        self._manual_route.clear()
        self._apply(lambda: self.controller.search_qualifying_route(
            RouteSearchOptions(attempts=int(self._search_attempts.get()), seed=int(self._search_seed.get()))
        ))

    def load_profile(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(parent=self.root,
                                          filetypes=(("Rule Profile JSON", "*.json"), ("JSON", "*.json")))
        if not path:
            return
        try:
            self._display(self.controller.load_rule_profile(path))
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            self._show_error(str(exc))

    def save_profile(self):
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension=".json",
                                            filetypes=(("Rule Profile JSON", "*.json"), ("JSON", "*.json")))
        if not path:
            return
        try:
            self.controller.save_rule_profile(path, self._profile)
            self._profile_label.set(f"Saved: {self._profile.name if self._profile else 'Rule Profile'}")
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            self._show_error(str(exc))


BoardInspectionGUI = BoardInspectionApp


def main() -> None:
    try:
        app = BoardInspectionApp()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    app.root.mainloop()


if __name__ == "__main__":
    main()
