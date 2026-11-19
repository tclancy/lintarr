# `hardlink-futility` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the first shared check. It FAILs when an arr has hardlinks enabled
but a download client saves that arr's downloads on a different mount, in the
arr's own view, from one of the arr's root folders.

**Architecture:** The arr adapter gains five facts read from five more `GET`s.
`ClientFacts` gains its first neutral field, `save_paths`, which the qBittorrent
backend populates. Pure path helpers translate paths and pick mounts. A shared
check in `lintarr.checks` combines them into one finding per client with one
premise. A nightly conformance test pins the `EXDEV` axiom the FAIL rests on.

**Tech Stack:** Python 3.13, uv, httpx, click, pytest, Hypothesis, Docker (conformance only).

**Spec:** `docs/superpowers/specs/2026-11-19-hardlink-futility-design.md`

## Global Constraints

- Facts are `Known` or `Unknown`, never defaulted. An unreadable input reaches the check as `Unknown`, and the check reports SKIP, never PASS.
- Every HTTP call goes through `ReadOnlyClient`. `GET` only.
- An HTTP 404 on one of the five new arr endpoints makes that one fact `Unknown("field-absent", …)`. Any other `ServiceError` propagates unchanged and ERRORs the arr, as indexers do. A body of the wrong shape raises `ServiceError("bad-response", …)`.
- Invariant id is `hardlink-futility`. Conflict is `cross-mount`. The single premise label is `arr.hardlink_import_crosses_a_mount`. The finding's instance is the client label `<kind>[<name>]`.
- Paths are compared as `PurePosixPath` and must be absolute POSIX. Anything else (Windows drive, relative, empty, non-string) is unknown for that triple.
- A path whose longest containing mount is `/` is unknown for that triple, not a verdict.
- qBittorrent's existing labels, findings, edge-trigger keys and "Therefore" text stay byte-identical.
- Any number or "N passed" quoted in a report comes from a run on the current checkout.
- No `--no-verify`. Use `uv run` for everything.

## Review Focus

1. **Tom's stack must PASS.** Two arrs, one qBittorrent, everything under `/data`, `/config` a separate mount, and remote path mappings empty. The fixture `homelab_arr()` must mirror the measured values in the spec's Inputs table. Task 4 has the PASS test, and Task 6 verifies live.
2. **A trailing slash or `//` in a root folder or save path** (`/data/media/tv/`) must not change the mount picked. Task 3 tests it.
3. **A prefix that isn't a path component:** `/data2/x` is not on mount `/data`, and remote path `/dl` must not rewrite `/dlx/y`. Task 3 tests both.
4. **An arr download client for a kind lintarr doesn't collect** (SABnzbd, or a disabled qBittorrent entry) must be ignored, not treated as unknown. Otherwise every Usenet user gets a permanent SKIP and a page. Task 4 tests it.
5. **An unknown in one triple must not hide a FAIL in another**, and a FAIL must still list every conflicting triple. Task 4 tests it.

---

### Task 1: Arr adapter reads hardlink, root folder, mount, mapping and download-client facts

**Files:**
- Modify: `src/lintarr/models.py` (add `RemotePathMapping`, `ArrDownloadClient`, five `ArrInstance` fields)
- Modify: `src/lintarr/collect/arr.py`
- Modify: `src/lintarr/cli.py` (`_instance_to_dict` handles a `Known` whose value is a tuple of dataclasses)
- Modify: `tests/strategies.py` (`ARR_INSTANCES` draws the new facts)
- Modify: every `ArrInstance(...)` construction in `tests/` (5 sites at time of writing)
- Create: `tests/fixtures/arrs.py` with `homelab_arr(kind, name, category)` and `arr_with(**overrides)`
- Test: `tests/collect/test_arr_paths.py`

**Interfaces:**
- Produces, in `models.py`:
  - `RemotePathMapping(host: str, remote_path: str, local_path: str)`, frozen and slotted.
  - `ArrDownloadClient(name: str, implementation: str, enabled: bool, host: str | None, category: str | None)`, frozen and slotted.
  - `ArrInstance` gains, after `indexers`: `copy_using_hardlinks: Fact[bool]`, `root_folders: Fact[tuple[str, ...]]`, `mounts: Fact[tuple[str, ...]]`, `remote_path_mappings: Fact[tuple[RemotePathMapping, ...]]`, `download_clients: Fact[tuple[ArrDownloadClient, ...]]`. No defaults.
