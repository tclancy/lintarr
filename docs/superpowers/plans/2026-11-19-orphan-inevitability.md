# `orphan-inevitability` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a qBittorrent backend check. It FAILs when a seed limit can fire for an arr's torrents but no configured setting removes their files.

**Architecture:**
- The arr download-client fact gains `remove_completed` and `imported_category`.
- `hardlink-futility`'s arr-to-client matching becomes a public helper in `lintarr.checks`.
- `Backend` gains `arr_dependent`, so `run.py` stops naming checks.
- A new `clients/qbittorrent/share_limits.py` answers, per category: the effective action, whether some limit can fire, and whether some limit the arr doesn't recognise can fire. It reuses `queue_liveness`'s helpers.
- The check lives in `clients/qbittorrent/orphan_inevitability.py`.

**Tech Stack:** Python 3.13, uv, pytest, Hypothesis.

**Spec:** `docs/superpowers/specs/2026-11-19-orphan-inevitability-design.md`. Its sections "How the arr removes a download" and "The check" are the binding predicate.

## Global Constraints

- Facts are `Known` or `Unknown`, never defaulted. An unreadable input the decision needs gives SKIP, never PASS. An input the decision doesn't need (reachability under action 3) must never cause a SKIP.
- **Names:**
  - invariant `orphan-inevitability`;
  - conflicts `remove-keeps-files` and `nothing-reclaims`;
  - premises `arr.limit_fires_and_files_stay` and `arr.limit_fires_and_nothing_reclaims`;
  - instance `qbittorrent[<name>]`.
- `queue-liveness` and `hardlink-futility` findings, details, edge keys and Therefore text stay byte-identical. Never edit their expected strings.
- No new `GET`s. Read only more keys from the existing `/api/v3/downloadclient` body.
- Never weaken an existing assertion. Filter it to its invariant, add a separate assertion for the new finding, and name each such test in the report. Don't change qBittorrent pref values in `tests/test_check_cli.py`'s `WEDGED_PREFS` or other base prefs. To keep those tests' exit codes, serve `"removeCompletedDownloads": true` from their arr transports.
- `tests/fixtures/homelab.py` is homelab#393 incident evidence and must not change.
- Fast suite: `uv run pytest -q`. No `--no-verify`. Revert stray `ruff format` edits to docs you didn't change.

## Review Focus

1. **Homelab must PASS on RemoveWithContent, not by accident.** The values are: global action 3; ratio 1.5 on; seeding time 20160 on; categories `share_limit_action: "Default"` with every limit `-2`; one torrent indexer with no seed criteria; `remove_completed` false; no imported category. Task 3's homelab test uses an explicit torrent indexer and asserts the RemoveWithContent detail. Task 4 verifies live in strict mode.
2. **Unlimited seeding must PASS, not SKIP.**
3. **Stop plus Remove Completed with a firing category-own limit must FAIL** `nothing-reclaims`. The arr never checks category limits.
4. **A post-import category means the arr can't remove the torrent.** Action and limits come from the imported category.
5. **Action 3 never reads reachability.** Junk seed criteria under action 3 still PASS.

---

### Task 1: Arr facts, a public matcher, `Backend.arr_dependent`

**Files:**
- Modify: `src/lintarr/models.py`. `ArrDownloadClient` gains `remove_completed: bool | None` and `imported_category: str`, appended in that order.
- Modify: `src/lintarr/collect/arr.py`.
  - `removeCompletedDownloads` is top-level: a bool is read as is; absent, null or anything else gives `None`. Never `bad-response`.
  - `tvImportedCategory` or `movieImportedCategory` is a `fields` entry: a non-empty str is read as is; anything else gives `""`.
- Create: `src/lintarr/checks/arr_clients.py`. Move `hardlink_futility`'s `_tuple_of`, `_save_paths`, `_arr_label`, `_is_well_formed`, `_is_this_client`, `_clients_that_are_this` and `NO_ARR_SENDS_HERE` here as public names: `tuple_of`, `save_paths_of`, `arr_label`, `is_well_formed`, `is_this_client`, `clients_that_are`, `NO_ARR_SENDS_HERE`. `hardlink_futility` imports them. Its behaviour and strings don't change.
- Modify: `src/lintarr/clients/base.py`. Add `Backend.arr_dependent: Mapping[str, str] = field(default_factory=dict)`, documented as invariant id → the premise label for the failed-arr SKIP. Check that `Backend` is a frozen dataclass and that the default keeps every existing construction valid.
- Modify: `src/lintarr/checks/__init__.py`. Add `SHARED_ARR_DEPENDENT = {hardlink_futility.INVARIANT_ID: hardlink_futility.PREMISE}`.
- Modify: `src/lintarr/run.py`. `_resolve_failed_arr` applies when `finding.invariant` is in the merged mapping, `BACKENDS[...].arr_dependent | checks.SHARED_ARR_DEPENDENT`. Its SKIP premise is the mapped label. Its detail is unchanged for hardlink-futility. For any other invariant it reads "an arr could not be read, so its downloads could not be checked". Remove the `hardlink_futility` import from `run.py`.
- Modify:
  - every `ArrDownloadClient(...)` site;
  - `tests/fixtures/homelab_paths.py`: add `removeCompletedDownloads: false` to the raw bodies, and give `QBT_CATEGORIES` the full measured category bodies, each with `share_limit_action: "Default"`, `ratio_limit: -2`, `seeding_time_limit: -2`, `inactive_seeding_time_limit: -2` and `download_path: None`;
  - `tests/strategies.py`.
