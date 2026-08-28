import json
import os
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from pad_router import (COLS, ROWS, CellFeatures, ConditionGroup, Grid, LeaderCondition, Orb, PlayVerification,
                        RouteSearchOptions, RuleProfile, _cell_features, _normal_color, detect_board_pixels,
                        expected_board_after_path)


from pad_router_gui import (_fit_scale, _photo_from_screenshot, BoardCalibration, BoardInspectionApp,
                            BoardInspectionController, OrbPrototypeModel, decode_png,
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
    def test_max_combo_does_not_offer_a_colour_until_a_shape_is_selected(self):
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        try:
            app = BoardInspectionApp(root)
            self.assertEqual(app._condition_colors[0].get(), "不指定")
            self.assertEqual(str(app._condition_color_boxes[0].cget("state")), "disabled")
            app._condition_choices[0].set("十字型")
            self.assertEqual(app._condition_colors[0].get(), "火")
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