- Produces `tests/fixtures/arrs.py`:
  - `homelab_arr(kind="sonarr", name="tv", category="tv") -> ArrInstance`, with every fact `Known` at the spec's measured values. Root `/data/media/tv` (or `/data/media/movies` for radarr), mounts `("/", "/config", "/data")`, no mappings, one enabled `QBittorrent` client on host `gluetun`, and the given category.
  - `arr_with(base=None, **overrides) -> ArrInstance`. Overrides are raw values wrapped `Known`, or an already-built `Fact` passed through, like `qbt_with`.

- [ ] **Step 1: Write failing collector tests** in `tests/collect/test_arr_paths.py`. Extend the `_collect`-style mock transport to serve all seven paths.
  - `test_reads_copy_using_hardlinks`: `{"copyUsingHardlinks": true}` gives `Known(True)`.
  - `test_reads_root_folder_paths`: `[{"path": "/data/media/tv", "freeSpace": 1}]` gives `Known(("/data/media/tv",))`.
  - `test_reads_mount_paths`: the three diskspace entries give `Known(("/", "/config", "/data"))`.
  - `test_reads_remote_path_mappings`: `[{"host": "qbt", "remotePath": "/downloads/", "localPath": "/data/torrents/"}]` gives one `RemotePathMapping("qbt", "/downloads/", "/data/torrents/")`, verbatim, not normalised.
  - `test_reads_download_client_category_for_sonarr_and_radarr`: a `tvCategory` field gives `category="tv"`, and a `movieCategory` field gives `category="movies"`.
  - `test_download_client_without_category_field_has_category_none`: covers both an absent field and `""`.
  - `test_download_client_host_is_read_from_fields`: `host` is `"gluetun"`. An absent host field gives `None`.
  - `test_a_404_on_one_endpoint_is_unknown_and_the_rest_still_read`: parametrize over the five paths. That fact is `Unknown("field-absent")` and the indexers are still read.
  - `test_a_500_on_a_new_endpoint_errors_the_arr`: raises `ServiceError` with kind `bad-response`.
  - `test_wrong_shape_is_bad_response`: parametrize. Mediamanagement as a list; rootfolder, diskspace, remotepathmapping or downloadclient as an object; an entry lacking `path`, `host`, `remotePath`, `localPath`, `name`, `implementation` or `enable`; `enable` not a bool; `copyUsingHardlinks` absent (that one is `Unknown("field-absent")`, not an error, like `read()`).
  - Copy the exact field names from the spec's Inputs table.

- [ ] **Step 2: Run** `uv run pytest tests/collect/test_arr_paths.py -q`. Expected: FAIL (import or attribute errors).

- [ ] **Step 3: Implement**
  - The models.
  - In `arr.py`, five readers, each `(client, *, version) -> Fact[...]`, plus `_optional(client, path, reader)`, which maps a `ServiceError` with `status == 404` to `Unknown("field-absent", f"{path}: HTTP 404")`.
  - `collect_arr` passes all five.
  - Each `Known` carries `source=f"GET {path}"`.
  - Update every `ArrInstance(...)` site, `ARR_INSTANCES` (draw each new fact from `known(...)` or `UNKNOWN_FACTS`) and `_instance_to_dict`. `dump-facts` must render `download_clients` and `remote_path_mappings` as lists of dicts inside the fact's `value`.

- [ ] **Step 4: Add** `test_dump_facts_shows_the_new_arr_facts` to the existing dump-facts test module. The JSON for one arr has all five keys, and `download_clients.value[0].category == "tv"`.

- [ ] **Step 5: Run the full suite** with `uv run pytest -q -m "not conformance"`, or the repo's default fast selection (see `pyproject.toml`). Expected: all pass.

- [ ] **Step 6: Commit** `feat(arr): read hardlink, root folder, mount, mapping and download-client facts`

### Task 2: First client-neutral fact, `ClientFacts.save_paths`

**Files:**
- Modify: `src/lintarr/models.py` (`ClientFacts.save_paths`, rewrite its docstring)
- Modify: `src/lintarr/clients/qbittorrent/collect.py` (read `save_path` and `auto_tmm_enabled`)
- Modify: `src/lintarr/clients/qbittorrent/__init__.py` (build `ClientFacts` from the instance)
- Create: `src/lintarr/clients/qbittorrent/save_paths.py`
- Modify: `tests/fakes/backend.py`, `tests/fixtures/clients.py`, `tests/fixtures/homelab.py`, `tests/strategies.py`
- Modify: `src/lintarr/cli.py` (`_client_to_dict` now includes `common`, under the key `"common"`)
- Modify: `docs/adding-a-backend.md` (the obligation, with the qBittorrent rule as the example)
- Test: `tests/clients/test_qbittorrent_save_paths.py`, extend `tests/clients/test_fake_backend.py`

