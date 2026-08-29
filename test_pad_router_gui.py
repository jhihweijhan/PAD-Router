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
from pad_router import (COLS, ROWS, CellFeatures, ConditionGroup, Grid, LeaderCondition, Orb, PlayVerification,
                        RouteSearchOptions, RouteSearchResult, RuleProfile, _cell_features, _normal_color, detect_board_pixels,
                        search_qualifying_route,
                        expected_board_after_path)


from pad_router_gui import (_fit_scale, _photo_from_screenshot, BoardCalibration, BoardInspectionApp,
                            BoardInspectionBridge, BoardInspectionController, OrbPrototypeModel, decode_png,
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

    def test_png_loader_returns_bgra_pixels(self):
        width, height, pixels = decode_png(self.path)
        self.assertEqual((width, height), (12, 10))
        self.assertEqual(pixels[:4], bytes((60, 40, 20, 255)))

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
        controller.approve_route(explicit_confirmation=True)
        protected = controller.state.route_evaluation.route[0]

        protected_state = controller.set_protected_cell(protected)

        self.assertEqual(protected_state.protected_cell, protected)
        self.assertFalse(protected_state.route_approved)
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
            controller.capture_device("one")
            controller.correct_cell(0, 0, "fire")
            state = controller.capture_device("two")

        self.assertEqual(state.board[0][0], Orb("normal", 1))
        self.assertTrue(state.confirmed)

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
        self.controller.approve_route(explicit_confirmation=True)
        self.assertTrue(self.controller.state.route_approved)
        self.controller.search_qualifying_route(
            RouteSearchOptions(attempts=1, seed=5, min_steps=0, max_steps=0)
        )
        self.assertFalse(self.controller.state.route_approved)

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
        self.assertFalse(state.route_approved)
        self.assertEqual(state.route_overlay, ())

    def test_execution_requires_final_confirmation_and_reports_post_gesture_board(self):
        board = tuple(tuple(Orb("normal", (r + c) % 6 + 1) for c in range(COLS)) for r in range(ROWS))
        source = (12, 10, bytes((60, 40, 20, 255)) * (12 * 10))
        calls = []

        def execute(serial, path, grid, delay, hold_delay, lift_threshold, expected_board,
                    max_corrections, on_verification):
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

        with self.assertRaisesRegex(ValueError, "明確確認"):
            controller.execute_route("test-device")
        self.assertEqual(calls, [])

        self.assertTrue(controller.execute_route("test-device", explicit_confirmation=True))
        self.assertEqual(calls[0][0], "test-device")
        self.assertEqual(calls[0][1], ((0, 0), (0, 1)))
        self.assertEqual(calls[0][2], board)
        self.assertEqual(controller.state.verification.expected_board, post_route)
        self.assertEqual(controller.state.verification.mismatches, 0)
        self.assertIn("驗證成功", controller.state.status)
        with self.assertRaisesRegex(ValueError, "符合條件的路徑"):
            controller.execute_route("test-device", explicit_confirmation=True)

    def test_execution_exposes_actionable_post_gesture_mismatch(self):
        board = tuple(tuple(Orb("normal", (r + c) % 6 + 1) for c in range(COLS)) for r in range(ROWS))
        actual = tuple(tuple(Orb("normal", (r + c + 1) % 6 + 1) for c in range(COLS)) for r in range(ROWS))
        source = (12, 10, bytes((60, 40, 20, 255)) * (12 * 10))

        def execute(*args, on_verification):
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

        self.assertFalse(controller.execute_route("test-device", explicit_confirmation=True))
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
            controller.execute_route("test-device", explicit_confirmation=True)
        self.assertEqual(calls, [])


class BoardInspectionBridgeTests(unittest.TestCase):
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
        self.assertFalse(snapshot["approval_allowed"])
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
        self.assertTrue(completed["snapshot"]["approval_allowed"])
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
        self.assertFalse(snapshot["approval_allowed"])
        self.assertFalse(snapshot["route_result"]["execution_eligible"])
        with self.assertRaisesRegex(ValueError, "核准"):
            bridge.command({"action": "approve_route"})
        with self.assertRaisesRegex(ValueError, "核准"):
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


    def test_web_approval_and_execution_learns_before_gesture_and_reports_verification(self):
        board = tuple(tuple(Orb("normal", (row + col) % 6 + 1) for col in range(COLS))
                      for row in range(ROWS))
        source = (144, 120, bytes((60, 40, 20, 255)) * (144 * 120))
        model = OrbPrototypeModel()
        execution_calls = []

        def execute(serial, path, grid, delay, hold_delay, lift_threshold, expected_board,
                    max_corrections, on_verification):
            execution_calls.append((serial, len(model.samples)))
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
        controller.capture_device("test-device", auto_search=False)
        controller.set_rule_profile(RuleProfile("safe"))
        controller.confirm_board()
        controller.evaluate_manual_route(((0, 0),))
        bridge = BoardInspectionBridge(controller=controller, executor=ImmediateExecutor())
        self.addCleanup(bridge.close)

        with self.assertRaisesRegex(ValueError, "核准"):
            bridge.command({"action": "execute_route", "serial": "test-device"})

        approved = bridge.command({"action": "approve_route"})
        self.assertTrue(approved["snapshot"]["route_approved"])
        executed = bridge.command({"action": "execute_route", "serial": "test-device"})

        self.assertTrue(executed["accepted"])
        self.assertEqual(execution_calls, [("test-device", 30)])
        self.assertEqual(executed["snapshot"]["execution"]["status"], "success")
        self.assertEqual(executed["snapshot"]["execution"]["verification"]["status"], "verified")
        self.assertFalse(executed["snapshot"]["route_approved"])
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
        controller.capture_device("test-device", auto_search=False)
        controller.set_rule_profile(RuleProfile("safe"))
        controller.confirm_board()
        controller.evaluate_manual_route(((0, 0),))
        bridge = BoardInspectionBridge(controller=controller, executor=ImmediateExecutor())
        self.addCleanup(bridge.close)
        bridge.command({"action": "approve_route"})

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
                    max_corrections, on_verification):
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

        bridge.command({"action": "approve_route"})
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
class WebviewAssetTests(unittest.TestCase):
    def test_workspace_uses_only_adjacent_local_assets(self):
        from pad_router_webview import ASSET_ROOT

        index = ASSET_ROOT / "index.html"
        style = ASSET_ROOT / "style.css"
        script = ASSET_ROOT / "app.js"
        self.assertTrue(index.is_file())
        self.assertTrue(style.is_file())
        self.assertTrue(script.is_file())

        html = index.read_text(encoding="utf-8")
        self.assertIn('href="style.css"', html)
        self.assertIn('src="app.js"', html)
        self.assertNotIn("://", html + style.read_text() + script.read_text())
        self.assertIn('id="board-grid"', html)
        self.assertIn('data-orb="fire"', html)
        self.assertIn('id="planning-controls"', html)
        self.assertIn('id="start-search"', html)
        self.assertIn('id="cancel-search"', html)
        self.assertIn('id="approve-route"', html)
        self.assertIn('id="execute-route"', html)
        self.assertIn('id="stop-execution"', html)
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
        self.assertIn('command("approve_route"', client)
        self.assertIn('command("execute_route"', client)
        self.assertIn('command("stop_execution"', client)
        self.assertIn('command("set_protected_cell"', client)
        self.assertNotIn("adb", client.lower())
        self.assertNotIn("solver", client.lower())
        styles = style.read_text()
        for marker in (".board-cell.unknown", ".board-cell.selected", ".board-cell.protected",
                       ".cell-badge", ".cell-badge.locked"):
            self.assertIn(marker, styles)


