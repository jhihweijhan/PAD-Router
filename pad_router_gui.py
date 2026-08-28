#!/usr/bin/env python3
"""Native desktop board inspection for PAD Router.

The controller is deliberately independent of tkinter.  It owns the small
workflow the GUI needs while recognition and device capture remain injected
adapters around the existing PAD Router functions.
"""

from __future__ import annotations

import binascii
import json
import math
import subprocess
import struct
import tempfile
import zlib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterable

from pad_router import (
    COLS,
    ConditionGroup,
    ExternalCondition,
    HAZARDS,
    LeaderCondition,
    NAMES,
    ROWS,
    Grid,
    Orb,
    PlayVerification,
    RouteEvaluation,
    RouteSearchOptions,
    RouteSearchResult,
    RuleProfile,
    _cell_features,
    detect_board_pixels,
    evaluate_manual_route,
    load_rule_profile,
    orb_display,
    orb_match_key,
    play,
    search_qualifying_route,
    save_rule_profile,
    screenshot,
)


Screenshot = tuple[int, int, bytes]
Board = tuple[tuple[object, ...], ...]
Detector = Callable[[int, int, bytes, Grid], Board]
Capture = Callable[[str], Screenshot]
Executor = Callable[..., bool | PlayVerification]


class OrbPrototypeModel:
    """Small local, no-training classifier fed by corrected board cells."""

    def __init__(self, path: Path | None = None):
        self.path = path
        self.samples: list[dict[str, object]] = []
        if path is not None and path.exists():
            try:
                self.samples = json.loads(path.read_text()).get("samples", [])
            except (OSError, ValueError):
                self.samples = []

    @classmethod
    def default(cls) -> "OrbPrototypeModel":
        return cls(Path(__file__).resolve().parent / ".pad-router" / "orb-prototypes.json")

    @staticmethod
    def _feature(feature) -> list[float]:
        center_hue = getattr(feature, "center_hue", None)
        values = [feature.hue, feature.saturation, feature.value, feature.dark, feature.white,
                  feature.orange, feature.purple, feature.blue, feature.green, feature.plus,
                  center_hue if center_hue is not None else -1.0,
                  getattr(feature, "center_saturation", 0.0),
                  getattr(feature, "center_value", 0.0)]
        pattern = getattr(feature, "center_pattern", None)
        if pattern is not None:
            values.extend(pattern)
        return values

    @staticmethod
    def _distance(left: list[float], right: list[float]) -> float:
        hue = min(abs(left[0] - right[0]), 1.0 - abs(left[0] - right[0]))
        weights = (1.4, 0.45, 0.75, 0.35, 0.25, 0.25, 0.25, 0.25, 0.25, 0.4)
        distance = sum(weight * abs(a - b) for weight, a, b in zip(
            weights, (hue, *left[1:]), (0.0, *right[1:])
        ))
        if (len(left) >= 13 and len(right) >= 13
                and left[10] >= 0 and right[10] >= 0):
            center_hue = min(abs(left[10] - right[10]), 1.0 - abs(left[10] - right[10]))
            distance += (0.5 * center_hue
                         + 0.2 * abs(left[11] - right[11])
                         + 0.2 * abs(left[12] - right[12]))
        if (len(left) == len(right) and len(left) > 18
                and len(left[13:]) == len(right[13:])):
            distance += 0.8 * sum(abs(a - b) for a, b in zip(left[13:], right[13:])) / len(left[13:])
        return distance

    @staticmethod
    def _record(orb: Orb, feature, human: bool,
                cell: tuple[int, int] | None = None) -> dict[str, object]:
        record = {"kind": orb.kind, "color": orb.color, "enhanced": orb.enhanced,
                  "visual_class": orb.visual_class, "locked": orb.locked,
                  "feature": OrbPrototypeModel._feature(feature), "human": human}
        if cell is not None:
            record["cell"] = list(cell)
        return record

    def _save(self, samples: list[dict[str, object]] | None = None) -> None:
        if self.path is None:
            return
        try:
            payload = json.dumps(
                {"samples": self.samples if samples is None else samples},
                ensure_ascii=False,
            )
        except Exception as exc:
            raise OSError("原型模型資料序列化失敗") from exc

        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=f".{self.path.name}.", suffix=".tmp", delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(payload)
                handle.flush()
            temporary.replace(self.path)
        except Exception as exc:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            if isinstance(exc, OSError):
                raise
            raise OSError("原型模型暫存檔寫入或取代失敗") from exc

    def learn(self, orb: object, feature, human: bool,
              cell: tuple[int, int] | None = None) -> bool:
        if not isinstance(orb, Orb) or orb_match_key(orb) is None:
            return False
        record = self._record(orb, feature, human, cell)
        candidate = self.samples
        if human:
            candidate = [
                sample for sample in self.samples
                if not (sample.get("feature") == record["feature"]
                        and sample.get("cell") == record.get("cell"))
            ]
        candidate = [*candidate, record]
        self._save(candidate)
        self.samples = candidate
        return True

    def _predict_sample(self, feature) -> tuple[Orb, float, dict[str, object]] | None:
        candidates = self.samples
        if not candidates:
            return None
        vector = self._feature(feature)
        scored = sorted(((self._distance(vector, sample["feature"]) + (0 if sample.get("human") else .02), sample)
                         for sample in candidates),
                        key=lambda item: item[0])
        distance, sample = scored[0]
        label = (sample["kind"], sample.get("color"), sample.get("enhanced"), sample.get("locked"))
        runner_up = next((score for score, other in scored[1:]
                          if (other["kind"], other.get("color"), other.get("enhanced"), other.get("locked")) != label),
                         math.inf)
        if distance > 0.14 or runner_up - distance < 0.035:
            return None
        return Orb(str(sample["kind"]), sample.get("color"), bool(sample.get("enhanced")),
                   sample.get("visual_class"), bool(sample.get("locked"))), distance, sample

    def predict(self, feature) -> tuple[Orb, float] | None:
        prediction = self._predict_sample(feature)
        return prediction[:2] if prediction is not None else None

    def detect(self, width: int, height: int, pixels: bytes, grid: Grid, baseline: Detector) -> Board:
        board = [list(row) for row in baseline(width, height, pixels, grid)]
        if not self.samples:
            return tuple(map(tuple, board))
        for row in range(ROWS):
            for col in range(COLS):
                try:
                    feature = _cell_features(width, height, pixels, grid.point(row, col), grid.cell)
                except ValueError:
                    continue
                prediction = self._predict_sample(feature)
                if prediction is None:
                    continue
                orb, distance, sample = prediction
                baseline_key = orb_match_key(board[row][col])
                if (baseline_key in (5, 6)
                        and orb_match_key(orb) != baseline_key
                        and not sample.get("human")):
                    continue
                if baseline_key is None or distance <= 0.06:
                    board[row][col] = orb
        return tuple(map(tuple, board))


# 介面顯示名稱；規則檔與核心運算仍使用英文代號。
ORB_LABELS = {
    "fire": "火", "water": "水", "wood": "木", "light": "光", "dark": "暗", "heart": "心",
    "jammer": "干擾", "poison": "毒", "mortal_poison": "猛毒", "bomb": "炸彈",
}
ORB_KINDS = {label: kind for kind, label in ORB_LABELS.items()}

