import tempfile
import unittest
from pathlib import Path

from pad_router import (
    COLS,
    ROWS,
    ConditionGroup,
    ExternalCondition,
    LeaderCondition,
    Orb,
    RuleProfile,
    evaluate_manual_route,
    load_rule_profile,
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
    def test_condition_groups_require_both_enabled_leaders_and_confirmed_external_state(self):
        board = ((1, 1, 1, 2, 2, 2), (3, 4, 5, 6, 3, 4),
                 (4, 5, 6, 3, 4, 5), (5, 6, 3, 4, 5, 6),
                 (6, 3, 4, 5, 6, 3))
        profile = RuleProfile(
            "leaders",
            condition_groups=(
                ConditionGroup.all_of((LeaderCondition("combo_minimum", minimum=2),)),
                ConditionGroup.any_of((LeaderCondition("attribute", value="fire"),
                                       LeaderCondition("attribute", value="dark"))),
            ),
            external_conditions=(ExternalCondition("hp", confirmed=False),),
        )

        result = evaluate_manual_route(board, ((0, 0),), profile, confirmed=True)

        self.assertEqual(result.combo_count, 2)
        self.assertFalse(result.qualifying)
        self.assertFalse(result.execution_eligible)
        self.assertEqual(result.failed_conditions, ("external:hp",))

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

        self.assertGreater(cascade_result.combo_count, cascade_result.rounds[0].combo_count)
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
        self.assertTrue(required.qualifying)
        self.assertEqual(required.hazard_outcome, "required")

    def test_controller_applies_profile_and_keeps_execution_locked_until_confirmation(self):
        board = ((1, 1, 1, 2, 2, 2), (3, 4, 5, 6, 3, 4),
                 (4, 5, 6, 3, 4, 5), (5, 6, 3, 4, 5, 6),
                 (6, 3, 4, 5, 6, 3))
        source = (12, 10, bytes((60, 40, 20, 255)) * (12 * 10))
        controller = BoardInspectionController(detector=lambda *args: board, capture=lambda serial: source)
        profile = RuleProfile("manual", condition_groups=(ConditionGroup.all_of((
            LeaderCondition.combo_minimum(2),
        )),))

        controller.capture_device("test-device")
        controller.apply_profile(profile)
        unconfirmed = controller.evaluate_route(((0, 0),))
        self.assertTrue(unconfirmed.qualifying)
        self.assertFalse(unconfirmed.execution_eligible)

        controller.confirm_board()
        confirmed = controller.evaluate_route(((0, 0),))
        self.assertTrue(confirmed.execution_eligible)
        with self.assertRaises(ValueError):
            controller.approve_route()
        controller.approve_route(explicit_confirmation=True)
        self.assertTrue(controller.state.route_approved)
        controller.apply_profile(RuleProfile("changed"))
        self.assertIsNone(controller.state.route_evaluation)
        self.assertFalse(controller.state.route_approved)


if __name__ == "__main__":
    unittest.main()