class EntrypointTests(unittest.TestCase):
    def test_gui_keeps_tk_and_webview_requires_explicit_flag(self):
        import pad_router
        with patch.object(sys, "argv", ["pad_router.py", "--gui"]), \
                patch("pad_router_gui.main") as tk_main, \
                patch("pad_router_webview.main") as webview_main:
            pad_router.main()
        tk_main.assert_called_once_with()
        webview_main.assert_not_called()

        with patch.object(sys, "argv", ["pad_router.py", "--webview"]), \
                patch("pad_router_gui.main") as tk_main, \
                patch("pad_router_webview.main") as webview_main:
            pad_router.main()
        webview_main.assert_called_once_with()
        tk_main.assert_not_called()


class ExecuteRouteUiTests(unittest.TestCase):
    @staticmethod
    def _ready_controller(model, executor):
        board = tuple(tuple(Orb("normal", (row + col) % 6 + 1) for col in range(COLS))
                      for row in range(ROWS))
        source = (144, 120, bytes((60, 40, 20, 255)) * (144 * 120))
        controller = BoardInspectionController(
            detector=lambda *_args: board, capture=lambda _serial: source,
            executor=executor, model=model,
        )
        controller.capture_device("test-device")
        controller.confirm_board()
        controller.set_rule_profile(RuleProfile("safe"))
        controller.evaluate_manual_route(((0, 0),))
        return controller

    def test_execute_learns_before_sending_without_a_second_confirmation(self):
        import tkinter.messagebox as messagebox

        calls = []
        with tempfile.TemporaryDirectory() as directory:
            model = OrbPrototypeModel(Path(directory) / "prototypes.json")

            def executor(*args, **kwargs):
                calls.append(len(model.samples))
                return True

            controller = self._ready_controller(model, executor)
            app = object.__new__(BoardInspectionApp)
            app.controller = controller
            app._serial = SimpleNamespace(get=lambda: "test-device")
            app._manual_route = []
            app._apply = lambda action: action()

            with patch.object(messagebox, "askyesno", side_effect=AssertionError("unexpected confirmation")):
                app.execute_route()

        self.assertEqual(calls, [30])

    def test_learning_failure_reports_inline_and_does_not_send(self):
        calls = []
        status = []
        with tempfile.TemporaryDirectory() as directory:
            model = OrbPrototypeModel(Path(directory) / "prototypes.json")
            controller = self._ready_controller(model, lambda *args, **kwargs: calls.append(args))
            app = object.__new__(BoardInspectionApp)
            app.controller = controller
            app._serial = SimpleNamespace(get=lambda: "test-device")
            app._manual_route = []
            app._status = SimpleNamespace(set=status.append)
            app._apply = lambda action: action()

            with patch.object(model, "learn", side_effect=OSError("prototype write failed")):
                app.execute_route()

        self.assertEqual(calls, [])
        self.assertTrue(status)
        self.assertIn("執行前學習失敗", status[-1])

    def test_execute_still_blocks_without_route_or_device(self):
        errors = []
        accepted = []
        state = SimpleNamespace(route_evaluation=None)
        app = object.__new__(BoardInspectionApp)
        app.controller = SimpleNamespace(
            state=state, accept_current_board=lambda: accepted.append(True),
        )
        app._serial = SimpleNamespace(get=lambda: "")
        app._show_error = errors.append
        app._manual_route = []

        app.execute_route()
        self.assertEqual(accepted, [])
        self.assertEqual(len(errors), 1)

        state.route_evaluation = SimpleNamespace(execution_eligible=True)
        app.execute_route()
        self.assertEqual(accepted, [])
        self.assertEqual(len(errors), 2)


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

        def executor(*args, on_verification):
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

        def executor(*args, on_verification):
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

        def executor(*args, on_verification):
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

        def executor(*args, on_verification):
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

        def executor(*args, on_verification):
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

    def test_gui_start_stop_delegates_worker_updates_through_after(self):
        threads = []
        callbacks = []
        displays = []
        calls = []

        class DeferredThread:
            def __init__(self, target, daemon):
                self.target = target
                self.daemon = daemon
                threads.append(self)

            def start(self):
                pass

        state = SimpleNamespace(
            route_evaluation=SimpleNamespace(execution_eligible=True),
            status="ready",
        )

        def run(serial, stop_event, on_state):
            calls.append((serial, stop_event))
            state.status = "連續執行中"
            on_state(state)
            return "連續執行已由使用者停止"

        app = object.__new__(BoardInspectionApp)
        app.controller = SimpleNamespace(state=state, execute_continuously=run)
        app.root = SimpleNamespace(after=lambda _delay, callback: callbacks.append(callback))
        app._serial = SimpleNamespace(get=lambda: "test-device")
        app._manual_route = []
        app._auto_search_generation = 0
        app._status = SimpleNamespace(set=lambda _value: None)
        app._execute_button = SimpleNamespace(configure=lambda **_kwargs: None)
        app._continuous_button = SimpleNamespace(configure=lambda **_kwargs: None)
        app._display = displays.append
        app._show_error = self.fail

        with patch("pad_router_gui.threading.Thread", DeferredThread):
            app.start_continuous_execution()
            self.assertEqual(calls, [])
            threads[0].target()

        self.assertEqual(len(calls), 1)
        self.assertEqual(displays, [])
        app.stop_continuous_execution()
        self.assertTrue(calls[0][1].is_set())
        self.assertEqual(len(callbacks), 1)
        callbacks[0]()
        self.assertEqual(displays, [state])

    def test_active_continuous_run_blocks_file_dialog_actions(self):
        statuses = []
        app = object.__new__(BoardInspectionApp)
        app._continuous_stop = threading.Event()
        app._status = SimpleNamespace(set=statuses.append)

        with (patch("tkinter.filedialog.askopenfilename",
                    side_effect=AssertionError("dialog opened")),
              patch("tkinter.filedialog.asksaveasfilename",
                    side_effect=AssertionError("dialog opened"))):
            app.open_png()
            app.save_profile()

        self.assertEqual(len(statuses), 2)
        self.assertTrue(all("先停止" in status for status in statuses))


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


