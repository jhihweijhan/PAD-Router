#!/usr/bin/env python3
"""Native desktop board inspection for PAD Router.

The controller is presentation-independent.  It owns the small workflow the
webview needs while recognition and device capture remain injected adapters
around the existing PAD Router functions.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import subprocess
import struct
import tempfile
import threading
import zlib
from copy import deepcopy
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from functools import lru_cache
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterable

import pad_router
from pad_router import (
    ConditionGroup,
    ExternalCondition,
    HAZARDS,
    LeaderCondition,
    NAMES,
    Grid,
    Orb,
    PlayVerification,
    RouteEvaluation,
    RouteSearchOptions,
    board_label,
    max_combo_ceiling,
    max_combo_layout,
    RouteSearchResult,
    RuleProfile,
    _cell_features,
    _validate_protected_cell,
    detect_board_pixels,
    evaluate_manual_route,
    load_rule_profile,
    expected_board_after_path,
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

# Keep the measured 0.04-second pacing until a real-device run justifies changing it.
DEFAULT_MOVE_DELAY = 0.04
WORKSPACE_SETTINGS_PATH = (
    Path(__file__).resolve().parent / ".pad-router" / "workspace-settings.json"
)


class OrbPrototypeModel:
    """Small local, no-training classifier fed by corrected board cells."""

    def __init__(self, path: Path | None = None):
        self.path = path
        self.samples: list[dict[str, object]] = []
        if path is not None and path.exists():
            try:
                self.samples = self._deduplicated(json.loads(path.read_text()).get("samples", []))
            except (OSError, ValueError):
                self.samples = []
        self._keys = {self._key(sample) for sample in self.samples}

    @staticmethod
    def _key(sample: dict[str, object]) -> tuple:
        return (sample.get("kind"), sample.get("color"), sample.get("enhanced"),
                sample.get("locked"), sample.get("visual_class"), sample.get("human"),
                tuple(sample.get("cell") or ()), tuple(sample.get("feature") or ()))

    @classmethod
    def _deduplicated(cls, samples: list[dict[str, object]]) -> list[dict[str, object]]:
        """Drop records identical to one already held.

        Every capture re-learns the whole visible board, so a board seen twice
        contributes the same feature vectors twice.  Identical records cannot
        change a prediction -- they neither move a nearest distance nor add a
        label -- they only make every later prediction scan more samples.
        """
        seen: set[tuple] = set()
        kept = []
        for sample in samples:
            key = cls._key(sample)
            if key not in seen:
                seen.add(key)
                kept.append(sample)
        return kept

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
        # Saturation/value stay near zero weight: the '+' flash moves them by
        # 0.25 between captures, which put every learned sample out of range.
        weights = (1.4, 0.15, 0.15, 0.35, 0.25, 0.25, 0.25, 0.25, 0.25, 0.4)
        distance = sum(weight * abs(a - b) for weight, a, b in zip(
            weights, (hue, *left[1:]), (0.0, *right[1:])
        ))
        if (len(left) >= 13 and len(right) >= 13
                and left[10] >= 0 and right[10] >= 0):
            center_hue = min(abs(left[10] - right[10]), 1.0 - abs(left[10] - right[10]))
            distance += (0.5 * center_hue
                         + 0.05 * abs(left[11] - right[11])
                         + 0.05 * abs(left[12] - right[12]))
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
              cell: tuple[int, int] | None = None, persist: bool = True) -> bool:
        """Record one cell.  ``persist=False`` defers the write to ``persist()``.

        The whole store is rewritten on every save, so a board's worth of cells
        saved one by one rewrites it ROWS*COLS times -- 253MB of writes for a
        6MB store on a 7x6 Board.  Batch callers defer instead.
        """
        if not isinstance(orb, Orb) or orb_match_key(orb) is None:
            return False
        record = self._record(orb, feature, human, cell)
        if self._key(record) in self._keys:
            return False
        candidate = self.samples
        if human:
            candidate = [
                sample for sample in self.samples
                if not (sample.get("feature") == record["feature"]
                        and sample.get("cell") == record.get("cell"))
            ]
        candidate = [*candidate, record]
        if persist:
            self._save(candidate)
        self.samples = candidate
        self._keys = {self._key(sample) for sample in candidate} if human else self._keys | {self._key(record)}
        return True

    def persist(self) -> None:
        """Write what deferred learns have accumulated."""
        self._save()

    def _predict_sample(self, feature) -> tuple[Orb, float, dict[str, object]] | None:
        candidates = self.samples
        if not candidates:
            return None
        vector = self._feature(feature)
        # Only the nearest sample and the nearest differently-labelled one
        # matter, so keep the best per label in one pass rather than sorting
        # every sample for every cell.
        best: tuple[float, dict[str, object], tuple] | None = None
        nearest: dict[tuple, float] = {}
        for sample in candidates:
            score = self._distance(vector, sample["feature"]) + (0 if sample.get("human") else .02)
            label = (sample["kind"], sample.get("color"), sample.get("enhanced"), sample.get("locked"))
            if score < nearest.get(label, math.inf):
                nearest[label] = score
            if best is None or score < best[0]:
                best = (score, sample, label)
        distance, sample, label = best
        runner_up = min((score for other, score in nearest.items() if other != label), default=math.inf)
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
        for row in range(pad_router.ROWS):
            for col in range(pad_router.COLS):
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

_COMBO_CEILINGS = {10: "（6×5 上限）", 14: "（7×6 上限）"}
CONDITION_PRESETS = {
    "不限（以最大 Combo 為主）": (),
    # 上限依 pazuma「最大火力配置：コンボ」的 2 色盤面表：6×5 盤面 15-15 分佈為 10 Combo，
    # 7×6 盤面 42 顆珠 21-21 分佈為 14 Combo（每 3 顆一組直線鋪滿整個盤面）。
    **{f"至少 {minimum} Combo{_COMBO_CEILINGS.get(minimum, '')}": (LeaderCondition.combo_minimum(minimum),)
       for minimum in (3, 5, 7, 8, 9, 10, 11, 12, 13, 14)},
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
CONDITION_COLORS = ("不指定",) + tuple(ORB_LABELS[name] for name in NAMES.values())
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
_OPERATIONAL_MUTATION_CONFLICTS = frozenset({
    "correct", "correct_cell", "set_board_size",
    "protect_cell", "set_protected_cell",
    "set_rule_profile", "update_rules", "import_rule_profile", "import_profile",
    "execute", "execute_route",
})


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
        orb_type = None if color == "不指定" else ORB_KINDS[color]
        if label in SHAPE_PRESETS:
            conditions.append(LeaderCondition.shape(SHAPE_PRESETS[label], orb_type=orb_type))
        elif label == "4 顆消除":
            conditions.append(LeaderCondition.connected_orb_count(4, orb_type, exact=True))
        else:
            conditions.extend(CONDITION_PRESETS[label])
    groups = (ConditionGroup(conditions, GROUP_OPERATORS[operator_label]),) if conditions else ()
    labels = tuple(f"{label}（{color}）" if label in COLORED_PRESETS else label for label, color in selections)
    return RuleProfile("、".join(labels) or "最大 Combo", condition_groups=groups,
                       external_conditions=EXTERNAL_CONDITIONS[external_label],
                       hazard_policy=HAZARD_POLICIES[hazard_label])


@dataclass(frozen=True)
class BoardCalibration:
    """Top-left pixel and cell size for the current Board."""

    left: int = 0
    top: int = 1380
    cell: int = 180

    def validate(self, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            raise ValueError("截圖尺寸必須為正數")
        if self.left < 0 or self.top < 0 or self.cell <= 0:
            raise ValueError("校正座標不可為負數，格寬必須為正數")
        if self.left + pad_router.COLS * self.cell > width or self.top + pad_router.ROWS * self.cell > height:
            raise ValueError(f"校正範圍必須讓 {board_label()} 盤面完整位於截圖內")

    def to_grid(self) -> Grid:
        return Grid(self.left, self.top, self.cell)


_LIT = 24  # Mean channel value separating drawn pixels from the black frame.


def _measure_board(width: int, height: int, pixels: bytes) -> BoardCalibration | None:
    """Read the Board's own edges out of the screenshot.

    PAD frames the Board in black and pins it to the bottom of the play area, so
    its bottom edge is the last lit row and its side edges are where that band's
    lit pixels stop.  Measuring beats assuming: a 7x6 Board is *not* drawn full
    width the way the 6x5 Board is (on the SM-A1560 it measures left 23, cell
    147, top 1381), and a tight crop of a Board reports its own full extent.
    """
    def lit(x: int, y: int) -> bool:
        return sum(pixels[(y * width + x) * 4:(y * width + x) * 4 + 3]) / 3 > _LIT

    columns = range(0, width, 8)
    bottom = next((y + 1 for y in range(height - 1, -1, -1)
                   if sum(sum(pixels[(y * width + x) * 4:(y * width + x) * 4 + 3]) / 3
                          for x in columns) / len(columns) > _LIT), 0)
    # A band inside the Board's lower rows: at most a third of it, whatever the
    # real cell size turns out to be, since the Board is never wider than the
    # screenshot.
    band = range(max(0, bottom - pad_router.ROWS * (width // pad_router.COLS) // 3), bottom)
    edges = [x for x in range(width) if any(lit(x, y) for y in band)]
    if not edges:
        return None
    cell = (edges[-1] + 1 - edges[0]) // pad_router.COLS
    top = bottom - pad_router.ROWS * cell
    if cell <= 0 or top < 0:
        return None
    return BoardCalibration(edges[0], top, cell)


def infer_calibration(width: int, height: int, pixels: bytes | None = None) -> BoardCalibration:
    """Measure the Board in the screenshot, falling back to a bottom-anchored fit."""
    measured = _measure_board(width, height, pixels) if pixels is not None else None
    if measured is not None:
        try:
            measured.validate(width, height)
            return measured
        except ValueError:
            pass
    cell = min(width // pad_router.COLS, height // pad_router.ROWS)
    if cell <= 0:
        raise ValueError(f"截圖太小，無法容納 {board_label()} 盤面")
    calibration = BoardCalibration((width - pad_router.COLS * cell) // 2,
                                   height - pad_router.ROWS * cell, cell)
    calibration.validate(width, height)
    return calibration


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
        result[index * 4:index * 4 + 4] = bytes((red, green, blue, alpha))
    return width, height, bytes(result)


def _board_shape(board: Board) -> None:
    if len(board) != pad_router.ROWS or any(len(row) != pad_router.COLS for row in board):
        raise ValueError(f"辨識器必須回傳 {board_label()} 盤面")


def _uncertain_cells(board: Board) -> tuple[tuple[int, int], ...]:
    return tuple((row, col) for row in range(pad_router.ROWS) for col in range(pad_router.COLS)
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
    enhanced = locked = False
    while text and text[-1] in "+*":
        if text[-1] == "+":
            enhanced = True
        else:
            locked = True
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
    verification: PlayVerification | None = None
    route_search: RouteSearchResult | None = None
    route_overlay: tuple[dict[str, object], ...] = ()
    search_options: RouteSearchOptions | None = None
    learning_status: str = "尚未學習資料"
    protected_cell: tuple[int, int] | None = None

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
        self._learning_toggle_lock = threading.Lock()
        self._learning_write_lock = threading.Lock()
        self._learning_enabled = False
        self._learning_disable_requested = threading.Event()
        self._verify_after_gesture = True
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

    @property
    def verify_after_gesture(self) -> bool:
        return self._verify_after_gesture

    def set_verify_after_gesture(self, enabled: bool) -> BoardInspectionState:
        """Choose whether a finished gesture is checked before the orb is released."""
        if not isinstance(enabled, bool):
            raise ValueError("放手前確認設定必須是布林值")
        self._verify_after_gesture = enabled
        self.state = replace(self.state, status=(
            "轉珠後將停手確認盤面，再放手" if enabled else "轉珠後直接放手，不做盤面確認"))
        return self.state

    @property
    def learning_enabled(self) -> bool:
        with self._learning_write_lock:
            return self._learning_enabled

    def set_learning_enabled(self, enabled: bool) -> BoardInspectionState:
        if not isinstance(enabled, bool):
            raise ValueError("AI 模型學習設定必須是布林值")
        with self._learning_toggle_lock:
            if not enabled:
                self._learning_disable_requested.set()
            with self._learning_write_lock:
                self._learning_enabled = enabled
                if enabled:
                    self._learning_disable_requested.clear()
            self.state = replace(
                self.state, learning_status=f"AI 模型學習：{'開啟' if enabled else '關閉'}",
            )
            return self.state

    def set_board_size(self, name: str) -> BoardInspectionState:
        """Switch between the 6×5 Standard Board and the 7×6 Board a 76 leader grants."""
        if name not in pad_router.BOARD_SIZES:
            raise ValueError("盤面大小必須是 6x5 或 7x6")
        if pad_router.BOARD_SIZES[name] == (pad_router.ROWS, pad_router.COLS):
            return self.state
        # ponytail: the board size is a module-wide switch, so one controller at
        # a time; per-controller sizes would mean threading it through every
        # pad_router function.
        pad_router.set_board_size(*pad_router.BOARD_SIZES[name])
        state = self.state
        if state.width is None or state.height is None or state.pixels is None:
            self.state = replace(BoardInspectionState(status=f"盤面大小已改為 {board_label()}"),
                                 rule_profile=state.rule_profile)
            return self.state
        # The old board has the wrong shape now, so re-read the same screenshot.
        return self._with_source((state.width, state.height, state.pixels),
                                 state.source_name or "source", auto_search=False)

    def _detect(self, width: int, height: int, pixels: bytes, grid: Grid) -> Board:
        if self._model is None:
            return self._detector(width, height, pixels, grid)
        return self._model.detect(width, height, pixels, grid, self._detector)
    def _detect_with_retries(self, width: int, height: int, pixels: bytes, grid: Grid) -> tuple[Board, int]:
        """Re-read the same source until it resolves, stops changing, or runs out.

        A retry can only help a detector that answers differently -- the source
        never changes here -- so an answer that repeats ends the attempts.  A
        board still animating its Combos reads as unknown every time, and that
        is precisely when a capture would otherwise pay for recognition twice.
        """
        previous = None
        for attempt in range(1, self.max_recognition_attempts + 1):
            detected = self._detect(width, height, pixels, grid)
            _board_shape(detected)
            if not _uncertain_cells(detected) or detected == previous:
                return detected, attempt
            previous = detected
        return detected, self.max_recognition_attempts


    def _learn_implicit(self, label: str = "上一張", data_label: str = "隱式資料") -> str:
        state = self.state
        if (self._model is None or state.board is None or state.width is None or state.height is None
                or state.pixels is None or state.calibration is None):
            return ""
        grid = state.calibration.to_grid()
        learned = 0
        try:
            for row in range(pad_router.ROWS):
                for col in range(pad_router.COLS):
                    try:
                        feature = _cell_features(state.width, state.height, state.pixels,
                                                 grid.point(row, col), grid.cell)
                    except ValueError:
                        continue
                    with self._learning_write_lock:
                        if not self._learning_enabled or self._learning_disable_requested.is_set():
                            return f"{label}已學習（{learned} 格{data_label}）" if learned else ""
                        learned += self._model.learn(state.board[row][col], feature, human=False,
                                                     cell=(row, col), persist=False)
        finally:
            if learned:
                with self._learning_write_lock:
                    self._model.persist()
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
        with self._learning_write_lock:
            if self._learning_enabled and not self._learning_disable_requested.is_set():
                self._model.learn(orb, feature, human=True, cell=(row, col))

    def _with_source(self, source: Screenshot, source_name: str,
                     auto_search: bool = True) -> BoardInspectionState:
        width, height, pixels = source
        if width <= 0 or height <= 0 or len(pixels) != width * height * 4:
            raise ValueError("截圖必須包含 width×height 個 RGBA 像素")
        calibration = infer_calibration(width, height, pixels)
        learned = self._learn_implicit()
        detected, recognition_attempts = self._detect_with_retries(
            width, height, pixels, calibration.to_grid()
        )
        return self._replace_source(source_name, source, calibration, detected, learned,
                                    recognition_attempts, auto_search)

    def _replace_source(self, source_name: str, source: Screenshot,
                        calibration: BoardCalibration, detected: Board, learned: str = "",
                        recognition_attempts: int = 1,
                        auto_search: bool = True) -> BoardInspectionState:
        uncertain = _uncertain_cells(detected)
        status = (f"盤面辨識完成：已載入 {source_name}；有 {len(uncertain)} 格需要手動修正"
                  if uncertain else f"盤面辨識完成：已載入 {source_name}")
        status += f"；主動辨識第 {recognition_attempts}/{self.max_recognition_attempts} 次"
        if uncertain:
            status += "；仍有問號，請手動修正"
        elif recognition_attempts < self.max_recognition_attempts:
            status += "；已提前停止"
        if learned:
            status += f"；{learned}"
        state = BoardInspectionState(source_name, source[0], source[1], source[2], calibration,
                                     detected, detected, detected if not uncertain else None, not uncertain,
                                     uncertain, (), status,
                                     learning_status=learned or ("目前畫面尚未學習" if self.learning_enabled else "AI 模型學習：關閉"))
        state = replace(state, rule_profile=self.state.rule_profile,
                        protected_cell=self.state.protected_cell)
        self.state = self._with_overlay(state)
        if auto_search and not uncertain and self.state.rule_profile is not None:
            self.search_qualifying_route()
        return self.state

    def _with_overlay(self, state: BoardInspectionState) -> BoardInspectionState:
        if state.board is None or state.calibration is None:
            return state
        grid = state.calibration.to_grid()
        source_board = state.board
        overlay = tuple({"cell": (row, col), "x": grid.point(row, col)[0],
                         "y": grid.point(row, col)[1], "label": orb_display(source_board[row][col]),
                         "uncertain": (row, col) in state.uncertain_cells}
                         for row in range(pad_router.ROWS) for col in range(pad_router.COLS))
        return replace(state, overlay=overlay)

    def _route_overlay(self, result: RouteEvaluation | None) -> tuple[dict[str, object], ...]:
        if result is None or self.state.calibration is None:
            return ()
        grid = self.state.calibration.to_grid()
        return tuple({"cell": cell, "step": index, "x": grid.point(*cell)[0], "y": grid.point(*cell)[1]}
                     for index, cell in enumerate(result.route, 1))

    def load_png(self, path: str | Path, auto_search: bool = True) -> BoardInspectionState:
        if Path(path).suffix.lower() != ".png":
            raise ValueError("僅支援 PNG 圖片")
        return self._with_source(decode_png(path), str(path), auto_search)

    def capture_device(self, serial: str, auto_search: bool = True) -> BoardInspectionState:
        serial = serial.strip()
        if not serial:
            raise ValueError("請選擇裝置")
        return self._with_source(self._capture(serial), serial, auto_search)

    def snapshot(self) -> dict[str, object]:
        """Return a JSON-safe view without exposing controller internals."""
        state = self.state
        source = None
        if state.width is not None and state.height is not None and state.pixels is not None:
            source = {
                "name": state.source_name,
                "width": state.width,
                "height": state.height,
                "image": _screenshot_image((state.width, state.height, state.pixels)),
            }
        return {"source": source, "status": state.status, "learning_enabled": self.learning_enabled}

    def set_calibration(self, calibration: BoardCalibration,
                        auto_search: bool = True) -> BoardInspectionState:
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
            auto_search=auto_search,
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
        if not (0 <= row < pad_router.ROWS and 0 <= col < pad_router.COLS):
            raise ValueError(f"珠子必須位於 {board_label()} 盤面內")
        board = [list(items) for items in self.state.board]
        orb = _coerce_orb(value)
        board[row][col] = orb
        updated_board = tuple(map(tuple, board))
        uncertain = _uncertain_cells(updated_board)
        self._learn_human(row, col, orb)
        updated = replace(self.state, board=updated_board,
                          confirmed_board=updated_board if not uncertain else None,
                          confirmed=not uncertain, uncertain_cells=uncertain,
                          overlay=(), route_evaluation=None, verification=None,
                          route_search=None, route_overlay=(), search_options=None,
                          status=("珠子已修正並寫入模型；辨識結果已自動更新" if self.learning_enabled
                                  else "珠子已修正；AI 模型學習已關閉"),
                          learning_status=("人工標記已寫入模型" if self.learning_enabled
                                           else "AI 模型學習：關閉"))
        self.state = self._with_overlay(updated)
        return self.state

    def confirm_board(self) -> Board:
        if self.state.board is None:
            raise ValueError("尚未載入盤面")
        if self.state.uncertain_cells:
            raise ValueError("請先手動修正無法辨識的珠子，才能確認盤面")
        self.state = replace(self.state, confirmed_board=self.state.board, confirmed=True,
                             uncertain_cells=(), route_evaluation=None, verification=None,
                             route_search=None, route_overlay=(), search_options=None,
                             status="盤面已確認")
        return self.state.board

    def set_rule_profile(self, profile: RuleProfile) -> BoardInspectionState:
        if not isinstance(profile, RuleProfile):
            raise TypeError("profile 必須是 RuleProfile")
        self.state = replace(self.state, rule_profile=profile, route_evaluation=None,
                             verification=None, route_search=None, route_overlay=(), search_options=None,
                             status=f"已套用規則設定：{profile.name}")
        return self.state

    def set_protected_cell(self, cell: tuple[int, int] | None,
                           recompute: bool = True) -> BoardInspectionState:
        cell = _validate_protected_cell(cell)
        self.state = replace(self.state, protected_cell=cell)
        self.invalidate_route("已清除保護格" if cell is None else
                              f"已保護第 {cell[0] + 1} 列、第 {cell[1] + 1} 行")
        if (recompute and self.state.board is not None and not self.state.uncertain_cells
                and self.state.rule_profile is not None):
            self.search_qualifying_route()
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
        route = tuple(path)
        if self.state.protected_cell in route:
            raise ValueError("路徑不可碰觸保護格")
        result = evaluate_manual_route(board, route, profile, confirmed=self.state.confirmed, cascade=cascade)
        self.state = replace(self.state, route_evaluation=result,
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
        result = search_qualifying_route(board, profile, options, confirmed=self.state.confirmed,
                                         protected_cell=self.state.protected_cell)
        self._apply_search_result(result, options)
        return result

    def _apply_search_result(self, result: RouteSearchResult,
                             options: RouteSearchOptions) -> None:
        candidate = result.candidate
        self.state = replace(self.state, route_search=result, route_evaluation=candidate,
                             verification=None, route_overlay=self._route_overlay(candidate),
                             search_options=options,
                             status=("搜尋完成：找到符合條件的路徑" if candidate and candidate.qualifying
                                     else "搜尋完成：沒有符合條件的路徑"))

    def search_route(self, options: RouteSearchOptions | None = None) -> RouteSearchResult:
        return self.search_qualifying_route(options)

    def invalidate_route(self, status: str = "路徑已失效；請確認設定後重新搜尋") -> BoardInspectionState:
        self.state = replace(self.state, route_search=None, route_evaluation=None,
                             verification=None, route_overlay=(), search_options=None,
                             status=status)
        return self.state

    def execute_route(self, serial: str,
                      delay: float = DEFAULT_MOVE_DELAY, hold_delay: float = 0.15,
                      lift_threshold: float = 12.0, max_corrections: int = 2) -> bool:
        result = self.state.route_evaluation
        if (result is None or self.state.confirmed_board is None
                or not self.state.confirmed or not result.execution_eligible):
            raise ValueError("僅能執行已確認盤面上、符合條件的路徑")
        serial = serial.strip()
        if not serial:
            raise ValueError("請選擇裝置")
        calibration = self.state.calibration
        if calibration is None:
            raise ValueError("執行前必須先校正盤面")
        self.state = replace(self.state, verification=None,
                             status="正在執行路徑；安全 ADB 驗證進行中")
        verification: PlayVerification | None = None

        def receive(report: PlayVerification) -> None:
            nonlocal verification
            verification = report

        # The captured frame already told us the screen size, so every check
        # inside play() can pull just its own band instead of a whole frame.
        screen_size = (None if self.state.width is None or self.state.height is None
                       else (self.state.width, self.state.height))
        outcome = self._executor(
            serial, result.route, calibration.to_grid(), delay, hold_delay, lift_threshold,
            self.state.confirmed_board, max_corrections, on_verification=receive,
            screen_size=screen_size, verify=self._verify_after_gesture,
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
        self.state = replace(self.state, verification=verification,
                             route_search=None, route_evaluation=None, route_overlay=(),
                             search_options=None, status=status)
        return succeeded

    def execute_continuously(self, serial: str, stop_event: threading.Event,
                             on_state: Callable[[BoardInspectionState], None] | None = None,
                             delay: float = DEFAULT_MOVE_DELAY) -> str:
        """Execute, release, recapture, and replan until stopped or unsafe."""
        def publish(status: str) -> str:
            self.state = replace(self.state, status=status)
            if on_state is not None:
                on_state(self.state)
            return status

        try:
            self.accept_current_board()
            publish("連續執行中：準備執行目前路徑")
            while True:
                if stop_event.is_set():
                    return publish("連續執行已由使用者停止")
                route = self.state.route_evaluation
                if route is None or not route.execution_eligible:
                    return publish("連續執行已停止：沒有可執行且符合條件的路徑")
                if not self.execute_route(serial, delay=delay):
                    return publish("連續執行已因執行或驗證失敗停止")
                if on_state is not None:
                    on_state(self.state)
                if stop_event.is_set():
                    return publish("連續執行已由使用者停止")
                state = self.capture_device(serial, auto_search=True)
                if state.uncertain_cells:
                    return publish("連續執行已停止：新盤面辨識不確定")
                route = state.route_evaluation
                if route is None or not route.execution_eligible:
                    return publish("連續執行已停止：新盤面沒有符合條件的路徑")
                publish("連續執行中：已擷取新盤面並規劃下一條路徑")
        except Exception as exc:
            return publish(f"連續執行因錯誤停止：{exc}")


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
    direct_estimate = result.direct_combo_estimate if result.direct_combo_estimate is not None else "無直接解"
    return (f"消除：{matches} | 落珠：{result.cascades} | Combo：{result.combo_count} | "
            f"直接：{result.direct_combo_count}／預估：{direct_estimate}\n"
            f"條件群組：{groups}\n條件：{conditions}\n"
            f"危害珠：{hazard} | 符合條件：{'是' if result.qualifying else '否'} | "
            f"可執行：{'是' if result.execution_eligible else '否'}")


def _format_layout(board, result: RouteEvaluation | None) -> str:
    """Show the reference-style block layout this Board's orbs can still reach."""
    if board is None or result is None:
        return ""
    goal, layout = max_combo_layout(board)
    rows = "\n".join("".join(orb_display(orb) if orb is not None else "．" for orb in row)
                     for row in layout)
    return f"目標版型（此排法可成立 {goal} Combo，路徑已排出 {result.direct_combo_count}）：\n{rows}"


