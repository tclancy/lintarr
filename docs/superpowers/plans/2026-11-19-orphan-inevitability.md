# `orphan-inevitability` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a qBittorrent backend check that FAILs when a seed limit can fire for an arr's torrents but no configured setting removes their files.

**Architecture:** The arr download-client fact gains `remove_completed`. `hardlink-futility`'s arr-to-client matching becomes a public helper in `lintarr.checks`. A new `clients/qbittorrent/share_limits.py` answers two questions per category: what is the effective action, and can a limit fire? It reuses `queue_liveness`'s helpers. The check lives in `clients/qbittorrent/orphan_inevitability.py`, is registered in the qBittorrent `Backend.checks`, and its two Therefore lines go in `THEREFORE`.

**Tech Stack:** Python 3.13, uv, pytest, Hypothesis.

**Spec:** `docs/superpowers/specs/2026-11-19-orphan-inevitability-design.md`. Its "The check" section is the binding predicate.

## Global Constraints

- Facts are `Known` or `Unknown`, never defaulted. An unreadable input the decision needs gives SKIP, never PASS.
- Invariant id `orphan-inevitability`; conflicts `remove-keeps-files` and `nothing-reclaims`; premises `arr.limit_fires_and_files_stay` and `arr.limit_fires_and_nothing_reclaims`; instance `qbittorrent[<name>]`.
- `queue-liveness` and `hardlink-futility` findings, details, edge keys and Therefore text stay byte-identical. Their existing tests are the gate. Never edit their expected strings.
- No new `GET`s except reading one more top-level key from the existing `/api/v3/downloadclient` body.
- Never weaken an existing assertion. Filter it to the invariant it was written about, add a separate assertion for the new finding, and name each such test in the report.
- Fast suite: `uv run pytest -q`. No `--no-verify`. Revert stray `ruff format` edits to docs you didn't change.

## Review Focus

1. **Homelab must PASS.** Global action 3, ratio 1.5 on, seeding time 20160 on, categories `"Default"` and `-2`, indexers with no seed criteria, `remove_completed` false. `homelab_paths.py` gains these values, and Task 3's PASS test uses them. Task 4 verifies live in strict mode.
2. **Unlimited seeding must PASS, not SKIP:** global limits off, categories inherit, indexers defer.
3. **An indexer override blocks only its own arm.** Ratio `-1` beside a seed-time goal is still reachable.
4. **A category `share_limit_action` beats the global action.** For example, global 3 with category `"Remove"` FAILs.
5. **Unknown `remove_completed` only matters when the action is Stop.** With action 3 it must not SKIP.

---

### Task 1: `remove_completed` fact and a public arr-client matcher

**Files:**
- Modify: `src/lintarr/models.py` (`ArrDownloadClient.remove_completed: bool | None`, the last field)
- Modify: `src/lintarr/collect/arr.py` (read top-level `removeCompletedDownloads`: absent → `None`, non-bool → `bad-response`, `null` → `None`)
- Create: `src/lintarr/checks/arr_clients.py`. Move `hardlink_futility`'s `_tuple_of`, `_is_this_client`, `_is_well_formed`, `_clients_that_are_this`, `_arr_label`, `_save_paths` and `NO_ARR_SENDS_HERE` here as public names (no leading underscore). `hardlink_futility` imports them. Behaviour and strings unchanged.
- Modify: every `ArrDownloadClient(...)` site in `src/` and `tests/`; `tests/fixtures/homelab_paths.py` (raw body `"removeCompletedDownloads": false` and the built fixture); `tests/strategies.py`
- Test: extend `tests/collect/test_arr_paths.py`; create `tests/checks/test_arr_clients.py`

**Interfaces (produced):**
- `lintarr.checks.arr_clients`:
  - `tuple_of[T](fact, kind) -> tuple[T, ...] | None`
  - `save_paths_of(instance) -> Mapping[str, object] | None` (renamed from `_save_paths`)
  - `arr_label(arr) -> str`
  - `is_well_formed(d) -> bool`; `is_well_formed` also requires `remove_completed` to be `bool | None`
  - `clients_that_are(arr, instance, save_paths) -> tuple[ArrDownloadClient, ...] | str`
  - `NO_ARR_SENDS_HERE`