class RecognitionRetryUiTests(unittest.TestCase):
    def test_retry_selector_is_readonly_and_updates_controller(self):
        import tkinter as tk

        controller = BoardInspectionController()
        root = tk.Tk()
        root.withdraw()
        try:
            app = BoardInspectionApp(root, controller)
            self.assertEqual(
                tuple(app._recognition_attempts_box.cget("values")),
                ("1", "2", "3", "4", "5"),
            )
            self.assertEqual(str(app._recognition_attempts_box.cget("state")), "readonly")
            self.assertEqual(controller.max_recognition_attempts, 2)

            app._recognition_attempts.set("4")

            self.assertEqual(controller.max_recognition_attempts, 4)
        finally:
            root.destroy()


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


class RecognitionRetryUiTests(unittest.TestCase):
    def test_retry_selector_is_readonly_and_updates_controller(self):
        import tkinter as tk

        controller = BoardInspectionController()
        root = tk.Tk()
        root.withdraw()
        try:
            app = BoardInspectionApp(root, controller)
            self.assertEqual(
                tuple(app._recognition_attempts_box.cget("values")),
                ("1", "2", "3", "4", "5"),
            )
            self.assertEqual(str(app._recognition_attempts_box.cget("state")), "readonly")
            self.assertEqual(controller.max_recognition_attempts, 2)

            app._recognition_attempts.set("4")

            self.assertEqual(controller.max_recognition_attempts, 4)
        finally:
            root.destroy()


