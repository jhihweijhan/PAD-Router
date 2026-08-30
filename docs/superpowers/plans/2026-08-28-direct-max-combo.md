# Direct Max Combo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rank maximum-Combo routes by direct, pre-gravity matches while preserving requested Match forms and respecting hazard policy.

**Architecture:** Add two direct metrics to `RouteEvaluation`, computed from the first resolved round and an evidence-reserving Match subset. Use those metrics for completed-route ranking while preserving incomplete beam candidates and existing cascade evaluation.

**Tech Stack:** Python 3.10+, standard library, unittest, uv.

**Spec:** `docs/reference/max-combo-direct-match-spec.md`

## Global Constraints

- No new dependency or Profile JSON schema.
- Existing `combo_count` retains its cascade-aware meaning.
- Hazard Match keys count unless the existing hazard policy excludes them.
- Preserve the user's uncommitted protected-cell changes.

---

### Task 1: Add deterministic direct metrics

**Files:**
- Modify: `pad_router.py`
- Test: `test_max_combo_direct.py`

**Interfaces:**
- Produces: `RouteEvaluation.direct_combo_count: int` and `RouteEvaluation.direct_combo_estimate: int | None`.

- [ ] **Step 1: Write a failing observable-metric test**

```python
result = evaluate_manual_route(board, ((4, 0),), RuleProfile("max"), cascade=True)
assert result.direct_combo_count == 6
assert result.direct_combo_estimate == 6
```

- [ ] **Step 2: Verify the test fails**

Run: `uv run python -m unittest test_max_combo_direct.DirectMaxComboTests.test_open_profile_counts_only_immediate_matches_and_inventory_target`

Expected: `AttributeError` for the missing direct metric.

- [ ] **Step 3: Implement first-round metrics and reservation**

```python
direct_matches = rounds[0].matches if rounds else ()
direct_combo_count = len(direct_matches)
direct_combo_estimate = _direct_combo_estimate(expected, profile, direct_matches)
```

- [ ] **Step 4: Verify the focused test passes**

Run: `uv run python -m unittest test_max_combo_direct`

Expected: PASS.

### Task 2: Use direct metrics for maximum route search and presentation

**Files:**
- Modify: `pad_router.py`
- Modify: `pad_router_gui.py`
- Modify: `test_max_combo_direct.py`
- Modify: `docs/reference/architecture.md`
- Modify: `docs/reference/desktop-gui-spec.md`
- Modify: `docs/guides/user-guide.md`

**Interfaces:**
- Consumes: `RouteEvaluation.direct_combo_count`, `RouteEvaluation.direct_combo_estimate`.
- Produces: direct-only candidate ranking and displayed metrics.

- [ ] **Step 1: Add failing ranking and hazard-policy tests**

```python
self.assertEqual(result.direct_combo_estimate, expected)
self.assertIsNone(cascade_only.direct_combo_estimate)
```

- [ ] **Step 2: Verify focused tests fail for the intended assertion**

Run: `uv run python -m unittest test_max_combo_direct`

Expected: failure caused by missing direct-only ranking/reservation behavior.

- [ ] **Step 3: Implement the smallest ranking and output changes**

```python
return (-result.direct_combo_count, -result.direct_combo_estimate,
        len(result.route) - 1, result.route)
```

- [ ] **Step 4: Verify focused and full suites**

Run: `uv run python -m unittest test_max_combo_direct && uv run python -m unittest && git diff --check`

Expected: all commands exit 0.
