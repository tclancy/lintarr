# `hardlink-futility` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the first shared check. It FAILs when an arr has hardlinks enabled
but a download client saves that arr's downloads on a different mount, in the
arr's own view, from one of the arr's root folders.

**Architecture:** The arr adapter gains five facts from five more `GET`s.
`ClientFacts` gains its first neutral field, `save_paths`, which the qBittorrent
backend populates. Pure path helpers translate paths and pick mounts. A shared
check in `lintarr.checks` combines them into one finding per client with one
premise. A nightly container test pins the `EXDEV` axiom the FAIL rests on.

**Tech Stack:** Python 3.13, uv, httpx, click, pytest, Hypothesis, Docker (container tests only).

**Spec:** `docs/superpowers/specs/2026-11-19-hardlink-futility-design.md`. Read its "The check" and "Model changes" sections. They are binding.

## Global Constraints

- Facts are `Known` or `Unknown`, never defaulted. An unreadable input reaches the check as `Unknown`, and the check reports SKIP, never PASS, unless the spec's rules settle it without that input (hardlinks known off; same mount).
- Every HTTP call goes through `ReadOnlyClient`. `GET` only.
- An HTTP 404 on one of the five new arr endpoints makes that one fact `Unknown("field-absent", …)`. Any other `ServiceError` propagates unchanged and ERRORs the arr, as indexers do. A body of the wrong shape raises `ServiceError("bad-response", …)`.
- Invariant id is `hardlink-futility`. Conflict is `cross-mount`. The single premise label is `arr.hardlink_import_crosses_a_mount`. The finding's instance is the client label `<kind>[<name>]`.
- qBittorrent's existing queue-liveness findings, edge-trigger keys and "Therefore" text stay byte-identical.
- `tests/fixtures/homelab.py` holds homelab#393 incident evidence: never change its recorded values. New `QbtInstance` fields get `Unknown("field-absent", "not recorded in homelab#393")` there. New measured values go in `tests/fixtures/homelab_paths.py`.
- The default pytest run deselects container tests (`pyproject.toml` `addopts = "-m 'not container'"`). Fast suite: `uv run pytest -q`. Container tests: `uv run pytest -m container …`.
- Never weaken an existing assertion to get green. Filter it to the invariant it was written about, and say so in the report.
- No `--no-verify`. Use `uv run` for everything.

## Review Focus

1. **Tom's stack must PASS.** Two arrs, one qBittorrent, everything under `/data`, `/config` a separate mount, and no remote path mappings. `homelab_paths.py` mirrors the spec's Inputs table. Task 4 has the PASS test, and Task 6 verifies it live under strict mode.
2. **A trailing slash, or a leading `//`, in a root folder or save path** must not change the mount picked. Task 3 tests it.
3. **A prefix that isn't a path component:** `/data2/x` is not on mount `/data`, and remote path `/dl` must not rewrite `/dlx/y`. Task 3 tests both.
4. **No new pages.** None of these may produce SKIP or N/A: an arr download client of another kind or disabled; a stack whose arrs use another client; a bare-metal arr on `/`. Task 4 tests each.
5. **An unknown in one triple must not hide a FAIL in another**, and a FAIL lists every conflicting triple. Task 4 tests it.

---

### Task 1: Arr adapter reads hardlink, root folder, mount, mapping and download-client facts