CONDITION_PRESETS = {
    "不限（以最大 Combo 為主）": (),
    "至少 3 Combo": (LeaderCondition.combo_minimum(3),),
    "至少 5 Combo": (LeaderCondition.combo_minimum(5),),
    "至少 7 Combo": (LeaderCondition.combo_minimum(7),),
    "消除火珠": (LeaderCondition.attribute("fire"),),
    "消除水珠": (LeaderCondition.attribute("water"),),
    "消除木珠": (LeaderCondition.attribute("wood"),),
    "消除光珠": (LeaderCondition.attribute("light"),),
    "消除暗珠": (LeaderCondition.attribute("dark"),),
    "消除心珠": (LeaderCondition.attribute("heart"),),
    "同時消除火、水、木": (LeaderCondition.simultaneous_attributes(("fire", "water", "wood")),),
    "同時消除火、水、木、光、暗": (LeaderCondition.simultaneous_attributes(("fire", "water", "wood", "light", "dark")),),
    "同色連 5 顆以上": (LeaderCondition.connected_orb_count(5),),
    "強化珠至少消除 1 顆": (LeaderCondition.enhanced_orb(1),),
    **{f"{ORB_LABELS[name]}珠至少消除 2 組": (LeaderCondition.match_count(name, 2),)
       for name in NAMES.values()},
}
SHAPE_PRESETS = {
    "色珠一橫列": "full_row", "9 顆正方形": "box_3x3", "十字型": "cross",
    "L 型": "l", "T 型": "t",
}
COLORED_PRESETS = tuple(SHAPE_PRESETS) + ("4 顆消除",)
CONDITION_OPTIONS = tuple(CONDITION_PRESETS) + COLORED_PRESETS
CONDITION_COLORS = tuple(ORB_LABELS[name] for name in NAMES.values())
NO_CONDITION = "不限（以最大 Combo 為主）"
GROUP_OPERATORS = {"全部符合": "all", "任一符合": "any"}
HAZARD_POLICIES = {"避免危害珠": "avoid", "允許危害珠": "allow"}
EXTERNAL_CONDITIONS = {
    "無": (),
    "HP 條件已確認": (ExternalCondition("HP 條件", confirmed=True),),
    "HP 條件未確認": (ExternalCondition("HP 條件", confirmed=False),),
    "技能條件已確認": (ExternalCondition("技能條件", confirmed=True),),
    "技能條件未確認": (ExternalCondition("技能條件", confirmed=False),),
}
CASCADE_OPTIONS = {"計入落珠連鎖": True, "只計轉珠直接消除": False}


def rule_profile_from_selections(condition_selections: Iterable[tuple[str, str] | str], operator_label: str,
                                 hazard_label: str, external_label: str) -> RuleProfile:
    """Build a profile solely from the GUI's fixed choices."""

    selections: list[tuple[str, str]] = []
    for selection in condition_selections:
        label, color = selection if isinstance(selection, tuple) else (selection, "火")
        if label != NO_CONDITION and (label, color) not in selections:
            selections.append((label, color))
    conditions: list[LeaderCondition] = []
    for label, color in selections:
        if label in SHAPE_PRESETS:
            conditions.append(LeaderCondition.shape(SHAPE_PRESETS[label], orb_type=ORB_KINDS[color]))
        elif label == "4 顆消除":
            conditions.append(LeaderCondition.connected_orb_count(4, ORB_KINDS[color], exact=True))
        else:
            conditions.extend(CONDITION_PRESETS[label])
    groups = (ConditionGroup(conditions, GROUP_OPERATORS[operator_label]),) if conditions else ()
    labels = tuple(f"{label}（{color}）" if label in COLORED_PRESETS else label for label, color in selections)
    return RuleProfile("、".join(labels) or "最大 Combo", condition_groups=groups,
                       external_conditions=EXTERNAL_CONDITIONS[external_label],
                       hazard_policy=HAZARD_POLICIES[hazard_label])


