# PAD Router Desktop GUI

## Problem Statement

PAD Router currently exposes board recognition, route search, and ADB gesture verification through a command line. A player cannot easily compare the game screen with the Detected Board, configure multiple Leader Conditions, inspect an automatically found route, or safely test a hand-drawn Route before an ADB gesture is sent.

## Solution

Provide a Python desktop GUI for a Standard Board. It accepts an Android screenshot or PNG image file, presents the original image with a detection overlay plus an editable Board, evaluates a saved Rule Profile, and searches or evaluates Routes. Only a Candidate Route that satisfies the enabled Team Condition on a Confirmed Board may be approved and executed after explicit confirmation.

## User Stories

1. As a PAD player, I want to open the desktop GUI, so that I can plan a turn without command-line arguments.
2. As a PAD player, I want to select a connected Android device and capture its screen, so that I can analyse my current board.
3. As a PAD player, I want to load a saved screenshot, so that I can analyse and reproduce boards without a device.
4. As a PAD player, I want to see the original image with detected-orb annotations, so that I can compare recognition with the game screen.
5. As a PAD player, I want to inspect a separate editable Board, so that I can correct a mistaken Detected Board.
6. As a PAD player, I want the GUI to first infer the Standard Board region, so that routine device use needs no manual calibration.
7. As a PAD player, I want to calibrate the Board region when the automatic inference is wrong, so that changed device geometry remains usable.
8. As a PAD player, I want to set every supported Orb type and observable state manually, so that a corrected Board represents the actual puzzle.
9. As a PAD player, I want to distinguish fire, water, wood, light, dark, heart, poison, mortal poison, jammer, and bomb Orbs, so that planning reflects the puzzle.
10. As a PAD player, I want to mark enhanced and locked states, so that state-sensitive Leader Conditions can be checked.
11. As a PAD player, I want to create Condition Groups using all-of or any-of logic, so that I can express my Leader Conditions without a character database.
12. As a PAD player, I want to configure combo, attribute, Match count, connected-orb count, enhanced-orb, shape, and required-or-forbidden Orb conditions, so that I can select the way I want to clear the Board.
13. As a PAD player, I want to enable two Leader Conditions that are both required, so that a Route meets both leader requirements.
14. As a PAD player, I want to mark non-board prerequisites such as HP or skill state as confirmed, so that route evaluation is honest about what it can and cannot infer.
15. As a PAD player, I want to save and load named Rule Profiles locally, so that I can reuse combinations of Team Conditions, External Conditions, and Hazard Policy.
16. As a PAD player, I want to choose whether Hazard Orbs are avoided, so that the search follows my risk preference while allowing conditions that require those Orbs.
17. As a PAD player, I want to set how many search attempts are made, so that I can spend more computation looking for a higher-Combo Route.
18. As a PAD player, I want the route search to discard routes that fail the Team Condition and then select the highest-Combo qualifying Route, so that combo optimization never defeats leader activation.
19. As a PAD player, I want tied qualifying Routes to prefer fewer drag steps, so that the selected Route is easier to execute.
20. As a PAD player, I want to see the best non-qualifying Candidate Route and its failed conditions when no qualifying Route exists, so that I can diagnose the limitation without executing it.
21. As a PAD player, I want to draw my own Route by dragging across the editable Board, so that I can evaluate my own solve idea.
22. As a PAD player, I want manual and automatically searched Routes to report Matches, cascades, Combo count, hazard treatment, and condition-by-condition results, so that I can compare them.
23. As a PAD player, I want a Candidate Route overlaid on both Board representations, so that I can visually verify the intended motion.
24. As a PAD player, I want execution disabled until the Board is confirmed and the Team Condition passes, so that a recognizer mistake or failed trigger cannot send a gesture.
25. As a PAD player, I want a final explicit Route confirmation before ADB execution, so that sending a gesture is always deliberate.
26. As a PAD player, I want post-gesture verification shown in the GUI, so that I can tell whether the actual Board agrees with the program's expected Board.
27. As a PAD player, I want changing the source image, calibration, Board, Rule Profile, or search settings to withdraw a previous route approval, so that stale results cannot be executed.

