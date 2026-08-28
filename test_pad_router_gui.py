import os
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from types import SimpleNamespace

from pad_router import (COLS, ROWS, ConditionGroup, LeaderCondition, Orb, PlayVerification,
                        RouteSearchOptions, RuleProfile, expected_board_after_path)
from pad_router_gui import (_photo_from_screenshot, BoardCalibration, BoardInspectionApp,
                            BoardInspectionController, decode_png, rule_profile_from_selections)


def png_bytes(width=12, height=10):
    rows = []
    for _ in range(height):
        rows.append(b"\x00" + bytes((20, 40, 60, 255)) * width)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b"")


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
        self.assertEqual(len(self.detected), 1)

    def test_cell_correction_obtains_confirmed_board(self):
        self.controller.load_png(self.path)
        with self.assertRaises(ValueError):
            self.controller.confirm_board()
        state = self.controller.correct_cell(0, 0, "fire+")
        self.assertFalse(state.uncertain_cells)
        self.assertTrue(state.overlay[0]["uncertain"])
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