class BoardCalibrationTests(unittest.TestCase):
    def test_standard_board_must_fit_image(self):
        BoardCalibration(0, 0, 1).validate(6, 5)
        with self.assertRaises(ValueError):
            BoardCalibration(1, 0, 1).validate(6, 5)
        with self.assertRaises(ValueError):
            BoardCalibration(0, 0, 0).validate(6, 5)


class SearchButtonTests(unittest.TestCase):
    def test_search_button_displays_controller_state_not_search_result(self):
        state = object()
        result = object()
        received = []
        app = object.__new__(BoardInspectionApp)
        app.controller = SimpleNamespace(state=state, search_qualifying_route=lambda options: received.append(options) or result)
        app._manual_route = []
        app._search_attempts = SimpleNamespace(get=lambda: "5")
        app._search_steps = SimpleNamespace(get=lambda: "50")
        app._search_seed = SimpleNamespace(get=lambda: "0")
        app._cascade = SimpleNamespace(get=lambda: "只計轉珠直接消除")
        displayed = []
        app._apply = lambda action: displayed.append(action())

        app.search_route()

        self.assertIs(displayed[0], state)
        self.assertEqual(received[0].max_steps, 50)
        self.assertFalse(received[0].cascade)


class ReviewModeTests(unittest.TestCase):
    def test_clicking_a_rejected_cell_selects_it_without_starting_a_route(self):
        app = object.__new__(BoardInspectionApp)
        app.controller = SimpleNamespace(state=SimpleNamespace(uncertain_cells=((0, 0),)))
        app._cell_at = lambda _event: (0, 0)
        app._selected_label = SimpleNamespace(set=lambda _value: None)
        app._manual_route = [(1, 1)]
        app._dragging_route = True
        app._display = lambda _state: None

        app.route_press(SimpleNamespace())

        self.assertEqual(app._selected_cell, (0, 0))
        self.assertEqual(app._manual_route, [])
        self.assertFalse(app._dragging_route)


