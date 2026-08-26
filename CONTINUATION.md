# CONTINUATION — lintarr

**Last updated:** 2026-08-26
**State:** P0a complete and merged to `main` (8c69dee). 91 tests, ruff clean.

## What lintarr is, and why

A sidecar that reads the live configuration of an arr stack and proves, **from
configuration alone**, whether the settings can coexist.

It exists because of a real incident on homelab: qBittorrent had
`max_active_torrents = 5` with share limits disabled. Seeding torrents count
against that limit, so once five torrents completed they held every slot
permanently. Fifty-two torrents, zero active downloads, zero kB/s, **no error
anywhere**, for weeks. Every individual setting was legal; the combination was
not.

Every peer tool (Cleanuparr, Decluttarr, Maintainerr, Sanitarr) reacts to
observed runtime state, so by construction they engage only after the damage.
Recyclarr writes config but never checks it. The gap is static cross-service
consistency.

## Where it stands

**P0a — the collect layer — is done.** It reads and labels configuration. It
does not check anything yet.

```
src/lintarr/
  facts.py            Known[T] | Unknown — absence lives in the type
  models.py           multi-instance StackFacts
  config.py           env-driven; API keys required, no auto-discovery
  collect/http.py     GET-only; one allow-listed POST (qBittorrent login)
  collect/qbittorrent.py   exactly one login attempt per run
  collect/arr.py      per-indexer seed criteria
  collect/stack.py    one failing service never aborts the run
  cli.py              dump-facts (human + JSON)
```

Verified working against the real homelab Sonarr and Radarr.

## Blockers to dogfooding, in order

### 1. qBittorrent credentials — REAL BLOCKER

`lintarr dump-facts` currently reports `ERROR qbittorrent[main]: banned` against
homelab. Half the tool is therefore unexercised against real data.

The LAN login is refused with HTTP 403. An earlier on-box success was almost
certainly qBittorrent's **localhost auth bypass**, meaning `admin/adminadmin`
was never actually validated. See the existing memory note: the WebUI password
is not in Ansible and drifts across rebuilds.

To resolve: set a known WebUI password in qBittorrent, and — because this is the
same class of problem as homelab#393 — put it in the Ansible vault so it
survives the next rebuild.

### 2. Two parked bugs (first work of P0b)

- `collect/arr.py` `_fields_as_mapping` does an unguarded `f["name"]`. A
  malformed arr payload raises a bare `KeyError` that escapes
  `collect_stack`'s `except ServiceError` and kills the whole run.
- The orphan-credential guard raises a raw `ValueError` traceback out of the
  CLI; should be a `click.UsageError`.

### 3. There are no checks yet

P0a only gathers facts. Nothing can pass or fail. Real dogfooding starts with
P0b's `queue-liveness`.

## The dogfooding asset nobody should lose

**homelab#393 records the exact broken qBittorrent values** from the original
incident — the documented-vs-found table. Since the live stack has since been
*repaired*, that issue is now the only surviving record of the known-positive
case.

That makes it the acceptance fixture for `queue-liveness`:

- the **repaired** live stack must report PASS
- the values recorded in homelab#393 must report FAIL

A check that cannot do both is not finished. Do not let #393 be closed without
first copying its before/after table into a test fixture.

## Next phase: P0b

Per `docs/superpowers/specs/2026-08-26-lintarr-design.md`:

- the premise combinator (tracked premises, minimal by construction)
- `queue-liveness` over the full three-limit queue model
- the exhaustive state-machine simulator in `tests/model/queue.py`
- the five-outcome lattice (PASS / FAIL / SKIP / ERROR / N-A) and exit codes

**Known gap carried forward:** the simulator validates that the closed form was
*derived* correctly. It cannot catch a *wrong model* — a sweep over the
predicate's own parameters cannot discover a parameter the predicate is
missing. That is P2's container differential test, and until it exists the
flagship check rests on one reading of the qBittorrent docs.

## Hard-won lessons from the P0a build

Recorded because they cost real review cycles:

1. **Running the tool against the real stack found what four code reviews
   missed.** Sonarr indexers have no `enable` key; the adapter defaulted it to
   `False` and reported all four enabled indexers as disabled. Reviews read
   code; only live data reveals what an API actually returns.
2. **Seed criteria live on `/api/v3/indexer`, not `/api/v3/downloadclient`.**
   The download client carries no seed fields at all.
3. **Sonarr omits the `value` key entirely** for unset seed criteria — it does
   not send `"value": null`. Anything using `.get("value")` fabricates a null.
4. **Three separate tests passed while proving nothing**, including the one
   carrying the read-only safety claim. Mutation-test every guard: reintroduce
   the defect, watch the test fail, restore.

## Repo notes

- Remote `origin` is `git@github.com:tclancy/lintarr`, but `origin/main`
  (`d850a3f`) is an **unrelated history** — the repo was created with an
  initial commit. Local `main` has never been pushed. Reconcile before pushing.
- MIT licensed. Python 3.13, uv, hatchling, src layout, click, httpx, pytest.
- Hypothesis is a declared dev dependency with no property tests written yet.