- [ ] **Step 1:** Write failing tests:
  - `test_reads_remove_completed`: `true`, `false`, absent → `None`, `null` → `None`.
  - Add `removeCompletedDownloads` = `"yes"` to the bad-response cases.
  - `test_arr_clients` covers `clients_that_are`: match, other kind, disabled, missing category, malformed, and unreadable list.
- [ ] **Step 2:** Run the tests and see them fail.
- [ ] **Step 3:** Implement. Move the helpers verbatim, then rename.
- [ ] **Step 4:** Run `uv run pytest -q`. All pass, and the `hardlink-futility` tests are untouched.
- [ ] **Step 5:** Commit `feat(arr): read removeCompletedDownloads; share the arr-client matcher`

### Task 2: qBittorrent share-limit questions

**Files:**
- Create: `src/lintarr/clients/qbittorrent/share_limits.py`
- Test: `tests/clients/test_qbittorrent_share_limits.py`

**Interfaces:**
- Consumes these, imported from `queue_liveness`: `_as_limit`, `_truth`, `_is_a_seed_goal`, `_is_an_unusable_seed_criterion`, `_is_a_readable_seed_criterion`, `_can_be_reached`, `_no_global_ratio`, `_not_inactive_gate`, `_is_a_torrent_source`, `_any_of`, `_all_of`, `USE_GLOBAL`, `UNLIMITED`. Don't move them, so `queue-liveness` stays byte-identical.
- Produces:
  - `ACTION_BY_NAME = {"Stop": 0, "Remove": 1, "EnableSuperSeeding": 2, "RemoveWithContent": 3}`
  - `effective_action(qbt: QbtInstance, category: str) -> int | None` follows spec step 1. `""`, a category not in the map, a category missing the key, or `"Default"` → global `max_ratio_act` via `_as_limit`. An unknown string, a non-string, unreadable categories or a non-dict entry → `None`.
  - `limit_can_fire(qbt: QbtInstance, arr: ArrInstance, category: str) -> bool | None` follows spec step 2. It's an existential over the arr's indexers, using `_any_of` over each indexer's `_all_of((_is_a_torrent_source(i), arm_reachable))`. `arm_reachable` is `_any_of` over the ratio, seed-time and inactive arms. Inside an arm:
    - an indexer criterion that is a goal → `True`;
    - one that overrides (readable, not `-2`, not reachable) → `False`;
    - an unusable criterion → `None`;
    - one that defers → the category's own limit (`True` if reachable, `False` if `-1` or another non-`-2` unreachable value, `None` if unreadable), or, if the category inherits or the key is absent, the global.
    - The global ratio is `not _no_global_ratio(qbt)`, keeping `None` as `None`. Global seed time is `_truth(max_seeding_time_enabled)`. The inactive arm takes the category's `inactive_seeding_time_limit` own value, or, when inheriting, `not _not_inactive_gate(qbt)`.
    - An arr with no indexers → `False`.

- [ ] **Step 1:** Write failing tests. For `effective_action`:
  - `test_default_category_uses_global_action`
  - `test_category_action_overrides_global` (one case per name)
  - `test_uncategorised_and_unknown_category_use_global`
  - `test_unknown_action_string_is_none`
  - `test_unreadable_categories_is_none`

  For `limit_can_fire`:
  - `test_homelab_limits_can_fire`
  - `test_unlimited_seeding_cannot_fire` (globals off, categories `-2`, indexers unset)
  - `test_indexer_goal_fires_even_with_globals_off`
  - `test_indexer_override_blocks_only_its_arm`
  - `test_category_own_limit_fires`
  - `test_category_minus_one_blocks_the_global`
  - `test_inactive_gate_fires`
  - `test_usenet_only_arr_cannot_fire`
  - `test_unusable_criterion_is_unknown_when_it_decides`
  - `test_no_indexers_cannot_fire`