## Implementation Decisions

- Build one native Python desktop interface using the standard library GUI toolkit; do not add a GUI dependency.
- Retain the existing recognition, simulation, search, ADB, and safe-play behavior behind adapters; the GUI is a caller, not a second solver.
- Make the route-planning Module the single high-leverage seam. It provides pure planning and manual-route evaluation operations over a Confirmed Board, Rule Profile, and declared search options. Every result includes the Route, resolved Match rounds, per-condition results, Hazard outcome, diagnostic status, and execution eligibility.
- Model an Orb as a base type plus enhanced and locked observable states. The launch set includes fire, water, wood, light, dark, heart, poison, mortal poison, jammer, and bomb.
- Limit the launch UI to a Standard Board. Board-size variants and non-orb board obstacles are not modelled.
- Use Condition Groups with explicit all-of or any-of semantics. Enabled leader groups are combined as a Team Condition with all-of semantics.
- Provide board-verifiable predicates for Combo minimum, simultaneous attributes, attribute Match count, connected-orb count with at-least or exactly semantics, enhanced-orb inclusion, cross, 3x3 box, row, column, L shape, and required or forbidden Orb types. Each predicate declares its counted unit and resolution phase, including whether cascade Matches count; locked Orbs retain their matching behaviour while preserving their observable state.
- Treat HP, team composition, and skill-use prerequisites as user-confirmed External Conditions rather than attempting unreliable full-screen game-state recognition.
- Persist Rule Profiles as local JSON. Do not create or sync a monster/character database.
- Let the player select a Hazard Policy. The default is a hard exclusion of resolved Hazard Matches unless an enabled Leader Condition explicitly requires that Hazard Orb.
- Search attempts are reproducible from the declared options and seed. Rank qualifying Routes by descending Combo count, ascending drag steps, then a stable Route order; use the same stable ordering for diagnostic candidates when none qualify.
- The GUI starts from automatic Board inference, exposes overlay evidence, and permits Board Calibration and individual cell correction only when needed. PNG is the accepted file format in v1; calibration maps source pixels to an in-bounds 6x5 grid, and unsupported locked or mortal-poison recognition is routed to manual correction.
- Any source, calibration, cell edit, Rule Profile, or search-option change invalidates an existing Candidate Route and its approval.
- Require a Confirmed Board, a satisfied Team Condition, and an explicit final confirmation before reusing the existing ADB execution and post-gesture verification flow.

## Testing Decisions

- Test observable behaviour, not implementation details or widget internals.
- The agreed seam is the route-planning Module Interface: known Boards and Rule Profiles must yield the expected qualification result, Combo ordering, deterministic tie-break, failed-condition diagnostic, manual-route evaluation, and execution eligibility.
- Add focused standard-library tests for Rule Profile JSON round-tripping and Board Calibration/cell-correction outcomes through their public operations, including in-bounds 6x5 validation.
- Keep GUI tests at the controller/view-model boundary and use the existing recognition and ADB adapters as replaceable inputs; do not require a connected device for routine tests. Cover condition-group truth tables, predicate resolution timing, Hazard exceptions, deterministic search, diagnostics, and stale or unconfirmed execution rejection.
- Reuse the repository's existing self-check style where it remains appropriate; new non-trivial planning logic also receives focused behavioural tests.
- Run focused tests during each ticket, then the complete suite before the implementation is committed.

## Out of Scope

- A character or monster database, automatic character-data updates, and region-specific roster support.
- Board sizes other than 6x5.
- Image formats other than PNG.
- Clouds, tape, roulette, and other non-orb board obstacles.
- Full recognition of HP, team composition, skill state, dungeon state, or combat damage.
- Automatic execution without a user confirmation.
- Replacing the existing recognition or ADB safety implementation.

## Further Notes

- PAD wording can mean at least or exactly; the Rule Profile must preserve this distinction, especially for enhanced-orb conditions.
- The GUI must make uncertainty visible. A diagnostic candidate is useful for investigation but must never be treated as an Executable Route.