**Interfaces:**
- Consumes: `QbtInstance.categories: Fact[dict[str, Any]]` (existing).
- Produces:
  - `ClientFacts(save_paths: Fact[Mapping[str, str]])`.
  - `QbtInstance` gains `save_path: Fact[str]` and `auto_tmm_enabled: Fact[bool]` (both read from `/api/v2/app/preferences`).
  - `save_paths(qbt: QbtInstance) -> Fact[Mapping[str, str]]` in `clients/qbittorrent/save_paths.py`.
  - `as_client(qbt)` builds `ClientFacts(save_paths(qbt))`.
  - `homelab.py`'s recorded values gain `save_path="/data/torrents"`, `auto_tmm_enabled=True`, and categories with `savePath` `/data/torrents/tv` and `/data/torrents/movies` (the measured values).

- [ ] **Step 1: Write failing tests** in `tests/clients/test_qbittorrent_save_paths.py`:
  - `test_auto_tmm_uses_each_category_save_path`: gives `{"tv": "/data/torrents/tv", "movies": "/data/torrents/movies"}`.
  - `test_empty_category_save_path_is_default_plus_name`: `savePath: ""` and `save_path="/data/torrents"` give `/data/torrents/tv`.
  - `test_relative_category_save_path_is_joined_to_default`: `savePath: "tv-x"` gives `/data/torrents/tv-x`.
  - `test_manual_mode_maps_every_category_to_the_default`: `auto_tmm_enabled=False`.
  - `test_any_unknown_input_makes_the_mapping_unknown`: parametrize over unknown `save_path`, unknown `auto_tmm_enabled`, unknown categories, categories not a dict, a category entry not a dict, and `savePath` not a string.
  - `test_known_mapping_cites_its_sources`: `source` names both prefs and categories.
  - `test_collect_reads_save_path_and_auto_tmm`: in the existing qbt facts test style, against a mock transport.
  - In `test_fake_backend.py`, `test_fake_backend_populates_save_paths`.

- [ ] **Step 2: Run** them. Expected: FAIL.

- [ ] **Step 3: Implement.**
  - Add `"save_path"` and `"auto_tmm_enabled"` to `_PREF_KEYS`.
  - Write `save_paths()`, joining with `PurePosixPath`. Keep `str` values. Don't normalise absolute ones; Task 3 does that at comparison time.
  - The backend's `collect` passes `ClientFacts(save_paths(qbt))`.
  - The fake backend returns `Known({"tv": "/data/torrents/tv"})` by default.
  - Update the `qbt_instances` strategy with the two new facts.
  - Update the `ClientFacts` docstring to name `save_paths` as the first neutral field and the check that reads it.

- [ ] **Step 4: Run the full fast suite.** Expected: all pass. The queue-liveness outputs are unchanged, because `test_render_human` and the edge-key tests pin them.

- [ ] **Step 5: Commit** `feat(clients): first neutral fact, save_paths, populated by every backend`

### Task 3: Path helpers

**Files:**
- Create: `src/lintarr/checks/paths.py`
- Test: `tests/checks/__init__.py`, `tests/checks/test_paths.py`

**Interfaces:**
- Consumes: `RemotePathMapping` (Task 1).
- Produces:
  - `posix_abs(raw: object) -> PurePosixPath | None`: `None` unless `raw` is a non-empty `str` starting with `/` and containing no `\`. Otherwise `PurePosixPath(raw)`, which drops the trailing slash and collapses `//`.
  - `translate(path: PurePosixPath, mappings: Iterable[RemotePathMapping], host: str | None) -> PurePosixPath | None`: applies the mapping whose `host` equals `host` case-insensitively and whose `remote_path` is the longest component-wise prefix of `path`. Returns `path` unchanged when none matches. Returns `None` if a matching mapping's paths fail `posix_abs`.
  - `mount_of(path: PurePosixPath, mounts: Iterable[str]) -> PurePosixPath | None`: the longest `posix_abs(mount)` that `path.is_relative_to`. `None` if no mount contains it. Unreadable mount strings are skipped.

