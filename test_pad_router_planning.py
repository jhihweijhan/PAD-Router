import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pad_router

from pad_router import (
    COLS,
    ROWS,
    ConditionGroup,
    ExternalCondition,
    LeaderCondition,
    Orb,
    RouteSearchOptions,
    RuleProfile,
    evaluate_manual_route,
    load_rule_profile,
    search_qualifying_route,
)
from pad_router_gui import BoardInspectionController


class RuleProfileTests(unittest.TestCase):
    def test_profile_round_trips_groups_external_conditions_and_hazard_policy(self):
        profile = RuleProfile(
            "two leaders",
            condition_groups=(
                ConditionGroup.all_of((LeaderCondition("combo_minimum", minimum=2),)),
                ConditionGroup.any_of((LeaderCondition("attribute", value="fire"),
                                       LeaderCondition("attribute", value="water"))),
            ),
            external_conditions=(ExternalCondition("skill ready", confirmed=True),),
            hazard_policy="avoid",
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            profile.save(path)
            restored = load_rule_profile(path)

        self.assertEqual(restored, profile)


class ManualRouteEvaluationTests(unittest.TestCase):
    def test_search_never_generates_a_route_through_the_protected_cell(self):
        board = tuple(tuple((row + col) % 6 + 1 for col in range(COLS)) for row in range(ROWS))
        profile = RuleProfile("protected", condition_groups=(ConditionGroup.all_of((
            LeaderCondition.combo_minimum(99),
        )),))
        protected = (0, 0)
        manual_evaluate = pad_router.evaluate_manual_route
        evaluate = pad_router._evaluate_expected_route
        evaluated_routes = []

        def reject_protected_manual(board, route, *args, **kwargs):
            route = tuple(route)
            self.assertNotIn(protected, route)
            evaluated_routes.append(route)
            return manual_evaluate(board, route, *args, **kwargs)

        def reject_protected(route, *args, **kwargs):
            self.assertNotIn(protected, route)
            evaluated_routes.append(route)
            return evaluate(route, *args, **kwargs)

        with (patch("pad_router.evaluate_manual_route", side_effect=reject_protected_manual),
              patch("pad_router._evaluate_expected_route", side_effect=reject_protected)):
            result = search_qualifying_route(
                board, profile,
                RouteSearchOptions(attempts=1, seed=0, min_steps=0, max_steps=1),
                confirmed=True, protected_cell=protected,
            )

        self.assertNotIn(protected, result.candidate.route)
        self.assertEqual(
            {route[0] for route in evaluated_routes},
            {(row, col) for row in range(ROWS) for col in range(COLS)} - {protected},
        )

    def test_search_rejects_an_invalid_protected_cell(self):
        board = tuple(tuple((row + col) % 6 + 1 for col in range(COLS)) for row in range(ROWS))

        for protected in ((-1, 0), (ROWS, 0), (0, COLS), (0,), (True, 0), "0,0"):
            with self.subTest(protected=protected), self.assertRaises(ValueError):
                search_qualifying_route(
                    board, RuleProfile("open"),
                    RouteSearchOptions(attempts=1, seed=0, min_steps=0, max_steps=0),
                    protected_cell=protected,
                )

    def test_dark_row_search_keeps_combo_candidates_after_the_row_is_formed(self):
        board = (
            (Orb("normal", 4), Orb("normal", 5, enhanced=True), Orb("normal", 2), Orb("normal", 4), Orb("normal", 1), Orb("normal", 5)),
            (Orb("normal", 4), Orb("normal", 3), Orb("normal", 1), Orb("normal", 2), Orb("normal", 6), Orb("normal", 5)),
            (Orb("normal", 6, enhanced=True), Orb("normal", 3), Orb("normal", 6), Orb("normal", 5, enhanced=True), Orb("normal", 2), Orb("normal", 1)),
            (Orb("normal", 3), Orb("normal", 6), Orb("normal", 4), Orb("normal", 3), Orb("normal", 2, enhanced=True), Orb("normal", 4)),
            (Orb("normal", 5), Orb("jammer"), Orb("normal", 4), Orb("normal", 3), Orb("normal", 1), Orb("normal", 5)),
        )
        profile = RuleProfile(
            "dark row", (ConditionGroup.all_of((LeaderCondition.shape("full_row", orb_type="dark"),)),),
            hazard_policy="allow",
        )

        result = search_qualifying_route(
            board, profile,
            RouteSearchOptions(attempts=50, seed=0, min_steps=1, max_steps=100),
            confirmed=True,
        )

        self.assertIsNotNone(result.qualifying_candidate)
        self.assertTrue(result.qualifying_candidate.qualifying)
        self.assertTrue(result.qualifying_candidate.condition_results[0].satisfied)
        self.assertIsNotNone(result.qualifying_candidate.direct_combo_estimate)
        self.assertGreaterEqual(result.qualifying_candidate.direct_combo_count, 1)

    def test_search_gathers_six_scattered_dark_orbs_into_a_full_row(self):
        raw_board = (
            (4, 5, 2, 4, 1, 5),
            (4, 3, 1, 2, 6, 5),
            (6, 3, 6, 5, 2, 1),
            (3, 6, 4, 3, 2, 4),
            (5, "jammer", 4, 3, 1, 5),
        )
        board = tuple(tuple(
            Orb("normal", color, enhanced=(row == 2 and col == 0) or (row == 3 and col == 4))
            if isinstance(color, int) else Orb("jammer")
            for col, color in enumerate(values)
        ) for row, values in enumerate(raw_board))
        profile = RuleProfile(
            "dark row",
            (ConditionGroup.all_of((LeaderCondition.shape("full_row", orb_type="dark"),)),),
            hazard_policy="allow",
        )

        result = search_qualifying_route(
            board, profile,
            RouteSearchOptions(attempts=30, seed=0, min_steps=1, max_steps=60),
            confirmed=True,
        )

        self.assertIsNotNone(result.qualifying_candidate)
        self.assertTrue(result.qualifying_candidate.qualifying)
        self.assertTrue(result.qualifying_candidate.condition_results[0].satisfied)
        self.assertLessEqual(len(result.qualifying_candidate.route) - 1, 60)

    def test_search_prioritizes_a_dark_full_row_before_combo_count(self):
        jammer = Orb("jammer")
        board = (
            (4, 2, 6, 5, 3, 5),
            (6, 1, 3, 1, 4, 1),
            (5, 2, 5, 2, 5, 2),
            (3, 2, 1, 3, 4, 5),
            (5, jammer, 5, 2, 3, 5),
        )
        profile = RuleProfile(
            "dark row",
            (ConditionGroup.all_of((LeaderCondition.shape("full_row", orb_type="dark"),)),),
            hazard_policy="allow",
        )

        result = search_qualifying_route(
            board, profile,
            RouteSearchOptions(attempts=50, seed=0, min_steps=1, max_steps=50),
            confirmed=True,
        )

        self.assertIsNotNone(result.qualifying_candidate)
        self.assertTrue(result.qualifying_candidate.qualifying)
        self.assertLessEqual(len(result.qualifying_candidate.route) - 1, 50)
        self.assertGreaterEqual(result.qualifying_candidate.combo_count, 2)

    def test_dark_row_search_maximizes_combos_on_reported_board(self):
        board = (
            (Orb("normal", 4), Orb("normal", 2), Orb("normal", 6), Orb("normal", 5, enhanced=True), Orb("normal", 3), Orb("normal", 5)),
            (Orb("normal", 6, enhanced=True), Orb("normal", 1), Orb("normal", 3), Orb("normal", 1), Orb("normal", 4), Orb("normal", 1)),
            (Orb("normal", 5, enhanced=True), Orb("normal", 2), Orb("normal", 5, enhanced=True), Orb("normal", 2), Orb("normal", 5, enhanced=True), Orb("normal", 2)),
            (Orb("normal", 3), Orb("normal", 2), Orb("normal", 1), Orb("normal", 3), Orb("normal", 4), Orb("normal", 5, enhanced=True)),
            (Orb("normal", 5), Orb("jammer"), Orb("normal", 5), Orb("normal", 2), Orb("normal", 3), Orb("normal", 5)),
        )
        profile = RuleProfile(
            "dark row",
            (ConditionGroup.all_of((LeaderCondition.shape("full_row", orb_type="dark"),)),),
            hazard_policy="allow",
        )

        result = search_qualifying_route(
            board, profile,
            RouteSearchOptions(attempts=50, seed=1, min_steps=1, max_steps=70),
            confirmed=True,
        )

        self.assertIsNotNone(result.qualifying_candidate)
        self.assertTrue(result.qualifying_candidate.qualifying)
        self.assertLessEqual(len(result.qualifying_candidate.route) - 1, 70)
        self.assertIsNotNone(result.qualifying_candidate.direct_combo_estimate)
        self.assertGreaterEqual(result.qualifying_candidate.direct_combo_count, 1)

    def test_search_is_reproducible_and_returns_a_qualifying_candidate(self):
        board = ((1, 1, 1, 2, 2, 2), (3, 4, 5, 6, 3, 4),
                 (4, 5, 6, 3, 4, 5), (5, 6, 3, 4, 5, 6),
                 (6, 3, 4, 5, 6, 3))
        profile = RuleProfile("search", condition_groups=(ConditionGroup.all_of((
            LeaderCondition.combo_minimum(2),
        )),))
        options = RouteSearchOptions(attempts=40, seed=17, min_steps=0, max_steps=4)

        first = search_qualifying_route(board, profile, options, confirmed=True)
        second = search_qualifying_route(board, profile, options, confirmed=True)

        self.assertEqual(first, second)
        self.assertIsNotNone(first.qualifying_candidate)
        self.assertTrue(first.qualifying_candidate.execution_eligible)


    def test_sparse_reward_fixture_prefers_a_direct_combo_candidate(self):
        board = (
            (4, 2, 1, 4, 5, 6),
            (6, 3, 2, 4, 5, 5),
            (2, 1, 2, 4, 2, 2),
            (1, 3, 2, 6, 2, 5),
            (5, 5, 4, 3, 2, 6),
        )
        profile = RuleProfile(
            "sparse", condition_groups=(ConditionGroup.all_of((
                LeaderCondition.combo_minimum(1),
            )),)
        )
        options = RouteSearchOptions(attempts=2, seed=0, min_steps=1, max_steps=6)
        first = search_qualifying_route(board, profile, options, confirmed=True)
        second = search_qualifying_route(board, profile, options, confirmed=True)

        self.assertEqual(first, second)
        self.assertIsNotNone(first.qualifying_candidate)
        self.assertTrue(first.qualifying_candidate.qualifying)
        self.assertIsNotNone(first.qualifying_candidate.direct_combo_estimate)
        self.assertGreaterEqual(first.qualifying_candidate.direct_combo_count, 1)
    def test_search_ranks_qualifying_candidates_by_combos_steps_then_route_order(self):
        board = ((1, 1, 1, 2, 2, 2), (3, 4, 5, 6, 3, 4),
                 (4, 5, 6, 3, 4, 5), (5, 6, 3, 4, 5, 6),
                 (6, 3, 4, 5, 6, 3))
        profile = RuleProfile("ranked", condition_groups=(ConditionGroup.all_of((
            LeaderCondition.combo_minimum(2),
        )),))

        result = search_qualifying_route(
            board, profile, RouteSearchOptions(attempts=20, seed=0, min_steps=0, max_steps=4), confirmed=True
        )

        self.assertIsNotNone(result.qualifying_candidate)
        self.assertEqual(result.qualifying_candidate.route, ((2, 4),))
        self.assertEqual(result.qualifying_candidate.combo_count, 2)

    def test_condition_groups_and_external_conditions_are_table_driven(self):
        board = ((1, 1, 1, 2, 2, 2), (3, 4, 5, 6, 3, 4),
                 (4, 5, 6, 3, 4, 5), (5, 6, 3, 4, 5, 6),
                 (6, 3, 4, 5, 6, 3))
        cases = (
            ("all-of passes", ConditionGroup.all_of((
                LeaderCondition.combo_minimum(2), LeaderCondition.attribute("fire"))),
             ExternalCondition("skill", confirmed=True), True, True),
            ("all-of fails", ConditionGroup.all_of((
                LeaderCondition.combo_minimum(2), LeaderCondition.attribute("dark"))),
             ExternalCondition("skill", confirmed=True), False, False),
            ("any-of passes", ConditionGroup.any_of((
                LeaderCondition.attribute("dark"), LeaderCondition.attribute("fire"))),
             ExternalCondition("skill", confirmed=True), True, True),
            ("any-of fails", ConditionGroup.any_of((
                LeaderCondition.attribute("dark"), LeaderCondition.attribute("heart"))),
             ExternalCondition("skill", confirmed=True), False, False),
            ("required external unconfirmed", ConditionGroup.all_of((
                LeaderCondition.combo_minimum(2),)),
             ExternalCondition("skill", confirmed=False, required=True), True, False),
            ("optional external unconfirmed", ConditionGroup.all_of((
                LeaderCondition.combo_minimum(2),)),
             ExternalCondition("skill", confirmed=False, required=False), True, True),
        )

        for name, group, external, group_satisfied, qualifies in cases:
            with self.subTest(name=name):
                result = evaluate_manual_route(
                    board, ((0, 0),), RuleProfile("leaders", (group,), (external,)), confirmed=True
                )
                self.assertEqual(result.combo_count, 2)
                self.assertEqual(result.group_results[0].satisfied, group_satisfied)
                self.assertEqual(result.qualifying, qualifies)
                self.assertEqual(result.execution_eligible, qualifies)

    def test_shape_conditions_cover_the_gui_presets(self):
        def board_for(points):
            return tuple(tuple(1 if (row, col) in points else (row + col) % 5 + 2
                               for col in range(COLS)) for row in range(ROWS))

        cases = (
            ("色珠一橫列", {(0, col) for col in range(COLS)}, LeaderCondition.shape("full_row")),
            ("9 顆正方形", {(row, col) for row in range(3) for col in range(3)}, LeaderCondition.shape("box_3x3")),
            ("十字型", {(1, 2), (0, 2), (2, 2), (1, 1), (1, 3)}, LeaderCondition.shape("cross")),
            ("4 顆消除", {(0, col) for col in range(4)}, LeaderCondition.connected_orb_count(4, exact=True)),
            ("L 型", {(0, 0), (1, 0), (2, 0), (2, 1), (2, 2)}, LeaderCondition.shape("l")),
            ("T 型", {(0, 0), (0, 1), (0, 2), (1, 1), (2, 1)}, LeaderCondition.shape("t")),
        )

        for name, points, condition in cases:
            with self.subTest(name=name):
                profile = RuleProfile(name, (ConditionGroup.all_of((condition,)),))
                result = evaluate_manual_route(board_for(points), ((4, 5),), profile, confirmed=True)
                self.assertTrue(result.qualifying)

    def test_untyped_exact_four_orb_condition_accepts_any_matching_component(self):
        board = (
            (1, 1, 1, 1, 2, 2),
            (2, 2, 2, 2, 2, 2),
            (3, 4, 5, 6, 3, 4),
            (4, 5, 6, 3, 4, 5),
            (5, 6, 3, 4, 5, 6),
        )
        profile = RuleProfile("four orbs", (ConditionGroup.all_of((
            LeaderCondition.connected_orb_count(4, exact=True),
        )),))

        result = evaluate_manual_route(board, ((4, 5),), profile, confirmed=True)

        self.assertTrue(result.qualifying)
        self.assertTrue(result.condition_results[0].satisfied)

        typed = RuleProfile("four fire orbs", (ConditionGroup.all_of((
            LeaderCondition.connected_orb_count(4, orb_type="fire", exact=True),
        )),))
        self.assertTrue(evaluate_manual_route(board, ((4, 5),), typed, confirmed=True).qualifying)
        water_typed = RuleProfile("six water orbs", (ConditionGroup.all_of((
            LeaderCondition.connected_orb_count(4, orb_type="water", exact=True),
        )),))
        self.assertFalse(evaluate_manual_route(board, ((4, 5),), water_typed, confirmed=True).qualifying)

    def test_search_finds_untyped_l_and_cross_shape_candidates(self):
        cases = (
            (
                "l",
                (
                    (1, 3, 2, 6, 3, 4),
                    (1, 3, 4, 6, 3, 2),
                    (2, 1, 1, 3, 4, 6),
                    (4, 2, 4, 1, 4, 1),
                    (5, 2, 5, 3, 6, 3),
                ),
            ),
            (
                "cross",
                (
                    (1, 3, 1, 5, 2, 5),
                    (2, 1, 2, 1, 4, 1),
                    (3, 6, 1, 4, 3, 4),
                    (2, 5, 2, 5, 6, 1),
                    (5, 5, 4, 3, 6, 5),
                ),
            ),
        )

        for shape, board in cases:
            with self.subTest(shape=shape):
                profile = RuleProfile(shape, (ConditionGroup.all_of((
                    LeaderCondition.shape(shape),
                )),))
                result = search_qualifying_route(
                    board, profile,
                    RouteSearchOptions(attempts=1, seed=3, min_steps=1, max_steps=12),
                    confirmed=True,
                )

                self.assertIsNotNone(result.qualifying_candidate)
                self.assertTrue(result.qualifying_candidate.condition_results[0].satisfied)

    def test_search_prefers_an_untyped_shape_over_an_alternate_qualifier(self):
        board = (
            (1, 3, 1, 5, 2, 5),
            (2, 1, 2, 1, 4, 1),
            (3, 6, 1, 4, 3, 4),
            (2, 5, 2, 5, 6, 1),
            (5, 5, 4, 3, 6, 5),
        )
        profile = RuleProfile("shape preference", (ConditionGroup.any_of((
            LeaderCondition.shape("cross"),
            LeaderCondition.combo_minimum(1),
        )),))

        result = search_qualifying_route(
            board, profile,
            RouteSearchOptions(attempts=1, seed=3, min_steps=1, max_steps=12),
            confirmed=True,
        )

        self.assertIsNotNone(result.qualifying_candidate)
        self.assertTrue(result.qualifying_candidate.condition_results[0].satisfied)

    def test_shape_condition_requires_the_selected_orb_colour(self):
        fire_row = ((1,) * COLS,) + tuple(tuple((row + col) % 5 + 2 for col in range(COLS))
                                           for row in range(1, ROWS))
        water_row = ((2,) * COLS,) + fire_row[1:]
        profile = RuleProfile("火一橫列", (ConditionGroup.all_of((
            LeaderCondition.shape("full_row", orb_type="fire"),
        )),))

        self.assertTrue(evaluate_manual_route(fire_row, ((4, 5),), profile, confirmed=True).qualifying)
        self.assertFalse(evaluate_manual_route(water_row, ((4, 5),), profile, confirmed=True).qualifying)

    def test_full_row_condition_allows_connected_extra_orbs(self):
        board = ((1,) * COLS, (1, 3, 4, 5, 6, 2),
                 (2, 3, 4, 5, 6, 2), (3, 4, 5, 6, 2, 3), (4, 5, 6, 2, 3, 4))
        profile = RuleProfile("火一橫列", (ConditionGroup.all_of((
            LeaderCondition.shape("full_row", orb_type="fire"),
        )),))

        self.assertTrue(evaluate_manual_route(board, ((4, 5),), profile, confirmed=True).qualifying)

    def test_cascade_timing_is_visible_and_condition_can_exclude_cascades(self):
        board = ((3, 2, 2, 3, 2, 1), (1, 2, 1, 3, 3, 1),
                 (2, 3, 1, 2, 3, 3), (3, 1, 1, 2, 3, 4),
                 (2, 4, 2, 4, 4, 3))
        all_rounds = RuleProfile(
            "cascades",
            condition_groups=(ConditionGroup.all_of((
                LeaderCondition("combo_minimum", minimum=3),
            )),),
        )
        direct_only = RuleProfile(
            "direct",
            condition_groups=(ConditionGroup.all_of((
                LeaderCondition("combo_minimum", minimum=3, include_cascades=False),
            )),),
        )

        cascade_result = evaluate_manual_route(board, ((0, 0),), all_rounds, confirmed=True)
        direct_result = evaluate_manual_route(board, ((0, 0),), direct_only, confirmed=True)

        self.assertGreater(cascade_result.combo_count, len(cascade_result.rounds[0].matches))
        self.assertTrue(cascade_result.qualifying)
        self.assertFalse(direct_result.qualifying)

    def test_hazard_policy_allows_an_explicitly_required_hazard_match(self):
        jammer = Orb("jammer")
        board = ((jammer, jammer, jammer, 1, 2, 3),) + ((1, 2, 3, 4, 5, 6),) * (ROWS - 1)
        avoid = RuleProfile(
            "safe",
            condition_groups=(ConditionGroup.all_of((LeaderCondition("combo_minimum", minimum=1),)),),
        )
        require = RuleProfile(
            "jammer leader",
            condition_groups=(ConditionGroup.all_of((
                LeaderCondition("required_orbs", value=("jammer",)),
            )),),
        )

        avoided = evaluate_manual_route(board, ((0, 0),), avoid, confirmed=True)
        required = evaluate_manual_route(board, ((0, 0),), require, confirmed=True)

        self.assertFalse(avoided.qualifying)
        self.assertEqual(avoided.hazard_outcome, "blocked")
        self.assertEqual(avoided.failed_conditions, ("hazard_policy",))
        self.assertTrue(required.qualifying)
        self.assertEqual(required.hazard_outcome, "required")

    def test_required_hazard_does_not_allow_another_hazard_match(self):
        board = ((Orb("jammer"), Orb("jammer"), Orb("jammer"),
                  Orb("poison"), Orb("poison"), Orb("poison")),) + ((1, 2, 3, 4, 5, 6),) * (ROWS - 1)
        profile = RuleProfile(
            "jammer leader",
            condition_groups=(ConditionGroup.all_of((LeaderCondition.required_orbs(("jammer",)),)),),
        )

        result = evaluate_manual_route(board, ((0, 0),), profile, confirmed=True)

        self.assertEqual(result.hazard_outcome, "blocked")
        self.assertIn("hazard_policy", result.failed_conditions)
        self.assertFalse(result.qualifying)

    def test_failed_any_of_hazard_requirement_does_not_allow_hazard(self):
        jammer = Orb("jammer")
        board = ((jammer, jammer, jammer, 1, 2, 3),) + ((1, 2, 3, 4, 5, 6),) * (ROWS - 1)
        profile = RuleProfile(
            "alternate leader",
            condition_groups=(ConditionGroup.any_of((
                LeaderCondition.required_orbs(("jammer", "poison")),
                LeaderCondition.combo_minimum(1),
            )),),
        )

        result = evaluate_manual_route(board, ((0, 0),), profile, confirmed=True)

        self.assertFalse(result.qualifying)
        self.assertEqual(result.hazard_outcome, "blocked")
        self.assertIn("hazard_policy", result.failed_conditions)

    def test_search_preserves_default_hazard_exclusion_and_required_hazard_exception(self):
        jammer = Orb("jammer")
        board = ((jammer, jammer, jammer, 1, 2, 3),) + ((1, 2, 3, 4, 5, 6),) * (ROWS - 1)
        options = RouteSearchOptions(attempts=1, seed=0, min_steps=0, max_steps=0)
        avoid = RuleProfile(
            "safe", condition_groups=(ConditionGroup.all_of((LeaderCondition.combo_minimum(1),)),)
        )
        require = RuleProfile(
            "jammer leader", condition_groups=(ConditionGroup.all_of((
                LeaderCondition.required_orbs(("jammer",)),
            )),)
        )

        avoided = search_qualifying_route(board, avoid, options, confirmed=True)
        required = search_qualifying_route(board, require, options, confirmed=True)

        self.assertIsNone(avoided.qualifying_candidate)
        self.assertEqual(avoided.diagnostic_candidate.hazard_outcome, "blocked")
        self.assertFalse(avoided.diagnostic_candidate.execution_eligible)
        self.assertIsNotNone(required.qualifying_candidate)
        self.assertEqual(required.qualifying_candidate.hazard_outcome, "required")

    def test_search_returns_the_best_non_qualifying_diagnostic_candidate(self):
        board = ((1, 1, 1, 2, 2, 2), (3, 4, 5, 6, 3, 4),
                 (4, 5, 6, 3, 4, 5), (5, 6, 3, 4, 5, 6),
                 (6, 3, 4, 5, 6, 3))
        options = RouteSearchOptions(attempts=60, seed=9, min_steps=0, max_steps=4)
        qualifying = search_qualifying_route(board, RuleProfile("open"), options, confirmed=True)
        blocked = search_qualifying_route(
            board, RuleProfile("blocked", external_conditions=(ExternalCondition("skill"),)),
            options, confirmed=True
        )

        self.assertIsNotNone(qualifying.qualifying_candidate)
        self.assertIsNone(blocked.qualifying_candidate)
        self.assertEqual(blocked.diagnostic_candidate.route, qualifying.qualifying_candidate.route)
        self.assertEqual(blocked.diagnostic_candidate.combo_count, qualifying.qualifying_candidate.combo_count)
        self.assertIn("external:skill", blocked.diagnostic_candidate.failed_conditions)
        self.assertFalse(blocked.diagnostic_candidate.execution_eligible)

    def test_search_diagnostic_explains_a_missing_condition(self):
        board = tuple(tuple((row + col) % 6 + 1 for col in range(COLS)) for row in range(ROWS))
        profile = RuleProfile(
            "needs a match", condition_groups=(ConditionGroup.all_of((
                LeaderCondition.combo_minimum(1),
            )),)
        )

        result = search_qualifying_route(
            board, profile, RouteSearchOptions(attempts=1, seed=0, min_steps=0, max_steps=0), confirmed=True
        )

        self.assertIsNone(result.qualifying_candidate)
        self.assertEqual(result.diagnostic_candidate.failed_conditions, ("combo_minimum",))
        self.assertIn("combo_minimum", result.diagnostic)
        self.assertIn("0 combos; need at least 1", result.diagnostic)
        self.assertFalse(result.diagnostic_candidate.execution_eligible)

    def test_controller_auto_confirms_clean_board_but_keeps_execution_locked_until_route_approval(self):
        board = ((1, 1, 1, 2, 2, 2), (3, 4, 5, 6, 3, 4),
                 (4, 5, 6, 3, 4, 5), (5, 6, 3, 4, 5, 6),
                 (6, 3, 4, 5, 6, 3))
        source = (12, 10, bytes((60, 40, 20, 255)) * (12 * 10))
        controller = BoardInspectionController(detector=lambda *args: board, capture=lambda serial: source)
        profile = RuleProfile("manual", condition_groups=(ConditionGroup.all_of((
            LeaderCondition.combo_minimum(2),
        )),))

        controller.capture_device("test-device")
        controller.set_rule_profile(profile)
        confirmed = controller.evaluate_manual_route(((0, 0),))
        self.assertTrue(confirmed.execution_eligible)
        with self.assertRaisesRegex(ValueError, "明確確認"):
            controller.execute_route("test-device")
        with self.assertRaises(ValueError):
            controller.approve_route()
        controller.approve_route(explicit_confirmation=True)
        self.assertTrue(controller.state.route_approved)
        controller.set_rule_profile(RuleProfile("changed"))
        self.assertIsNone(controller.state.route_evaluation)
        self.assertFalse(controller.state.route_approved)


if __name__ == "__main__":
    unittest.main()