@lru_cache(maxsize=1)
def _png_from_screenshot(screenshot_data: Screenshot) -> bytes:
    width, height, pixels = screenshot_data
    if width <= 0 or height <= 0 or len(pixels) != width * height * 4:
        raise ValueError("截圖必須包含 width×height 個 RGBA 像素")
    raw = bytearray()
    for row in range(height):
        raw.append(0)
        start = row * width * 4
        # adb screencap reports pixel format 1, RGBA_8888, and Android's own
        # `screencap -p` encodes those bytes in this order, so they go out as-is.
        raw.extend(pixels[start:start + width * 4])

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", binascii.crc32(kind + payload) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b""))


def _screenshot_image(screenshot_data: Screenshot) -> str:
    encoded = base64.b64encode(_png_from_screenshot(screenshot_data)).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _list_adb_devices() -> tuple[str, ...]:
    try:
        output = subprocess.check_output(["adb", "devices"], text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("無法取得 Android 裝置；請確認 adb 已安裝且可執行") from exc
    return tuple(
        parts[0] for line in output.splitlines()[1:]
        if len(parts := line.split()) == 2 and parts[1] == "device"
    )


def _review_cell(cell: object) -> tuple[int, int]:
    if not isinstance(cell, (list, tuple)) or len(cell) != 2:
        raise ValueError("盤面座標必須是 [row, col]")
    row, col = cell
    if (isinstance(row, bool) or not isinstance(row, int)
            or isinstance(col, bool) or not isinstance(col, int)
            or not (0 <= row < pad_router.ROWS and 0 <= col < pad_router.COLS)):
        raise ValueError(f"盤面座標必須位於 {board_label()} 盤面內")
    return row, col


def _review_board(board: Board | None, selected: tuple[int, int] | None,
                  protected: tuple[int, int] | None) -> list[dict[str, object]]:
    if board is None:
        return []
    cells: list[dict[str, object]] = []
    for row, values in enumerate(board):
        for col, orb in enumerate(values):
            key = orb_match_key(orb)
            cells.append({
                "cell": [row, col],
                "label": orb_display(orb),
                "kind": getattr(orb, "kind", "normal" if isinstance(key, int) else "unknown"),
                "color": getattr(orb, "color", key if isinstance(key, int) else None),
                "visual_class": getattr(orb, "visual_class", None),
                "enhanced": bool(getattr(orb, "enhanced", False)),
                "locked": bool(getattr(orb, "locked", False)),
                "unknown": key is None,
                "selected": selected == (row, col),
                "protected": protected == (row, col),
            })
    return cells


def _route_evaluation_snapshot(result: RouteEvaluation | None) -> dict[str, object] | None:
    if result is None:
        return None
    return {
        "route": [list(cell) for cell in result.route],
        "qualifying": result.qualifying,
        "confirmed": result.confirmed,
        "execution_eligible": result.execution_eligible,
        "diagnostic_status": result.diagnostic_status,
        "diagnostic": result.diagnostic,
        "combo_count": result.combo_count,
        "direct_combo_count": result.direct_combo_count,
        "direct_combo_estimate": result.direct_combo_estimate,
        "cascades": result.cascades,
        "hazard_outcome": result.hazard_outcome,
        "conditions": [
            {
                "identifier": item.identifier,
                "satisfied": item.satisfied,
                "message": item.message,
            }
            for item in result.condition_results
        ],
    }


def _route_search_snapshot(result: RouteSearchResult | None) -> dict[str, object] | None:
    if result is None:
        return None
    return {
        "qualifying_candidate": _route_evaluation_snapshot(result.qualifying_candidate),
        "diagnostic_candidate": _route_evaluation_snapshot(result.diagnostic_candidate),
        "selected": ("qualifying" if result.qualifying_candidate is not None else
                     "diagnostic" if result.diagnostic_candidate is not None else None),
        "cancelled": result.cancelled,
        "attempts": result.attempts,
        "seed": result.seed,
    }


def _search_options_snapshot(options: RouteSearchOptions | None) -> dict[str, object] | None:
    if options is None:
        return None
    return {
        "attempts": options.attempts,
        "seed": options.seed,
        "min_steps": options.min_steps,
        "max_steps": options.max_steps,
        "cascade": options.cascade,
    }


def _verification_snapshot(report: PlayVerification | None) -> dict[str, object] | None:
    if report is None:
        return None
    return {
        "status": report.status,
        "success": report.success,
        "mismatches": report.mismatches,
        "expected_board": _review_board(report.expected_board, None, None),
        "detected_board": _review_board(report.detected_board, None, None),
    }

def _route_overlay_snapshot(overlay: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "cell": list(item["cell"]),
            "step": item["step"],
            "x": item["x"],
            "y": item["y"],
        }
        for item in overlay
    ]