- [ ] **Step 1: Write failing tests**:
  - `test_trailing_slash_and_double_slash_are_the_same_path`
  - `test_windows_relative_empty_and_non_string_are_none`: `"C:\\tv"`, `"tv"`, `""`, `None`, `3`.
  - `test_mount_is_longest_containing_prefix`: `/data/media/tv` with mounts `("/", "/config", "/data")` gives `/data`.
  - `test_mount_respects_components`: `/data2/x` with mounts `("/", "/data")` gives `/`.
  - `test_translate_uses_longest_remote_prefix_for_the_matching_host`
  - `test_translate_ignores_other_hosts_and_is_case_insensitive_on_host`
  - `test_translate_respects_components`: `/dl` must not rewrite `/dlx/y`.
  - `test_no_mapping_returns_the_path_unchanged`
  - Hypothesis `test_mount_of_is_a_prefix_of_its_input`: whenever it isn't `None`, `path.is_relative_to(result)`, and no longer listed mount also contains `path`.

- [ ] **Step 2: Run.** Expected: FAIL.

- [ ] **Step 3: Implement.** Each function is under 15 lines.

- [ ] **Step 4: Run.** Expected: PASS.

- [ ] **Step 5: Commit** `feat(checks): posix path, remote-mapping and mount helpers`

### Task 4: The `hardlink-futility` shared check

**Files:**
- Create: `src/lintarr/checks/hardlink_futility.py`
- Modify: `src/lintarr/checks/__init__.py` (`SHARED_CHECKS = (hardlink_futility.check,)` and `SHARED_THEREFORE`)
- Modify: `src/lintarr/cli.py` (`_therefore` consults `checks.SHARED_THEREFORE` for an invariant in `SHARED_INVARIANTS`; the backend lookup is unchanged)
- Modify: the property-test tables in `tests/test_properties.py` and `tests/test_needs_are_load_bearing.py`, where they enumerate invariants, premise labels or conflicts, so the shared check is covered. Read them and extend each enumerating table. Don't special-case.
- Test: `tests/checks/test_hardlink_futility.py`; extend `tests/test_render_human.py`

