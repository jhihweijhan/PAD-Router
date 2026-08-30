import base64
import json
import os
import struct
import sys
import tempfile
import threading
import unittest
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pad_router
from pad_router import (COLS, ROWS, CellFeatures, ConditionGroup, ExternalCondition, Grid, LeaderCondition, Orb, PlayVerification,
                        RouteSearchOptions, RouteSearchResult, RuleProfile, _cell_features, _normal_color, detect_board_pixels,
                        search_qualifying_route,
                        expected_board_after_path)


from pad_router_gui import (BoardCalibration, BoardInspectionBridge, BoardInspectionController,
                            OrbPrototypeModel, _png_from_screenshot, decode_png, infer_calibration,
                            rule_profile_from_selections)


def png_bytes(width=12, height=10):
    rows = []
    for _ in range(height):
        rows.append(b"\x00" + bytes((20, 40, 60, 255)) * width)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b"")


class NormalColorTextureTests(unittest.TestCase):
    @staticmethod
    def _feature(center_hue):
        return SimpleNamespace(
            hue=.82, saturation=.58, value=.84, dark=0, white=0, orange=0,
            purple=0, blue=0, green=0, plus=0, center_hue=center_hue,
            center_saturation=.60, center_value=.90,
        )

    def test_distinct_center_patterns_resolve_heart_and_dark(self):
        self.assertEqual(_normal_color(self._feature(.76)), 6)
        self.assertEqual(_normal_color(self._feature(.94)), 5)

    def test_ambiguous_center_pattern_remains_unknown(self):
        self.assertIsNone(_normal_color(self._feature(.85)))