def _route_preview_snapshot(state: BoardInspectionState) -> dict[str, object] | None:
    result = state.route_evaluation
    board = state.confirmed_board or state.board
    if result is None or board is None:
        return None
    applied = expected_board_after_path(board, result.route)
    return {
        "stage": "drag_applied",
        "route": [list(cell) for cell in result.route],
        "board": _review_board(applied, None, state.protected_cell),
        "projected_combo": result.combo_count,
        "direct_combo_count": result.direct_combo_count,
        "direct_combo_estimate": result.direct_combo_estimate,
        "qualifying": result.qualifying,
        "confirmed": result.confirmed,
        "execution_eligible": result.execution_eligible,
        "diagnostic_status": result.diagnostic_status,
        "diagnostic": result.diagnostic,
    }

def _calibration_snapshot(calibration: BoardCalibration | None) -> dict[str, int] | None:
    if calibration is None:
        return None
    return {
        "left": calibration.left,
        "top": calibration.top,
        "cell": calibration.cell,
    }


def _calibration_from_payload(payload: dict[str, object]) -> BoardCalibration:
    raw = payload.get("calibration", payload)
    if not isinstance(raw, dict):
        raise ValueError("校正設定必須是 JSON 物件")
    values = {}
    for name in ("left", "top", "cell"):
        value = raw.get(name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"校正 {name} 必須是整數")
        values[name] = value
    return BoardCalibration(**values)



