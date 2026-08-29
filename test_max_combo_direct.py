import unittest
from collections import Counter

from pad_router import (ConditionGroup, LeaderCondition, Orb, RouteSearchOptions, RuleProfile,
                        evaluate_manual_route, max_combo_layout, resolve_matches,
                        search_qualifying_route)
from pad_router_gui import BoardInspectionApp


class DirectMaxComboTests(unittest.TestCase):
    def test_open_profile_counts_only_immediate_matches_and_inventory_target(self):
        board = (
            (1, 1, 1, 2, 2, 2),
            (3, 3, 3, 4, 4, 4),
            (5, 5, 5, 6, 6, 6),
            (1, 2, 3, 4, 5, 6),
            (1, 2, 3, 4, 5, 6),
        )

        result = evaluate_manual_route(board, ((4, 0),), RuleProfile("max"), cascade=True)

        self.assertEqual(result.direct_combo_count, 6)
        self.assertEqual(result.direct_combo_estimate, 6)

    def test_shape_match_is_reserved_before_counting_the_remaining_keys(self):
        board = (
            (2, 1, 2, 3, 4, 5),
            (3, 1, 4, 5, 6, 2),
            (1, 1, 1, 2, 3, 4),
            (4, 1, 5, 6, 2, 3),
            (5, 6, 2, 3, 4, 5),
        )
        profile = RuleProfile("cross", condition_groups=(
            ConditionGroup.all_of((LeaderCondition.shape("cross", orb_type="fire"),)),
        ))

        result = evaluate_manual_route(board, ((4, 0),), profile)

        self.assertEqual(result.direct_combo_count, 1)
        self.assertEqual(result.direct_combo_estimate, 7)

    def test_permitted_hazards_are_counted_but_avoided_hazards_are_not(self):
        board = (
            (Orb("jammer"), Orb("jammer"), Orb("jammer"), 1, 1, 1),
            (2, 3, 4, 5, 6, 2),
            (3, 4, 5, 6, 2, 3),
            (4, 5, 6, 2, 3, 4),
            (5, 6, 2, 3, 4, 5),
        )

        allowed = evaluate_manual_route(board, ((4, 0),), RuleProfile("allow", hazard_policy="allow"))
        avoided = evaluate_manual_route(board, ((4, 0),), RuleProfile("avoid", hazard_policy="avoid"))

        self.assertEqual(allowed.direct_combo_estimate, avoided.direct_combo_estimate + 1)

    def test_any_group_reserves_only_a_condition_true_on_the_full_direct_round(self):
        board = (
            (1, 1, 1, 3, 4, 5),
            (2, 2, 2, 2, 2, 2),
            (3, 4, 5, 6, 3, 4),
            (4, 5, 6, 3, 4, 5),
            (5, 6, 3, 4, 5, 6),
        )
        profile = RuleProfile("any", condition_groups=(ConditionGroup.any_of((
            LeaderCondition.forbidden_orbs(("fire",)),
            LeaderCondition.attribute("water"),
        )),))

        result = evaluate_manual_route(board, ((4, 0),), profile, cascade=False)

        self.assertEqual(result.direct_combo_estimate, 8)

    def test_any_group_cannot_make_an_exact_condition_true_by_omitting_matches(self):
        board = (
            (1, 1, 1, 3, 4, 5),
            (2, 2, 2, 2, 2, 2),
            (1, 1, 1, 6, 3, 4),
            (4, 5, 6, 3, 4, 5),
            (5, 6, 3, 4, 5, 6),
        )
        profile = RuleProfile("any", condition_groups=(ConditionGroup.any_of((
            LeaderCondition.match_count("fire", 1, exact=True),
            LeaderCondition.attribute("water"),
        )),))

        result = evaluate_manual_route(board, ((4, 0),), profile, cascade=False)

        self.assertEqual(result.direct_combo_estimate, 7)

    def test_open_search_clears_nearly_every_triple_the_inventory_allows(self):
        board = (
            (4, 4, 1, 3, 5, 4),
            (4, 3, 4, 3, 5, 2),
            (5, 2, 3, 2, 1, 5),
            (3, 5, 6, 5, 2, 3),
            (1, 6, 1, 6, 3, 4),
        )

        result = search_qualifying_route(board, RuleProfile("max"), RouteSearchOptions(attempts=50))

        self.assertIsNotNone(result.qualifying_candidate)
        candidate = result.qualifying_candidate
        self.assertGreaterEqual(candidate.direct_combo_count, candidate.direct_combo_estimate - 1)

    def test_default_step_ceiling_reaches_the_long_route_the_last_combo_needs(self):
        board = (
            (1, 3, 3, 6, 4, 4),
            (3, 2, 4, 4, 2, 5),
            (3, 3, 5, 6, 6, 4),
            (1, 6, 3, 3, 5, 6),
            (2, 4, 3, 2, 6, 1),
        )

        options = RouteSearchOptions(attempts=50, max_steps=80)
        result = search_qualifying_route(board, RuleProfile("max"), options)

        self.assertGreaterEqual(result.qualifying_candidate.direct_combo_count, 8)

    def test_a_chosen_shape_keeps_collecting_combos_after_it_is_formed(self):
        board = (
            (1, 1, 1, 3, 2, 6),
            (6, 3, 3, 5, 2, 5),
            (1, 5, 6, 2, 4, 6),
            (4, 6, 5, 3, 5, 4),
            (5, 3, 1, 1, 3, 4),
        )
        profile = RuleProfile("dark row", condition_groups=(
            ConditionGroup.all_of((LeaderCondition.shape("full_row", orb_type="dark"),)),
        ))

        result = search_qualifying_route(board, profile, RouteSearchOptions(attempts=50, max_steps=80))

        candidate = result.qualifying_candidate
        self.assertIsNotNone(candidate)
        self.assertTrue(candidate.condition_results[0].satisfied)
        self.assertGreaterEqual(candidate.direct_combo_count, 7)

    def test_layout_never_touches_two_blocks_of_one_key_and_is_shown_as_the_target(self):
        board = (
            (1, 3, 3, 6, 4, 4),
            (3, 2, 4, 4, 2, 5),
            (3, 3, 5, 6, 6, 4),
            (1, 6, 3, 3, 5, 6),
            (2, 4, 3, 2, 6, 1),
        )

        goal, layout = max_combo_layout(board)

        self.assertEqual(goal, 9)
        # The plan places every orb the Board holds and really does hold that
        # many Matches, so leftovers cannot quietly merge two planned blocks.
        self.assertEqual(Counter(key for row in layout for key in row),
                         Counter(orb for line in board for orb in line))
        self.assertEqual(len(resolve_matches(layout, cascade=False)[0].matches), goal)
        result = evaluate_manual_route(board, ((4, 0),), RuleProfile("max"))
        self.assertIn("目標版型（此排法可成立 9 Combo", BoardInspectionApp._format_layout(board, result))

    def test_layout_does_not_promise_blocks_a_dominant_colour_would_merge(self):
        board = tuple(tuple(3 for _ in range(6)) for _ in range(4)) + ((3, 5, 5, 5, 5, 5),)

        goal, layout = max_combo_layout(board)

        # 25 wood orbs cannot hold 8 separate blocks: the ones no block claims
        # bridge them, so the plan must promise far less than 25 // 3 + 5 // 3.
        self.assertLess(goal, 9)
        self.assertEqual(len(resolve_matches(layout, cascade=False)[0].matches), goal)

    def test_direct_metrics_ignore_cascades_and_are_rendered_in_the_gui(self):
        board = (
            (3, 2, 2, 3, 2, 1),
            (1, 2, 1, 3, 3, 1),
            (2, 3, 1, 2, 3, 3),
            (3, 1, 1, 2, 3, 4),
            (2, 4, 2, 4, 4, 3),
        )

        with_cascade = evaluate_manual_route(board, ((0, 0),), RuleProfile("max"), cascade=True)
        without_cascade = evaluate_manual_route(board, ((0, 0),), RuleProfile("max"), cascade=False)

        self.assertNotEqual(with_cascade.combo_count, without_cascade.combo_count)
        self.assertEqual(
            (with_cascade.direct_combo_count, with_cascade.direct_combo_estimate),
            (without_cascade.direct_combo_count, without_cascade.direct_combo_estimate),
        )
        self.assertIn("直接：2／預估：9", BoardInspectionApp._format_evaluation(with_cascade))


if __name__ == "__main__":
    unittest.main()