@dataclass(frozen=True)
class BoardCalibration:
    """Top-left pixel and cell size for a Standard Board."""

    left: int = 0
    top: int = 1380
    cell: int = 180

    def validate(self, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            raise ValueError("截圖尺寸必須為正數")
        if self.left < 0 or self.top < 0 or self.cell <= 0:
            raise ValueError("校正座標不可為負數，格寬必須為正數")
        if self.left + COLS * self.cell > width or self.top + ROWS * self.cell > height:
            raise ValueError("校正範圍必須讓 6×5 標準盤面完整位於截圖內")

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
            raise ValueError("截圖太小，無法容納 6×5 標準盤面")
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
        raise ValueError("PNG 像素資料大小不正確")
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
            raise ValueError(f"不支援的 PNG 濾鏡：{filter_type}")
        rows.append(bytes(current))
    return b"".join(rows)


def decode_png(path: str | Path) -> Screenshot:
    """Decode an 8-bit, non-interlaced PNG into the existing BGRA format."""

    data = Path(path).read_bytes()
    signature = b"\x89PNG\r\n\x1a\n"
    if not data.startswith(signature):
        raise ValueError("請選擇 PNG 圖片")
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
            raise ValueError("PNG 區塊資料不完整")
        payload = data[payload_start:payload_end]
        checksum = struct.unpack_from(">I", data, payload_end)[0]
        if binascii.crc32(kind + payload) & 0xFFFFFFFF != checksum:
            raise ValueError("PNG 區塊檢查碼不符")
        if kind == b"IHDR":
            if length != 13:
                raise ValueError("PNG 標頭無效")
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
        raise ValueError("僅支援 8 位元、非交錯式 PNG 圖片")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
    if channels is None:
        raise ValueError("不支援的 PNG 色彩格式")
    if color_type == 3 and (palette is None or len(palette) % 3):
        raise ValueError("索引式 PNG 缺少有效調色盤")
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
                raise ValueError("PNG 調色盤索引超出範圍")
            red, green, blue = (palette or b"")[palette_offset:palette_offset + 3]
            alpha = transparency[palette_index] if transparency and palette_index < len(transparency) else 255
        result[index * 4:index * 4 + 4] = bytes((blue, green, red, alpha))
    return width, height, bytes(result)


def _board_shape(board: Board) -> None:
    if len(board) != ROWS or any(len(row) != COLS for row in board):
        raise ValueError("辨識器必須回傳 5×6 盤面")


def _uncertain_cells(board: Board) -> tuple[tuple[int, int], ...]:
    return tuple((row, col) for row in range(ROWS) for col in range(COLS)
                 if orb_match_key(board[row][col]) is None)


def _coerce_orb(value: object) -> Orb:
    if isinstance(value, Orb):
        if value.kind == "normal" and value.color not in NAMES:
            raise ValueError("普通珠的顏色必須介於 1 到 6")
        if value.kind not in HAZARDS and value.kind != "normal":
            raise ValueError(f"不支援的珠子類型：{value.kind}")
        return value
    if isinstance(value, int):
        if value not in NAMES:
            raise ValueError("珠子顏色必須是介於 1 到 6 的整數")
        return Orb("normal", value)
    if not isinstance(value, str):
        raise ValueError("珠子修正值必須是珠子、顏色數字或珠子名稱")
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
    raise ValueError(f"不支援的珠子類型：{value}")


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
    status: str = "尚未載入來源"
    rule_profile: RuleProfile | None = None
    route_evaluation: RouteEvaluation | None = None
    route_approved: bool = False
    verification: PlayVerification | None = None
    route_search: RouteSearchResult | None = None
    route_overlay: tuple[dict[str, object], ...] = ()
    search_options: RouteSearchOptions | None = None
    learning_status: str = "尚未學習資料"

    @property
    def source_path(self) -> str | None:
        return self.source_name

    @property
    def editable_board(self) -> Board | None:
        return self.board


class BoardInspectionController:
    """Public board inspection seam used by the GUI and standard-library tests."""

    def __init__(self, detector: Detector = detect_board_pixels, capture: Capture = screenshot,
                 executor: Executor = play, model: OrbPrototypeModel | None = None,
                 max_recognition_attempts: int = 2):
        self._detector = detector
        self._capture = capture
        self._executor = executor
        self._model = model
        self._max_recognition_attempts = 2
        self.max_recognition_attempts = max_recognition_attempts
        self.state = BoardInspectionState()

    @property
    def max_recognition_attempts(self) -> int:
        return self._max_recognition_attempts

    @max_recognition_attempts.setter
    def max_recognition_attempts(self, value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
            raise ValueError("主動辨識次數必須是 1 到 5 的整數")
        self._max_recognition_attempts = value

    def _detect(self, width: int, height: int, pixels: bytes, grid: Grid) -> Board:
        if self._model is None:
            return self._detector(width, height, pixels, grid)
        return self._model.detect(width, height, pixels, grid, self._detector)
    def _detect_with_retries(self, width: int, height: int, pixels: bytes, grid: Grid) -> tuple[Board, int]:
        for attempt in range(1, self.max_recognition_attempts + 1):
            detected = self._detect(width, height, pixels, grid)
            _board_shape(detected)
            if not _uncertain_cells(detected):
                return detected, attempt
        return detected, self.max_recognition_attempts


    def _learn_implicit(self, label: str = "上一張", data_label: str = "隱式資料") -> str:
        state = self.state
        if (self._model is None or state.board is None or state.width is None or state.height is None
                or state.pixels is None or state.calibration is None):
            return ""
        grid = state.calibration.to_grid()
        learned = 0
        for row in range(ROWS):
            for col in range(COLS):
                try:
                    feature = _cell_features(state.width, state.height, state.pixels,
                                             grid.point(row, col), grid.cell)
                except ValueError:
                    continue
                learned += self._model.learn(state.board[row][col], feature, human=False,
                                             cell=(row, col))
        return f"{label}已學習（{learned} 格{data_label}）" if learned else ""

    def accept_current_board(self) -> BoardInspectionState:
        learned = self._learn_implicit("目前盤面", "低權重資料")
        if learned:
            self.state = replace(self.state, learning_status=learned)
        return self.state

    def _learn_human(self, row: int, col: int, orb: Orb) -> None:
        if (self._model is None or self.state.width is None or self.state.height is None
                or self.state.pixels is None or self.state.calibration is None):
            return
        grid = self.state.calibration.to_grid()
        try:
            feature = _cell_features(self.state.width, self.state.height, self.state.pixels,
                                     grid.point(row, col), grid.cell)
        except ValueError:
            return
        self._model.learn(orb, feature, human=True, cell=(row, col))

    def _with_source(self, source: Screenshot, source_name: str) -> BoardInspectionState:
        width, height, pixels = source
        if width <= 0 or height <= 0 or len(pixels) != width * height * 4:
            raise ValueError("截圖必須包含 width×height 個 BGRA 像素")
        calibration = infer_calibration(width, height)
        learned = self._learn_implicit()
        detected, recognition_attempts = self._detect_with_retries(
            width, height, pixels, calibration.to_grid()
        )
        return self._replace_source(source_name, source, calibration, detected, learned,
                                    recognition_attempts)

    def _replace_source(self, source_name: str, source: Screenshot,
                        calibration: BoardCalibration, detected: Board, learned: str = "",
                        recognition_attempts: int = 1) -> BoardInspectionState:
        uncertain = _uncertain_cells(detected)
        status = (f"已載入 {source_name}；有 {len(uncertain)} 格需要手動修正"
                  if uncertain else f"已載入 {source_name}；辨識完成")
        status += f"；主動辨識第 {recognition_attempts}/{self.max_recognition_attempts} 次"
        if uncertain:
            status += "；仍有問號，請手動修正"
        elif recognition_attempts < self.max_recognition_attempts:
            status += "；已提前停止"
        if learned:
            status += f"；{learned}"
        state = BoardInspectionState(source_name, source[0], source[1], source[2], calibration,
                                     detected, detected, detected if not uncertain else None, not uncertain,
                                     uncertain, (), status, learning_status=learned or "目前畫面尚未學習")
        if self.state.rule_profile is not None:
            state = replace(state, rule_profile=self.state.rule_profile)
        self.state = self._with_overlay(state)
        return self.state

    def _with_overlay(self, state: BoardInspectionState) -> BoardInspectionState:
        if state.board is None or state.calibration is None:
            return state
        grid = state.calibration.to_grid()
        source_board = state.board
        overlay = tuple({"cell": (row, col), "x": grid.point(row, col)[0],
                         "y": grid.point(row, col)[1], "label": orb_display(source_board[row][col]),
                         "uncertain": (row, col) in state.uncertain_cells}
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
            raise ValueError("僅支援 PNG 圖片")
        return self._with_source(decode_png(path), str(path))

    def capture_device(self, serial: str) -> BoardInspectionState:
        serial = serial.strip()
        if not serial:
            raise ValueError("請選擇裝置")
        return self._with_source(self._capture(serial), serial)

    def set_calibration(self, calibration: BoardCalibration) -> BoardInspectionState:
        if self.state.pixels is None or self.state.width is None or self.state.height is None:
            raise ValueError("請先載入圖片或擷取裝置畫面，再校正盤面")
        if not isinstance(calibration, BoardCalibration):
            raise TypeError("calibration 必須是 BoardCalibration")
        calibration.validate(self.state.width, self.state.height)
        detected, recognition_attempts = self._detect_with_retries(
            self.state.width, self.state.height, self.state.pixels, calibration.to_grid()
        )
        return self._replace_source(
            self.state.source_name or "source",
            (self.state.width, self.state.height, self.state.pixels),
            calibration, detected, recognition_attempts=recognition_attempts,
        )

    def calibrate(self, left: BoardCalibration | int, top: int | None = None,
                  cell: int | None = None) -> BoardInspectionState:
        if isinstance(left, BoardCalibration):
            if top is not None or cell is not None:
                raise TypeError("不可同時提供 BoardCalibration 與座標參數")
            calibration = left
        elif top is not None and cell is not None:
            calibration = BoardCalibration(left, top, cell)
        else:
            raise TypeError("calibrate 需要 BoardCalibration，或 left、top、cell 三個座標參數")
        return self.set_calibration(calibration)

    def correct_cell(self, row: int, col: int, value: object) -> BoardInspectionState:
        if self.state.board is None:
            raise ValueError("請先載入圖片或擷取裝置畫面，再修正珠子")
        if not (0 <= row < ROWS and 0 <= col < COLS):
            raise ValueError("珠子必須位於 6×5 標準盤面內")
        board = [list(items) for items in self.state.board]
        orb = _coerce_orb(value)
        board[row][col] = orb
        updated_board = tuple(map(tuple, board))
        uncertain = _uncertain_cells(updated_board)
        self._learn_human(row, col, orb)
        updated = replace(self.state, board=updated_board,
                          confirmed_board=updated_board if not uncertain else None,
                          confirmed=not uncertain, uncertain_cells=uncertain,
                          overlay=(), route_evaluation=None, route_approved=False, verification=None,
                          route_search=None, route_overlay=(), search_options=None,
                          status="珠子已修正並寫入模型；辨識結果已自動更新",
                          learning_status="人工標記已寫入模型")
        self.state = self._with_overlay(updated)
        return self.state

    def confirm_board(self) -> Board:
        if self.state.board is None:
            raise ValueError("尚未載入盤面")
        if self.state.uncertain_cells:
            raise ValueError("請先手動修正無法辨識的珠子，才能確認盤面")
        self.state = replace(self.state, confirmed_board=self.state.board, confirmed=True,
                             uncertain_cells=(), route_evaluation=None, route_approved=False, verification=None,
                             route_search=None, route_overlay=(), search_options=None,
                             status="盤面已確認")
        return self.state.board

    def set_rule_profile(self, profile: RuleProfile) -> BoardInspectionState:
        if not isinstance(profile, RuleProfile):
            raise TypeError("profile 必須是 RuleProfile")
        self.state = replace(self.state, rule_profile=profile, route_evaluation=None,
                             route_approved=False, verification=None, route_search=None, route_overlay=(), search_options=None,
                             status=f"已套用規則設定：{profile.name}")
        return self.state

    def save_rule_profile(self, path: str | Path, profile: RuleProfile | None = None) -> None:
        profile = profile or self.state.rule_profile
        if profile is None:
            raise ValueError("尚未套用規則設定")
        save_rule_profile(profile, path)

    def load_rule_profile(self, path: str | Path) -> BoardInspectionState:
        return self.set_rule_profile(load_rule_profile(path))

    def evaluate_manual_route(self, path: Iterable[tuple[int, int]],
                               profile: RuleProfile | None = None, cascade: bool = True) -> RouteEvaluation:
        board = self.state.confirmed_board or self.state.board
        if board is None:
            raise ValueError("請先載入並確認盤面，再評估路徑")
        profile = profile or self.state.rule_profile
        if profile is None:
            raise ValueError("請先套用規則設定，再評估路徑")
        result = evaluate_manual_route(board, path, profile, confirmed=self.state.confirmed, cascade=cascade)
        self.state = replace(self.state, route_evaluation=result, route_approved=False,
                             verification=None,
                             route_search=None, route_overlay=self._route_overlay(result), search_options=None,
                             status=f"路徑已評估：{'符合' if result.qualifying else '不符合'}條件")
        return result

    def search_qualifying_route(self, options: RouteSearchOptions | None = None) -> RouteSearchResult:
        board = self.state.confirmed_board or self.state.board
        if board is None:
            raise ValueError("請先載入並確認盤面，再搜尋路徑")
        profile = self.state.rule_profile
        if profile is None:
            raise ValueError("請先套用規則設定，再搜尋路徑")
        options = options if options is not None else RouteSearchOptions()
        result = search_qualifying_route(board, profile, options, confirmed=self.state.confirmed)
        candidate = result.candidate
        self.state = replace(self.state, route_search=result, route_evaluation=candidate,
                             route_approved=False, verification=None, route_overlay=self._route_overlay(candidate),
                             search_options=options,
                             status=("搜尋完成：找到符合條件的路徑" if candidate and candidate.qualifying
                                     else "搜尋完成：沒有符合條件的路徑"))
        return result

    def search_route(self, options: RouteSearchOptions | None = None) -> RouteSearchResult:
        return self.search_qualifying_route(options)

    def invalidate_route(self, status: str = "路徑已失效；請確認設定後重新搜尋") -> BoardInspectionState:
        self.state = replace(self.state, route_search=None, route_evaluation=None,
                             route_approved=False, verification=None, route_overlay=(), search_options=None,
                             status=status)
        return self.state

    def approve_route(self, explicit_confirmation: bool = False) -> RouteEvaluation:
        result = self.state.route_evaluation
        if result is None or not result.execution_eligible:
            raise ValueError("僅能核准已確認盤面上、符合條件的路徑")
        if not explicit_confirmation:
            raise ValueError("必須明確確認路徑")
        self.state = replace(self.state, route_approved=True, status="路徑已核准")
        return result

    def execute_route(self, serial: str, explicit_confirmation: bool = False,
                      delay: float = 0.04, hold_delay: float = 0.15,
                      lift_threshold: float = 12.0, max_corrections: int = 2) -> bool:
        result = self.state.route_evaluation
        if (result is None or self.state.confirmed_board is None
                or not self.state.confirmed or not result.execution_eligible):
            raise ValueError("僅能執行已確認盤面上、符合條件的路徑")
        if not explicit_confirmation and not self.state.route_approved:
            raise ValueError("必須明確確認路徑")
        serial = serial.strip()
        if not serial:
            raise ValueError("請選擇裝置")
        calibration = self.state.calibration
        if calibration is None:
            raise ValueError("執行前必須先校正盤面")
        if explicit_confirmation:
            self.approve_route(explicit_confirmation=True)
        self.state = replace(self.state, verification=None,
                             status="正在執行路徑；安全 ADB 驗證進行中")
        verification: PlayVerification | None = None

        def receive(report: PlayVerification) -> None:
            nonlocal verification
            verification = report

        outcome = self._executor(
            serial, result.route, calibration.to_grid(), delay, hold_delay, lift_threshold,
            self.state.confirmed_board, max_corrections, on_verification=receive,
        )
        if isinstance(outcome, PlayVerification):
            verification = outcome
            succeeded = outcome.success
        else:
            succeeded = bool(outcome)
        if verification is None:
            verification = PlayVerification(
                result.expected_board, None, None, succeeded,
                "executor_result" if succeeded else "verification_unavailable",
            )
        succeeded = succeeded and verification.success
        if not succeeded and verification.success:
            verification = replace(verification, success=False, status="gesture_failed")
        if succeeded:
            status = ("手勢已送出；手勢後盤面驗證成功（0 格不符）。"
                      "再次規劃前請擷取新盤面。"
                      if verification.detected_board is not None else
                      "手勢已送出；安全 ADB 流程已完成，但沒有手勢後盤面的比對結果。"
                      "再次規劃前請擷取新盤面。")
        elif verification.detected_board is None:
            status = ("手勢已安全放開；沒有可供驗證的手勢後盤面。"
                      "請擷取新盤面後再試。")
        else:
            mismatch = (f"{verification.mismatches} 格不符"
                        if verification.mismatches is not None else "比對結果不確定")
            status = (f"手勢已安全放開；手勢後盤面驗證失敗（{mismatch}）。"
                      "請擷取新盤面後再試。")
        self.state = replace(self.state, verification=verification, route_approved=False,
                             route_search=None, route_evaluation=None, route_overlay=(),
                             search_options=None, status=status)
        return succeeded


def _fit_scale(width: int, height: int, available_width: int,
               available_height: int) -> tuple[float, int, int]:
    if width <= 0 or height <= 0:
        raise ValueError("截圖尺寸必須為正數")
    if available_width <= 0 or available_height <= 0:
        raise ValueError("可視區域尺寸必須為正數")
    scale = min(1.0, available_width / width, available_height / height)
    return scale, max(1, int(width * scale)), max(1, int(height * scale))


def _photo_from_screenshot(screenshot_data: Screenshot, tk_module,
                           display_size: tuple[int, int] | None = None):
    width, height, pixels = screenshot_data
    display_width, display_height = display_size or (width, height)
    if display_width <= 0 or display_height <= 0:
        raise ValueError("顯示尺寸必須為正數")
    rgb = bytearray(display_width * display_height * 3)
    for display_y in range(display_height):
        source_y = display_y * height // display_height
        source_row = source_y * width * 4
        display_row = display_y * display_width * 3
        for display_x in range(display_width):
            source_x = display_x * width // display_width
            blue, green, red = pixels[source_row + source_x * 4:source_row + source_x * 4 + 3]
            rgb[display_row + display_x * 3:display_row + display_x * 3 + 3] = bytes((red, green, blue))
    ppm = f"P6\n{display_width} {display_height}\n255\n".encode() + bytes(rgb)
    return tk_module.PhotoImage(data=ppm, format="PPM")


class BoardInspectionApp:
    """Small tkinter view for the controller's board inspection workflow."""

    def __init__(self, root=None, controller: BoardInspectionController | None = None):
        try:
            import tkinter as tk
            from tkinter import ttk
        except ModuleNotFoundError as exc:
            raise RuntimeError("開啟桌面介面需要 Python 的 tkinter 模組") from exc

        self.tk = tk
        self.ttk = ttk
        self.root = root or tk.Tk()
        self.root.title("PAD Router — 珠盤判讀")
        self.controller = controller or BoardInspectionController(model=OrbPrototypeModel.default())
        self._photo = None
        self._display_scale = 1.0
        self._selected_cell: tuple[int, int] | None = None
        self._manual_route: list[tuple[int, int]] = []
        self._dragging_route = False
        self._correction_mode = False
        self._selected_orb = tk.StringVar(value="火")
        self._enhanced = tk.BooleanVar()
        self._locked = tk.BooleanVar()
        self._serial = tk.StringVar()
        self._selected_label = tk.StringVar(value="尚未選取珠子")
        self._condition_choices = [tk.StringVar(value=NO_CONDITION) for _ in range(3)]
        self._condition_colors = [tk.StringVar(value="不指定") for _ in range(3)]
        self._condition_operator = tk.StringVar(value="全部符合")
        self._hazard_policy = tk.StringVar(value="避免危害珠")
        self._external_condition = tk.StringVar(value="無")
        self._search_attempts = tk.StringVar(value="50")
        self._search_steps = tk.StringVar(value="50")
        self._search_seed = tk.StringVar(value="0")
        self._cascade = tk.StringVar(value="計入落珠連鎖")
        self._recognition_attempts = tk.StringVar(value=str(self.controller.max_recognition_attempts))
        self._profile_label = tk.StringVar(value="尚未套用規則設定")
        self._evaluation = tk.StringVar(value="尚未評估路徑")
        self._verification = tk.StringVar(value="尚無手勢後驗證結果")
        self._learning = tk.StringVar(value=self.controller.state.learning_status)
        self._status = tk.StringVar(value=self.controller.state.status)
        self._syncing_controls = False
        self._build()
        if self.controller.state.rule_profile is None:
            self._apply_profile_from_controls()
        else:
            self._display(self.controller.state)

    def _build(self):
        tk, ttk = self.tk, self.ttk
        controls = ttk.Frame(self.root, padding=8)
        controls.pack(fill="x")
        ttk.Button(controls, text="開啟 PNG", command=self.open_png).pack(side="left")
        ttk.Label(controls, text="裝置序號：").pack(side="left", padx=(12, 2))
        self._serial_box = ttk.Combobox(controls, width=18, textvariable=self._serial, state="readonly")
        self._serial_box.pack(side="left")
        ttk.Button(controls, text="更新裝置", command=self.refresh_devices).pack(side="left", padx=4)
        ttk.Button(controls, text="擷取畫面", command=self.capture_device).pack(side="left", padx=4)
        ttk.Button(controls, text="重新自動校正", command=self.auto_calibration).pack(side="left", padx=(12, 4))

        body = ttk.Frame(self.root, padding=(8, 0, 8, 8))
        body.pack(fill="both", expand=True)
        source_frame = ttk.LabelFrame(body, text="來源畫面與辨識標示", padding=4)
        source_frame.pack(side="left", fill="both", expand=True)
        self.source = tk.Canvas(source_frame, width=650, height=700, background="#202020")
        source_scroll = ttk.Scrollbar(source_frame, orient="vertical", command=self.source.yview)
        self.source.configure(yscrollcommand=source_scroll.set)
        self.source.pack(side="left", fill="both", expand=True)
        self.source.bind("<Configure>", self._source_resized)
        source_scroll.pack(side="right", fill="y")

        board_frame = ttk.LabelFrame(body, text="可編輯盤面", padding=8)
        board_frame.pack(side="right", fill="y")
        self.board = tk.Canvas(board_frame, width=390, height=330, background="#111111",
                               highlightthickness=2, takefocus=True)
        self.board.pack()
        self.board.bind("<ButtonPress-1>", self.route_press)
        self.board.bind("<B1-Motion>", self.route_motion)
        self.board.bind("<ButtonRelease-1>", self.route_release)
        self.board.bind("<Key>", self.answer_key)
        ttk.Label(board_frame, textvariable=self._selected_label).pack(pady=(8, 2))
        self._review_frame = ttk.LabelFrame(board_frame, text="回答模型無法判斷的珠子", padding=6)
        self._review_frame.pack(fill="x", pady=(4, 0))
        self._review_message = tk.StringVar(value="載入盤面後，這裡會顯示需要回答的珠子。")
        ttk.Label(self._review_frame, textvariable=self._review_message, wraplength=370,
                  justify="left").pack(anchor="w")
        answer_buttons = ttk.Frame(self._review_frame)
        answer_buttons.pack(fill="x", pady=(4, 0))
        self._answer_buttons = []
        for index, kind in enumerate((*NAMES.values(), *sorted(HAZARDS)), 1):
            shortcut = index % 10
            button = ttk.Button(answer_buttons, text=f"{ORB_LABELS[kind]} {shortcut}",
                                command=lambda kind=kind: self.answer_selected(kind), width=8)
            button.grid(row=(index - 1) // 5, column=(index - 1) % 5, sticky="ew", padx=1, pady=1)
            self._answer_buttons.append(button)
        for column in range(5):
            answer_buttons.columnconfigure(column, weight=1)
        ttk.Checkbutton(self._review_frame, text="強化（E）", variable=self._enhanced).pack(side="left")
        ttk.Checkbutton(self._review_frame, text="鎖定（L）", variable=self._locked).pack(side="left", padx=8)
        self._correction_button = ttk.Button(board_frame, text="修正辨識",
                                             command=self.toggle_correction_mode)
        self._correction_button.pack(fill="x", pady=(4, 0))
        ttk.Label(board_frame, textvariable=self._learning, wraplength=370, justify="left").pack(anchor="w")
        self._execute_button = ttk.Button(board_frame, text="執行路徑", command=self.execute_route,
                                          state="disabled")
        self._execute_button.pack(fill="x", pady=(4, 0))
        profile_frame = ttk.LabelFrame(board_frame, text="規則設定", padding=6)
        profile_frame.pack(fill="x", pady=(10, 0))
        ttk.Label(profile_frame, text="消珠條件（預設不限＝最大 Combo；形狀才選色珠）：").pack(anchor="w")
        self._condition_color_boxes = []
        for variable, color in zip(self._condition_choices, self._condition_colors):
            condition_row = ttk.Frame(profile_frame)
            condition_row.pack(fill="x", pady=(2, 0))
            ttk.Combobox(condition_row, textvariable=variable, values=CONDITION_OPTIONS,
                         state="readonly", width=28).pack(side="left", fill="x", expand=True)
            color_box = ttk.Combobox(condition_row, textvariable=color, values=CONDITION_COLORS,
                                     state="disabled", width=4)
            color_box.pack(side="right", padx=(4, 0))
            self._condition_color_boxes.append(color_box)
        for index, variable in enumerate(self._condition_choices):
            variable.trace_add("write", lambda *_args, index=index: self._sync_condition_color(index))
        for variable in (*self._condition_colors, self._condition_operator, self._hazard_policy,
                         self._external_condition, self._cascade, self._search_attempts,
                         self._search_steps, self._search_seed):
            variable.trace_add("write", self._settings_changed)
        ttk.Label(profile_frame, text="條件關係：").pack(anchor="w", pady=(4, 0))
        ttk.Combobox(profile_frame, textvariable=self._condition_operator, values=tuple(GROUP_OPERATORS),
                     state="readonly", width=12).pack(fill="x")
        ttk.Label(profile_frame, text="危害珠策略：").pack(anchor="w", pady=(4, 0))
        ttk.Combobox(profile_frame, textvariable=self._hazard_policy, values=tuple(HAZARD_POLICIES),
                     state="readonly", width=12).pack(fill="x")
        ttk.Label(profile_frame, text="外部條件：").pack(anchor="w", pady=(4, 0))
        ttk.Combobox(profile_frame, textvariable=self._external_condition, values=tuple(EXTERNAL_CONDITIONS),
                     state="readonly", width=18).pack(fill="x")
        ttk.Label(profile_frame, text="消珠結算：").pack(anchor="w", pady=(4, 0))
        ttk.Combobox(profile_frame, textvariable=self._cascade, values=tuple(CASCADE_OPTIONS),
                     state="readonly", width=18).pack(fill="x")
        ttk.Label(profile_frame, text="問號時主動重試：").pack(anchor="w", pady=(4, 0))
        self._recognition_attempts_box = ttk.Combobox(
            profile_frame, textvariable=self._recognition_attempts,
            values=tuple(str(value) for value in range(1, 6)),
            state="readonly", width=8,
        )
        self._recognition_attempts_box.pack(fill="x")
        self._recognition_attempts.trace_add("write", self._recognition_attempts_changed)
        search_controls = ttk.Frame(profile_frame)
        search_controls.pack(fill="x", pady=(4, 0))
        ttk.Label(search_controls, text="嘗試次數：").pack(side="left")
        ttk.Combobox(search_controls, textvariable=self._search_attempts,
                     values=tuple(str(value) for value in range(5, 51, 5)),
                     state="readonly", width=7).pack(side="left", padx=(2, 6))
        ttk.Label(search_controls, text="執行步數上限：").pack(side="left")
        ttk.Combobox(search_controls, textvariable=self._search_steps,
                     values=tuple(str(value) for value in range(30, 101, 5)),
                     state="readonly", width=7).pack(side="left", padx=(2, 6))
        ttk.Label(search_controls, text="隨機種子：").pack(side="left")
        ttk.Combobox(search_controls, textvariable=self._search_seed, values=("0", "1", "42", "2026"),
                     state="readonly", width=7).pack(side="left", padx=2)
        ttk.Button(search_controls, text="搜尋", command=self.search_route).pack(side="right")
        ttk.Label(profile_frame, textvariable=self._profile_label, wraplength=340).pack(anchor="w", pady=4)
        profile_buttons = ttk.Frame(profile_frame)
        profile_buttons.pack(fill="x")
        ttk.Button(profile_buttons, text="載入 JSON", command=self.load_profile).pack(side="left", expand=True, fill="x")
        ttk.Button(profile_buttons, text="儲存 JSON", command=self.save_profile).pack(side="left", expand=True, fill="x", padx=(2, 0))
        ttk.Label(board_frame, textvariable=self._evaluation, wraplength=370, justify="left").pack(anchor="w", pady=(10, 0))
        ttk.Label(board_frame, textvariable=self._verification, wraplength=370, justify="left").pack(anchor="w", pady=(8, 0))
        ttk.Label(self.root, textvariable=self._status, anchor="w", relief="sunken").pack(fill="x", side="bottom")

    def _recognition_attempts_changed(self, *_args) -> None:
        if self._syncing_controls:
            return
        try:
            self.controller.max_recognition_attempts = int(self._recognition_attempts.get())
        except (TypeError, ValueError) as exc:
            self._show_error(str(exc))

    @staticmethod
    def _condition_selection(condition: LeaderCondition) -> tuple[str, str] | None:
        for label, presets in CONDITION_PRESETS.items():
            if len(presets) == 1 and condition == presets[0]:
                return label, "不指定"
        if condition.kind == "shape" and isinstance(condition.value, dict):
            shape = condition.value.get("shape")
            orb_type = ORB_LABELS.get(str(condition.value.get("orb_type")))
            if shape in SHAPE_PRESETS.values() and orb_type is not None:
                label = next(label for label, value in SHAPE_PRESETS.items() if value == shape)
                return label, orb_type
        if (condition.kind == "connected_orb_count" and condition.minimum == 4
                and condition.exact):
            color = ORB_LABELS.get(str(condition.value))
            if color is not None:
                return "4 顆消除", color
        return None

    @classmethod
    def _profile_control_values(cls, profile: RuleProfile) -> tuple[list[str], list[str], str, str, str]:
        choices = [NO_CONDITION] * 3
        colors = ["不指定"] * 3
        groups = tuple(group for group in profile.condition_groups if group.enabled)
        operator = "全部符合"
        if len(groups) == 1:
            group = groups[0]
            operator = next((label for label, value in GROUP_OPERATORS.items()
                             if value == group.operator), operator)
            selections = tuple(
                selection for condition in group.conditions
                if (selection := cls._condition_selection(condition)) is not None
            )
            for index, (choice, color) in enumerate(selections[:3]):
                choices[index], colors[index] = choice, color
        hazard = next((label for label, value in HAZARD_POLICIES.items()
                       if value == profile.hazard_policy), "避免危害珠")
        external = next((label for label, value in EXTERNAL_CONDITIONS.items()
                         if value == profile.external_conditions), "無")
        return choices, colors, operator, hazard, external

    def _sync_profile_controls(self, profile: RuleProfile) -> None:
        choices, colors, operator, hazard, external = self._profile_control_values(profile)
        self._syncing_controls = True
        try:
            for variable, value in zip(self._condition_choices, choices):
                variable.set(value)
            for variable, value in zip(self._condition_colors, colors):
                variable.set(value)
            self._condition_operator.set(operator)
            self._hazard_policy.set(hazard)
            self._external_condition.set(external)
            for index in range(len(self._condition_choices)):
                self._sync_condition_color(index)
        finally:
            self._syncing_controls = False

    def _settings_changed(self, *_args) -> None:
        if not self._syncing_controls:
            self._apply_profile_from_controls()

    def _apply_profile_from_controls(self) -> None:
        profile = rule_profile_from_selections(
            ((variable.get(), color.get()) for variable, color in zip(
                self._condition_choices, self._condition_colors)),
            self._condition_operator.get(),
            self._hazard_policy.get(),
            self._external_condition.get(),
        )
        self._manual_route.clear()

        def apply():
            state = self.controller.set_rule_profile(profile)
            return state

        self._apply(apply)

    def _sync_condition_color(self, index: int) -> None:
        syncing = self._syncing_controls
        self._syncing_controls = True
        try:
            choice = self._condition_choices[index].get()
            color = self._condition_colors[index]
            if choice in COLORED_PRESETS:
                if color.get() not in CONDITION_COLORS:
                    color.set("火")
                self._condition_color_boxes[index].configure(state="readonly")
            else:
                color.set("不指定")
                self._condition_color_boxes[index].configure(state="disabled")
        finally:
            self._syncing_controls = syncing
        if not syncing:
            self._apply_profile_from_controls()


    def _show_error(self, message: str):
        from tkinter import messagebox
        messagebox.showerror("PAD Router 錯誤", message, parent=self.root)

    @staticmethod
    def _format_evaluation(result: RouteEvaluation | None) -> str:
        if result is None:
            return "尚未評估路徑"
        matches = ", ".join(
            f"{NAMES.get(match.key, match.key)}×{len(match.cells)}"
            for match in result.resolved_matches
        ) or "無"
        groups = ", ".join(
            f"第 {item.index} 組：{'通過' if item.satisfied else '未通過'}"
            for item in result.group_results
        ) or "無"
        conditions = ", ".join(
            f"{item.identifier}：{'通過' if item.satisfied else '未通過'}"
            for item in result.condition_results
        ) or "無"
        hazard = {"none": "無", "allowed": "允許", "required": "依條件消除", "blocked": "已阻擋"}.get(
            result.hazard_outcome, result.hazard_outcome)
        return (f"消除：{matches} | 落珠：{result.cascades} | Combo：{result.combo_count}\n"
                f"條件群組：{groups}\n條件：{conditions}\n"
                f"危害珠：{hazard} | 符合條件：{'是' if result.qualifying else '否'} | "
                f"可執行：{'是' if result.execution_eligible else '否'}")

    @staticmethod
    def _format_board(board) -> str:
        if board is None:
            return "無資料"
        return "/".join(" ".join(orb_display(orb) for orb in row) for row in board)

    @classmethod
    def _format_verification(cls, verification: PlayVerification | None) -> str:
        if verification is None:
            return "尚無手勢後驗證結果"
        mismatches = ("未知" if verification.mismatches is None
                      else str(verification.mismatches))
        return (f"手勢後驗證：{'成功' if verification.success else '失敗'} "
                f"（{mismatches} 格不符）\n"
                f"預期：{cls._format_board(verification.expected_board)}\n"
                f"辨識：{cls._format_board(verification.detected_board)}")

    def _display(self, state: BoardInspectionState):
        self._status.set(state.status)
        self._learning.set(state.learning_status)
        if state.rule_profile is not None:
            self._sync_profile_controls(state.rule_profile)
            self._profile_label.set(f"已套用：{state.rule_profile.name}")
        syncing = self._syncing_controls
        self._syncing_controls = True
        try:
            self._recognition_attempts.set(str(self.controller.max_recognition_attempts))
            if state.search_options is not None:
                self._search_attempts.set(str(state.search_options.attempts))
                self._search_steps.set(str(state.search_options.max_steps))
                self._search_seed.set(str(state.search_options.seed))
                self._cascade.set(next(label for label, value in CASCADE_OPTIONS.items()
                                       if value == state.search_options.cascade))
        finally:
            self._syncing_controls = syncing
        result = state.route_evaluation
        self._evaluation.set(self._format_evaluation(result))
        self._verification.set(self._format_verification(state.verification))
        self._execute_button.configure(
            state="normal" if result is not None and result.execution_eligible else "disabled"
        )
        correction_mode = getattr(self, "_correction_mode", False)
        if state.uncertain_cells:
            if correction_mode:
                self._correction_mode = False
                correction_mode = False
                self._manual_route.clear()
                self._dragging_route = False
            self._correction_button.configure(state="disabled", text="修正辨識")
            if self._selected_cell not in state.uncertain_cells:
                self._selected_cell = state.uncertain_cells[0]
                self.board.focus_set()
            row, col = self._selected_cell
            self._selected_label.set(f"第 {row + 1} 列、第 {col + 1} 行")
            self._review_message.set(
                f"目前：檢視模式。模型無法判斷 {len(state.uncertain_cells)} 格；"
                "按一次珠種即永久學習，完成後可拖曳盤面規劃路徑。"
            )
            for button in self._answer_buttons:
                button.configure(state="normal")
        elif correction_mode:
            self._correction_button.configure(state="normal", text="結束修正")
            self._review_message.set(
                "目前：修正模式。點選任一格後按珠種覆寫；完成後按「結束修正」回到路徑模式。"
            )
            for button in self._answer_buttons:
                button.configure(state="normal")
        elif state.board is not None:
            self._correction_button.configure(state="normal", text="修正辨識")
            self._review_message.set(
                "目前：路徑模式。拖曳盤面規劃路徑；模型仍可能看錯，"
                "按「修正辨識」後可點選任一格覆寫。"
            )
            for button in self._answer_buttons:
                button.configure(state="disabled")
        else:
            self._correction_button.configure(state="disabled", text="修正辨識")
            self._review_message.set("尚未載入盤面。載入後可拖曳規劃路徑，或進入修正辨識。")
            for button in self._answer_buttons:
                button.configure(state="disabled")
        self.source.delete("all")
        if state.width and state.height and state.pixels:
            viewport_width = self.source.winfo_width()
            viewport_height = self.source.winfo_height()
            if viewport_width <= 1:
                viewport_width = int(self.source.cget("width"))
            if viewport_height <= 1:
                viewport_height = int(self.source.cget("height"))
            scale, display_width, display_height = _fit_scale(
                state.width, state.height, viewport_width, viewport_height)
            self._display_scale = scale
            self._photo = _photo_from_screenshot(
                (state.width, state.height, state.pixels), self.tk,
                (display_width, display_height),
            )
            self.source.create_image(0, 0, image=self._photo, anchor="nw")
            radius = max(8, min(36, (state.calibration.cell // 3) if state.calibration else 20)) * scale
            for item in state.overlay:
                x, y = item["x"] * scale, item["y"] * scale
                uncertain = (item["cell"] in state.uncertain_cells)
                self.source.create_oval(x - radius, y - radius, x + radius, y + radius,
                                        outline="#ff4444" if uncertain else "#61dafb", width=3)
                self.source.create_text(x, y, text="?" if uncertain else item["label"], fill="white",
                                        font=("TkDefaultFont", 12, "bold"))
            route_points = tuple((item["x"] * scale, item["y"] * scale) for item in state.route_overlay)
            if route_points:
                coords = tuple(value for point in route_points for value in point)
                if len(coords) > 2:
                    self.source.create_line(*coords, fill="#ffcc33", width=6,
                                            capstyle="round", joinstyle="round")
                for item, (x, y) in zip(state.route_overlay, route_points):
                    self.source.create_oval(x - 12 * scale, y - 12 * scale,
                                            x + 12 * scale, y + 12 * scale,
                                            outline="#ffcc33", width=2)
                    self.source.create_text(x, y, text=str(item["step"]), fill="white",
                                            font=("TkDefaultFont", 10, "bold"))
            self.source.configure(scrollregion=(0, 0, display_width, display_height))
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

    def _source_resized(self, _event=None):
        state = self.controller.state
        if state.width and state.height and state.pixels:
            self._display(state)

    def _apply(self, action):
        try:
            self._display(action())
        except (OSError, RuntimeError, ValueError, TypeError, zlib.error) as exc:
            self._show_error(str(exc))

    def open_png(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(parent=self.root, filetypes=(("PNG 圖片", "*.png"),))
        if path:
            self._manual_route.clear()
            self._apply(lambda: self.controller.load_png(path))

    def capture_device(self):
        self._manual_route.clear()
        self._apply(lambda: self.controller.capture_device(self._serial.get()))

    def refresh_devices(self):
        try:
            output = subprocess.check_output(["adb", "devices"], text=True)
        except (OSError, subprocess.CalledProcessError):
            self._show_error("無法取得 Android 裝置；請確認 adb 已安裝且可執行")
            return
        serials = tuple(line.split()[0] for line in output.splitlines()[1:]
                        if len(line.split()) == 2 and line.split()[1] == "device")
        self._serial_box.configure(values=serials)
        self._serial.set(serials[0] if serials else "")

    def auto_calibration(self):
        self._manual_route.clear()
        state = self.controller.state
        self._apply(lambda: self.controller.set_calibration(infer_calibration(
            state.width or 0, state.height or 0)))

    def toggle_correction_mode(self):
        state = self.controller.state
        if getattr(self, "_correction_mode", False):
            self._correction_mode = False
        elif state.board is not None and not state.uncertain_cells:
            self._correction_mode = True
        else:
            return "break"
        self._manual_route.clear()
        self._dragging_route = False
        self._display(state)
        return "break"

    def _cell_at(self, event) -> tuple[int, int] | None:
        cell = (event.y // 60, event.x // 60)
        return cell if 0 <= cell[0] < ROWS and 0 <= cell[1] < COLS else None

    def select_cell(self, event):
        cell = self._cell_at(event)
        if cell is not None:
            self._selected_cell = cell
            self._selected_label.set(f"第 {cell[0] + 1} 列、第 {cell[1] + 1} 行")
            self._display(self.controller.state)
        return "break"

    def route_press(self, event):
        cell = self._cell_at(event)
        if cell is None:
            return "break"
        self._selected_cell = cell
        self._selected_label.set(f"第 {cell[0] + 1} 列、第 {cell[1] + 1} 行")
        if getattr(self, "_correction_mode", False) or self.controller.state.uncertain_cells:
            self._manual_route.clear()
            self._dragging_route = False
            board = getattr(self, "board", None)
            if board is not None:
                board.focus_set()
            self._display(self.controller.state)
            return "break"
        self._manual_route[:] = [cell]
        self._dragging_route = True
        self._display(self.controller.state)
        return "break"

    def route_motion(self, event):
        if getattr(self, "_correction_mode", False) or not self._dragging_route:
            self._dragging_route = False
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
            def evaluate():
                self.controller.evaluate_manual_route(
                    tuple(self._manual_route), cascade=CASCADE_OPTIONS[self._cascade.get()])
                return self.controller.state

            self._apply(evaluate)
        else:
            self._display(state)
        return "break"

    def answer_key(self, event):
        key = event.char.lower()
        kinds = (*NAMES.values(), *sorted(HAZARDS))
        correction_mode = getattr(self, "_correction_mode", False)
        if key in "1234567890" and (self.controller.state.uncertain_cells or correction_mode):
            self.answer_selected(kinds[(int(key) - 1) % 10])
            return "break"
        if key == "e" and (self.controller.state.uncertain_cells or correction_mode):
            self._enhanced.set(not self._enhanced.get())
            return "break"
        if key == "l" and (self.controller.state.uncertain_cells or correction_mode):
            self._locked.set(not self._locked.get())
            return "break"

    def answer_selected(self, kind: str):
        if not (self.controller.state.uncertain_cells or getattr(self, "_correction_mode", False)):
            self._show_error("請先進入修正辨識模式")
            return
        if self._selected_cell is None:
            self._show_error("請先選取盤面上的珠子")
            return
        value = kind
        if kind in NAMES.values():
            value += "+" if self._enhanced.get() else ""
            value += "*" if self._locked.get() else ""
        self._manual_route.clear()
        self._apply(lambda: self.controller.correct_cell(*self._selected_cell, value))

    def correct_selected(self):
        self.answer_selected(ORB_KINDS[self._selected_orb.get()])

    def confirm_board(self):
        try:
            self.controller.confirm_board()
            self._manual_route.clear()
            self._display(self.controller.state)
        except ValueError as exc:
            self._show_error(str(exc))

    def execute_route(self):
        state = self.controller.state
        if state.route_evaluation is None or not state.route_evaluation.execution_eligible:
            self._show_error("僅能執行已確認盤面上、符合條件的路徑")
            return
        if not self._serial.get().strip():
            self._show_error("請先按「更新裝置」並選擇裝置")
            return
        try:
            self.controller.accept_current_board()
        except OSError as exc:
            self._status.set(f"執行前學習失敗：{exc}")
            return

        def execute():
            self.controller.execute_route(self._serial.get(), explicit_confirmation=True)
            self._manual_route.clear()
            return self.controller.state

        self._apply(execute)


    def search_route(self):
        self._manual_route.clear()

        def search():
            self.controller.search_qualifying_route(
                RouteSearchOptions(attempts=int(self._search_attempts.get()), max_steps=int(self._search_steps.get()),
                                   seed=int(self._search_seed.get()), cascade=CASCADE_OPTIONS[self._cascade.get()])
            )
            return self.controller.state

        self._apply(search)


    def load_profile(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(parent=self.root,
                                          filetypes=(("規則設定 JSON", "*.json"), ("JSON", "*.json")))
        if not path:
            return
        try:
            self._manual_route.clear()
            self._display(self.controller.load_rule_profile(path))
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            self._show_error(str(exc))

    def save_profile(self):
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension=".json",
                                            filetypes=(("規則設定 JSON", "*.json"), ("JSON", "*.json")))
        if not path:
            return
        try:
            profile = self.controller.state.rule_profile
            if profile is None:
                raise ValueError("尚未套用規則設定")
            self.controller.save_rule_profile(path, profile)
            self._profile_label.set(f"已儲存：{profile.name}")
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