class OrbPrototypeSpatialTextureTests(unittest.TestCase):
    @staticmethod
    def _pixels(pattern):
        width, height, cell = COLS * 85, ROWS * 85, 85
        base_rgb = (204, 90, 214)
        bright_rgb = (169, 92, 230)
        dim_rgb = (103, 56, 140)
        pixels = bytearray(bytes((base_rgb[2], base_rgb[1], base_rgb[0], 255)) * (width * height))
        for row in range(ROWS):
            for col in range(COLS):
                center_x, center_y = col * cell + cell // 2, row * cell + cell // 2
                for y in range(center_y - 14, center_y + 15):
                    for x in range(center_x - 14, center_x + 15):
                        dx, dy = x - center_x, y - center_y
                        if dx * dx + dy * dy < 14 ** 2:
                            rgb = bright_rgb if pattern(dx, dy) else dim_rgb
                            pixels[(y * width + x) * 4:(y * width + x) * 4 + 4] = bytes(
                                (rgb[2], rgb[1], rgb[0], 255)
                            )
        return width, height, bytes(pixels)

    @staticmethod
    def _features(image):
        width, height, pixels = image
        grid = Grid(0, 0, 85)
        return _cell_features(width, height, pixels, grid.point(0, 0), grid.cell)

    @staticmethod
    def _learned_model(path, heart_feature, dark_feature):
        model = OrbPrototypeModel(path)
        model.learn(Orb("normal", 6), heart_feature, human=True, cell=(0, 1))
        model.learn(Orb("normal", 5), dark_feature, human=True, cell=(0, 2))
        return model

    def test_human_spatial_pattern_corrects_baseline_and_survives_reload(self):
        heart_image = self._pixels(lambda dx, _dy: dx < 0)
        dark_image = self._pixels(lambda _dx, dy: dy < 0)
        heart_feature = self._features(heart_image)
        dark_feature = self._features(dark_image)
        grid = Grid(0, 0, 85)

        self.assertAlmostEqual(heart_feature.center_hue, dark_feature.center_hue)
        self.assertAlmostEqual(heart_feature.center_saturation, dark_feature.center_saturation)
        self.assertAlmostEqual(heart_feature.center_value, dark_feature.center_value)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prototypes.json"
            model = self._learned_model(path, heart_feature, dark_feature)
            baseline = detect_board_pixels(*dark_image, grid)
            self.assertEqual(baseline[0][0].color, 6)
            detected = OrbPrototypeModel(path).detect(*dark_image, grid, detect_board_pixels)

        self.assertEqual(detected[0][0].color, 5)

    def test_human_spatial_pattern_corrects_opposite_baseline(self):
        heart_image = self._pixels(lambda dx, _dy: dx < 0)
        dark_image = self._pixels(lambda _dx, dy: dy < 0)
        model = self._learned_model(None, self._features(heart_image), self._features(dark_image))
        dark_baseline = ((Orb("normal", 5),) * COLS,) * ROWS

        detected = model.detect(*heart_image, Grid(0, 0, 85), lambda *_args: dark_baseline)

        self.assertEqual(detected[0][0].color, 6)

    def test_equally_ambiguous_spatial_pattern_does_not_override_baseline(self):
        heart_image = self._pixels(lambda dx, _dy: dx < 0)
        dark_image = self._pixels(lambda _dx, dy: dy < 0)
        ambiguous_image = self._pixels(lambda dx, dy: (dx // 5 + dy // 5) % 2 == 0)
        model = self._learned_model(None, self._features(heart_image), self._features(dark_image))
        grid = Grid(0, 0, 85)
        baseline = detect_board_pixels(*ambiguous_image, grid)

        detected = model.detect(*ambiguous_image, grid, detect_board_pixels)

        self.assertEqual(detected[0][0], baseline[0][0])


class OrbPrototypeEdgeTextureTests(unittest.TestCase):
    @staticmethod
    def _pixels(pattern, brightness=1.0):
        width, height, cell = COLS * 85, ROWS * 85, 85
        base_rgb = (204, 90, 214)
        bright_rgb = (169, 92, 230)
        dim_rgb = (103, 56, 140)
        scaled = lambda rgb: tuple(round(channel * brightness) for channel in rgb)
        base = scaled(base_rgb)
        bright = scaled(bright_rgb)
        dim = scaled(dim_rgb)
        pixels = bytearray(bytes((base[2], base[1], base[0], 255)) * (width * height))
        for row in range(ROWS):
            for col in range(COLS):
                center_x, center_y = col * cell + cell // 2, row * cell + cell // 2
                for y in range(center_y - 27, center_y + 28):
                    for x in range(center_x - 27, center_x + 28):
                        dx, dy = x - center_x, y - center_y
                        distance = dx * dx + dy * dy
                        if distance < 27 ** 2:
                            rgb = dim if distance < 14 ** 2 or not pattern(dx, dy) else bright
                            pixels[(y * width + x) * 4:(y * width + x) * 4 + 4] = bytes(
                                (rgb[2], rgb[1], rgb[0], 255)
                            )
        return width, height, bytes(pixels)

    @staticmethod
    def _features(image):
        width, height, pixels = image
        grid = Grid(0, 0, 85)
        return _cell_features(width, height, pixels, grid.point(0, 0), grid.cell)

    @staticmethod
    def _learned_model(path, heart_feature, dark_feature):
        model = OrbPrototypeModel(path)
        model.learn(Orb("normal", 6), heart_feature, human=True, cell=(0, 1))
        model.learn(Orb("normal", 5), dark_feature, human=True, cell=(0, 2))
        return model

    def test_edge_shape_correction_survives_reload_and_brightness_change(self):
        heart_image = self._pixels(lambda _dx, dy: abs(dy) <= 4)
        dark_image = self._pixels(lambda dx, _dy: abs(dx) <= 4, brightness=.95)
        heart_feature = self._features(heart_image)
        dark_feature = self._features(dark_image)
        grid = Grid(0, 0, 85)

        self.assertAlmostEqual(heart_feature.center_hue, dark_feature.center_hue, delta=.02)
        self.assertAlmostEqual(heart_feature.center_saturation, dark_feature.center_saturation, delta=.02)
        baseline = detect_board_pixels(*dark_image, grid)
        self.assertEqual(baseline[0][0].color, 6)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prototypes.json"
            self._learned_model(path, heart_feature, dark_feature)
            detected = OrbPrototypeModel(path).detect(*dark_image, grid, detect_board_pixels)

        self.assertEqual(detected[0][0].color, 5)

    def test_edge_shape_correction_can_reverse_a_dark_baseline(self):
        heart_image = self._pixels(lambda _dx, dy: abs(dy) <= 4)
        dark_image = self._pixels(lambda dx, _dy: abs(dx) <= 4)
        model = self._learned_model(None, self._features(heart_image), self._features(dark_image))
        dark_baseline = ((Orb("normal", 5),) * COLS,) * ROWS

        detected = model.detect(*heart_image, Grid(0, 0, 85), lambda *_args: dark_baseline)

        self.assertEqual(detected[0][0].color, 6)

    def test_flat_center_does_not_override_the_baseline(self):
        heart_image = self._pixels(lambda _dx, dy: abs(dy) <= 4)
        dark_image = self._pixels(lambda dx, _dy: abs(dx) <= 4)
        flat_image = self._pixels(lambda _dx, _dy: False)
        model = self._learned_model(None, self._features(heart_image), self._features(dark_image))
        grid = Grid(0, 0, 85)
        baseline = detect_board_pixels(*flat_image, grid)
        detected = model.detect(*flat_image, grid, detect_board_pixels)

        self.assertEqual(baseline[0][0].color, 6)
        self.assertEqual(detected[0][0], baseline[0][0])

    def test_descriptor_covers_more_than_the_previous_five_points(self):
        feature = self._features(self._pixels(lambda _dx, dy: abs(dy) <= 4))

        self.assertIsNotNone(feature.center_pattern)
        self.assertGreater(len(feature.center_pattern), 5)
        self.assertGreater(len(OrbPrototypeModel._feature(feature)), 18)

class NormalColorPixelTextureTests(unittest.TestCase):
    @staticmethod
    def _classify(center_rgb):
        width = height = cell = 85
        point = (42, 42)
        # Keep the annulus purple; only the centre icon changes hue.
        base_rgb = (204, 90, 214)
        pixels = bytearray(bytes((base_rgb[2], base_rgb[1], base_rgb[0], 255)) * (width * height))
        center_bgra = bytes((center_rgb[2], center_rgb[1], center_rgb[0], 255))
        for y in range(height):
            for x in range(width):
                if (x - point[0]) ** 2 + (y - point[1]) ** 2 < 15 ** 2:
                    pixels[(y * width + x) * 4:(y * width + x) * 4 + 4] = center_bgra
        return _normal_color(_cell_features(width, height, bytes(pixels), point, cell))

    def test_same_annulus_uses_heart_or_dark_center_pattern(self):
        self.assertEqual(self._classify((169, 92, 230)), 6)
        self.assertEqual(self._classify((230, 92, 141)), 5)

    def test_missing_center_signal_is_unknown(self):
        self.assertIsNone(self._classify((128, 128, 128)))




class PrototypeCompatibilityTests(unittest.TestCase):
    @staticmethod
    def _old_sample(human=False):
        return {
            "kind": "normal", "color": 6, "enhanced": False,
            "visual_class": "heart", "locked": False,
            "feature": [.82, .58, .84, 0, 0, 0, 0, 0, 0, 0],
            "human": human,
        }

    def test_old_json_feature_length_still_predicts(self):
        feature = NormalColorTextureTests._feature(.76)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prototypes.json"
            path.write_text(json.dumps({"samples": [self._old_sample()]}))

            prediction = OrbPrototypeModel(path).predict(feature)

        self.assertIsNotNone(prediction)
        self.assertEqual((prediction[0].kind, prediction[0].color), ("normal", 6))

    def test_low_weight_legacy_sample_cannot_replace_new_dark_baseline(self):
        feature = NormalColorTextureTests._feature(.76)
        model = OrbPrototypeModel()
        model.samples = [self._old_sample()]
        baseline = ((Orb("normal", 5),) * COLS,) * ROWS

        with patch("pad_router_gui._cell_features", return_value=feature):
            detected = model.detect(1, 1, b"", Grid(), lambda *_args: baseline)

        self.assertEqual(detected, baseline)


class BoardInspectionControllerTests(unittest.TestCase):
    def setUp(self):
        self.board = tuple(tuple(Orb("normal", (r + c) % 6 + 1) for c in range(COLS)) for r in range(ROWS))
        self.board = ((Orb("unknown", visual_class="unknown"),) + self.board[0][1:],) + self.board[1:]
        self.detected = []

        def detect(width, height, pixels, grid):
            self.detected.append((width, height, grid))
            return self.board

        self.controller = BoardInspectionController(detector=detect)
        handle, path = tempfile.mkstemp(suffix=".png")
        Path(path).write_bytes(png_bytes())
        self.addCleanup(lambda: Path(path).unlink(missing_ok=True))
        self.addCleanup(lambda: os.close(handle))
        self.path = path

    def test_png_loader_keeps_the_rgba_order_adb_screencap_uses(self):
        width, height, pixels = decode_png(self.path)
        self.assertEqual((width, height), (12, 10))
        self.assertEqual(pixels[:4], bytes((20, 40, 60, 255)))

    def test_screenshot_png_round_trips_without_swapping_channels(self):
        source = (2, 1, bytes((10, 20, 30, 255)) + bytes((200, 100, 50, 255)))
        encoded = tempfile.mkstemp(suffix=".png")[1]
        self.addCleanup(lambda: Path(encoded).unlink(missing_ok=True))
        Path(encoded).write_bytes(_png_from_screenshot(source))
        self.assertEqual(decode_png(encoded), source)

    def test_load_exposes_calibration_overlay_and_uncertainty(self):
        state = self.controller.load_png(self.path)
        self.assertEqual((state.width, state.height), (12, 10))
        self.assertEqual(state.uncertain_cells, ((0, 0),))
        self.assertFalse(state.confirmed)
        marker = state.overlay[0]
        self.assertEqual(marker["cell"], (0, 0))
        self.assertTrue(marker["uncertain"])
        self.assertEqual(len(self.detected), 2)
        self.assertIn("主動辨識第 2/2 次", state.status)

    def test_cell_correction_obtains_confirmed_board(self):
        self.controller.load_png(self.path)
        with self.assertRaises(ValueError):
            self.controller.confirm_board()
        state = self.controller.correct_cell(0, 0, "fire+")
        self.assertFalse(state.uncertain_cells)
        self.assertFalse(state.overlay[0]["uncertain"])
        confirmed = self.controller.confirm_board()
        self.assertEqual(confirmed[0][0], Orb("normal", 1, enhanced=True))
        self.assertTrue(self.controller.state.confirmed)

    def test_calibration_requires_in_bounds_board_and_resets_corrections(self):
        self.controller.load_png(self.path)
        self.controller.correct_cell(0, 0, 1)
        with self.assertRaises(ValueError):
            self.controller.set_calibration(BoardCalibration(left=7, top=0, cell=2))
        state = self.controller.set_calibration(BoardCalibration(left=0, top=0, cell=2))
        self.assertFalse(state.confirmed)
        self.assertEqual(state.uncertain_cells, ((0, 0),))
        self.assertEqual(self.detected[-1][2].left, 0)

    def test_capture_uses_replaceable_screenshot_adapter(self):
        source = (12, 10, bytes((60, 40, 20, 255)) * (12 * 10))
        controller = BoardInspectionController(detector=lambda *args: self.board, capture=lambda serial: source)
        state = controller.capture_device("test-device")
        self.assertEqual(state.source_name, "test-device")
        self.assertEqual((state.width, state.height), (12, 10))
        self.assertFalse(state.confirmed)

    def test_capture_snapshot_serializes_source_without_internal_pixels(self):
        source = (12, 10, bytes((60, 40, 20, 255)) * (12 * 10))
        controller = BoardInspectionController(
            detector=lambda *_args: self.board,
            capture=lambda _serial: source,
        )

        state = controller.capture_device("test-device", auto_search=False)
        snapshot = controller.snapshot()

        self.assertEqual(snapshot["source"]["name"], "test-device")
        self.assertEqual(snapshot["source"]["width"], 12)
        self.assertEqual(snapshot["source"]["height"], 10)
        self.assertTrue(snapshot["source"]["image"].startswith("data:image/png;base64,"))
        self.assertNotIn("pixels", snapshot)
        self.assertEqual(snapshot["status"], state.status)
        json.dumps(snapshot)
        encoded = snapshot["source"]["image"].split(",", 1)[1]
        with tempfile.NamedTemporaryFile(suffix=".png") as image_file:
            image_file.write(base64.b64decode(encoded))
            image_file.flush()
            self.assertEqual(decode_png(image_file.name)[0:2], (12, 10))
            self.assertEqual(decode_png(image_file.name)[2][:4], bytes((60, 40, 20, 255)))



    def test_clean_png_and_capture_with_preset_profile_auto_search(self):
        board = tuple(tuple(Orb("normal", (row + col) % 6 + 1) for col in range(COLS))
                      for row in range(ROWS))
        source = (12, 10, bytes((60, 40, 20, 255)) * (12 * 10))
        executions = []

        for name, load in (
            ("PNG", lambda controller: controller.load_png(self.path)),
            ("capture", lambda controller: controller.capture_device("test-device")),
        ):
            with self.subTest(name=name):
                controller = BoardInspectionController(
                    detector=lambda *_args: board, capture=lambda _serial: source,
                    executor=lambda *_args, **_kwargs: executions.append(True),
                )
                controller.set_rule_profile(RuleProfile("preset"))

                state = load(controller)

                self.assertIsNotNone(state.route_search)
                self.assertIs(state.route_search.candidate, state.route_evaluation)
                self.assertEqual(state.search_options, RouteSearchOptions())
                self.assertTrue(state.route_overlay)

        self.assertEqual(executions, [])

    def test_source_auto_search_skips_uncertain_board_or_missing_profile(self):
        clean = tuple(tuple(Orb("normal", 1) for _ in range(COLS)) for _ in range(ROWS))
        no_profile = BoardInspectionController(detector=lambda *_args: clean)
        no_profile.set_protected_cell((2, 3))
        uncertain = BoardInspectionController(detector=lambda *_args: self.board)
        uncertain.set_rule_profile(RuleProfile("preset"))

        no_profile_state = no_profile.load_png(self.path)
        self.assertIsNone(no_profile_state.route_search)
        self.assertEqual(no_profile_state.protected_cell, (2, 3))
        self.assertIsNone(uncertain.load_png(self.path).route_search)

    def test_protection_researches_rejects_manual_route_and_survives_new_source(self):
        board = tuple(tuple(Orb("normal", (row + col) % 6 + 1) for col in range(COLS))
                      for row in range(ROWS))
        source = (12, 10, bytes((60, 40, 20, 255)) * (12 * 10))
        controller = BoardInspectionController(
            detector=lambda *_args: board, capture=lambda _serial: source,
        )
        controller.set_rule_profile(RuleProfile("preset"))
        controller.capture_device("first")
        protected = controller.state.route_evaluation.route[0]

        protected_state = controller.set_protected_cell(protected)

        self.assertEqual(protected_state.protected_cell, protected)
        self.assertIsNotNone(protected_state.route_search)
        self.assertNotIn(protected, protected_state.route_evaluation.route)
        with self.assertRaisesRegex(ValueError, "保護"):
            controller.evaluate_manual_route((protected,))

        refreshed = controller.capture_device("second")
        self.assertEqual(refreshed.protected_cell, protected)
        self.assertNotIn(protected, refreshed.route_evaluation.route)

        cleared = controller.set_protected_cell(None)
        self.assertIsNone(cleared.protected_cell)
        self.assertIsNotNone(cleared.route_search)

    def test_protected_cell_rejects_invalid_coordinates(self):
        for cell in ((-1, 0), (ROWS, 0), (0, COLS), (0,), (True, 0), "0,0"):
            with self.subTest(cell=cell), self.assertRaises(ValueError):
                self.controller.set_protected_cell(cell)

    def test_prototype_model_persists_a_human_label_and_predicts_it(self):
        feature = CellFeatures(.66, .82, .86, 0, 0, 0, 0, 0, 0, 0)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prototypes.json"
            model = OrbPrototypeModel(path)
            self.assertTrue(model.learn(Orb("normal", 1), feature, human=True))
            self.assertTrue(path.exists())
            predicted = OrbPrototypeModel(path).predict(feature)

        self.assertIsNotNone(predicted)
        self.assertEqual(predicted[0], Orb("normal", 1))

    def test_default_model_uses_project_local_storage(self):
        expected = Path(__file__).resolve().parent / ".pad-router" / "orb-prototypes.json"

        self.assertEqual(OrbPrototypeModel.default().path, expected)


    def test_prototype_model_uses_atomic_replace_in_the_target_directory(self):
        feature = CellFeatures(.66, .82, .86, 0, 0, 0, 0, 0, 0, 0)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prototypes.json"
            model = OrbPrototypeModel(path)
            replacements = []
            original_replace = type(path).replace

            def replace(source, target):
                replacements.append((source, target))
                return original_replace(source, target)

            with patch.object(type(path), "replace", replace):
                model.learn(Orb("normal", 1), feature, human=True)

            self.assertEqual(len(replacements), 1)
            self.assertEqual(replacements[0][1], path)
            self.assertEqual(replacements[0][0].parent, path.parent)
            self.assertFalse(replacements[0][0].exists())
            self.assertIsNotNone(OrbPrototypeModel(path).predict(feature))

    def test_prototype_model_replace_failure_preserves_file_and_memory(self):
        feature = CellFeatures(.66, .82, .86, 0, 0, 0, 0, 0, 0, 0)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prototypes.json"
            model = OrbPrototypeModel(path)
            model.learn(Orb("normal", 1), feature, human=True)
            original_text = path.read_text()
            original_samples = [dict(sample) for sample in model.samples]

            with patch.object(Path, "replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    model.learn(Orb("normal", 2), feature, human=True)

            self.assertEqual(path.read_text(), original_text)
            self.assertEqual(model.samples, original_samples)
            self.assertEqual(tuple(path.parent.glob(f".{path.name}.*.tmp")), ())

    def test_relabeling_same_human_cell_replaces_old_prediction(self):
        feature = CellFeatures(.66, .82, .86, 0, 0, 0, 0, 0, 0, 0)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prototypes.json"
            model = OrbPrototypeModel(path)
            model.learn(Orb("normal", 1), feature, human=True, cell=(0, 0))
            model.learn(Orb("normal", 2), feature, human=True, cell=(0, 0))
            prediction = model.predict(feature)

        self.assertEqual(len(model.samples), 1)
        self.assertIsNotNone(prediction)
        self.assertEqual(prediction[0], Orb("normal", 2))

    def test_clean_board_is_usable_without_a_confirmation_click(self):
        board = tuple(tuple(Orb("normal", (r + c) % 6 + 1) for c in range(COLS)) for r in range(ROWS))
        controller = BoardInspectionController(detector=lambda *_args: board)
        state = controller.load_png(self.path)
        self.assertTrue(state.confirmed)
        self.assertEqual(state.confirmed_board, board)

    def test_next_screenshot_implicitly_learns_the_previous_board(self):
        board = tuple(tuple(Orb("normal", 1) for _ in range(COLS)) for _ in range(ROWS))
        unknown = tuple(tuple(Orb("unknown", visual_class="unknown") for _ in range(COLS)) for _ in range(ROWS))
        source = (144, 120, bytes((60, 40, 20, 255)) * (144 * 120))
        with tempfile.TemporaryDirectory() as directory:
            model = OrbPrototypeModel(Path(directory) / "prototypes.json")
            results = iter((board, unknown))
            controller = BoardInspectionController(detector=lambda *_args: next(results), model=model,
                                                  capture=lambda _serial: source)
            controller.set_learning_enabled(True)
            controller.capture_device("one")
            state = controller.capture_device("two")

        self.assertEqual(state.learning_status, "上一張已學習（30 格隱式資料）")
        self.assertEqual(len(model.samples), 30)
        self.assertEqual(state.board[0][0], Orb("normal", 1))

    def test_human_annotation_updates_the_next_recognition(self):
        unknown = tuple(tuple(Orb("unknown", visual_class="unknown") for _ in range(COLS)) for _ in range(ROWS))
        source = (144, 120, bytes((60, 40, 20, 255)) * (144 * 120))
        with tempfile.TemporaryDirectory() as directory:
            model = OrbPrototypeModel(Path(directory) / "prototypes.json")
            controller = BoardInspectionController(detector=lambda *_args: unknown, model=model,
                                                  capture=lambda _serial: source)
            controller.set_learning_enabled(True)
            controller.capture_device("one")
            controller.correct_cell(0, 0, "fire")
            state = controller.capture_device("two")

        self.assertEqual(state.board[0][0], Orb("normal", 1))
        self.assertTrue(state.confirmed)

    def test_learning_defaults_off_and_can_resume_all_model_writes(self):
        board = tuple(tuple(Orb("normal", 1) for _ in range(COLS)) for _ in range(ROWS))
        source = (144, 120, bytes((60, 40, 20, 255)) * (144 * 120))
        with tempfile.TemporaryDirectory() as directory:
            model = OrbPrototypeModel(Path(directory) / "prototypes.json")
            controller = BoardInspectionController(
                detector=lambda *_args: board, model=model, capture=lambda _serial: source,
            )
            self.assertFalse(controller.learning_enabled)
            controller.capture_device("one")
            controller.accept_current_board()
            controller.correct_cell(0, 0, "fire")
            controller.capture_device("two")
            self.assertEqual(model.samples, [])

            controller.set_learning_enabled(True)
            controller.correct_cell(0, 0, "water")
            self.assertEqual(len(model.samples), 1)
            controller.accept_current_board()
            self.assertEqual(len(model.samples), 31)

    def test_bridge_disables_learning_during_an_inflight_implicit_write(self):
        board = tuple(tuple(Orb("normal", 1) for _ in range(COLS)) for _ in range(ROWS))
        source = (144, 120, bytes((60, 40, 20, 255)) * (144 * 120))
        with tempfile.TemporaryDirectory() as directory:
            model = OrbPrototypeModel(Path(directory) / "prototypes.json")
            controller = BoardInspectionController(
                detector=lambda *_args: board, model=model, capture=lambda _serial: source,
            )
            controller.set_learning_enabled(True)
            controller.capture_device("one")
            learn = model.learn
            write_started = threading.Event()
            release_write = threading.Event()

            def block_first_write(*args, **kwargs):
                if not write_started.is_set():
                    write_started.set()
                    release_write.wait(1)
                return learn(*args, **kwargs)

            bridge = BoardInspectionBridge(controller=controller)
            self.addCleanup(bridge.close)
            worker = threading.Thread(target=controller.accept_current_board)
            updated = {}
            disable_done = threading.Event()

            def disable() -> None:
                updated["snapshot"] = bridge.command({"action": "set_learning_enabled", "enabled": False})
                disable_done.set()

            with patch.object(model, "learn", side_effect=block_first_write):
                worker.start()
                self.assertTrue(write_started.wait(1))
                toggle = threading.Thread(target=disable)
                toggle.start()
                try:
                    self.assertFalse(disable_done.wait(.1))
                finally:
                    release_write.set()
                    worker.join(1)
                    toggle.join(1)

        self.assertEqual(len(model.samples), 1)
        self.assertFalse(worker.is_alive())
        self.assertTrue(disable_done.is_set())
        self.assertFalse(updated["snapshot"]["learning_enabled"])
        self.assertFalse(controller.learning_enabled)

    def test_overlapping_enable_waits_for_pending_disable(self):
        board = tuple(tuple(Orb("normal", 1) for _ in range(COLS)) for _ in range(ROWS))
        source = (144, 120, bytes((60, 40, 20, 255)) * (144 * 120))
        with tempfile.TemporaryDirectory() as directory:
            model = OrbPrototypeModel(Path(directory) / "prototypes.json")
            controller = BoardInspectionController(
                detector=lambda *_args: board, model=model, capture=lambda _serial: source,
            )
            controller.set_learning_enabled(True)
            controller.capture_device("one")
            learn = model.learn
            write_started = threading.Event()
            release_write = threading.Event()
            disable_marked = threading.Event()
            continue_disable = threading.Event()
            disable_done = threading.Event()
            enable_started = threading.Event()
            enable_done = threading.Event()
            completion_order = []
            mark_disable = controller._learning_disable_requested.set

            def block_first_write(*args, **kwargs):
                if not write_started.is_set():
                    write_started.set()
                    release_write.wait(1)
                return learn(*args, **kwargs)

            def pause_after_marking_disable() -> None:
                mark_disable()
                disable_marked.set()
                continue_disable.wait(1)

            def disable() -> None:
                controller.set_learning_enabled(False)
                completion_order.append("off")
                disable_done.set()

            def enable() -> None:
                enable_started.set()
                controller.set_learning_enabled(True)
                completion_order.append("on")
                enable_done.set()

            worker = threading.Thread(target=controller.accept_current_board)
            disable_toggle = threading.Thread(target=disable)
            enable_toggle = threading.Thread(target=enable)
            with (patch.object(model, "learn", side_effect=block_first_write),
                  patch.object(controller._learning_disable_requested, "set",
                               side_effect=pause_after_marking_disable)):
                worker.start()
                self.assertTrue(write_started.wait(1))
                disable_toggle.start()
                self.assertTrue(disable_marked.wait(1))
                enable_toggle.start()
                self.assertTrue(enable_started.wait(1))
                release_write.set()
                try:
                    self.assertFalse(enable_done.wait(.1))
                finally:
                    continue_disable.set()
                    worker.join(1)
                    disable_toggle.join(1)
                    enable_toggle.join(1)

        self.assertFalse(worker.is_alive())
        self.assertFalse(disable_toggle.is_alive())
        self.assertFalse(enable_toggle.is_alive())
        self.assertTrue(disable_done.is_set())
        self.assertTrue(enable_done.is_set())
        self.assertEqual(completion_order, ["off", "on"])
        self.assertEqual(len(model.samples), 1)
        self.assertTrue(controller.learning_enabled)

    def test_learning_command_updates_bridge_snapshot(self):
        bridge = BoardInspectionBridge()
        self.addCleanup(bridge.close)

        snapshot = bridge.snapshot()
        self.assertFalse(snapshot["learning_enabled"])
        updated = bridge.command({"action": "set_learning_enabled", "enabled": True})

        self.assertTrue(updated["learning_enabled"])

    def test_rule_profile_file_flow_and_manual_route(self):
        self.controller.load_png(self.path)
        self.controller.correct_cell(0, 0, 1)
        self.controller.confirm_board()
        profile = RuleProfile("manual")
        self.controller.set_rule_profile(profile)

        with tempfile.TemporaryDirectory() as directory:
            profile_path = Path(directory) / "profile.json"
            self.controller.save_rule_profile(profile_path)
            state = self.controller.load_rule_profile(profile_path)

        self.assertEqual(state.rule_profile, profile)
        result = self.controller.evaluate_manual_route(((0, 0), (0, 1)))
        self.assertEqual(result.route, ((0, 0), (0, 1)))
        self.assertTrue(result.execution_eligible)

    def test_search_exposes_candidate_overlay_and_invalidates_it_on_profile_change(self):
        self.controller.load_png(self.path)
        self.controller.correct_cell(0, 0, 1)
        self.controller.confirm_board()
        self.controller.set_rule_profile(RuleProfile("search"))

        result = self.controller.search_qualifying_route(
            RouteSearchOptions(attempts=1, seed=4, min_steps=0, max_steps=0)
        )

        self.assertIs(result, self.controller.state.route_search)
        self.assertIs(result.candidate, self.controller.state.route_evaluation)
        self.assertTrue(result.candidate.execution_eligible)
        self.assertEqual(len(self.controller.state.route_overlay), 1)
        marker = self.controller.state.route_overlay[0]
        self.assertEqual(marker["cell"], result.candidate.route[0])
        self.assertIn("x", marker)
        self.assertIn("y", marker)
        self.controller.search_qualifying_route(
            RouteSearchOptions(attempts=1, seed=5, min_steps=0, max_steps=0)
        )

        self.controller.set_rule_profile(RuleProfile("changed"))
        self.assertIsNone(self.controller.state.route_search)
        self.assertIsNone(self.controller.state.route_evaluation)
        self.assertEqual(self.controller.state.route_overlay, ())

    def test_search_tracks_options_and_explicit_invalidation_clears_candidate(self):
        self.controller.load_png(self.path)
        self.controller.correct_cell(0, 0, 1)
        self.controller.confirm_board()
        self.controller.set_rule_profile(RuleProfile("search"))
        options = RouteSearchOptions(attempts=1, seed=4, min_steps=0, max_steps=0)

        self.controller.search_qualifying_route(options)
        self.assertEqual(self.controller.state.search_options, options)
        state = self.controller.invalidate_route("Search settings changed; Route invalidated")
        self.assertEqual(state.status, "Search settings changed; Route invalidated")
        self.assertIsNone(state.route_search)
        self.assertIsNone(state.route_evaluation)
        self.assertEqual(state.route_overlay, ())

    def test_execution_runs_eligible_route_and_reports_post_gesture_board(self):
        board = tuple(tuple(Orb("normal", (r + c) % 6 + 1) for c in range(COLS)) for r in range(ROWS))
        source = (12, 10, bytes((60, 40, 20, 255)) * (12 * 10))
        calls = []

        def execute(serial, path, grid, delay, hold_delay, lift_threshold, expected_board,
                    max_corrections, on_verification, screen_size=None):
            calls.append((serial, path, expected_board))
            actual = expected_board_after_path(expected_board, path)
            on_verification(PlayVerification(actual, actual, 0, True, "verified"))
            return True

        controller = BoardInspectionController(
            detector=lambda *args: board, capture=lambda serial: source, executor=execute
        )
        controller.capture_device("test-device")
        controller.set_rule_profile(RuleProfile("safe"))
        controller.confirm_board()
        controller.evaluate_manual_route(((0, 0), (0, 1)))
        post_route = expected_board_after_path(board, ((0, 0), (0, 1)))

        self.assertTrue(controller.execute_route("test-device"))
        self.assertEqual(calls[0][0], "test-device")
        self.assertEqual(calls[0][1], ((0, 0), (0, 1)))
        self.assertEqual(calls[0][2], board)
        self.assertEqual(controller.state.verification.expected_board, post_route)
        self.assertEqual(controller.state.verification.mismatches, 0)
        self.assertIn("驗證成功", controller.state.status)
        with self.assertRaisesRegex(ValueError, "符合條件的路徑"):
            controller.execute_route("test-device")

    def test_execution_exposes_actionable_post_gesture_mismatch(self):
        board = tuple(tuple(Orb("normal", (r + c) % 6 + 1) for c in range(COLS)) for r in range(ROWS))
        actual = tuple(tuple(Orb("normal", (r + c + 1) % 6 + 1) for c in range(COLS)) for r in range(ROWS))
        source = (12, 10, bytes((60, 40, 20, 255)) * (12 * 10))

        def execute(*args, on_verification, screen_size=None):
            expected = args[6]
            on_verification(PlayVerification(expected, actual, 2, False, "post_gesture_mismatch"))
            return False

        controller = BoardInspectionController(
            detector=lambda *args: board, capture=lambda serial: source, executor=execute
        )
        controller.capture_device("test-device")
        controller.set_rule_profile(RuleProfile("safe"))
        controller.confirm_board()
        controller.evaluate_manual_route(((0, 0),))

        self.assertFalse(controller.execute_route("test-device"))
        self.assertEqual(controller.state.verification.detected_board, actual)
        self.assertEqual(controller.state.verification.mismatches, 2)
        self.assertIn("2 格不符", controller.state.status)
        self.assertIn("擷取新盤面", controller.state.status)

    def test_non_qualifying_candidate_cannot_execute(self):
        board = tuple(tuple(Orb("normal", (r + c) % 6 + 1) for c in range(COLS)) for r in range(ROWS))
        source = (12, 10, bytes((60, 40, 20, 255)) * (12 * 10))
        calls = []
        profile = RuleProfile("blocked", condition_groups=(ConditionGroup.all_of((
            LeaderCondition.combo_minimum(99),
        )),))
        controller = BoardInspectionController(
            detector=lambda *args: board, capture=lambda serial: source,
            executor=lambda *args, **kwargs: calls.append(args),
        )
        controller.capture_device("test-device")
        controller.set_rule_profile(profile)
        controller.confirm_board()
        result = controller.evaluate_manual_route(((0, 0),))

        self.assertFalse(result.qualifying)
        with self.assertRaisesRegex(ValueError, "符合條件的路徑"):
            controller.execute_route("test-device")
        self.assertEqual(calls, [])


class BoardInspectionBridgeTests(unittest.TestCase):
    def test_identical_source_reuses_cached_png_encoding(self):
        source = (12, 10, bytes((60, 40, 20, 255)) * (12 * 10))
        _png_from_screenshot.cache_clear()
        self.addCleanup(_png_from_screenshot.cache_clear)

        first = _png_from_screenshot(source)
        second = _png_from_screenshot((source[0], source[1], source[2]))

        self.assertIs(first, second)
        self.assertEqual(_png_from_screenshot.cache_info().hits, 1)

    def test_1080x2400_capture_defers_work_and_coalesces_updates(self):
        board = tuple(tuple(Orb("normal", 1) for _ in range(COLS)) for _ in range(ROWS))
        source = (1080, 2400, bytes((60, 40, 20, 255)) * (1080 * 2400))
        captures = []

        def capture(serial):
            captures.append(serial)
            return source

        class DeferredExecutor:
            def __init__(self):
                self.pending = []

            def submit(self, function, *args):
                self.pending.append((function, args))
                return object()

            def run_next(self):
                function, args = self.pending.pop(0)
                function(*args)

            def shutdown(self, **_kwargs):
                self.pending.clear()

        executor = DeferredExecutor()
        bridge = BoardInspectionBridge(
            controller=BoardInspectionController(detector=lambda *_args: board, capture=capture),
            device_lister=lambda: ("test-device",),
            executor=executor,
        )
        self.addCleanup(bridge.close)
        bridge.drain_events()

        refresh = bridge.command({"action": "refresh_devices"})
        self.assertTrue(refresh["accepted"])
        self.assertEqual(captures, [])
        executor.run_next()

        selected = bridge.command({"action": "select_device", "serial": "test-device"})
        self.assertEqual(selected["selected_device"], "test-device")
        acknowledgement = bridge.command({"action": "capture_screen"})
        self.assertTrue(acknowledgement["accepted"])
        self.assertIsNone(acknowledgement["snapshot"]["source"])
        self.assertEqual(captures, [])

        executor.run_next()
        self.assertEqual(captures, ["test-device"])
        events = bridge.drain_events()
        self.assertEqual(len(events), 1)
        snapshot = events[0]["snapshot"]
        self.assertEqual(snapshot["source"]["width"], 1080)
        self.assertEqual(snapshot["source"]["height"], 2400)
        self.assertFalse(snapshot["busy"])
        self.assertGreaterEqual(len(snapshot["console"]), 5)
        json.dumps(events)

    def test_board_review_serializes_markers_and_advances_unknown_correction(self):
        unknown = Orb("unknown", visual_class="unknown")
        first_row = (
            unknown,
            unknown,
            Orb("normal", 1, enhanced=True, locked=True),
            Orb("normal", 2),
            Orb("normal", 3),
            Orb("normal", 4),
        )
        board = (first_row,) + tuple(
            tuple(Orb("normal", 5) for _ in range(COLS))
            for _ in range(ROWS - 1)
        )
        source = (12, 10, bytes((60, 40, 20, 255)) * (12 * 10))

        class ImmediateExecutor:
            def submit(self, function, *args):
                function(*args)
                return object()

            def shutdown(self, **_kwargs):
                pass

        controller = BoardInspectionController(
            detector=lambda *_args: board,
            capture=lambda _serial: source,
        )
        controller.capture_device("test-device", auto_search=False)
        controller.set_protected_cell((0, 2), recompute=False)
        bridge = BoardInspectionBridge(controller=controller, executor=ImmediateExecutor())
        self.addCleanup(bridge.close)

        snapshot = bridge.snapshot()
        source_image = snapshot["source"]["image"]
        cells = {tuple(cell["cell"]): cell for cell in snapshot["board"]}
        self.assertEqual(snapshot["unknown_count"], 2)
        self.assertEqual(snapshot["selected_cell"], [0, 0])
        self.assertTrue(cells[(0, 0)]["unknown"])
        self.assertTrue(cells[(0, 2)]["protected"])
        self.assertTrue(cells[(0, 2)]["enhanced"])
        self.assertTrue(cells[(0, 2)]["locked"])
        with self.assertRaises(ValueError):
            controller.confirm_board()

        selected = bridge.command({"action": "select_cell", "cell": [0, 2]})
        self.assertEqual(selected["selected_cell"], [0, 2])
        selected = bridge.command({"action": "select_cell", "row": 0, "col": 0})
        self.assertEqual(selected["selected_cell"], [0, 0])

        corrected = bridge.command({"action": "correct_cell", "value": "fire*+"})
        self.assertEqual(corrected["snapshot"]["unknown_count"], 1)
        self.assertEqual(corrected["snapshot"]["selected_cell"], [0, 1])
        corrected_cell = {
            tuple(cell["cell"]): cell for cell in corrected["snapshot"]["board"]
        }[(0, 0)]
        self.assertEqual(corrected_cell["label"], "火+L")
        self.assertTrue(corrected_cell["enhanced"])
        self.assertTrue(corrected_cell["locked"])

        completed = bridge.command({"action": "correct_cell", "value": "water"})
        self.assertEqual(completed["snapshot"]["unknown_count"], 0)
        self.assertIsNone(completed["snapshot"]["selected_cell"])
        self.assertEqual(completed["snapshot"]["source"]["image"], source_image)
        json.dumps(completed["snapshot"])
        controller.confirm_board()

    def test_protected_cell_intent_follows_selection_and_keeps_event_serializable(self):
        board = tuple(tuple(Orb("normal", 1) for _ in range(COLS)) for _ in range(ROWS))
        source = (12, 10, bytes((60, 40, 20, 255)) * (12 * 10))

        class ImmediateExecutor:
            def submit(self, function, *args):
                function(*args)
                return object()

            def shutdown(self, **_kwargs):
                pass

        controller = BoardInspectionController(
            detector=lambda *_args: board,
            capture=lambda _serial: source,
        )
        controller.capture_device("test-device", auto_search=False)
        bridge = BoardInspectionBridge(controller=controller, executor=ImmediateExecutor())
        self.addCleanup(bridge.close)
        bridge.drain_events()

        selected = bridge.command({"action": "select_cell", "row": 2, "col": 3})
        self.assertEqual(selected["selected_cell"], [2, 3])
        protected = bridge.command({"action": "set_protected_cell"})
        self.assertEqual(protected["snapshot"]["protected_cell"], [2, 3])
        protected_cell = {
            tuple(cell["cell"]): cell for cell in protected["snapshot"]["board"]
        }[(2, 3)]
        self.assertTrue(protected_cell["protected"])
        self.assertIn("保護", protected["snapshot"]["status"])

        cleared = bridge.command({"action": "set_protected_cell", "cell": None})
        self.assertIsNone(cleared["snapshot"]["protected_cell"])
        events = bridge.drain_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["phase"], "review")
        json.dumps(events)
    def test_rule_intent_updates_active_profile_and_invalidates_candidate(self):
        board = tuple(tuple(Orb("normal", 1) for _ in range(COLS)) for _ in range(ROWS))
        source = (12, 10, bytes((60, 40, 20, 255)) * (12 * 10))

        class ImmediateExecutor:
            def submit(self, function, *args):
                function(*args)
                return object()

            def shutdown(self, **_kwargs):
                pass

        controller = BoardInspectionController(
            detector=lambda *_args: board,
            capture=lambda _serial: source,
        )
        controller.capture_device("test-device", auto_search=False)
        controller.set_rule_profile(RuleProfile("old"))
        controller.evaluate_manual_route(((0, 0),))
        bridge = BoardInspectionBridge(controller=controller, executor=ImmediateExecutor())
        self.addCleanup(bridge.close)

        with patch("pad_router_gui._screenshot_image") as encode:
            updated = bridge.command({
                "action": "set_rule_profile",
                "conditions": [{"label": "至少 3 Combo", "color": "不指定"}],
                "operator": "全部符合",
                "hazard_policy": "避免危害珠",
                "external": "無",
            })
            encode.assert_not_called()
        snapshot = updated["snapshot"]
        self.assertEqual(snapshot["rule_profile"]["name"], "至少 3 Combo")
        self.assertIsNone(snapshot["route_result"])
        self.assertIsNone(snapshot["search"]["result"])
        self.assertFalse(snapshot["busy"])

    def test_search_can_be_cancelled_without_publishing_result(self):
        board = tuple(tuple(Orb("normal", 1) for _ in range(COLS)) for _ in range(ROWS))
        source = (12, 10, bytes((60, 40, 20, 255)) * (12 * 10))

        class DeferredExecutor:
            def __init__(self):
                self.pending = []

            def submit(self, function, *args):
                self.pending.append((function, args))
                return object()

            def run_next(self):
                function, args = self.pending.pop(0)
                function(*args)

            def shutdown(self, **_kwargs):
                self.pending.clear()

        executor = DeferredExecutor()
        controller = BoardInspectionController(
            detector=lambda *_args: board,
            capture=lambda _serial: source,
        )
        controller.capture_device("test-device", auto_search=False)
        controller.set_rule_profile(RuleProfile("search"))
        bridge = BoardInspectionBridge(controller=controller, executor=executor)
        self.addCleanup(bridge.close)
        bridge.drain_events()
        search_calls = []

        def fake_search(*_args, on_progress=None, cancel=None, **_kwargs):
            search_calls.append(True)
            on_progress("attempts", 0, 1)
            on_progress("attempts", 1, 1)
            return RouteSearchResult(None, None, 1, 0, cancelled=bool(cancel and cancel()))

        with patch("pad_router_gui.search_qualifying_route", side_effect=fake_search):
            started = bridge.command({
                "action": "search_route",
                "attempts": 1,
                "max_steps": 0,
                "seed": 0,
                "cascade": True,
            })
            self.assertTrue(started["accepted"])
            self.assertEqual(started["snapshot"]["search"]["status"], "running")
            self.assertEqual(search_calls, [])
            cancelling = bridge.command({"action": "cancel_search"})
            self.assertEqual(cancelling["search"]["status"], "cancelling")
            executor.run_next()
        self.assertEqual(search_calls, [True])

        snapshot = bridge.snapshot()
        self.assertEqual(snapshot["search"]["status"], "cancelled")
        self.assertIsNone(snapshot["search"]["result"])
        self.assertEqual(len(snapshot["console"]), 4)
        self.assertFalse(snapshot["busy"])

    def test_search_options_reject_string_cascade_at_json_boundary(self):
        bridge = BoardInspectionBridge(device_lister=lambda: ())
        self.addCleanup(bridge.close)

        with self.assertRaisesRegex(ValueError, "boolean"):
            bridge.command({"action": "search_route", "cascade": "false"})
    def test_unknown_board_search_exposes_only_non_executable_preview(self):
        unknown = Orb("unknown", visual_class="unknown")
        board = ((unknown,) + tuple(Orb("normal", 1) for _ in range(COLS - 1)),) + tuple(
            tuple(Orb("normal", 1) for _ in range(COLS))
            for _ in range(ROWS - 1)
        )
        source = (12, 10, bytes((60, 40, 20, 255)) * (12 * 10))

        class ImmediateExecutor:
            def submit(self, function, *args):
                function(*args)
                return object()

            def shutdown(self, **_kwargs):
                pass

        bridge = BoardInspectionBridge(
            controller=BoardInspectionController(
                detector=lambda *_args: board,
                capture=lambda _serial: source,
            ),
            executor=ImmediateExecutor(),
        )
        self.addCleanup(bridge.close)
        bridge.command({"action": "capture_screen", "serial": "test-device"})

        with patch("pad_router_gui._screenshot_image") as encode:
            result = bridge.command({
                "action": "search_route",
                "attempts": 1,
                "min_steps": 0,
                "max_steps": 0,
                "seed": 0,
                "cascade": True,
            })
            encode.assert_not_called()
        snapshot = result["snapshot"]
        self.assertEqual(snapshot["search"]["status"], "complete")
        self.assertIsNotNone(snapshot["search"]["result"])
        candidate = (snapshot["search"]["result"]["diagnostic_candidate"]
                     or snapshot["search"]["result"]["qualifying_candidate"])
        self.assertIsNotNone(candidate)
        self.assertFalse(candidate["execution_eligible"])
        self.assertFalse(candidate["confirmed"])
        self.assertFalse(snapshot["route_result"]["execution_eligible"])
        with self.assertRaisesRegex(ValueError, "確認且符合"):
            bridge.command({"action": "execute_route", "serial": "test-device"})
        json.dumps(snapshot)

    def test_stale_search_result_cannot_overwrite_new_rule_generation(self):
        board = tuple(tuple(Orb("normal", 1) for _ in range(COLS)) for _ in range(ROWS))
        source = (12, 10, bytes((60, 40, 20, 255)) * (12 * 10))

        class DeferredExecutor:
            def __init__(self):
                self.pending = []

            def submit(self, function, *args):
                self.pending.append((function, args))
                return object()

            def run_next(self):
                function, args = self.pending.pop(0)
                function(*args)

            def shutdown(self, **_kwargs):
                self.pending.clear()

        executor = DeferredExecutor()
        controller = BoardInspectionController(
            detector=lambda *_args: board,
            capture=lambda _serial: source,
        )
        controller.capture_device("test-device", auto_search=False)
        controller.set_rule_profile(RuleProfile("old"))
        bridge = BoardInspectionBridge(controller=controller, executor=executor)
        self.addCleanup(bridge.close)
        bridge.drain_events()
        old_result = search_qualifying_route(
            board, RuleProfile("old"),
            RouteSearchOptions(attempts=1, min_steps=0, max_steps=0),
            confirmed=True,
        )
        self.assertIsNotNone(old_result.candidate)

        with patch("pad_router_gui.search_qualifying_route", return_value=old_result):
            started = bridge.command({
                "action": "search_route",
                "attempts": 1,
                "max_steps": 0,
                "seed": 0,
                "cascade": True,
            })
            self.assertTrue(started["accepted"])
            changed = bridge.command({
                "action": "set_rule_profile",
                "conditions": [{"label": "至少 3 Combo", "color": "不指定"}],
                "operator": "全部符合",
                "hazard_policy": "避免危害珠",
                "external": "無",
            })
            self.assertTrue(changed["accepted"])
            executor.run_next()
            stale = bridge.snapshot()
            self.assertEqual(stale["search"]["status"], "stale")
            self.assertIsNone(stale["route_result"])
            executor.run_next()

        current = bridge.snapshot()
        self.assertEqual(current["rule_profile"]["name"], "至少 3 Combo")
        self.assertIsNone(current["route_result"])
    def test_stale_search_result_cannot_overwrite_new_board_generation(self):
        board = tuple(tuple(Orb("normal", 1) for _ in range(COLS)) for _ in range(ROWS))
        source = (12, 10, bytes((60, 40, 20, 255)) * (12 * 10))

        class DeferredExecutor:
            def __init__(self):
                self.pending = []

            def submit(self, function, *args):
                self.pending.append((function, args))
                return object()

            def run_next(self):
                function, args = self.pending.pop(0)
                function(*args)

            def shutdown(self, **_kwargs):
                self.pending.clear()

        executor = DeferredExecutor()
        controller = BoardInspectionController(
            detector=lambda *_args: board,
            capture=lambda _serial: source,
        )
        controller.capture_device("first-device", auto_search=False)
        controller.set_rule_profile(RuleProfile("old"))
        bridge = BoardInspectionBridge(controller=controller, executor=executor)
        self.addCleanup(bridge.close)
        bridge.drain_events()
        old_result = search_qualifying_route(
            board, RuleProfile("old"),
            RouteSearchOptions(attempts=1, min_steps=0, max_steps=0),
            confirmed=True,
        )
        self.assertIsNotNone(old_result.candidate)

        with patch("pad_router_gui.search_qualifying_route", return_value=old_result):
            bridge.command({
                "action": "search_route",
                "attempts": 1,
                "max_steps": 0,
                "seed": 0,
                "cascade": True,
            })
            bridge.command({"action": "capture_screen", "serial": "second-device"})
            executor.run_next()
            stale = bridge.snapshot()
            self.assertEqual(stale["search"]["status"], "stale")
            self.assertIsNone(stale["route_result"])
            executor.run_next()

        current = bridge.snapshot()
        self.assertEqual(current["source"]["name"], "second-device")
        self.assertIsNone(current["route_result"])
    def test_search_does_not_block_rule_action_or_event_updates(self):
        board = tuple(tuple(Orb("normal", 1) for _ in range(COLS)) for _ in range(ROWS))
        source = (12, 10, bytes((60, 40, 20, 255)) * (12 * 10))

        class DeferredExecutor:
            def __init__(self):
                self.pending = []

            def submit(self, function, *args):
                self.pending.append((function, args))
                return object()

            def run_next(self):
                function, args = self.pending.pop(0)
                function(*args)

            def shutdown(self, **_kwargs):
                self.pending.clear()

        interaction_executor = DeferredExecutor()
        search_executor = DeferredExecutor()
        controller = BoardInspectionController(
            detector=lambda *_args: board,
            capture=lambda _serial: source,
        )
        controller.capture_device("test-device", auto_search=False)
        bridge = BoardInspectionBridge(
            controller=controller,
            executor=interaction_executor,
            search_executor=search_executor,
        )
        self.addCleanup(bridge.close)
        bridge.drain_events()

        with patch(
            "pad_router_gui.search_qualifying_route",
            return_value=RouteSearchResult(None, None, 1, 0),
        ):
            started = bridge.command({
                "action": "search_route",
                "attempts": 1,
                "max_steps": 0,
                "seed": 0,
                "cascade": True,
            })
            self.assertTrue(started["accepted"])
            self.assertFalse(started["snapshot"]["busy"])
            self.assertTrue(started["snapshot"]["search_busy"])
            self.assertEqual(len(search_executor.pending), 1)

            changed = bridge.command({
                "action": "set_rule_profile",
                "conditions": [{"label": "至少 3 Combo", "color": "不指定"}],
                "operator": "全部符合",
                "hazard_policy": "避免危害珠",
                "external": "無",
            })
            self.assertTrue(changed["accepted"])
            self.assertEqual(len(interaction_executor.pending), 1)
            interaction_executor.run_next()

            current = bridge.snapshot()
            self.assertEqual(current["rule_profile"]["name"], "至少 3 Combo")
            self.assertFalse(current["busy"])
            self.assertTrue(current["search_busy"])
            events = bridge.drain_events()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["phase"], "rules")
            self.assertIn("規則", events[0]["message"])

            search_executor.run_next()

        self.assertEqual(bridge.snapshot()["search"]["status"], "stale")


    def test_web_execution_runs_eligible_route_without_approval_and_reports_verification(self):
        board = tuple(tuple(Orb("normal", (row + col) % 6 + 1) for col in range(COLS))
                      for row in range(ROWS))
        source = (144, 120, bytes((60, 40, 20, 255)) * (144 * 120))
        model = OrbPrototypeModel()
        execution_calls = []

        def execute(serial, path, grid, delay, hold_delay, lift_threshold, expected_board,
                    max_corrections, on_verification, screen_size=None):
            execution_calls.append((serial, len(model.samples), delay))
            on_verification(PlayVerification(expected_board, expected_board, 0, True, "verified"))
            return True

        class ImmediateExecutor:
            def submit(self, function, *args):
                function(*args)
                return object()

            def shutdown(self, **_kwargs):
                pass

        controller = BoardInspectionController(
            detector=lambda *_args: board,
            capture=lambda _serial: source,
            executor=execute,
            model=model,
        )
        controller.set_learning_enabled(True)
        controller.capture_device("test-device", auto_search=False)
        controller.set_rule_profile(RuleProfile("safe"))
        controller.confirm_board()
        controller.evaluate_manual_route(((0, 0),))
        bridge = BoardInspectionBridge(controller=controller, executor=ImmediateExecutor())
        self.addCleanup(bridge.close)

        executed = bridge.command({"action": "execute_route", "serial": "test-device", "delay": 0.08})
        self.assertTrue(executed["accepted"])
        self.assertEqual(execution_calls, [("test-device", 30, 0.08)])
        self.assertEqual(executed["snapshot"]["execution"]["verification"]["status"], "verified")
        json.dumps(executed["snapshot"])
    def test_learning_failure_stops_before_adb_gesture(self):
        board = tuple(tuple(Orb("normal", (row + col) % 6 + 1) for col in range(COLS))
                      for row in range(ROWS))
        source = (144, 120, bytes((60, 40, 20, 255)) * (144 * 120))
        model = OrbPrototypeModel()
        gestures = []

        def execute(*_args, **_kwargs):
            gestures.append(True)
            return True

        class ImmediateExecutor:
            def submit(self, function, *args):
                function(*args)
                return object()

            def shutdown(self, **_kwargs):
                pass

        controller = BoardInspectionController(
            detector=lambda *_args: board,
            capture=lambda _serial: source,
            executor=execute,
            model=model,
        )
        controller.set_learning_enabled(True)
        controller.capture_device("test-device", auto_search=False)
        controller.set_rule_profile(RuleProfile("safe"))
        controller.confirm_board()
        controller.evaluate_manual_route(((0, 0),))
        bridge = BoardInspectionBridge(controller=controller, executor=ImmediateExecutor())
        self.addCleanup(bridge.close)
        with patch.object(model, "learn", side_effect=OSError("learning failed")):
            result = bridge.command({"action": "execute_route", "serial": "test-device"})

        self.assertTrue(result["accepted"])
        self.assertEqual(gestures, [])
        self.assertEqual(result["snapshot"]["execution"]["status"], "failed")
        self.assertIn("learning failed", result["snapshot"]["status"])

    def test_execution_rejects_conflicts_and_stop_takes_effect_after_safe_gesture(self):
        board = tuple(tuple(Orb("normal", (row + col) % 6 + 1) for col in range(COLS))
                      for row in range(ROWS))
        source = (144, 120, bytes((60, 40, 20, 255)) * (144 * 120))
        model = OrbPrototypeModel()
        gesture_calls = []
        stop_replies = []

        class ImmediateExecutor:
            def submit(self, function, *args):
                function(*args)
                return object()

            def shutdown(self, **_kwargs):
                pass

        class DeferredExecutor:
            def __init__(self):
                self.pending = []

            def submit(self, function, *args):
                self.pending.append((function, args))
                return object()

            def run_next(self):
                function, args = self.pending.pop(0)
                function(*args)

            def shutdown(self, **_kwargs):
                self.pending.clear()

        bridge = None

        def execute(serial, path, grid, delay, hold_delay, lift_threshold, expected_board,
                    max_corrections, on_verification, screen_size=None):
            gesture_calls.append((serial, len(model.samples)))
            stop_replies.append(bridge.command({"action": "stop_execution"}))
            on_verification(PlayVerification(expected_board, expected_board, 0, True, "verified"))
            return True

        controller = BoardInspectionController(
            detector=lambda *_args: board,
            capture=lambda _serial: source,
            executor=execute,
            model=model,
        )
        controller.set_learning_enabled(True)
        controller.capture_device("test-device", auto_search=False)
        controller.set_rule_profile(RuleProfile("safe"))
        controller.confirm_board()
        controller.evaluate_manual_route(((0, 0),))
        execution_executor = DeferredExecutor()
        bridge = BoardInspectionBridge(
            controller=controller,
            executor=ImmediateExecutor(),
            execution_executor=execution_executor,
        )
        self.addCleanup(bridge.close)

        started = bridge.command({"action": "execute_route", "serial": "test-device"})
        self.assertTrue(started["accepted"])
        self.assertTrue(started["snapshot"]["execution"]["busy"])
        self.assertEqual(gesture_calls, [])
        with self.assertRaisesRegex(ValueError, "執行中"):
            bridge.command({"action": "select_cell", "cell": [0, 0]})

        execution_executor.run_next()

        self.assertEqual(gesture_calls, [("test-device", 30)])
        self.assertIn("安全放手", stop_replies[0]["status"])
        result = bridge.snapshot()
        self.assertEqual(result["execution"]["status"], "stopped")
        self.assertEqual(result["execution"]["phase"], "stopped")
        self.assertEqual(result["execution"]["verification"]["status"], "verified")
        self.assertTrue(result["execution"]["stop_requested"])
    def test_close_requests_execution_stop_before_executor_shutdown(self):
        board = tuple(tuple(Orb("normal", (row + col) % 6 + 1) for col in range(COLS))
                      for row in range(ROWS))
        source = (144, 120, bytes((60, 40, 20, 255)) * (144 * 120))
        model = OrbPrototypeModel()
        gestures = []

        class ImmediateExecutor:
            def submit(self, function, *args):
                function(*args)
                return object()

            def shutdown(self, **_kwargs):
                pass

        class DeferredExecutor:
            def __init__(self):
                self.pending = []
                self.shutdown_called = False

            def submit(self, function, *args):
                self.pending.append((function, args))
                return object()

            def run_next(self):
                function, args = self.pending.pop(0)
                function(*args)

            def shutdown(self, **_kwargs):
                self.shutdown_called = True

        def execute(*_args, **_kwargs):
            gestures.append(True)
            return True

        controller = BoardInspectionController(
            detector=lambda *_args: board,
            capture=lambda _serial: source,
            executor=execute,
            model=model,
        )
        controller.capture_device("test-device", auto_search=False)
        controller.set_rule_profile(RuleProfile("safe"))
        controller.confirm_board()
        controller.evaluate_manual_route(((0, 0),))
        execution_executor = DeferredExecutor()
        bridge = BoardInspectionBridge(
            controller=controller,
            executor=ImmediateExecutor(),
            execution_executor=execution_executor,
        )
        started = bridge.command({"action": "execute_route", "serial": "test-device"})
        self.assertTrue(started["snapshot"]["execution"]["busy"])

        bridge.close()

        closed = bridge.snapshot()
        self.assertTrue(closed["execution"]["stop_requested"])
        self.assertTrue(execution_executor.shutdown_called)
        with self.assertRaisesRegex(ValueError, "執行中"):
            bridge.command({"action": "select_cell", "cell": [0, 0]})
        acceptance_status = []
        accept_current_board = controller.accept_current_board

        def capture_acceptance_status():
            acceptance_status.append(bridge.snapshot()["status"])
            return accept_current_board()

        with patch.object(controller, "accept_current_board", side_effect=capture_acceptance_status):
            execution_executor.run_next()
        result = bridge.snapshot()
        self.assertEqual(result["execution"]["status"], "stopped")
        self.assertEqual(gestures, [])
        self.assertIn("正在接受目前盤面", acceptance_status[0])
        self.assertNotIn("學習", acceptance_status[0])
        self.assertIn("尚未開始手勢", result["status"])
        self.assertNotIn("學習", result["status"])

    def test_device_and_calibration_operations_report_async_success_and_error(self):
        board = tuple(tuple(Orb("normal", 1) for _ in range(COLS)) for _ in range(ROWS))
        source = (144, 120, bytes((60, 40, 20, 255)) * (144 * 120))
        listed = []
        captures = []
        fail_devices = []

        class DeferredExecutor:
            def __init__(self):
                self.pending = []

            def submit(self, function, *args):
                self.pending.append((function, args))
                return object()

            def run_next(self):
                function, args = self.pending.pop(0)
                function(*args)

            def shutdown(self, **_kwargs):
                self.pending.clear()
        class ImmediateExecutor:
            def submit(self, function, *args):
                function(*args)
                return object()

            def shutdown(self, **_kwargs):
                pass


        def list_devices():
            if fail_devices:
                raise RuntimeError("adb offline")
            listed.append(True)
            return ("device-a", "device-b")

        def capture(serial):
            captures.append(serial)
            return source

        controller = BoardInspectionController(
            detector=lambda *_args: board,
            capture=capture,
        )
        executor = DeferredExecutor()
        bridge = BoardInspectionBridge(
            controller=controller,
            device_lister=list_devices,
            executor=executor,
            search_executor=ImmediateExecutor(),
        )
        self.addCleanup(bridge.close)
        bridge.drain_events()

        refreshed = bridge.command({"action": "refresh_devices"})
        self.assertTrue(refreshed["accepted"])
        self.assertFalse(refreshed["snapshot"]["busy"])
        self.assertTrue(refreshed["snapshot"]["operational_busy"])
        self.assertEqual(listed, [])
        executor.run_next()
        refreshed = bridge.snapshot()
        self.assertEqual(refreshed["devices"], ["device-a", "device-b"])
        self.assertEqual(refreshed["selected_device"], "device-a")
        self.assertEqual(refreshed["console"][-1]["level"], "success")

        bridge.command({"action": "select_device", "serial": "device-b"})
        captured = bridge.command({"action": "capture_screen"})
        self.assertTrue(captured["accepted"])
        self.assertFalse(captured["snapshot"]["busy"])
        self.assertTrue(captured["snapshot"]["operational_busy"])
        self.assertEqual(captures, [])
        executor.run_next()
        self.assertEqual(captures, ["device-b"])

        calibration = bridge.command({
            "action": "calibrate",
            "left": 0,
            "top": 0,
            "cell": 20,
        })
        self.assertTrue(calibration["accepted"])
        self.assertFalse(calibration["snapshot"]["busy"])
        self.assertTrue(calibration["snapshot"]["operational_busy"])
        executor.run_next()
        calibrated = bridge.snapshot()
        self.assertEqual(calibrated["calibration"], {"left": 0, "top": 0, "cell": 20})
        self.assertEqual(calibrated["console"][-1]["level"], "success")

        automatic = bridge.command({"action": "auto_calibrate"})
        self.assertTrue(automatic["accepted"])
        self.assertFalse(automatic["snapshot"]["busy"])
        self.assertTrue(automatic["snapshot"]["operational_busy"])
        executor.run_next()
        self.assertEqual(
            bridge.snapshot()["calibration"],
            {"left": 0, "top": 0, "cell": 24},
        )
        self.assertEqual(bridge.snapshot()["console"][-1]["level"], "success")

        invalid = bridge.command({
            "action": "calibrate",
            "left": 120,
            "top": 0,
            "cell": 20,
        })
        self.assertTrue(invalid["accepted"])
        executor.run_next()
        failed = bridge.snapshot()
        self.assertEqual(failed["console"][-1]["level"], "error")
        self.assertIn("校正範圍", failed["status"])

        fail_devices.append(True)
        failed_refresh = bridge.command({"action": "refresh_devices"})
        self.assertTrue(failed_refresh["accepted"])
        executor.run_next()
        self.assertEqual(bridge.snapshot()["console"][-1]["level"], "error")
        self.assertIn("adb offline", bridge.snapshot()["status"])

    def test_rule_profile_import_export_preserves_legacy_json_outside_board_flow(self):
        board = tuple(tuple(Orb("normal", 1) for _ in range(COLS)) for _ in range(ROWS))
        source = (12, 10, bytes((60, 40, 20, 255)) * (12 * 10))

        class ImmediateExecutor:
            def submit(self, function, *args):
                function(*args)
                return object()

            def shutdown(self, **_kwargs):
                pass

        controller = BoardInspectionController(
            detector=lambda *_args: board,
            capture=lambda _serial: source,
        )
        controller.capture_device("test-device", auto_search=False)
        bridge = BoardInspectionBridge(controller=controller, executor=ImmediateExecutor())
        self.addCleanup(bridge.close)
        original_source = bridge.snapshot()["source"]
        profile = RuleProfile(
            "legacy-compatible",
            condition_groups=(ConditionGroup.all_of(
                (LeaderCondition.combo_minimum(3),), name="combo",
            ),),
            external_conditions=(ExternalCondition("HP", confirmed=True),),
            hazard_policy="allow",
        )

        exported = bridge.command({"action": "export_rule_profile"})
        self.assertEqual(exported["profile"], bridge.snapshot()["rule_profile"])
        self.assertEqual(
            json.loads(exported["profile_json"]),
            exported["profile"],
        )

        imported = bridge.command({
            "action": "import_rule_profile",
            "profile_json": profile.to_json(),
        })
        self.assertTrue(imported["accepted"])
        snapshot = imported["snapshot"]
        self.assertEqual(snapshot["rule_profile"], profile.to_dict())
        self.assertEqual(snapshot["source"], original_source)
        self.assertIsNone(snapshot["route_result"])

    def test_console_levels_are_structured_and_bounded_under_rapid_activity(self):
        class ImmediateExecutor:
            def submit(self, function, *args):
                function(*args)
                return object()

            def shutdown(self, **_kwargs):
                pass

        bridge = BoardInspectionBridge(
            device_lister=lambda: ("device-a",),
            executor=ImmediateExecutor(),
        )
        self.addCleanup(bridge.close)
        bridge.drain_events()
        for _ in range(125):
            bridge.command({"action": "refresh_devices"})
        snapshot = bridge.snapshot()
        self.assertEqual(len(snapshot["console"]), 100)
        self.assertTrue(all(
            {"level", "phase", "message"} <= set(entry)
            for entry in snapshot["console"]
        ))
        self.assertEqual(snapshot["console"][-1]["level"], "success")

        no_devices = BoardInspectionBridge(
            device_lister=lambda: (),
            executor=ImmediateExecutor(),
        )
        self.addCleanup(no_devices.close)
        no_devices.command({"action": "refresh_devices"})
        self.assertEqual(no_devices.snapshot()["console"][-1]["level"], "warning")

    def test_snapshot_keeps_primary_workflow_with_calibration_and_debug_visibility(self):
        board = tuple(tuple(Orb("normal", 1) for _ in range(COLS)) for _ in range(ROWS))
        source = (12, 10, bytes((60, 40, 20, 255)) * (12 * 10))

        class ImmediateExecutor:
            def submit(self, function, *args):
                function(*args)
                return object()

            def shutdown(self, **_kwargs):
                pass

        controller = BoardInspectionController(
            detector=lambda *_args: board,
            capture=lambda _serial: source,
        )
        controller.capture_device("test-device", auto_search=False)
        bridge = BoardInspectionBridge(controller=controller, executor=ImmediateExecutor())
        self.addCleanup(bridge.close)
        snapshot = bridge.snapshot()

        self.assertEqual(snapshot["calibration"], {"left": 0, "top": 0, "cell": 2})
        self.assertEqual(snapshot["debug"]["source_name"], "test-device")
        self.assertEqual(snapshot["debug"]["pending_operations"], 0)
        for key in ("board", "rule_profile", "search", "execution", "console"):
            self.assertIn(key, snapshot)


    def test_operational_requests_do_not_block_primary_interactions(self):
        board = tuple(tuple(Orb("normal", 1) for _ in range(COLS)) for _ in range(ROWS))
        source = (12, 10, bytes((60, 40, 20, 255)) * (12 * 10))

        class ImmediateExecutor:
            def submit(self, function, *args):
                function(*args)
                return object()

            def shutdown(self, **_kwargs):
                pass

        class DeferredExecutor:
            def __init__(self):
                self.pending = []

            def submit(self, function, *args):
                self.pending.append((function, args))
                return object()

            def run_next(self):
                function, args = self.pending.pop(0)
                function(*args)

            def shutdown(self, **_kwargs):
                self.pending.clear()

        controller = BoardInspectionController(
            detector=lambda *_args: board,
            capture=lambda _serial: source,
        )
        controller.capture_device("test-device", auto_search=False)
        operational_executor = DeferredExecutor()
        bridge = BoardInspectionBridge(
            controller=controller,
            device_lister=lambda: ("test-device",),
            executor=ImmediateExecutor(),
            operational_executor=operational_executor,
        )
        self.addCleanup(bridge.close)
        bridge.drain_events()
        refreshed = bridge.command({"action": "refresh_devices"})
        self.assertTrue(refreshed["accepted"])
        self.assertFalse(refreshed["snapshot"]["busy"])
        self.assertTrue(refreshed["snapshot"]["operational_busy"])
        self.assertEqual(refreshed["snapshot"]["debug"]["pending_operational"], 1)

        selected = bridge.command({"action": "select_cell", "cell": [0, 0]})
        self.assertEqual(selected["selected_cell"], [0, 0])
        self.assertFalse(selected["busy"])
        self.assertTrue(selected["operational_busy"])
        updated = bridge.command({
            "action": "set_rule_profile",
            "conditions": [{"label": "至少 3 Combo", "color": "不指定"}],
            "operator": "全部符合",
            "hazard_policy": "避免危害珠",
            "external": "無",
        })
        self.assertTrue(updated["accepted"])
        self.assertFalse(updated["snapshot"]["busy"])
        self.assertTrue(updated["snapshot"]["operational_busy"])
        self.assertEqual(len(operational_executor.pending), 1)

        operational_executor.run_next()
        refreshed = bridge.snapshot()
        self.assertFalse(refreshed["busy"])
        self.assertFalse(refreshed["operational_busy"])
        self.assertEqual(refreshed["devices"], ["test-device"])
        self.assertEqual(refreshed["rule_profile"]["name"], "至少 3 Combo")

        calibrated = bridge.command({
            "action": "calibrate",
            "left": 0,
            "top": 0,
            "cell": 2,
        })
        self.assertTrue(calibrated["accepted"])
        self.assertFalse(calibrated["snapshot"]["busy"])
        self.assertTrue(calibrated["snapshot"]["operational_busy"])
        with self.assertRaisesRegex(ValueError, "裝置盤面作業中"):
            bridge.command({
                "action": "set_rule_profile",
                "conditions": [{"label": "至少 5 Combo", "color": "不指定"}],
                "operator": "全部符合",
                "hazard_policy": "避免危害珠",
                "external": "無",
            })
        selected = bridge.command({"action": "select_cell", "cell": [0, 1]})
        self.assertEqual(selected["selected_cell"], [0, 1])
        operational_executor.run_next()
        self.assertFalse(bridge.snapshot()["operational_busy"])


    def test_capture_auto_searches_thirty_attempts_and_explicit_settings_override(self):
        board = tuple(tuple(Orb("normal", (row + col) % 6 + 1) for col in range(COLS))
                      for row in range(ROWS))
        source = (144, 120, bytes((60, 40, 20, 255)) * (144 * 120))
        search_calls = []

        class DeferredExecutor:
            def __init__(self):
                self.pending = []

            def submit(self, function, *args):
                self.pending.append((function, args))
                return object()

            def run_next(self):
                function, args = self.pending.pop(0)
                function(*args)

            def shutdown(self, **_kwargs):
                self.pending.clear()

        interaction_executor = DeferredExecutor()
        search_executor = DeferredExecutor()
        controller = BoardInspectionController(
            detector=lambda *_args: board,
            capture=lambda _serial: source,
        )
        bridge = BoardInspectionBridge(
            controller=controller,
            executor=interaction_executor,
            search_executor=search_executor,
        )
        self.addCleanup(bridge.close)
        bridge.drain_events()

        def fake_search(_board, _profile, options, **_kwargs):
            search_calls.append(options)
            return RouteSearchResult(None, None, 1, options.attempts)

        with patch("pad_router_gui.search_qualifying_route", side_effect=fake_search):
            started = bridge.command({
                "action": "capture_screen",
                "serial": "test-device",
                "search": {
                    "attempts": 7,
                    "max_steps": 0,
                    "seed": 42,
                    "cascade": False,
                },
            })
            self.assertTrue(started["accepted"])
            self.assertFalse(started["snapshot"]["busy"])
            self.assertEqual(search_executor.pending, [])
            interaction_executor.run_next()

            pending = bridge.snapshot()
            self.assertEqual(pending["search"]["status"], "running")
            self.assertEqual(pending["search"]["options"]["attempts"], 7)
            self.assertEqual(len(search_executor.pending), 1)
            search_executor.run_next()

            fallback = bridge.command({"action": "capture_screen", "serial": "test-device"})
            self.assertTrue(fallback["accepted"])
            self.assertFalse(fallback["snapshot"]["busy"])
            interaction_executor.run_next()

            pending = bridge.snapshot()
            self.assertEqual(pending["search"]["status"], "running")
            self.assertEqual(pending["search"]["options"]["attempts"], 30)
            self.assertEqual(len(search_executor.pending), 1)
            search_executor.run_next()

        self.assertEqual([options.attempts for options in search_calls], [7, 30])

    def test_route_preview_keeps_authoritative_overlay_and_drag_only_board_state(self):
        board = tuple(tuple(Orb("normal", (row * COLS + col) % 6 + 1)
                            for col in range(COLS)) for row in range(ROWS))
        source = (144, 120, bytes((60, 40, 20, 255)) * (144 * 120))

        class ImmediateExecutor:
            def submit(self, function, *args):
                function(*args)
                return object()

            def shutdown(self, **_kwargs):
                pass

        controller = BoardInspectionController(
            detector=lambda *_args: board,
            capture=lambda _serial: source,
        )
        controller.capture_device("test-device", auto_search=False)
        controller.set_rule_profile(RuleProfile("preview"))
        controller.confirm_board()
        result = controller.evaluate_manual_route(((0, 0), (0, 1)), cascade=False)
        bridge = BoardInspectionBridge(controller=controller, executor=ImmediateExecutor())
        self.addCleanup(bridge.close)

        snapshot = bridge.snapshot()
        preview = snapshot["route_preview"]
        self.assertEqual(preview["stage"], "drag_applied")
        self.assertEqual(preview["route"], [[0, 0], [0, 1]])
        self.assertEqual(preview["projected_combo"], result.combo_count)
        self.assertEqual(
            {tuple(item["cell"]): item["color"] for item in preview["board"]},
            {
                (row, col): getattr(orb, "color", None)
                for row, values in enumerate(expected_board_after_path(board, ((0, 0), (0, 1))))
                for col, orb in enumerate(values)
            },
        )
        self.assertEqual(
            [(point["x"], point["y"]) for point in snapshot["route_overlay"]],
            [(12, 12), (36, 12)],
        )
        self.assertEqual(snapshot["route_overlay"][1]["step"], 2)
        json.dumps(snapshot)

class ExpandedBoardBridgeTests(unittest.TestCase):
    """Switching to the 7x6 Board reshapes the reviewed board and the snapshot."""

    def setUp(self):
        self.addCleanup(pad_router.set_board_size, 5, 6)

        def detect(width, height, pixels, grid):
            return tuple(tuple(Orb("normal", (r + c) % 6 + 1) for c in range(pad_router.COLS))
                         for r in range(pad_router.ROWS))

        self.bridge = BoardInspectionBridge(BoardInspectionController(
            detector=detect, capture=lambda serial: (7 * 85, 6 * 85, bytes(7 * 85 * 6 * 85 * 4))))
        self.addCleanup(self.bridge.close)
        self.bridge.command({"action": "select_device", "serial": "device"})
        self.bridge.command({"action": "capture", "serial": "device"})
        self.bridge.wait_for_idle(30)

    def test_switch_reshapes_board_and_reports_the_new_ceiling(self):
        before = self.bridge.command({"action": "snapshot"})
        self.assertEqual(before["board_size"]["max_combo"], 10)
        self.assertEqual(len(before["board"]), 30)

        self.bridge.command({"action": "set_board_size", "size": "7x6"})
        self.bridge.wait_for_idle(30)

        after = self.bridge.command({"action": "snapshot"})
        self.assertEqual(after["board_size"],
                         {"name": "7x6", "label": "7\u00d76", "rows": 6, "cols": 7, "max_combo": 14})
        self.assertEqual(len(after["board"]), 42)

    def test_unknown_board_size_is_rejected(self):
        with self.assertRaises(ValueError):
            self.bridge.command({"action": "set_board_size", "size": "9x9"})
            self.bridge.wait_for_idle(30)


class InferCalibrationTests(unittest.TestCase):
    """PAD pins the Board to the bottom of the play area, whatever its size."""

    @staticmethod
    def _screenshot(width: int, height: int, lit_below: int) -> bytes:
        pixels = bytearray(width * height * 4)
        for y in range(lit_below):
            for x in range(width):
                pixels[(y * width + x) * 4:(y * width + x) * 4 + 3] = b"\x80\x80\x80"
        return bytes(pixels)

    def tearDown(self):
        pad_router.set_board_size(5, 6)

    def test_calibrated_device_reproduces_the_recorded_standard_calibration(self):
        pixels = self._screenshot(1080, 2340, 2280)
        self.assertEqual(infer_calibration(1080, 2340, pixels), BoardCalibration(0, 1380, 180))

    def test_expanded_board_is_measured_not_assumed_to_span_the_width(self):
        # Measured off a live SM-A1560 7x6 capture: the Board is framed in black
        # at x 23..1052 and y 1381..2262, so it is narrower than the screen.
        pixels = bytearray(1080 * 2340 * 4)
        for y in range(1381, 2263):
            for x in range(23, 1053):
                pixels[(y * 1080 + x) * 4:(y * 1080 + x) * 4 + 3] = b"\x80\x80\x80"
        pad_router.set_board_size(6, 7)
        self.assertEqual(infer_calibration(1080, 2340, bytes(pixels)),
                         BoardCalibration(23, 1381, 147))

    def test_tight_board_crop_starts_at_the_top(self):
        pad_router.set_board_size(6, 7)
        calibration = infer_calibration(7 * 82, 6 * 82, self._screenshot(7 * 82, 6 * 82, 6 * 82))
        self.assertEqual(calibration, BoardCalibration(0, 0, 82))


class WebviewAssetTests(unittest.TestCase):
    def test_workspace_uses_only_adjacent_local_assets(self):
        from pad_router_webview import ASSET_ROOT

        index = ASSET_ROOT / "index.html"
        style = ASSET_ROOT / "style.css"
        script = ASSET_ROOT / "app.js"
        self.assertTrue(index.is_file())
        self.assertTrue(style.is_file())
        self.assertTrue(script.is_file())
        gui_source = Path("pad_router_gui.py").read_text(encoding="utf-8")
        self.assertIn("BoardInspectionBridge", gui_source)
        self.assertNotIn("BoardInspectionApp", gui_source)
        self.assertNotIn("tkinter", gui_source)

        html = index.read_text(encoding="utf-8")
        self.assertIn('href="style.css"', html)
        self.assertIn('src="app.js"', html)
        self.assertNotIn("://", html + style.read_text() + script.read_text())
        self.assertIn('id="board-grid"', html)
        self.assertIn('data-orb="fire"', html)
        self.assertIn('id="planning-controls"', html)
        self.assertIn('<option selected>30</option>', html)
        self.assertIn('id="start-search"', html)
        self.assertIn('id="cancel-search"', html)
        self.assertNotIn('id="approve-route"', html)
        self.assertIn('id="action-rail"', html)
        self.assertIn('id="execute-route"', html)
        self.assertIn('id="stop-execution"', html)
        self.assertIn('id="move-delay"', html)
        self.assertIn('value="0.04"', html)
        self.assertIn('id="learning-enabled"', html)
        self.assertIn('id="learning-status"', html)
        self.assertIn('AI 模型學習', html)
        rail_start = html.index('<div id="action-rail"')
        rail = html[rail_start:html.index("</div>", rail_start)]
        for control in ('id="capture"', 'id="execute-route"', 'id="stop-execution"', 'id="move-delay"'):
            self.assertIn(control, rail)
        for marker in (
                'id="source-stage"', 'id="route-overlay"', 'id="route-preview"',
                'id="route-preview-grid"', 'id="projected-combo"', 'id="route-preview-status"',
                'id="device-status"', 'id="calibration-controls"',
                'id="calibration-left"', 'id="calibration-top"', 'id="calibration-cell"',
                'id="apply-calibration"', 'id="auto-calibration"', 'id="profile-controls"',
                'id="profile-file"', 'id="import-profile"', 'id="export-profile"',
                'id="debug-controls"', 'id="debug-state"',
        ):
            self.assertIn(marker, html)
        client = script.read_text()
        self.assertIn("ArrowRight", client)
        self.assertIn("requestAnimationFrame", client)
        self.assertIn("pendingSnapshot", client)
        self.assertIn("setInterval(pollEvents, 200)", client)
        self.assertIn('command("select_cell"', client)
        self.assertIn('command("correct_cell"', client)
        self.assertIn('className = "cell-badge plus"', client)
        self.assertIn('command("set_rule_profile"', client)
        self.assertIn('command("search_route"', client)
        self.assertIn('command("cancel_search"', client)
        self.assertNotIn('command("approve_route"', client)
        self.assertIn('command("execute_route"', client)
        self.assertIn('delay: moveDelay.valueAsNumber', client)
        self.assertIn('command("set_learning_enabled"', client)
        for phase in ("正在擷取畫面", "盤面辨識完成", "正在計算並搜尋路徑",
                      "正在執行手勢", "正在驗證結果"):
            self.assertIn(phase, gui_source)
        self.assertIn('command("set_protected_cell"', client)
        for marker in (
                'command("calibrate"', 'command("auto_calibrate"',
                'command("import_rule_profile"', 'command("export_rule_profile"',
                'FileReader', 'Blob', 'snapshot.debug', 'entry.level',
                'capture_screen", { search: searchPayload() }',
                'route_overlay', 'route_preview', 'projected_combo',
                'createElementNS', 'preview-cell',
        ):
            self.assertIn(marker, client)
        self.assertNotIn("adb", client.lower())
        self.assertNotIn("solver", client.lower())
        styles = style.read_text()
        self.assertIn("min-width: 960px", styles)
        self.assertIn("min-height: 640px", styles)
        self.assertIn("min-height: 140px", styles)
        self.assertIn("@media (max-width: 1100px)", styles)
        self.assertIn("@media (max-height: 920px)", styles)
        self.assertIn("@media (max-height: 800px)", styles)
        self.assertIn("@media (max-height: 740px)", styles)
        for marker in (".board-cell.unknown", ".board-cell.selected", ".board-cell.protected",
                       ".cell-badge", ".cell-badge.locked", ".aux-controls", ".debug-grid",
                       ".route-preview", ".route-line", ".route-marker", ".combo-badge",
                       ".move-delay-control"):
            self.assertIn(marker, styles)
        rail_style_start = styles.index(".action-rail {")
        rail_style = styles[rail_style_start:styles.index("}", rail_style_start)]
        for declaration in ("position: absolute", "left:", "top: 50%", "translateY(-50%)"):
            self.assertIn(declaration, rail_style)

    def test_learning_toggle_waits_for_confirmed_backend_snapshot(self):
        from pad_router_webview import ASSET_ROOT

        client = (ASSET_ROOT / "app.js").read_text(encoding="utf-8")
        handler_start = client.index('learningEnabled.addEventListener("change"')
        handler_end = client.index('protect.addEventListener("click"', handler_start)
        handler = client[handler_start:handler_end]

        self.assertIn('learningEnabled.addEventListener("change", async () => {', handler)
        self.assertIn("learningEnabled.checked = confirmedLearningEnabled", handler)
        self.assertIn("learningEnabled.disabled = true", handler)
        self.assertIn('learningStatus.textContent = "AI 模型學習：更新中…"', handler)
        self.assertIn('await command("set_learning_enabled"', handler)
        self.assertIn("renderSnapshot(reply.snapshot || reply)", handler)


class EntrypointTests(unittest.TestCase):
    def test_desktop_entrypoint_uses_webview(self):
        import pad_router
        with patch.object(sys, "argv", ["pad_router.py", "--gui"]), \
                patch("pad_router_webview.main") as webview_main:
            pad_router.main()
        webview_main.assert_called_once_with()

        with patch.object(sys, "argv", ["pad_router.py", "--webview"]), \
                patch("pad_router_webview.main") as webview_main:
            pad_router.main()
        webview_main.assert_called_once_with()

    def test_webview_entrypoint_uses_gtk_and_adjacent_assets(self):
        import pad_router_webview

        window_calls = []
        start_calls = []
        gi_calls = []
        bridge_closes = []

        class ClosedEvent:
            def __iadd__(self, callback):
                self.callback = callback
                return self

        window = SimpleNamespace(events=SimpleNamespace(closed=ClosedEvent()))
        fake_webview = SimpleNamespace(
            create_window=lambda *args, **kwargs: (
                window_calls.append((args, kwargs)) or window
            ),
            start=lambda *args, **kwargs: start_calls.append((args, kwargs)),
        )
        fake_gi = SimpleNamespace(require_version=lambda *args: gi_calls.append(args))
        bridge = SimpleNamespace(close=lambda: bridge_closes.append(True))

        with patch.dict(sys.modules, {"webview": fake_webview, "gi": fake_gi}), \
                patch.object(pad_router_webview, "BoardInspectionBridge", return_value=bridge):
            pad_router_webview.main()

        self.assertEqual(gi_calls, [("Gtk", "3.0"), ("WebKit2", "4.1")])
        self.assertEqual(len(window_calls), 1)
        args, options = window_calls[0]
        self.assertEqual(args[0], "PAD Router — 裝置工作區")
        self.assertEqual(options["url"], pad_router_webview.ASSET_ROOT.joinpath("index.html").as_uri())
        self.assertEqual(options["min_size"], (960, 640))
        self.assertTrue(options["resizable"])
        self.assertEqual(len(start_calls), 1)
        self.assertEqual(start_calls[0][1], {"gui": "gtk"})
        self.assertEqual(bridge_closes, [True])




class ContinuousExecutionTests(unittest.TestCase):
    @staticmethod
    def _controller(executor, capture_calls, protected_cell=(0, 0)):
        board = tuple(tuple(Orb("normal", (row + col) % 6 + 1) for col in range(COLS))
                      for row in range(ROWS))
        source = (144, 120, bytes((60, 40, 20, 255)) * (144 * 120))

        def capture(serial):
            capture_calls.append(serial)
            return source

        controller = BoardInspectionController(
            detector=lambda *_args: board, capture=capture, executor=executor,
        )
        controller.set_rule_profile(RuleProfile("continuous"))
        controller.set_protected_cell(protected_cell, recompute=False)
        controller.capture_device("test-device")
        return controller

    def test_two_rounds_then_user_stop_preserves_protected_route(self):
        stop = threading.Event()
        capture_calls = []
        routes = []

        def executor(*args, on_verification, screen_size=None):
            routes.append(args[1])
            expected = expected_board_after_path(args[6], args[1])
            on_verification(PlayVerification(expected, expected, 0, True, "verified"))
            if len(routes) == 2:
                stop.set()
            return True

        controller = self._controller(executor, capture_calls)
        status = controller.execute_continuously("test-device", stop)

        self.assertEqual(len(routes), 2)
        self.assertEqual(capture_calls, ["test-device", "test-device"])
        self.assertTrue(all((0, 0) not in route for route in routes))
        self.assertEqual(status, "連續執行已由使用者停止")
        self.assertEqual(controller.state.status, status)

    def test_executor_failure_stops_without_capture_or_second_execution(self):
        capture_calls = []
        executions = []

        def executor(*_args, **_kwargs):
            executions.append(True)
            return False

        controller = self._controller(executor, capture_calls)
        status = controller.execute_continuously("test-device", threading.Event())

        self.assertEqual(executions, [True])
        self.assertEqual(capture_calls, ["test-device"])
        self.assertIn("執行或驗證失敗", status)

    def test_failed_verification_stops_before_rescan(self):
        capture_calls = []
        executions = []

        def executor(*args, on_verification, screen_size=None):
            executions.append(True)
            expected = expected_board_after_path(args[6], args[1])
            on_verification(PlayVerification(expected, args[6], 2, False,
                                               "post_gesture_mismatch"))
            return True

        controller = self._controller(executor, capture_calls)
        status = controller.execute_continuously("test-device", threading.Event())

        self.assertEqual(executions, [True])
        self.assertEqual(capture_calls, ["test-device"])
        self.assertIn("執行或驗證失敗", status)

    def test_capture_error_stops_without_second_execution(self):
        capture_calls = []
        executions = []

        def executor(*args, on_verification, screen_size=None):
            executions.append(True)
            expected = expected_board_after_path(args[6], args[1])
            on_verification(PlayVerification(expected, expected, 0, True, "verified"))
            return True

        controller = self._controller(executor, capture_calls)

        def fail_capture(_serial):
            capture_calls.append("failed")
            raise RuntimeError("capture failed")

        controller._capture = fail_capture
        status = controller.execute_continuously("test-device", threading.Event())

        self.assertEqual(executions, [True])
        self.assertEqual(capture_calls, ["test-device", "failed"])
        self.assertIn("capture failed", status)

    def test_uncertain_recapture_stops_without_second_execution(self):
        capture_calls = []
        executions = []

        def executor(*args, on_verification, screen_size=None):
            executions.append(True)
            expected = expected_board_after_path(args[6], args[1])
            on_verification(PlayVerification(expected, expected, 0, True, "verified"))
            return True

        controller = self._controller(executor, capture_calls)
        unknown = tuple(tuple(Orb("unknown", visual_class="unknown") for _ in range(COLS))
                        for _ in range(ROWS))
        controller._detector = lambda *_args: unknown
        status = controller.execute_continuously("test-device", threading.Event())

        self.assertEqual(executions, [True])
        self.assertEqual(capture_calls, ["test-device", "test-device"])
        self.assertIn("新盤面辨識不確定", status)

    def test_noneligible_new_route_stops_without_second_execution(self):
        capture_calls = []
        executions = []

        def executor(*args, on_verification, screen_size=None):
            executions.append(True)
            expected = expected_board_after_path(args[6], args[1])
            on_verification(PlayVerification(expected, expected, 0, True, "verified"))
            return True

        controller = self._controller(executor, capture_calls)
        original_capture = controller._capture

        def capture(serial):
            controller.set_rule_profile(RuleProfile(
                "blocked", condition_groups=(ConditionGroup.all_of((
                    LeaderCondition.combo_minimum(99),
                )),),
            ))
            return original_capture(serial)

        controller._capture = capture
        with patch("pad_router_gui.RouteSearchOptions", return_value=RouteSearchOptions(
                attempts=1, min_steps=0, max_steps=0)):
            status = controller.execute_continuously("test-device", threading.Event())

        self.assertEqual(executions, [True])
        self.assertEqual(capture_calls, ["test-device", "test-device"])
        self.assertIn("新盤面沒有符合條件的路徑", status)




class RecognitionRetryControllerTests(unittest.TestCase):
    def setUp(self):
        handle, path = tempfile.mkstemp(suffix=".png")
        os.close(handle)
        self.path = Path(path)
        self.path.write_bytes(png_bytes())
        self.addCleanup(lambda: self.path.unlink(missing_ok=True))

    @staticmethod
    def _unknown_board():
        return tuple(tuple(Orb("unknown", visual_class="unknown") for _ in range(COLS))
                     for _ in range(ROWS))

    @staticmethod
    def _complete_board():
        return tuple(tuple(Orb("normal", 1) for _ in range(COLS)) for _ in range(ROWS))

    def _controller(self, results, max_attempts=2):
        calls = []
        results = iter(results)

        def detect(width, height, pixels, grid):
            calls.append((width, height, pixels, grid))
            return next(results)

        return BoardInspectionController(
            detector=detect, max_recognition_attempts=max_attempts
        ), calls

    def test_unknown_then_complete_retries_with_same_source_and_stops(self):
        controller, calls = self._controller(
            [self._unknown_board(), self._complete_board()]
        )

        state = controller.load_png(self.path)

        self.assertEqual(len(calls), 2)
        self.assertIs(calls[0][2], calls[1][2])
        self.assertIs(calls[0][3], calls[1][3])
        self.assertFalse(state.uncertain_cells)
        self.assertIn("主動辨識第 2/2 次", state.status)

    def test_complete_first_stops_without_a_retry(self):
        controller, calls = self._controller([self._complete_board()])

        state = controller.load_png(self.path)

        self.assertEqual(len(calls), 1)
        self.assertFalse(state.uncertain_cells)
        self.assertIn("主動辨識第 1/2 次", state.status)
        self.assertIn("已提前停止", state.status)

    def test_retry_limit_five_preserves_unknown_board(self):
        unknown = self._unknown_board()
        controller, calls = self._controller([unknown] * 5, max_attempts=5)

        state = controller.load_png(self.path)

        self.assertEqual(len(calls), 5)
        self.assertEqual(state.uncertain_cells, tuple(
            (row, col) for row in range(ROWS) for col in range(COLS)
        ))
        self.assertIn("主動辨識第 5/5 次", state.status)

    def test_calibration_uses_the_same_retry_flow(self):
        controller, calls = self._controller(
            [self._complete_board(), self._unknown_board(), self._complete_board()]
        )
        controller.load_png(self.path)

        state = controller.set_calibration(BoardCalibration(0, 0, 2))

        self.assertEqual(len(calls), 3)
        self.assertFalse(state.uncertain_cells)
        self.assertIn("主動辨識第 2/2 次", state.status)

    def test_recognition_attempt_setting_validates_one_to_five(self):
        controller = BoardInspectionController()

        for value in (0, 6, True, False, 1.5, "2"):
            with self.assertRaises(ValueError):
                controller.max_recognition_attempts = value
        controller.max_recognition_attempts = 4
        self.assertEqual(controller.max_recognition_attempts, 4)




class RecognitionRetryControllerTests(unittest.TestCase):
    def setUp(self):
        handle, path = tempfile.mkstemp(suffix=".png")
        os.close(handle)
        self.path = Path(path)
        self.path.write_bytes(png_bytes())
        self.addCleanup(lambda: self.path.unlink(missing_ok=True))

    @staticmethod
    def _unknown_board():
        return tuple(tuple(Orb("unknown", visual_class="unknown") for _ in range(COLS))
                     for _ in range(ROWS))

    @staticmethod
    def _complete_board():
        return tuple(tuple(Orb("normal", 1) for _ in range(COLS)) for _ in range(ROWS))

    def _controller(self, results, max_attempts=2):
        calls = []
        results = iter(results)

        def detect(width, height, pixels, grid):
            calls.append((width, height, pixels, grid))
            return next(results)

        return BoardInspectionController(
            detector=detect, max_recognition_attempts=max_attempts
        ), calls

    def test_unknown_then_complete_retries_with_same_source_and_stops(self):
        controller, calls = self._controller(
            [self._unknown_board(), self._complete_board()]
        )

        state = controller.load_png(self.path)

        self.assertEqual(len(calls), 2)
        self.assertIs(calls[0][2], calls[1][2])
        self.assertIs(calls[0][3], calls[1][3])
        self.assertFalse(state.uncertain_cells)
        self.assertIn("主動辨識第 2/2 次", state.status)

    def test_complete_first_stops_without_a_retry(self):
        controller, calls = self._controller([self._complete_board()])

        state = controller.load_png(self.path)

        self.assertEqual(len(calls), 1)
        self.assertFalse(state.uncertain_cells)
        self.assertIn("主動辨識第 1/2 次", state.status)
        self.assertIn("已提前停止", state.status)

    def test_retry_limit_five_preserves_unknown_board(self):
        unknown = self._unknown_board()
        controller, calls = self._controller([unknown] * 5, max_attempts=5)

        state = controller.load_png(self.path)

        self.assertEqual(len(calls), 5)
        self.assertEqual(state.uncertain_cells, tuple(
            (row, col) for row in range(ROWS) for col in range(COLS)
        ))
        self.assertIn("主動辨識第 5/5 次", state.status)

    def test_calibration_uses_the_same_retry_flow(self):
        controller, calls = self._controller(
            [self._complete_board(), self._unknown_board(), self._complete_board()]
        )
        controller.load_png(self.path)

        state = controller.set_calibration(BoardCalibration(0, 0, 2))

        self.assertEqual(len(calls), 3)
        self.assertFalse(state.uncertain_cells)
        self.assertIn("主動辨識第 2/2 次", state.status)

    def test_recognition_attempt_setting_validates_one_to_five(self):
        controller = BoardInspectionController()

        for value in (0, 6, True, False, 1.5, "2"):
            with self.assertRaises(ValueError):
                controller.max_recognition_attempts = value
        controller.max_recognition_attempts = 4
        self.assertEqual(controller.max_recognition_attempts, 4)




class BoardCalibrationTests(unittest.TestCase):
    def test_standard_board_must_fit_image(self):
        BoardCalibration(0, 0, 1).validate(6, 5)
        with self.assertRaises(ValueError):
            BoardCalibration(1, 0, 1).validate(6, 5)
        with self.assertRaises(ValueError):
            BoardCalibration(0, 0, 0).validate(6, 5)






















class RuleProfileSelectionTests(unittest.TestCase):
    def test_untyped_shape_and_four_orb_choices_use_no_orb_type(self):
        profile = rule_profile_from_selections(
            (("L 型", "不指定"), ("4 顆消除", "不指定")),
            "全部符合", "避免危害珠", "無"
        )

        self.assertEqual([item.value for item in profile.condition_groups[0].conditions], ["l", None])

    def test_fixed_choices_build_combined_conditions_without_json_input(self):
        profile = rule_profile_from_selections(
            (("至少 5 Combo", "火"), ("十字型", "暗"), ("4 顆消除", "水")),
            "全部符合", "避免危害珠", "HP 條件已確認"
        )

        self.assertEqual(profile.hazard_policy, "avoid")
        self.assertEqual([item.kind for item in profile.condition_groups[0].conditions],
                         ["combo_minimum", "shape", "connected_orb_count"])
        self.assertEqual(profile.condition_groups[0].conditions[1].value,
                         {"shape": "cross", "orb_type": "dark"})
        self.assertEqual(profile.condition_groups[0].conditions[2].value, "water")
        self.assertEqual(profile.external_conditions[0].name, "HP 條件")
        self.assertTrue(profile.external_conditions[0].confirmed)


if __name__ == "__main__":
    unittest.main()