class CorrectionModeTests(unittest.TestCase):
    def test_ready_correction_mode_selects_any_cell_and_calls_existing_override(self):
        state = SimpleNamespace(board=object(), uncertain_cells=())
        corrections = []
        app = object.__new__(BoardInspectionApp)
        app.controller = SimpleNamespace(
            state=state,
            correct_cell=lambda row, col, value: corrections.append((row, col, value)) or state,
        )
        app._correction_mode = False
        app._manual_route = [(1, 1)]
        app._dragging_route = True
        app._cell_at = lambda _event: (0, 0)
        app._selected_label = SimpleNamespace(set=lambda _value: None)
        app._display = lambda _state: None
        app._enhanced = SimpleNamespace(get=lambda: False)
        app._locked = SimpleNamespace(get=lambda: False)
        app._apply = lambda action: action()

        app.toggle_correction_mode()
        app.route_press(SimpleNamespace())
        app.answer_selected("fire")

        self.assertTrue(app._correction_mode)
        self.assertEqual(app._selected_cell, (0, 0))
        self.assertEqual(app._manual_route, [])
        self.assertFalse(app._dragging_route)
        self.assertEqual(corrections, [(0, 0, "fire")])


class ReadyRouteModeTests(unittest.TestCase):
    def test_ready_route_mode_still_starts_a_route_drag(self):
        app = object.__new__(BoardInspectionApp)
        app.controller = SimpleNamespace(state=SimpleNamespace(uncertain_cells=()))
        app._correction_mode = False
        app._manual_route = []
        app._dragging_route = False
        app._cell_at = lambda _event: (0, 0)
        app._selected_label = SimpleNamespace(set=lambda _value: None)
        app._display = lambda _state: None

        app.route_press(SimpleNamespace())

        self.assertEqual(app._manual_route, [(0, 0)])
        self.assertTrue(app._dragging_route)

    def test_route_drag_cannot_start_or_extend_through_protected_cell(self):
        state = SimpleNamespace(uncertain_cells=(), protected_cell=(0, 1))
        app = object.__new__(BoardInspectionApp)
        app.controller = SimpleNamespace(state=state)
        app._correction_mode = False
        app._manual_route = []
        app._dragging_route = False
        app._selected_label = SimpleNamespace(set=lambda _value: None)
        app._display = lambda _state: None
        app._cell_at = lambda event: event.cell

        app.route_press(SimpleNamespace(cell=(0, 1)))
        self.assertEqual(app._manual_route, [])
        self.assertFalse(app._dragging_route)

        app.route_press(SimpleNamespace(cell=(0, 0)))
        app.route_motion(SimpleNamespace(cell=(0, 1)))
        self.assertEqual(app._manual_route, [(0, 0)])