- [ ] **Step 2:** Run the tests and see them fail.
- [ ] **Step 3:** Implement with small functions, one per arm.
- [ ] **Step 4:** Run `uv run pytest -q`.
- [ ] **Step 5:** Commit `feat(qbittorrent): effective share-limit action and limit reachability per category`

### Task 3: The `orphan-inevitability` check

**Files:**
- Create: `src/lintarr/clients/qbittorrent/orphan_inevitability.py`
- Modify: `src/lintarr/clients/qbittorrent/__init__.py` (append to `checks`, after `queue_liveness`)
- Modify: `src/lintarr/clients/qbittorrent/therefore.py` (two entries, spec text verbatim)
- Modify: `src/lintarr/run.py`. `_resolve_failed_arr` applies to an invariant in a set `{hardlink_futility.INVARIANT_ID, orphan_inevitability.INVARIANT_ID}`. The SKIP premise is the finding's own invariant's premise: for orphan, use `arr.limit_fires_and_nothing_reclaims`. The detail says "an arr could not be read, so its downloads could not be checked" for orphan. The hardlink detail is unchanged byte for byte.
- Modify, without weakening: any exact-finding-list or exit-code test the full suite turns red, such as `test_fake_backend` and `test_check_cli`. Mock transports that serve qBittorrent prefs must yield homelab-like values that PASS.
- Test: `tests/clients/test_orphan_inevitability.py`, `tests/clients/test_orphan_inevitability_properties.py`; extend `tests/test_render_human.py`

**Interfaces:**
- Produces:
  - `INVARIANT_ID`, `REMOVE_KEEPS_FILES`, `NOTHING_RECLAIMS`, `PREMISE_FILES_STAY`, `PREMISE_NOTHING_RECLAIMS`
  - `check(qbt_instance: ClientInstance, arrs) -> Finding`. It is registered as `lambda inst, arrs: orphan_inevitability.check(inst, arrs)`, because it needs `inst.common.save_paths` *and* `inst.specific`.

- [ ] **Step 1:** Write failing tests:
  - `test_homelab_passes`
  - `test_remove_action_fails_remove_keeps_files`: global 1, limits on.
  - `test_stop_without_arr_removal_fails_nothing_reclaims`
  - `test_stop_with_arr_removal_passes`
  - `test_super_seeding_fails_nothing_reclaims`
  - `test_default_action_minus_one_fails_nothing_reclaims`
  - `test_category_remove_beats_global_remove_with_content`
  - `test_unlimited_seeding_passes_with_detail`
  - `test_remove_keeps_files_wins_over_nothing_reclaims_when_both_present` (two arrs, different categories)
  - `test_unknown_remove_completed_skips_only_for_stop`
  - `test_unknown_action_skips`
  - `test_unknown_in_one_arr_does_not_hide_a_fail_in_another`
  - `test_no_arrs_is_not_applicable`
  - `test_arrs_using_another_client_pass_with_detail`
  - `test_failed_arr_turns_pass_into_skip` (via `run_checks`)
  - `test_render_shows_each_therefore_line`
  - Properties: never raises; every FAIL carries exactly its one holding premise; never PASS when the action is unknown and a limit can fire for a matched arr.
  - Mutation twin in the Remove test: flip only the action to 3 and get PASS.
- [ ] **Step 2:** Run the tests and see them fail.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run `uv run pytest -q`.
- [ ] **Step 5:** Run a mutation round and record the results:
  1. Treat action 1 as reclaiming.
  2. Ignore `remove_completed`.
  3. Drop the "no limit can fire → no conflict" rule.
  4. Ignore the category action.

  All four must go red.
- [ ] **Step 6:** Commit `feat(qbittorrent): orphan-inevitability`

### Task 4: Live verification and docs (controller)

- [ ] On homelab, from a throwaway checkout at the branch head, with the deployed env: strict `check --json` exits 0 and `orphan-inevitability` passes. Remove the checkout.
- [ ] Update `CONTINUATION.md`: this slice; next is `seed-goal-conflict`; follow-up to conformance-test share-limit actions. Commit `docs(continuation): orphan-inevitability slice`.