def _profile_from_payload(payload: dict[str, object]) -> RuleProfile:
    raw_profile = payload.get("profile")
    if raw_profile is None:
        raw_profile = payload.get("profile_json")
    if isinstance(raw_profile, str):
        try:
            raw_profile = json.loads(raw_profile)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("規則設定 JSON 無效") from exc
    if raw_profile is not None:
        if not isinstance(raw_profile, dict):
            raise ValueError("profile 必須是 JSON 物件")
        try:
            return RuleProfile.from_dict(raw_profile)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"規則設定無效：{exc}") from exc

    raw_conditions = payload.get("conditions", ())
    if not isinstance(raw_conditions, (list, tuple)):
        raise ValueError("conditions 必須是陣列")
    selections: list[tuple[str, str]] = []
    for item in raw_conditions:
        if isinstance(item, dict):
            label = item.get("label", NO_CONDITION)
            color = item.get("color", "不指定")
        else:
            label, color = item, "不指定"
        if not isinstance(label, str) or not isinstance(color, str):
            raise ValueError("規則條件必須包含文字 label/color")
        selections.append((label, color))
    try:
        return rule_profile_from_selections(
            selections,
            str(payload.get("operator", "全部符合")),
            str(payload.get("hazard_policy", "避免危害珠")),
            str(payload.get("external", "無")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"規則設定無效：{exc}") from exc


def _search_options_from_payload(payload: dict[str, object],
                                  default_attempts: int = 50) -> RouteSearchOptions:
    try:
        cascade = payload.get("cascade", True)
        if not isinstance(cascade, bool):
            raise ValueError("cascade 必須是 JSON boolean")
        return RouteSearchOptions(
            attempts=int(payload.get("attempts", default_attempts)),
            seed=int(payload.get("seed", 0)),
            min_steps=int(payload.get("min_steps", 0)),
            max_steps=int(payload.get("max_steps", 80)),
            cascade=cascade,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"搜尋設定無效：{exc}") from exc

def _capture_search_options(payload: dict[str, object]) -> RouteSearchOptions:
    raw = payload.get("search")
    if raw is None:
        if not any(key in payload for key in ("attempts", "seed", "min_steps", "max_steps", "cascade")):
            return RouteSearchOptions(attempts=30)
        raw = payload
    if not isinstance(raw, dict):
        raise ValueError("capture 的 search 必須是 JSON 物件")
    return _search_options_from_payload(raw, default_attempts=30)

def _move_delay_from_payload(payload: dict[str, object]) -> float:
    raw = payload.get("delay", DEFAULT_MOVE_DELAY)
    if isinstance(raw, bool):
        raise ValueError("MOVE delay 必須是有限的非負數")
    try:
        delay = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("MOVE delay 必須是有限的非負數") from exc
    if not math.isfinite(delay) or delay < 0:
        raise ValueError("MOVE delay 必須是有限的非負數")
    return delay


class BoardInspectionBridge:
    """Serialized, JSON-safe backend surface for the local webview."""

    def __init__(self, controller: BoardInspectionController | None = None,
                 device_lister: Callable[[], Iterable[str]] | None = None,
                 executor: Executor | None = None,
                 search_executor: Executor | None = None,
                 execution_executor: Executor | None = None,
                 operational_executor: Executor | None = None,
                 settings_path: Path | None = None):
        self.controller = controller or BoardInspectionController(model=OrbPrototypeModel.default())
        self._device_lister = device_lister or _list_adb_devices
        self._lock = threading.RLock()
        self._executor = executor if executor is not None else ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="pad-router"
        )
        self._search_executor = (
            search_executor if search_executor is not None else
            (executor if executor is not None else ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="pad-router-search"
            ))
        )
        self._execution_executor = (
            execution_executor if execution_executor is not None else
            (executor if executor is not None else ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="pad-router-execution"
            ))
        )
        self._operational_executor = (
            operational_executor if operational_executor is not None else
            (executor if executor is not None else ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="pad-router-operational"
            ))
        )
        self._settings_path = (
            Path(settings_path) if settings_path is not None else WORKSPACE_SETTINGS_PATH
        )
        self._future: Future | None = None
        self._interaction_future: Future | None = None
        self._search_future: Future | None = None
        self._execution_future: Future | None = None
        self._operational_future: Future | None = None
        self._closed = False
        self._busy = False
        self._pending_operations = 0
        self._pending_interactions = 0
        self._pending_searches = 0
        self._pending_operational = 0
        self._pending_operational_mutations = 0
        self._execution_busy = False
        self._execution_stop: threading.Event | None = None
        self._execution_stop_requested = False
        self._execution_status = "idle"
        self._execution_phase = "idle"
        self._execution_verification: dict[str, object] | None = None
        self._generation = 0
        self._search_cancel: threading.Event | None = None
        self._search_generation: int | None = None
        self._invalid_search_generations: set[int] = set()
        self._cancelled_search_generations: set[int] = set()
        self._search_status = "idle"
        self._search_progress: dict[str, object] | None = None
        self._search_options: RouteSearchOptions | None = None
        self._devices: tuple[str, ...] = ()
        self._selected_device = ""
        state = self.controller.state
        if state.rule_profile is None:
            self.controller.set_rule_profile(RuleProfile("最大 Combo"))
            state = self.controller.state
        self._selected_cell: tuple[int, int] | None = (
            state.uncertain_cells[0] if state.uncertain_cells else None
        )
        self._console: list[dict[str, str]] = []
        self._pending_update: dict[str, object] | None = None
        self._controller_snapshot = self._review_snapshot()
        self._announce("info", "ready", str(self._controller_snapshot["status"]))

    def _review_snapshot(self, refresh_source: bool = False) -> dict[str, object]:
        state = self.controller.state
        cached = getattr(self, "_controller_snapshot", {})
        if refresh_source or not cached:
            base = self.controller.snapshot()
        else:
            base = {**cached, "status": state.status}
        selected = self._selected_cell if state.board is not None else None
        protected = state.protected_cell
        profile = state.rule_profile
        return {
            **base,
            "board": _review_board(state.board, selected, protected),
            "board_size": {"name": f"{pad_router.COLS}x{pad_router.ROWS}", "label": board_label(),
                           "rows": pad_router.ROWS, "cols": pad_router.COLS,
                           "max_combo": max_combo_ceiling()},
            "unknown_count": len(state.uncertain_cells),
            "confirmed": state.confirmed,
            "selected_cell": list(selected) if selected is not None else None,
            "protected_cell": list(protected) if protected is not None else None,
            "calibration": _calibration_snapshot(state.calibration),
            "learning_enabled": self.controller.learning_enabled,
            "verify_after_gesture": self.controller.verify_after_gesture,
            "learning_status": state.learning_status,
            "rule_profile": profile.to_dict() if profile is not None else None,
            "route_result": _route_evaluation_snapshot(state.route_evaluation),
            "route_overlay": _route_overlay_snapshot(state.route_overlay),
            "route_preview": _route_preview_snapshot(state),
            "search_result": _route_search_snapshot(state.route_search),
        }

    @staticmethod
    def _copy_route(result: object) -> object:
        if not isinstance(result, dict):
            return result
        return {
            **result,
            "route": [list(cell) for cell in result.get("route", ())],
            "conditions": [dict(item) for item in result.get("conditions", ())],
        }

    @classmethod
    def _copy_search(cls, result: object) -> object:
        if not isinstance(result, dict):
            return result
        return {
            **result,
            "qualifying_candidate": cls._copy_route(result.get("qualifying_candidate")),
            "diagnostic_candidate": cls._copy_route(result.get("diagnostic_candidate")),
        }
    @staticmethod
    def _copy_execution(execution: object) -> object:
        if not isinstance(execution, dict):
            return execution
        verification = execution.get("verification")
        return {
            **execution,
            "verification": deepcopy(verification) if isinstance(verification, dict) else verification,
        }


    @classmethod
    def _copy_snapshot(cls, snapshot: dict[str, object]) -> dict[str, object]:
        source = snapshot.get("source")
        board = []
        for cell in snapshot.get("board", ()):
            copied = dict(cell)
            if isinstance(copied.get("cell"), (list, tuple)):
                copied["cell"] = list(copied["cell"])
            board.append(copied)
        selected = snapshot.get("selected_cell")
        protected = snapshot.get("protected_cell")
        profile = snapshot.get("rule_profile")
        search = snapshot.get("search")
        if isinstance(search, dict):
            progress = search.get("progress")
            options = search.get("options")
            search = {
                **search,
                "progress": dict(progress) if isinstance(progress, dict) else progress,
                "options": dict(options) if isinstance(options, dict) else options,
                "result": cls._copy_search(search.get("result")),
            }
        route_overlay = [
            {
                **item,
                "cell": list(item["cell"]),
            }
            for item in snapshot.get("route_overlay", ())
            if isinstance(item, dict) and isinstance(item.get("cell"), (list, tuple))
        ]
        route_preview = snapshot.get("route_preview")
        if isinstance(route_preview, dict):
            route_preview = deepcopy(route_preview)
        return {
            **snapshot,
            "source": dict(source) if isinstance(source, dict) else None,
            "board": board,
            "devices": list(snapshot.get("devices", ())),
            "selected_cell": list(selected) if isinstance(selected, (list, tuple)) else selected,
            "protected_cell": list(protected) if isinstance(protected, (list, tuple)) else protected,
            "calibration": (dict(snapshot["calibration"])
                            if isinstance(snapshot.get("calibration"), dict) else None),
            "route_result": cls._copy_route(snapshot.get("route_result")),
            "route_overlay": route_overlay,
            "route_preview": route_preview,
            "search_result": cls._copy_search(snapshot.get("search_result")),
            "rule_profile": deepcopy(profile) if isinstance(profile, dict) else profile,
            "search": search,
            "execution": cls._copy_execution(snapshot.get("execution")),
            "debug": (dict(snapshot["debug"])
                      if isinstance(snapshot.get("debug"), dict) else {}),
            "console": [dict(item) for item in snapshot.get("console", ())],
        }

    def _load_settings(self) -> dict[str, object]:
        try:
            settings = json.loads(self._settings_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeError, json.JSONDecodeError):
            self._announce("info", "settings", "工作區設定無效，已回到預設值")
            return {}
        if (
            not isinstance(settings, dict)
            or isinstance(settings.get("version"), bool)
            or not isinstance(settings.get("version"), int)
            or settings["version"] != 1
            or any(key not in settings for key in (
                "rule_profile", "search", "board_size", "move_delay",
                "learning_enabled", "verify_after_gesture",
            ))
        ):
            self._announce("info", "settings", "工作區設定無效，已回到預設值")
            return {}
        return settings

    def _save_settings(self, settings: object) -> None:
        if not isinstance(settings, dict):
            raise ValueError("工作區設定必須是 JSON 物件")
        temporary: Path | None = None
        try:
            payload = json.dumps(settings, ensure_ascii=False)
            self._settings_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self._settings_path.parent,
                prefix=f".{self._settings_path.name}.", suffix=".tmp", delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(payload)
                handle.flush()
            temporary.replace(self._settings_path)
        except Exception as exc:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            if isinstance(exc, OSError):
                raise
            raise OSError("工作區設定暫存檔寫入或取代失敗") from exc


    def _debug_snapshot_locked(self) -> dict[str, object]:
        state = self.controller.state
        return {
            "bridge_closed": self._closed,
            "source_name": state.source_name,
            "confirmed": state.confirmed,
            "unknown_count": len(state.uncertain_cells),
            "calibration": _calibration_snapshot(state.calibration),
            "generation": self._generation,
            "pending_operations": self._pending_operations,
            "pending_interactions": self._pending_interactions,
            "pending_searches": self._pending_searches,
            "pending_operational": self._pending_operational,
            "pending_operational_mutations": self._pending_operational_mutations,
            "search_generation": self._search_generation,
            "execution_phase": self._execution_phase,
        }

    def _view_locked(self) -> dict[str, object]:
        return self._copy_snapshot({
            **self._controller_snapshot,
            "devices": self._devices,
            "selected_device": self._selected_device or None,
            "busy": self._busy,
            "search_busy": self._pending_searches > 0,
            "operational_busy": self._pending_operational > 0,
            "operational_mutation_busy": self._pending_operational_mutations > 0,
            "search": {
                "status": self._search_status,
                "progress": self._search_progress,
                "options": _search_options_snapshot(self._search_options),
                "generation": self._generation,
                "result": self._controller_snapshot.get("search_result"),
            },
            "execution": {
                "status": self._execution_status,
                "phase": self._execution_phase,
                "busy": self._execution_busy,
                "stop_requested": self._execution_stop_requested,
                "verification": self._execution_verification,
            },
            "debug": self._debug_snapshot_locked(),
            "console": self._console,
        })

    def _announce(self, level: str, phase: str, message: str,
                  *, status: str | None = None) -> None:
        entry = {"level": level, "phase": phase, "message": message}
        self._console.append(entry)
        del self._console[:-100]
        self._controller_snapshot = {
            **self._controller_snapshot,
            "status": status if status is not None else message,
        }
        self._pending_update = {
            "type": "snapshot",
            "level": level,
            "phase": phase,
            "message": message,
            "snapshot": self._view_locked(),
        }

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return self._view_locked()

    def drain_events(self) -> list[dict[str, object]]:
        with self._lock:
            if self._pending_update is None:
                return []
            event = {
                **self._pending_update,
                "snapshot": self._copy_snapshot(self._pending_update["snapshot"]),
            }
            self._pending_update = None
            return [event]

    def _invalidate_generation(self) -> None:
        self._generation += 1
        if hasattr(self, "_controller_snapshot"):
            self._controller_snapshot = {
                **self._controller_snapshot,
                "route_result": None,
                "route_overlay": [],
                "route_preview": None,
                "search_result": None,
            }
        if self._search_cancel is not None:
            self._search_cancel.set()
            if self._search_generation is not None:
                self._invalid_search_generations.add(self._search_generation)
        self._search_cancel = None
        self._search_generation = None
        self._search_status = "idle"
        self._search_progress = None
        self._search_options = None

    def _begin_search(self, options: RouteSearchOptions) -> tuple[int, threading.Event]:
        if self._search_cancel is not None and self._search_generation is not None:
            self._search_cancel.set()
            self._invalid_search_generations.add(self._search_generation)
        self._generation += 1
        generation = self._generation
        cancel = threading.Event()
        self._search_cancel = cancel
        self._search_generation = generation
        self._search_status = "running"
        self._search_progress = None
        self._search_options = options
        return generation, cancel

    def _search_progress_update(self, generation: int, phase: str,
                                completed: int, total: int) -> None:
        with self._lock:
            if generation != self._generation or self._search_generation != generation:
                return
            self._search_progress = {
                "phase": phase,
                "completed": completed,
                "total": total,
            }
            self._pending_update = {
                "type": "snapshot",
                "level": "info",
                "phase": "search",
                "message": f"搜尋 {phase}：{completed}/{total}",
                "snapshot": self._view_locked(),
            }

    def _search_operation(self, generation: int, board: Board, profile: RuleProfile,
                          options: RouteSearchOptions, confirmed: bool,
                          protected: tuple[int, int] | None,
                          cancel: threading.Event) -> tuple[int, RouteSearchResult, RouteSearchOptions]:
        result = search_qualifying_route(
            board, profile, options, confirmed=confirmed, protected_cell=protected,
            on_progress=lambda phase, completed, total: self._search_progress_update(
                generation, phase, completed, total
            ),
            cancel=cancel.is_set,
        )
        return generation, result, options

    def _start_search(self, options: RouteSearchOptions) -> dict[str, object]:
        with self._lock:
            state = self.controller.state
            if state.board is None:
                raise ValueError("請先擷取裝置畫面，再搜尋路徑")
            if state.rule_profile is None:
                raise ValueError("請先套用規則設定，再搜尋路徑")
            board = state.confirmed_board or state.board
            profile = state.rule_profile
            confirmed = state.confirmed
            protected = state.protected_cell
            generation, cancel = self._begin_search(options)
            self.controller.invalidate_route("正在搜尋；先前候選已失效")
            self._controller_snapshot = self._review_snapshot()
        return self._submit(
            "search",
            "正在計算並搜尋路徑",
            lambda: self._search_operation(
                generation, board, profile, options, confirmed, protected, cancel
            ),
        )


    def _execution_announce(self, phase: str, message: str,
                            level: str = "info") -> None:
        with self._lock:
            self._execution_phase = phase
            self._announce(level, "execution", message)

    def _finish_execution(self, status: str, message: str,
                          verification: dict[str, object] | None = None) -> None:
        with self._lock:
            self._execution_status = status
            self._execution_phase = "stopped" if status == "stopped" else "complete"
            self._execution_verification = verification
            self._execution_busy = False
            self._execution_stop = None
            self._busy = self._pending_interactions > 0
            self._controller_snapshot = self._review_snapshot()
            level = ("success" if status == "success"
                     else "warning" if status == "stopped" else "error")
            self._announce(level, "execution", message)

    def _run_execution(self, serial: str, stop_event: threading.Event,
                       delay: float = DEFAULT_MOVE_DELAY) -> None:
        verification = None
        try:
            self._execution_announce(
                "acceptance",
                "執行準備：正在接受目前盤面",
            )
            self.controller.accept_current_board()
            if stop_event.is_set():
                self._finish_execution(
                    "stopped",
                    "停止已生效：目前盤面已接受，尚未開始手勢。",
                )
                return
            self._execution_announce(
                "gesture",
                "正在執行手勢；停止要求將於目前手勢安全放手後生效",
            )
            succeeded = self.controller.execute_route(serial, delay=delay)
            verification = _verification_snapshot(self.controller.state.verification)
            if verification is not None:
                with self._lock:
                    self._execution_verification = verification
                mismatch = verification["mismatches"]
                if verification["status"] == "released_without_verification":
                    self._execution_announce("verification", "手勢已送出並直接放手；未做盤面確認", "warning")
                else:
                    detail = ("成功（0 格不符）" if verification["success"]
                              else f"失敗（{mismatch if mismatch is not None else '未知'} 格不符）")
                    self._execution_announce("verification", f"正在驗證結果：手勢後盤面驗證：{detail}",
                                             "success" if verification["success"] else "warning")
            if stop_event.is_set():
                self._finish_execution(
                    "stopped",
                    "停止已生效：目前手勢已安全放手。",
                    verification,
                )
            elif succeeded:
                self._finish_execution(
                    "success",
                    ("執行完成：手勢已送出並直接放手，未做盤面確認。"
                     if verification is not None
                     and verification["status"] == "released_without_verification"
                     else "執行成功：手勢後盤面驗證完成。"),
                    verification,
                )
            else:
                self._finish_execution(
                    "failed",
                    "執行失敗：請查看手勢後驗證結果。",
                    verification,
                )
        except Exception as exc:
            self._finish_execution("failed", f"執行失敗：{exc}", verification)

    def _run_continuous_execution(self, serial: str, stop_event: threading.Event,
                                  delay: float = DEFAULT_MOVE_DELAY) -> None:
        self._execution_announce(
            "gesture",
            "連續執行中；停止要求將於目前手勢安全放手後生效",
        )
        status = self.controller.execute_continuously(
            serial, stop_event, delay=delay,
            on_state=lambda state: self._execution_announce("gesture", state.status),
        )
        self._finish_execution(
            "stopped" if stop_event.is_set() else "failed", status,
            _verification_snapshot(self.controller.state.verification),
        )

    def _start_execution(self, serial: object, delay: float = DEFAULT_MOVE_DELAY,
                         continuous: bool = False) -> dict[str, object]:
        with self._lock:
            if self._execution_busy:
                self._announce("warning", "execution", "執行中；命令已拒絕")
                raise ValueError("執行中；請等待目前手勢安全結束")
            if self._pending_interactions:
                self._announce("warning", "execution", "仍有後端命令執行中；執行命令已拒絕")
                raise ValueError("仍有後端命令執行中；請稍候")
            if self._pending_operational_mutations:
                self._announce("warning", "execution", "裝置盤面作業中；執行命令已拒絕")
                raise ValueError("裝置盤面作業中；請等待目前作業完成")
            if not isinstance(serial, str) or not serial.strip():
                raise ValueError("請先更新並選擇 Android 裝置")
            state = self.controller.state
            result = state.route_evaluation
            if result is None or not state.confirmed or not result.execution_eligible:
                raise ValueError("僅能執行目前已確認且符合條件的路徑")
            serial = serial.strip()
            self._execution_busy = True
            self._execution_stop_requested = False
            self._execution_status = "running"
            self._execution_phase = "acceptance"
            self._execution_verification = None
            self._execution_stop = threading.Event()
            self._busy = True
            self._announce(
                "info",
                "execution",
                ("連續執行準備：接受目前路徑；停止前會持續擷取並轉珠"
                 if continuous else "執行準備：接受目前路徑；ADB 手勢尚未開始"),
            )
            future = self._execution_executor.submit(
                self._run_continuous_execution if continuous else self._run_execution,
                serial, self._execution_stop, delay,
            )
            self._execution_future = future
            self._future = future
            return {"accepted": True, "snapshot": self._view_locked()}

    def _stop_execution(self) -> dict[str, object]:
        with self._lock:
            if not self._execution_busy:
                return self._view_locked()
            self._execution_stop_requested = True
            if self._execution_stop is not None:
                self._execution_stop.set()
            self._execution_phase = "stopping"
            self._announce(
                "info",
                "execution",
                "停止已要求；目前手勢完成並安全放手後生效",
            )
            return self._view_locked()

    def _submit(self, phase: str, started: str, operation: Callable[[], object]) -> dict[str, object]:
        with self._lock:
            if self._closed:
                raise RuntimeError("後端已關閉")
            if self._execution_busy:
                self._announce("warning", "execution", "執行中；命令已拒絕")
                raise ValueError("執行中；請等待目前手勢安全結束")
            is_search = phase == "search"
            is_operational = phase in {"devices", "capture", "calibration"}
            is_operational_mutation = phase in {"capture", "calibration"}
            if is_operational and self._pending_operational:
                self._announce("warning", "device", "裝置作業中；命令已拒絕")
                raise ValueError("裝置作業中；請等待目前作業完成")
            if is_operational_mutation and self._pending_interactions:
                self._announce("warning", "device", "仍有盤面命令執行中；裝置作業已拒絕")
                raise ValueError("仍有盤面命令執行中；請稍候")
            if is_operational_mutation and self._pending_operational_mutations:
                self._announce("warning", "device", "裝置盤面作業中；命令已拒絕")
                raise ValueError("裝置盤面作業中；請等待目前作業完成")
            self._pending_operations += 1
            if is_search:
                self._pending_searches += 1
            elif is_operational:
                self._pending_operational += 1
                if is_operational_mutation:
                    self._pending_operational_mutations += 1
            else:
                self._pending_interactions += 1
            self._busy = self._pending_interactions > 0 or self._execution_busy
            self._announce("info", phase, started)
            executor = (
                self._search_executor if is_search
                else self._operational_executor if is_operational
                else self._executor
            )
            future = executor.submit(self._run, phase, operation)
            self._future = future
            if is_search:
                self._search_future = future
            elif is_operational:
                self._operational_future = future
            else:
                self._interaction_future = future
            return {"accepted": True, "snapshot": self._view_locked()}

    def _run(self, phase: str, operation: Callable[[], object]) -> None:
        is_search = phase == "search"
        is_operational = phase in {"devices", "capture", "calibration"}
        is_operational_mutation = phase in {"capture", "calibration"}
        try:
            result = operation()
            with self._lock:
                if phase == "devices":
                    self._devices = tuple(result)
                    if self._selected_device not in self._devices:
                        self._selected_device = self._devices[0] if self._devices else ""
                    message = (f"已找到 {len(self._devices)} 個 Android 裝置"
                               if self._devices else "沒有可用的 Android 裝置")
                    level = "success" if self._devices else "warning"
                elif phase == "capture":
                    if (isinstance(result, tuple) and len(result) == 2
                            and isinstance(result[0], BoardInspectionState)):
                        state, search_options = result
                    else:
                        state, search_options = self.controller.state, RouteSearchOptions(attempts=30)
                    self._invalidate_generation()
                    self._selected_cell = (
                        state.uncertain_cells[0] if state.uncertain_cells else None
                    )
                    self._controller_snapshot = self._review_snapshot(refresh_source=True)
                    message = str(self._controller_snapshot["status"])
                    level = "success"
                    if state.board is not None and state.rule_profile is not None:
                        self._start_search(search_options)
                elif phase == "review":
                    state, selected = result
                    self._selected_cell = selected
                    self._controller_snapshot = self._review_snapshot()
                    message = str(state.status)
                    level = "success"
                elif phase == "calibration":
                    state = result
                    self._invalidate_generation()
                    self._selected_cell = (
                        state.uncertain_cells[0] if state.uncertain_cells else None
                    )
                    self._controller_snapshot = self._review_snapshot()
                    message = str(state.status)
                    level = "success"
                elif phase == "rules":
                    state = result
                    self._search_status = "idle"
                    self._search_progress = None
                    self._search_options = None
                    self._controller_snapshot = self._review_snapshot()
                    message = str(state.status)
                    level = "success"
                elif phase == "search":
                    generation, search_result, options = result
                    self._search_progress = None
                    if generation != self._generation:
                        newer_active = (
                            self._search_generation is not None
                            and self._search_generation != generation
                        )
                        if generation in self._cancelled_search_generations:
                            self._cancelled_search_generations.discard(generation)
                            if not newer_active:
                                self._search_status = "cancelled"
                            message = "搜尋已取消"
                        else:
                            self._invalid_search_generations.discard(generation)
                            if not newer_active:
                                self._search_status = "stale"
                            message = "搜尋結果已失效，未套用"
                        if self._search_generation == generation:
                            self._search_cancel = None
                            self._search_generation = None
                        level = "info"
                    elif search_result.cancelled:
                        self._search_status = "cancelled"
                        self._search_cancel = None
                        self._search_generation = None
                        message = "搜尋已取消"
                        level = "info"
                    else:
                        self.controller._apply_search_result(search_result, options)
                        self._search_status = "complete"
                        self._search_cancel = None
                        self._search_generation = None
                        self._controller_snapshot = self._review_snapshot()
                        message = ("搜尋完成：找到符合條件的路徑"
                                   if search_result.qualifying
                                   else "搜尋完成：保留診斷預覽")
                        level = "success" if search_result.qualifying else "warning"
                else:
                    message = str(result)
                    level = "success"
                self._pending_operations -= 1
                if is_search:
                    self._pending_searches = max(0, self._pending_searches - 1)
                elif is_operational:
                    self._pending_operational = max(0, self._pending_operational - 1)
                    if is_operational_mutation:
                        self._pending_operational_mutations = max(
                            0, self._pending_operational_mutations - 1
                        )
                else:
                    self._pending_interactions = max(0, self._pending_interactions - 1)
                self._busy = self._pending_interactions > 0 or self._execution_busy
                self._announce(level, phase, message)
        except Exception as exc:
            with self._lock:
                self._pending_operations = max(0, self._pending_operations - 1)
                if is_search:
                    self._pending_searches = max(0, self._pending_searches - 1)
                    self._search_status = "failed"
                    self._search_progress = None
                elif is_operational:
                    self._pending_operational = max(0, self._pending_operational - 1)
                    if is_operational_mutation:
                        self._pending_operational_mutations = max(
                            0, self._pending_operational_mutations - 1
                        )
                else:
                    self._pending_interactions = max(0, self._pending_interactions - 1)
                self._busy = self._pending_interactions > 0 or self._execution_busy
                self._announce("error", phase, str(exc))

    def _resolve_cell(self, payload: dict[str, object], *, selected: bool = False) -> tuple[int, int]:
        if "cell" in payload:
            return _review_cell(payload["cell"])
        if "row" in payload or "col" in payload:
            return _review_cell((payload.get("row"), payload.get("col")))
        if selected and self._selected_cell is not None:
            return self._selected_cell
        raise ValueError("請先選取盤面格")

    def _correct(self, cell: tuple[int, int], value: object) -> tuple[BoardInspectionState, tuple[int, int] | None]:
        was_unknown = cell in self.controller.state.uncertain_cells
        state = self.controller.correct_cell(*cell, value)
        if not was_unknown:
            return state, cell
        next_cell = next((item for item in state.uncertain_cells if item > cell), None)
        return state, next_cell or (state.uncertain_cells[0] if state.uncertain_cells else None)

    def _protect(self, cell: tuple[int, int] | None) -> tuple[BoardInspectionState, tuple[int, int] | None]:
        return self.controller.set_protected_cell(cell, recompute=False), self._selected_cell

    def command(self, payload: str | dict[str, object]) -> dict[str, object] | list[dict[str, object]]:
        """Accept one intent and return a JSON-safe acknowledgement or view."""
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError) as exc:
                raise ValueError("命令必須是有效 JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("命令必須是 JSON 物件")
        action = payload.get("action", payload.get("command"))
        if action not in {
            "snapshot", "events", "drain_events", "stop_execution", "cancel_execution",
            "export_rule_profile", "export_profile", "set_learning_enabled",
            "set_verify_after_gesture", "load_settings", "save_settings",
        }:
            with self._lock:
                if self._execution_busy:
                    self._announce("warning", "execution", "執行中；命令已拒絕")
                    raise ValueError("執行中；請等待目前手勢安全結束")
        if action in _OPERATIONAL_MUTATION_CONFLICTS:
            with self._lock:
                if self._pending_operational_mutations:
                    self._announce("warning", "device", "裝置盤面作業中；命令已拒絕")
                    raise ValueError("裝置盤面作業中；請等待目前作業完成")
        if action == "snapshot":
            return self.snapshot()
        if action in {"events", "drain_events"}:
            return self.drain_events()
        if action == "load_settings":
            with self._lock:
                return self._load_settings()
        if action == "save_settings":
            with self._lock:
                try:
                    self._save_settings(payload.get("settings"))
                except Exception as exc:
                    self._announce("warning", "settings", f"設定寫入失敗：{exc}")
                return self._view_locked()
        if action == "set_learning_enabled":
            enabled = payload.get("enabled")
            with self._lock:
                self.controller.set_learning_enabled(enabled)
                self._controller_snapshot = self._review_snapshot()
                self._announce("info", "learning", f"AI 模型學習已{'開啟' if enabled else '關閉'}")
                return self._view_locked()
        if action == "set_board_size":
            size = payload.get("size")
            if size not in pad_router.BOARD_SIZES:
                raise ValueError("盤面大小必須是 6x5 或 7x6")
            with self._lock:
                self._invalidate_generation()
            return self._submit(
                "calibration",
                f"正在切換盤面大小：{size}",
                lambda: self.controller.set_board_size(size),
            )
        if action == "set_verify_after_gesture":
            enabled = payload.get("enabled")
            if not isinstance(enabled, bool):
                raise ValueError("放手前確認設定必須是布林值")
            with self._lock:
                self.controller.set_verify_after_gesture(enabled)
                self._controller_snapshot = self._review_snapshot()
                self._announce("info", "execution",
                               "轉珠後將停手確認盤面" if enabled else "轉珠後直接放手，不做盤面確認")
                return self._view_locked()
        if action == "refresh_devices":
            return self._submit("devices", "正在更新 Android 裝置清單", self._device_lister)
        if action in {"export_rule_profile", "export_profile"}:
            with self._lock:
                profile = self.controller.state.rule_profile
                if profile is None:
                    raise ValueError("尚未套用規則設定")
                profile_data = profile.to_dict()
                self._announce("success", "rules", f"規則設定已匯出：{profile.name}")
                return {
                    "accepted": True,
                    "profile": profile_data,
                    "profile_json": profile.to_json(),
                    "snapshot": self._view_locked(),
                }
        if action == "select_device":
            serial = payload.get("serial")
            if not isinstance(serial, str) or not serial.strip():
                raise ValueError("請選擇裝置")
            serial = serial.strip()
            with self._lock:
                if self._devices and serial not in self._devices:
                    raise ValueError("選取的裝置不在目前清單")
                self._selected_device = serial
                self._announce("info", "device", f"已選擇裝置：{serial}")
                return self._view_locked()
        if action == "select_cell":
            if self.controller.state.board is None:
                raise ValueError("請先擷取裝置畫面，再選取盤面格")
            with self._lock:
                self._selected_cell = self._resolve_cell(payload)
                self._controller_snapshot = self._review_snapshot()
                self._announce(
                    "info", "review",
                    f"已選取第 {self._selected_cell[0] + 1} 列、第 {self._selected_cell[1] + 1} 行",
                )
                return self._view_locked()
        if action in {"correct", "correct_cell"}:
            with self._lock:
                cell = self._resolve_cell(payload, selected=True)
            with self._lock:
                self._invalidate_generation()
            return self._submit(
                "review",
                f"正在修正第 {cell[0] + 1} 列、第 {cell[1] + 1} 行",
                lambda: self._correct(cell, payload.get("value")),
            )
        if action in {"protect_cell", "set_protected_cell"}:
            with self._lock:
                if "cell" in payload and payload["cell"] is None:
                    cell = None
                elif "cell" in payload or "row" in payload or "col" in payload:
                    cell = self._resolve_cell(payload)
                else:
                    cell = self._selected_cell
                self._invalidate_generation()
            return self._submit(
                "review",
                "正在更新保護格",
                lambda: self._protect(cell),
            )
        if action in {"import_rule_profile", "import_profile"}:
            profile = _profile_from_payload(payload)
            with self._lock:
                self._invalidate_generation()
            return self._submit(
                "rules",
                f"正在匯入規則設定：{profile.name}",
                lambda: self.controller.set_rule_profile(profile),
            )
        if action in {"calibrate", "set_calibration", "apply_calibration"}:
            with self._lock:
                self._invalidate_generation()
            return self._submit(
                "calibration",
                "正在套用盤面校正",
                lambda: self.controller.set_calibration(
                    _calibration_from_payload(payload), auto_search=False
                ),
            )
        if action in {"auto_calibrate", "infer_calibration"}:
            with self._lock:
                self._invalidate_generation()
            return self._submit(
                "calibration",
                "正在重新自動校正盤面",
                lambda: self.controller.set_calibration(
                    infer_calibration(
                        self.controller.state.width or 0,
                        self.controller.state.height or 0,
                        self.controller.state.pixels,
                    ),
                    auto_search=False,
                ),
            )
        if action in {"set_rule_profile", "update_rules"}:
            profile = _profile_from_payload(payload)
            with self._lock:
                self._invalidate_generation()
            return self._submit(
                "rules",
                "正在套用規則設定",
                lambda: self.controller.set_rule_profile(profile),
            )
        if action in {"search", "search_route", "start_search"}:
            return self._start_search(_search_options_from_payload(payload))
        if action in {"cancel_search", "stop_search"}:
            with self._lock:
                if self._search_cancel is None or self._search_generation is None:
                    return self._view_locked()
                generation = self._search_generation
                self._search_cancel.set()
                self._cancelled_search_generations.add(generation)
                self._generation += 1
                self._search_cancel = None
                self._search_generation = None
                self._search_status = "cancelling"
                self._search_progress = None
                self._announce("info", "search", "正在取消搜尋")
                return self._view_locked()
        if action in {"execute", "execute_route", "execute_continuously",
                      "start_continuous_execution"}:
            with self._lock:
                serial = self._selected_device if payload.get("serial") is None else payload.get("serial")
            delay = _move_delay_from_payload(payload)
            return self._start_execution(serial, delay,
                                         continuous=action not in {"execute", "execute_route"})
        if action in {"stop_execution", "cancel_execution"}:
            return self._stop_execution()

        if action in {"capture", "capture_device", "capture_screen"}:
            search_options = _capture_search_options(payload)
            serial = payload.get("serial")
            with self._lock:
                serial = self._selected_device if serial is None else serial
                if not isinstance(serial, str) or not serial.strip():
                    raise ValueError("請先更新並選擇 Android 裝置")
                serial = serial.strip()
                self._invalidate_generation()
            return self._submit(
                "capture",
                f"正在擷取畫面並辨識盤面：{serial}",
                lambda: (
                    self.controller.capture_device(serial, auto_search=False),
                    search_options,
                ),
            )
        raise ValueError(f"不支援的命令：{action}")

    def wait_for_idle(self, timeout: float | None = None) -> None:
        with self._lock:
            futures = tuple(
                future for future in (
                    self._interaction_future, self._search_future,
                    self._execution_future, self._operational_future, self._future,
                )
                if future is not None
            )
        for future in dict.fromkeys(futures):
            future.result(timeout=timeout)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            if self._execution_busy:
                self._stop_execution()
            self._closed = True
            executors = []
            for executor in (
                self._executor, self._search_executor,
                self._execution_executor, self._operational_executor,
            ):
                if executor not in executors:
                    executors.append(executor)
        for executor in executors:
            executor.shutdown(wait=False, cancel_futures=True)