**Files:**
- Modify: `src/lintarr/models.py` (add `RootFolder`, `RemotePathMapping`, `ArrDownloadClient`, and five `ArrInstance` fields)
- Modify: `src/lintarr/collect/arr.py`
- Modify: `src/lintarr/cli.py`. `_fact_to_dict` must serialise a `Known` whose value is a tuple of dataclasses as a list of dicts, and the human `dump-facts` rendering must show the new facts.
- Modify: `tests/strategies.py`. `ARR_INSTANCES` draws each new fact from known valid values, `UNKNOWN_FACTS`, *and* `JUNK_VALUES`-shaped junk, so the check's properties search malformed inputs.
- Modify: every `ArrInstance(...)` site in `tests/` and `src/`.
- Modify: every mock arr transport in `tests/` that a check-running test uses (at least `tests/test_check_cli.py`'s `_transport`; grep for `/api/v3/indexer` to find the others). Serve the five new paths with homelab-shaped bodies from `tests/fixtures/homelab_paths.py`. This keeps exit codes unchanged once Task 4 registers the check.
- Create: `tests/fixtures/homelab_paths.py`. It holds raw API bodies measured 2026-11-19 (from the spec's Inputs table), plus `homelab_arr(kind="sonarr", name="tv")` and `arr_with(base=None, **overrides)` builders. Overrides are raw values wrapped `Known`, or an already-built `Fact` passed through, as `qbt_with` does.
- Test: `tests/collect/test_arr_paths.py`

**Interfaces (produced):**
- `RootFolder(path: str, accessible: bool | None)`
- `RemotePathMapping(host: str, remote_path: str, local_path: str)`
- `ArrDownloadClient(name: str, implementation: str, enabled: bool, host: str | None, category: str)`. `category` is `""` when the field is absent or empty.
- All three are frozen and slotted.
- `ArrInstance` gains, after `indexers` and with no defaults:
  - `copy_using_hardlinks: Fact[bool]`
  - `root_folders: Fact[tuple[RootFolder, ...]]`
  - `mounts: Fact[tuple[str, ...]]`
  - `remote_path_mappings: Fact[tuple[RemotePathMapping, ...]]` (API order preserved)
  - `download_clients: Fact[tuple[ArrDownloadClient, ...]]`

- [ ] **Step 1: Write failing tests** in `tests/collect/test_arr_paths.py`, using a mock transport serving all seven paths:
  - `test_reads_copy_using_hardlinks`
  - `test_non_bool_copy_using_hardlinks_is_bad_response`: the string `"false"`.
  - `test_absent_copy_using_hardlinks_is_unknown`
  - `test_reads_root_folders_with_accessible`: an absent `accessible` gives `None`.
  - `test_reads_mount_paths`: `("/", "/config", "/data")`.
  - `test_reads_remote_path_mappings_verbatim_in_order`
  - `test_reads_download_client_category_for_sonarr_and_radarr`: `tvCategory` and `movieCategory`.
  - `test_download_client_without_category_has_empty_category`: covers both absent and `""`.
  - `test_download_client_host_is_read_from_fields`: an absent host gives `None`.
  - `test_a_404_on_one_endpoint_is_unknown_and_the_rest_still_read`: parametrize over the five paths.
  - `test_a_500_on_a_new_endpoint_errors_the_arr`
  - `test_wrong_shape_is_bad_response`: parametrize the shapes. Mediamanagement as a list; the others as an object; an entry lacking a required key; `enable` not a bool; `accessible` neither bool nor absent.

- [ ] **Step 2: Run** `uv run pytest tests/collect/test_arr_paths.py -q`. Expected: FAIL.

- [ ] **Step 3: Implement.**
  - Write five readers `(client, *, version) -> Fact[...]` and one `_optional(client, path, reader)` mapping `ServiceError` with `status == 404` to `Unknown`.
  - `source=f"GET {path}"`.
  - Then update the construction sites, strategies, transports and `dump-facts`.

- [ ] **Step 4: Add** `test_dump_facts_shows_the_new_arr_facts`, covering JSON (`download_clients.value[0].category == "tv"`) and human output (each new key name appears).

- [ ] **Step 5: Run** `uv run pytest -q`. Expected: all pass.

- [ ] **Step 6: Commit** `feat(arr): read hardlink, root folder, mount, mapping and download-client facts`

### Task 2: First client-neutral fact, `ClientFacts.save_paths`

**Files:**
- Modify: `src/lintarr/models.py`. Add `ClientFacts.save_paths: Fact[Mapping[str, str]]`, rewrite the `ClientFacts` docstring, and add three `QbtInstance` fields.
- Modify: `src/lintarr/clients/qbittorrent/collect.py`. Add `save_path`, `auto_tmm_enabled` and `use_category_paths_in_manual_mode` to `_PREF_KEYS`.
- Create: `src/lintarr/clients/qbittorrent/save_paths.py`
- Modify: `src/lintarr/clients/qbittorrent/__init__.py` (`ClientFacts(save_paths(qbt))`)
- Modify: `tests/fakes/backend.py`. Return `Known({"": "/data/torrents", "tv": "/data/torrents/tv"})`.
- Modify: `tests/fixtures/clients.py`. `as_client` builds `ClientFacts(save_paths(qbt))`.
- Modify: `tests/fixtures/homelab.py`. Add the three new fields as `Unknown`, per the Global Constraints.
- Modify: `tests/fixtures/homelab_paths.py`. Add `homelab_qbt()`, which is `repaired_qbt()` with the three prefs and categories set to the 2026-11-19 measured values: `save_path="/data/torrents"`, auto TMM on, manual-mode category paths off, `savePath` `/data/torrents/tv` and `/data/torrents/movies`.
- Modify: `tests/strategies.py`. `qbt_instances` draws the three new facts.
- Modify: `src/lintarr/cli.py`. `dump-facts` shows `common.save_paths` in JSON and human output.
- Modify: `docs/adding-a-backend.md`. State the obligation, with the qBittorrent rule as the example.
- Test: `tests/clients/test_qbittorrent_save_paths.py`; extend `tests/clients/test_fake_backend.py`.

**Interfaces (produced):** `save_paths(qbt: QbtInstance) -> Fact[Mapping[str, str]]`, implementing the spec's qBittorrent rule exactly, including the `""` key.

- [ ] **Step 1: Write failing tests**:
  - `test_uncategorised_key_is_the_default_save_path`
  - `test_auto_tmm_uses_each_category_save_path`
  - `test_empty_category_save_path_is_default_plus_name`
  - `test_relative_category_save_path_is_joined_to_default`
  - `test_manual_mode_maps_every_category_to_the_default`
  - `test_manual_mode_with_category_paths_on_uses_category_paths`
  - `test_any_needed_unknown_input_makes_the_mapping_unknown`: parametrize over an unknown pref, unknown categories, categories not a dict, an entry not a dict, and a `savePath` that isn't a string.
  - `test_known_mapping_cites_its_sources`
  - `test_collect_reads_the_three_new_prefs`
  - `test_fake_backend_populates_save_paths`

- [ ] **Step 2: Run.** Expected: FAIL.

- [ ] **Step 3: Implement.** Join with `PurePosixPath`, and keep the values as `str`.

- [ ] **Step 4: Run** `uv run pytest -q`. Expected: all pass. `dump-facts` gains lines, and nothing else changes.

- [ ] **Step 5: Commit** `feat(clients): first neutral fact, save_paths, populated by every backend`

### Task 3: Path helpers

**Files:**
- Create: `src/lintarr/checks/paths.py`
- Test: `tests/checks/__init__.py`, `tests/checks/test_paths.py`

**Interfaces (produced):**
- `posix_abs(raw: object) -> PurePosixPath | None` returns `None` unless `raw` is a non-empty `str` starting with `/` and containing no `\`. It collapses a leading `//` to `/` and drops a trailing slash.
- `translate(raw: str, mappings: Iterable[RemotePathMapping], host: str | None) -> str` works on raw strings, before any POSIX check. It finds the **first** mapping, in iteration order, with `host` equal to `host` case-insensitively and `remote_path` a prefix of `raw` on a separator boundary. The separator is `/` or `\`; trailing separators on `remote_path` are ignored when testing. It then returns `local_path` joined to the remainder, with the remainder's `\` turned into `/`. If no mapping matches, `raw` comes back unchanged. A `host` of `None` matches nothing.
- `mount_of(path: PurePosixPath, mounts: Iterable[str]) -> PurePosixPath | None` returns the longest `posix_abs(mount)` that `path.is_relative_to`, or `None`. It skips unreadable mounts. `/` is an ordinary mount here; the check decides what `/` means.

- [ ] **Step 1: Write failing tests**:
  - `test_trailing_slash_and_leading_double_slash_normalise`
  - `test_windows_relative_empty_and_non_string_are_none`
  - `test_mount_is_longest_containing_prefix`
  - `test_mount_respects_components`: `/data2/x` with mounts `("/", "/data")` gives `/`.
  - `test_translate_takes_the_first_match_not_the_longest`
  - `test_translate_ignores_other_hosts_and_is_case_insensitive_on_host`
  - `test_translate_respects_components`
  - `test_translate_maps_a_windows_client_path_to_posix`: `C:\Downloads\tv` with mapping `C:\Downloads\` → `/data/torrents/` gives `/data/torrents/tv`.
  - `test_no_mapping_returns_the_path_unchanged`
  - Hypothesis `test_mount_of_is_the_longest_listed_prefix`

- [ ] **Step 2: Run.** Expected: FAIL.

- [ ] **Step 3: Implement.**

- [ ] **Step 4: Run.** Expected: PASS.

- [ ] **Step 5: Commit** `feat(checks): posix path, remote-mapping and mount helpers`

### Task 4: The `hardlink-futility` shared check

**Files:**
- Create: `src/lintarr/checks/hardlink_futility.py`
- Modify: `src/lintarr/checks/__init__.py`. Set `SHARED_CHECKS = (hardlink_futility.check,)`, and add `SHARED_THEREFORE` and `SHARED_INVARIANTS`.
- Modify: `src/lintarr/cli.py`. `_therefore` consults `checks.SHARED_THEREFORE` for an invariant in `SHARED_INVARIANTS`, **before** the backend lookup's `None` early return.
- Modify, without weakening: `tests/clients/test_fake_backend.py` (exact finding lists around lines 54-63; exit 0 around 104-109). Filter the lists to the invariants they pin, then add a separate assertion for the new finding. Check any other exact-list or exit-code test the full suite turns red, the same way.
- Test: `tests/checks/test_hardlink_futility.py`, `tests/checks/test_hardlink_futility_properties.py`; extend `tests/test_render_human.py`

**Interfaces:**
- Consumes: Tasks 1-3 and `combinator.premise`/`conflict_if`.
- Produces:
  - `INVARIANT_ID`, `CROSS_MOUNT` and `PREMISE` as in the Global Constraints.
  - `check(instance: ClientInstance, arrs: tuple[ArrInstance, ...]) -> Finding`.
  - `SHARED_THEREFORE = {(INVARIANT_ID, CROSS_MOUNT): <spec Therefore text verbatim>}`.
  - `SHARED_INVARIANTS = frozenset(k[0] for k in SHARED_THEREFORE)`.
- **Arr-failure SKIP:** the check sees no errors. So the "an arr failed to collect" rule (spec, "Which arrs are involved") goes in `run.py`. Add `_resolve_failed_arr(finding, facts)`, which turns a `hardlink-futility` PASS or N/A into SKIP when `_an_arr_failed(facts)`. Its detail is "an arr could not be read, so its imports could not be checked". Wire it into `_client_findings` next to `_resolve_missing_arrs`, guarded by `finding.invariant == hardlink_futility.INVARIANT_ID`, so queue-liveness is untouched.

- [ ] **Step 1: Write failing tests**, built from `homelab_paths` builders:
  - `test_homelab_passes`
  - `test_separate_download_and_library_mounts_fail`: mounts `("/", "/downloads", "/tv")`, save `/downloads/tv`, root `/tv/shows`. Asserts the premise holds and the detail matches the spec's format exactly.
  - Mutation twin in the same test: change only the mounts to `("/", "/data")` with save `/data/torrents/tv` and root `/data/media/tv`. PASS.
  - `test_hardlinks_off_passes_even_with_unknown_mounts`
  - `test_remote_path_mapping_is_applied_before_picking_the_mount` (PASS), and its other-host twin (SKIP: the download is only on `/` while the root is on `/data`).
  - `test_bare_metal_arr_with_everything_on_root_passes`: mounts `("/",)`.
  - `test_blank_arr_category_uses_the_uncategorised_save_path`
  - `test_inaccessible_root_folder_is_ignored_and_named`
  - `test_no_arrs_is_not_applicable`
  - `test_arrs_that_use_no_client_of_this_kind_pass_with_detail`: Sabnzbd only, a disabled entry, or a category this client lacks.
  - `test_any_needed_unknown_input_skips`: parametrize over root folders, mounts, mappings, download clients and client `save_paths` as `Unknown`, with hardlinks on.
  - `test_unknown_in_one_triple_does_not_hide_a_fail_in_another`
  - `test_fail_lists_every_conflicting_triple`
  - `test_finding_instance_is_the_client_label`
  - `test_a_failed_arr_turns_pass_into_skip` (through `run_checks`)
  - `test_shared_check_runs_for_the_fake_backend_too` (through `run_checks`, using the `tests/clients/conftest.py` registration)
  - `test_render_shows_the_therefore_line_for_a_cross_mount_fail`
  - Properties file, built on `qbt_instances()` + `as_client` and `ARR_INSTANCES`:
    - the check never raises;
    - every FAIL carries exactly the one premise, holding;
    - the outcome is never PASS while an input the decision needed was unreadable. Use a small oracle: if all arrs have hardlinks known false, or no arr has a matching download client, PASS is allowed; otherwise PASS requires every involved fact to be `Known`.

- [ ] **Step 2: Run.** Expected: FAIL.

- [ ] **Step 3: Implement** in small functions following the spec.

- [ ] **Step 4: Run** `uv run pytest -q`. Expected: all pass.

- [ ] **Step 5: Mutation round.** Make each mutation by hand, run the check's tests, and record red or green in the report:
  1. Invert the mount comparison.
  2. Drop the hardlinks-known-false short-circuit.
  3. Treat a "one side only on `/`" triple as a conflict instead of unknown.

  All must go red.

- [ ] **Step 6: Commit** `feat(checks): hardlink-futility, the first shared check`

### Task 5: Container test: `link(2)` across bind mounts

**Files:**
- Create: `tests/conformance/test_hardlink_exdev.py`
- Modify: `.github/workflows/conformance.yml` (timing comment only)
- Modify: the `src/lintarr/checks/hardlink_futility.py` docstring (the spec's axiom table, citing this test)

- [ ] **Step 1: Write the tests** with `pytestmark = pytest.mark.container`.
  - Image: `alpine:3.20`, pinned by tag the way `tests/conformance/image.py` pins.
  - Skip if `shutil.which("docker")` is missing or `docker info` fails.
  - Use `tmp_path` with `a/` and `b/`, and create `a/f`.
  - `test_link_across_two_bind_mounts_is_exdev`: `-v a:/a -v b:/b`, `ln /a/f /b/f` exits non-zero, and `"cross-device link" in stderr.lower()`.
  - `test_link_within_one_bind_mount_succeeds`: `-v tmp:/x`, `ln /x/a/f /x/b/f` exits 0, and `os.stat(a/f).st_nlink == 2` on the host.
  - On macOS, Docker Desktop shares `/private/tmp` paths. If the host `st_nlink` assertion can't hold through the VM file share, assert it inside the container with `stat -c %h` and say so in the report.

- [ ] **Step 2: Run** `uv run pytest -m container tests/conformance/test_hardlink_exdev.py -v`. Expected: 2 passed. Record the wall time.

- [ ] **Step 3: Update** the workflow comment and the docstring.

- [ ] **Step 4: Commit** `test(conformance): link(2) across bind mounts is EXDEV`

### Task 6: Live verification and docs (controller)

- [ ] **Step 1: Run it on homelab** from a throwaway checkout of this branch, outside `~/sources/lintarr`, with the deployed env file.
  - Run `uv run lintarr dump-facts`. The new arr facts and `save_paths` must be `Known`.
  - Run `uv run lintarr check --json` in **strict** mode (no state file). It must exit 0 and show `hardlink-futility` PASS for `qbittorrent[main]`.
  - Remove the checkout afterwards.
- [ ] **Step 2: Update `CONTINUATION.md`.** Clear the resolved BLOCKED ON lines (conformance run and homelab#692 are done), describe this slice, and name the next ones: `orphan-inevitability`, then `seed-goal-conflict`. Commit `docs(continuation): hardlink-futility slice; clear resolved blockers`.