**Interfaces:**
- Consumes: Tasks 1-3 and `combinator.premise`/`conflict_if`.
- Produces:
  - `INVARIANT_ID = "hardlink-futility"`, `CROSS_MOUNT = "cross-mount"`, `PREMISE = "arr.hardlink_import_crosses_a_mount"`.
  - `check(instance: ClientInstance, arrs: tuple[ArrInstance, ...]) -> Finding`.
  - `checks.SHARED_THEREFORE: Mapping[tuple[str, str], str]` holds `{(INVARIANT_ID, CROSS_MOUNT): <spec's Therefore text, verbatim>}`.
  - `checks.SHARED_INVARIANTS: frozenset[str]` is derived from the keys of `SHARED_THEREFORE`.
  - Internals, suggested (names are the builder's): `_users(instance, arrs) -> list[(arr, dl_client)]`, where a client of another kind, a disabled one, or one whose category this client doesn't define is excluded; `_triple_state(...) -> tuple[bool | None, str]` returns a state plus a detail fragment; `_aggregate`.
- Ambiguity rule from the spec: an arr download client is ambiguous when more than one collected client of its kind defines its category. The check has only one instance, so it can't see the others. It sees `arrs` but not `facts.clients`. **Ruling:** drop the ambiguity branch from this slice and match on kind plus category only. With two same-kind clients both defining the category, both report on that arr. Record that in the module docstring as a known over-report, which can SKIP or FAIL twice but never falsely PASS. Widening `Check`'s signature is a breaking change to every backend, out of scope here.

- [ ] **Step 1: Write failing tests**, using `homelab_arr`, `arr_with`, `as_client(qbt_with(...))`:
  - `test_homelab_passes`: both homelab arrs and the homelab qbt give PASS, conflict `cross-mount`.
  - `test_separate_download_and_library_mounts_fail`: mounts `("/", "/downloads", "/tv")`, save path translated to `/downloads/tv`, root `/tv`. FAIL; premise `arr.hardlink_import_crosses_a_mount` holds; detail names `sonarr[tv]`, `/downloads/tv`, `/downloads`, `/tv`.
  - `test_hardlinks_off_passes_even_across_mounts`
  - `test_remote_path_mapping_is_applied_before_picking_the_mount`: client save `/downloads/tv`; mapping `gluetun` `/downloads` → `/data/torrents`; mounts include `/data`; root `/data/media/tv`. PASS. The same mapping for another host gives SKIP: `/downloads/tv` is on `/` only, which is unknown.
  - `test_no_arr_uses_this_client_is_not_applicable`: covers no arrs, an arr whose only download client is `Sabnzbd`, a disabled client, and a category the client doesn't define.
  - `test_any_unknown_input_skips`: parametrize over each of the five arr facts, plus client `save_paths`, as `Unknown`. SKIP with premise state `None`.
  - `test_unknown_in_one_triple_does_not_hide_a_fail_in_another`: two root folders, one cross-mount and one with root `"C:\\tv"`, give FAIL.
  - `test_fail_lists_every_conflicting_triple`: two arrs both cross-mount. Detail names both.
  - `test_path_only_on_root_mount_skips_with_reason`: detail says the arr cannot see the download path.
  - `test_finding_instance_is_the_client_label`: `qbittorrent[main]`.
  - `test_shared_check_runs_for_the_fake_backend_too`: through `run_checks` with the fake backend registered (see `tests/clients/conftest.py`). A `hardlink-futility` finding labelled `fake[...]` appears.
  - `test_render_shows_the_therefore_line_for_a_cross_mount_fail` (in `test_render_human.py`).
  - **Mutation discipline:** in the FAIL test, assert the PASS twin by flipping only the mounts to `("/", "/data")` with root `/data/media/tv` and save `/data/torrents/tv`.

- [ ] **Step 2: Run.** Expected: FAIL.

- [ ] **Step 3: Implement**, with small functions (spec's "The check", steps 1-5). Register it in `SHARED_CHECKS` and add `SHARED_THEREFORE`. The edit to `_therefore` is three lines.

- [ ] **Step 4: Run the full fast suite.** The property tests now run the shared check on every drawn snapshot: never raises, every FAIL explains itself, premise labels are declared. Fix the check rather than the properties. Expected: all pass.

- [ ] **Step 5: Mutation round.** Manually make three mutations, run the check's tests after each, and confirm red each time:
  1. `!=` → `==` in the mount comparison.
  2. Drop the `copy_using_hardlinks` conjunct.
  3. Make `mount_of`'s `/`-only case return the mount instead of unknown.

  Record the three results in the report.

- [ ] **Step 6: Commit** `feat(checks): hardlink-futility, the first shared check`

### Task 5: Conformance: `link(2)` across bind mounts

**Files:**
- Create: `tests/conformance/test_hardlink_exdev.py`
- Modify: `.github/workflows/conformance.yml` (timing comment only: add the measured seconds)
- Modify: `src/lintarr/checks/hardlink_futility.py` module docstring (axiom status table from the spec, with the test's name)

**Interfaces:**
- Consumes: the existing conformance markers and fixtures in `tests/conformance/conftest.py` (read them; reuse the Docker-availability skip the other container tests use).

- [ ] **Step 1: Write the tests.** Use `tmp_path` with `a/` and `b/` on one filesystem, create `a/f`, then `docker run --rm` a pinned Alpine image (pin by digest, like the existing image pins; see `tests/conformance/image.py`):
  - `test_link_across_two_bind_mounts_is_exdev`: `-v tmp/a:/a -v tmp/b:/b`, `ln /a/f /b/f`. Non-zero exit, and stderr contains `Invalid cross-device link`.
  - `test_link_within_one_bind_mount_succeeds`: `-v tmp:/x`, `ln /x/a/f /x/b/f`. Exit 0, and on the host `os.stat(a/f).st_nlink == 2`.
  - Mark them exactly as the other container conformance tests are marked.

- [ ] **Step 2: Run locally** with `uv run pytest tests/conformance/test_hardlink_exdev.py -v`. Docker Desktop on macOS is fine for the bind-mount semantics. Expected: 2 passed. Record the wall time.

- [ ] **Step 3: Update** the workflow timing comment and the axiom table.

- [ ] **Step 4: Commit** `test(conformance): link(2) across bind mounts is EXDEV`

### Task 6: Live verification and docs

**Files:**
- Modify: `CONTINUATION.md` (replace the stale BLOCKED ON lines: conformance and homelab#692 are done; state this slice; next slices are `orphan-inevitability`, then `seed-goal-conflict`)
- Modify: `src/lintarr/models.py` `ClientFacts` docstring, if Task 2 left it stale

- [ ] **Step 1: Live run (controller does this, not a subagent).** On homelab, in a throwaway checkout of this branch outside `~/sources/lintarr`, run `uv run lintarr dump-facts` and `uv run lintarr check --no-strict` with the deployed env file. Expected: `PASS hardlink-futility [qbittorrent[main]]`, and the five new facts on both arrs are `Known`. Remove the throwaway checkout.
- [ ] **Step 2: Update CONTINUATION.md** and commit `docs(continuation): hardlink-futility slice; clear resolved blockers`.