class ProtectedCellUiTests(unittest.TestCase):
    def test_capture_installs_source_then_schedules_search_off_the_ui_thread(self):
        board = tuple(tuple(Orb("normal", 1) for _ in range(COLS)) for _ in range(ROWS))
        profile = RuleProfile("preset")
        state = SimpleNamespace(
            source_name="test-device", pixels=b"pixels", board=board, confirmed_board=board,
            confirmed=True, uncertain_cells=(), rule_profile=profile, protected_cell=None,
        )
        captures = []
        applied = []
        callbacks = []
        threads = []
        result = SimpleNamespace(candidate=SimpleNamespace(route=((0, 0),)))

        class DeferredThread:
            def __init__(self, target, daemon):
                self.target = target
                self.daemon = daemon
                threads.append(self)

            def start(self):
                pass

        controller = SimpleNamespace(
            state=state,
            capture_device=lambda serial, auto_search=True: (
                captures.append((serial, auto_search)) or state),
            _apply_search_result=lambda value, options: applied.append((value, options)),
        )
        app = object.__new__(BoardInspectionApp)
        app.controller = controller
        app.root = SimpleNamespace(after=lambda _delay, callback: callbacks.append(callback))
        app._serial = SimpleNamespace(get=lambda: "test-device")
        app._manual_route = []
        app._auto_search_generation = 0
        app._display = lambda _state: None

        with (patch("pad_router_gui.threading.Thread", DeferredThread),
              patch("pad_router_gui.search_qualifying_route", return_value=result) as search):
            app.capture_device()
            self.assertEqual(captures, [("test-device", False)])
            self.assertEqual(len(threads), 1)
            self.assertEqual(len(callbacks), 1)
            search.assert_not_called()
            self.assertEqual(applied, [])

            threads[0].target()
            search.assert_called_once()
            self.assertEqual(applied, [])
            self.assertEqual(len(callbacks), 1)

            callbacks[0]()
            self.assertEqual(applied, [(result, RouteSearchOptions())])

            app.capture_device()
            threads[1].target()
            app._apply(lambda: state)  # A later manual/search/execute-style state action.
            callbacks[1]()

        self.assertEqual(applied, [(result, RouteSearchOptions())])

    def test_protect_and_clear_actions_use_the_selected_coordinate(self):
        calls = []
        state = SimpleNamespace(board=None, uncertain_cells=(), rule_profile=None)
        app = object.__new__(BoardInspectionApp)
        app.controller = SimpleNamespace(
            state=state,
            set_protected_cell=lambda cell, recompute=True: calls.append((cell, recompute)) or state,
        )
        app._selected_cell = (2, 3)
        app._manual_route = [(0, 0)]
        app._dragging_route = True
        app._apply = lambda action: action()

        app.protect_selected_cell()
        app.clear_protected_cell()

        self.assertEqual(calls, [((2, 3), False), (None, False)])
        self.assertEqual(app._manual_route, [])
        self.assertFalse(app._dragging_route)

    def test_protected_cell_is_marked_and_capture_button_lives_in_right_controls(self):
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        try:
            board = tuple(tuple(Orb("normal", 1) for _ in range(COLS)) for _ in range(ROWS))
            source = (144, 120, bytes((60, 40, 20, 255)) * (144 * 120))
            controller = BoardInspectionController(
                detector=lambda *_args: board, capture=lambda _serial: source,
            )
            app = BoardInspectionApp(root, controller)
            controller.capture_device("test-device")
            app._selected_cell = (0, 0)
            app._display(controller.set_protected_cell((0, 0)))

            self.assertTrue(app.board.find_withtag("protected"))
            self.assertTrue(app.source.find_withtag("protected"))
            self.assertIn("保護格", app._protected_label.get())
            self.assertIn("第 1 列、第 1 行", app._protected_label.get())
            self.assertEqual(app._capture_button.winfo_parent(), app.board.winfo_parent())

            def buttons(widget):
                return [child for child in widget.winfo_children()
                        if child.winfo_class() == "TButton"] + [button for child in widget.winfo_children()
                                                               for button in buttons(child)]

            self.assertEqual(sum(button.cget("text") == "擷取畫面" for button in buttons(root)), 1)
        finally:
            root.destroy()


class ManualRouteButtonTests(unittest.TestCase):
    def test_manual_route_button_displays_controller_state_not_evaluation(self):
        state = SimpleNamespace(board=object(), rule_profile=object())
        result = object()
        received = []
        app = object.__new__(BoardInspectionApp)
        app.controller = SimpleNamespace(
            state=state, evaluate_manual_route=lambda route, cascade=True: received.append(cascade) or result)
        app._manual_route = [(0, 0)]
        app._dragging_route = True
        app._cascade = SimpleNamespace(get=lambda: "只計轉珠直接消除")
        displayed = []
        app._apply = lambda action: displayed.append(action())

        app.route_release(None)

        self.assertIs(displayed[0], state)
        self.assertEqual(received, [False])


class DisplayScaleTests(unittest.TestCase):
    def test_landscape_screenshot_fits_width_without_stretching(self):
        scale, width, height = _fit_scale(1920, 1080, 650, 700)

        self.assertAlmostEqual(scale, 650 / 1920)
        self.assertEqual((width, height), (650, 365))

    def test_portrait_screenshot_fits_height_without_stretching(self):
        scale, width, height = _fit_scale(1080, 1920, 650, 700)

        self.assertAlmostEqual(scale, 700 / 1920)
        self.assertEqual((width, height), (393, 700))

    def test_display_scale_does_not_change_calibration_coordinates(self):
        calibration = BoardCalibration(left=35, top=120, cell=180)
        point = calibration.to_grid().point(2, 3)

        _fit_scale(1920, 1080, 650, 700)

        self.assertEqual(calibration.to_grid().point(2, 3), point)