- Test: extend `tests/collect/test_arr_paths.py`; create `tests/checks/test_arr_clients.py`; extend `tests/test_run.py` (a fake backend's `arr_dependent` entry gets the failed-arr SKIP).

- [ ] **Step 1:** Write failing tests.
  - `test_reads_remove_completed`: true, false, absent, null and `"yes"`. The last three give `None`.
  - `test_reads_imported_category`: set, absent, empty and non-string.
  - `clients_that_are` cases: match, other kind, disabled, missing category, malformed, and an unreadable list.
  - The `arr_dependent` run test.
- [ ] **Step 2:** Run the tests and see them fail.
- [ ] **Step 3:** Implement. Move the helpers verbatim before renaming them.
- [ ] **Step 4:** Run `uv run pytest -q`. All pass.
- [ ] **Step 5:** Commit `feat: arr remove/imported-category facts, shared arr-client matcher, Backend.arr_dependent`.

### Task 2: qBittorrent share-limit questions

**Files:**
- Create: `src/lintarr/clients/qbittorrent/share_limits.py`
- Test: `tests/clients/test_qbittorrent_share_limits.py`

**Interfaces:**
- **Consumes, imported from `queue_liveness` and not moved:** `_as_limit`, `_truth`, `_is_a_seed_goal`, `_is_an_unusable_seed_criterion`, `_is_a_readable_seed_criterion`, `_can_be_reached`, `_no_global_ratio`, `_not_inactive_gate`, `_is_a_torrent_source`, `_any_of`, `_all_of`, `USE_GLOBAL` and `UNLIMITED`. If a name or semantics differs from this list, report it rather than guess.
- **Produces:**
  - `ACTION_BY_NAME = {"Stop": 0, "Remove": 1, "EnableSuperSeeding": 2, "RemoveWithContent": 3}`.
  - `effective_action(qbt, category: str) -> int | None`, as spec step 1.
  - `can_fire(qbt, arr, category: str) -> bool | None`, as spec step 3.
  - `can_fire_unrecognised(qbt, arr, category: str) -> bool | None`, as spec step 4.
  - Both reachability functions are an existential over the arr's indexers: `_any_of(_all_of((_is_a_torrent_source(i), arms(i))) for i in arr.indexers)`. An arr with no indexers gives `False`.
  - Each arm is a small function returning `bool | None`.
- **Per-arm rule for an indexer criterion:**
  - a goal → reachable, and recognised;
  - overriding (readable, not a goal, not `-2`) → `False`;
  - unusable → `None`;
  - deferring (absent, null or `-2`) → go to the category's own limit:
    - reachable → `True`, unrecognised;
    - `-1` or another non-`-2` unreachable value → `False`;
    - unreadable → `None`;
    - `-2` or key absent → go to the global, reachable means recognised.
- **Global readings:**
  - ratio: `not _no_global_ratio(qbt)`, with `None` kept as `None`;
  - seed time: `_truth(qbt.max_seeding_time_enabled)`;
  - inactive: `not _not_inactive_gate(qbt)`.
- **Inactive arm:** there is no indexer criterion, so start at the category's `inactive_seeding_time_limit`.
- **Seed-time goal:** for `arr.kind == "sonarr"`, `season_pack_seed_time` being a goal also counts as one.

- [ ] **Step 1:** Write failing tests.
  - `effective_action`:
    - Default → global;
    - each name overrides;
    - `""`, an unknown category and a missing key → global;
    - an unknown string → `None`;
    - unreadable categories → `None`.
  - `can_fire`:
    - homelab (one torrent indexer with no criteria) → True;
    - unlimited seeding → False;
    - an indexer goal with globals off → True;
    - an override blocks only its own arm;
    - a category's own limit → True;
    - category `-1` blocks the global;
    - the inactive gate → True;
    - a usenet-only arr → False;
    - no indexers → False;
    - an unusable criterion that decides → None;
    - a sonarr season-pack goal → True.
  - `can_fire_unrecognised`:
    - homelab → False (the global is recognised);
    - category ratio 2 with globals off → True;
    - an indexer goal only → False;
    - category `-2` inheriting a reachable global → False.
- [ ] **Step 2:** Run the tests and see them fail.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run `uv run pytest -q`.
- [ ] **Step 5:** Commit `feat(qbittorrent): share-limit action and reachability per category`.

### Task 3: The `orphan-inevitability` check

**Files:**
- Create: `src/lintarr/clients/qbittorrent/orphan_inevitability.py`.
- Modify: `src/lintarr/clients/qbittorrent/__init__.py`.
  - Append `orphan_inevitability.check` to `checks`, after the queue-liveness entry. Pass the function itself: it takes `(ClientInstance, arrs)` and reads `inst.common` and `inst.specific`.
  - Set `arr_dependent={orphan_inevitability.INVARIANT_ID: orphan_inevitability.PREMISE_NOTHING_RECLAIMS}`.
- Modify: `src/lintarr/clients/qbittorrent/therefore.py`. Add two entries, with the spec's text verbatim.
- Modify, without weakening, whichever exact-list or exit-code tests the suite turns red (expect `test_fake_backend`, `test_check_cli`, `test_cli`). Fix them through the arr transports serving `removeCompletedDownloads: true`, plus filtering by invariant.
- Modify: `tests/strategies.py`.
  - Make `CATEGORY` draw `share_limit_action` (valid names, `"Default"`, junk or absent) and `inactive_seeding_time_limit`.
  - Draw category-map keys from `{"tv", "movies"}` plus text.
  - Make `ARR_DOWNLOAD_CLIENTS` draw `remove_completed` and `imported_category`.
  - Update `test_the_strategies_still_generate_every_category_map_shape_they_claim` accordingly.
- Test: create `tests/clients/test_orphan_inevitability.py` and `tests/clients/test_orphan_inevitability_properties.py`; extend `tests/test_render_human.py`.

- [ ] **Step 1:** Write failing tests:
  - `test_homelab_passes_because_qbittorrent_removes_files`. Use an explicit torrent indexer and assert the detail. Its twin, action 1, FAILs `remove-keeps-files`.
  - `test_stop_without_arr_removal_fails_nothing_reclaims`
  - `test_stop_with_arr_removal_and_global_limits_passes`
  - `test_stop_with_arr_removal_but_category_own_limit_fails`
  - `test_imported_category_means_the_arr_cannot_remove`. Its action comes from the imported category.
  - `test_super_seeding_fails_nothing_reclaims`
  - `test_default_action_minus_one_fails_nothing_reclaims`
  - `test_category_remove_beats_global_remove_with_content`
  - `test_action_three_never_reads_reachability`. Junk criteria still give PASS.
  - `test_unlimited_seeding_passes_with_detail`
  - `test_remove_keeps_files_wins_over_nothing_reclaims` (two arrs)
  - `test_unknown_remove_completed_skips_only_for_stop`
  - `test_unknown_action_skips`
  - `test_unknown_in_one_arr_does_not_hide_a_fail_in_another`
  - `test_no_arrs_is_not_applicable`
  - `test_arrs_using_another_client_pass_with_detail`
  - `test_failed_arr_turns_pass_into_skip`, via `run_checks`
  - `test_render_shows_each_therefore_line`
  - **Properties:**
    - the check never raises;
    - every FAIL carries exactly its one premise, and that premise holds;
    - it never PASSes when, for a matched arr, the effective action is unknown;
    - a hand-built control proves the strategy reaches a matched category whose action is not Default.
- [ ] **Step 2:** Run the tests and see them fail.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run `uv run pytest -q`.
- [ ] **Step 5:** Mutation round. Record the results; all must go red:
  1. Treat action 1 as reclaiming.
  2. Ignore `remove_completed`.
  3. Drop the "no limit can fire → no conflict" rule.
  4. Ignore the category action.
  5. Treat a category-own arm as recognised.
  6. Ignore `imported_category`.
- [ ] **Step 6:** Commit `feat(qbittorrent): orphan-inevitability`.

### Task 4: Live verification and docs (controller)

- [ ] **Live check on homelab.** From a throwaway checkout at the head, run strict `check --json` with the deployed env. It must exit 0 with `orphan-inevitability` PASS and the detail "qBittorrent removes files at the limit" for both arrs. Remove the checkout afterwards.
- [ ] **`CONTINUATION.md`.** Record this slice, the next slice (`seed-goal-conflict`), and the follow-up to conformance-test the share-limit actions. Commit.