class ScreenshotPhotoTests(unittest.TestCase):
    def test_bgra_screenshot_loads_in_tk(self):
        import tkinter as tk

        root = tk.Tk()
        try:
            image = _photo_from_screenshot((1, 1, bytes((60, 40, 20, 255))), tk)
            self.assertEqual((image.width(), image.height()), (1, 1))
        finally:
            root.destroy()


class ConditionColorUiTests(unittest.TestCase):
    def test_untyped_shape_conditions_restore_as_untyped_controls(self):
        self.assertEqual(
            BoardInspectionApp._condition_selection(LeaderCondition.shape("l")),
            ("L 型", "不指定"),
        )
        self.assertEqual(
            BoardInspectionApp._condition_selection(LeaderCondition.connected_orb_count(4, exact=True)),
            ("4 顆消除", "不指定"),
        )

    def test_shape_can_be_left_untyped_from_the_gui(self):
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        try:
            app = BoardInspectionApp(root)
            app._condition_choices[0].set("十字型")

            self.assertIn("不指定", app._condition_color_boxes[0].cget("values"))
            self.assertEqual(app._condition_colors[0].get(), "不指定")
            self.assertEqual(app.controller.state.rule_profile.condition_groups[0].conditions[0].value,
                             "cross")
        finally:
            root.destroy()

    def test_shape_defaults_to_untyped_but_keeps_the_colour_choice_available(self):
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        try:
            app = BoardInspectionApp(root)
            self.assertEqual(app._condition_colors[0].get(), "不指定")
            self.assertEqual(str(app._condition_color_boxes[0].cget("state")), "disabled")
            app._condition_choices[0].set("十字型")
            self.assertEqual(app._condition_colors[0].get(), "不指定")
            self.assertEqual(str(app._condition_color_boxes[0].cget("state")), "readonly")
            app._condition_choices[0].set("不限（以最大 Combo 為主）")
            self.assertEqual(app._condition_colors[0].get(), "不指定")
            self.assertEqual(str(app._condition_color_boxes[0].cget("state")), "disabled")
        finally:
            root.destroy()


class AutoProfileUiTests(unittest.TestCase):
    def test_rule_changes_apply_immediately_without_create_apply_buttons(self):
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        try:
            app = BoardInspectionApp(root)
            self.assertFalse(hasattr(app, "create_profile"))
            self.assertFalse(hasattr(app, "apply_profile"))

            app._condition_choices[0].set("至少 5 Combo")
            self.assertIsNotNone(app.controller.state.rule_profile)
            self.assertEqual(app.controller.state.rule_profile.name, "至少 5 Combo")
            self.assertIn("已套用", app._profile_label.get())

            app._search_attempts.set("10")
            self.assertEqual(app.controller.state.rule_profile.name, "至少 5 Combo")
        finally:
            root.destroy()

    def test_profile_load_and_save_keep_controller_and_controls_in_sync(self):
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        try:
            app = BoardInspectionApp(root)
            app._condition_choices[0].set("十字型")
            app._condition_colors[0].set("暗")
            app._condition_operator.set("任一符合")
            app._hazard_policy.set("允許危害珠")
            app._external_condition.set("HP 條件已確認")
            saved_profile = app.controller.state.rule_profile

            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "profile.json"
                with patch("tkinter.filedialog.asksaveasfilename", return_value=str(path)):
                    app.save_profile()
                self.assertTrue(path.exists())

                app._condition_choices[0].set("至少 3 Combo")
                with patch("tkinter.filedialog.askopenfilename", return_value=str(path)):
                    app.load_profile()

            self.assertEqual(app.controller.state.rule_profile, saved_profile)
            self.assertEqual(app._condition_choices[0].get(), "十字型")
            self.assertEqual(app._condition_colors[0].get(), "暗")
            self.assertEqual(app._condition_operator.get(), "任一符合")
            self.assertEqual(app._hazard_policy.get(), "允許危害珠")
            self.assertEqual(app._external_condition.get(), "HP 條件已確認")
            self.assertIn("已套用", app._profile_label.get())
        finally:
            root.destroy()


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
