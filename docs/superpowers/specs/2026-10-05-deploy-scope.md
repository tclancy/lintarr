# Scope — deploying lintarr to homelab

**Date:** 2026-10-05 (UTC)
**Status:** Decided 2026-10-05: shape A (systemd timer on the host). IN PROGRESS: plan task 1.

## Goal

`itguy deploy lintarr` runs on homelab, and `lintarr check` runs against the
real arr stack on a schedule. A FAIL or ERROR reaches Tom's phone without
anyone having to look.

## Where things stand (verified 2026-10-05)

| Thing | State | Evidence |
|---|---|---|
| `lintarr check` | Exists, with documented exit codes (0 pass, 1 FAIL, 2 ERROR/usage, 3 SKIP under `--strict`) | `src/lintarr/cli.py:258` |
| Scheduler, webhook, HTTP API, SQLite history | **Not built** | only described in the design spec's Runtime section |
| Dockerfile / `restart.sh` | None | repo root |
| Homelab template for lintarr | None, so `itguy` can't resolve it | `ansible/roles/*/templates` |
| qBittorrent WebUI password | **Already vaulted** (`vault_qbittorrent_webui_password`, homelab#190) | `group_vars/homelab/vars.yml:446` |
| Sonarr / Radarr API keys | **Not vaulted** | no `*sonarr*`/`*radarr*` key vars |
| Alerting on a failed scheduled unit | Exists: `OnFailure=unit-failure-alert-user@%n.service`, with streak counting so a persistent failure doesn't page every tick (homelab#421) | `roles/monitoring/templates/` |

`CONTINUATION.md` is stale: it lists the qBittorrent password as the top
blocker, but homelab#190 already vaulted it. It also predates P0b's `check`
command. Updating it is part of this work.

## The one decision

The design spec calls for a **Docker sidecar** with its own scheduler, webhook
and API. None of that runtime exists. There are two ways to get lintarr running
now.

**A. systemd timer on the host (recommended for now).** A `Type=oneshot`
user unit runs `uv run --frozen lintarr check` hourly, following the
`parsons-pulse-disk` pattern. A non-zero exit fires the existing
`unit-failure-alert-user@` ntfy path, which already has streak dedup. Nothing
new is needed in lintarr except `restart.sh`. itguy resolves it as
systemd-shape from `lintarr.service.j2`.

**B. Docker sidecar per the spec.** This requires building the scheduler,
the edge-triggered webhook and the health endpoint first, and those are spec
P-phases that haven't started. It's a deploy that waits on product work.

A doesn't block B. When the sidecar runtime exists, swap the systemd templates
for a compose template and itguy picks up the new shape on its own.

**Known limitation of A:** host-run lintarr sees host paths, not container
paths. That doesn't matter for `queue-liveness`. It does matter for the P3
`hardlink-futility` mount contract, which should use B anyway.

## In scope (shape A)

1. lintarr repo: add `restart.sh` (`uv sync --frozen`, then
   `systemctl --user start --no-block lintarr.service`, never `restart`).
   Keep `uv sync` and the unit's `uv run` on the same dependency groups, or
   every tick re-syncs.
2. homelab repo, `roles/native-apps/templates/`: add `lintarr.service.j2`
   (oneshot, `OnFailure=`, `SuccessExitStatus=143 SIGTERM`, `SyslogIdentifier`,
   `TimeoutStartSec`), `lintarr.timer.j2` (hourly, `Persistent=false`) and
   `lintarr.env.j2`.
3. homelab vault: add `vault_lintarr_sonarr_api_key` and
   `vault_lintarr_radarr_api_key`, and reuse the already-vaulted qBittorrent
   password.
4. homelab tasks: a `Clone or pull lintarr source` git task that triggers
   a `restart lintarr` step, plus rendering the env file and enabling the timer.
   That git task is the **only** thing that runs `restart.sh`. itguy's
   pre-deploy deliberately doesn't pull, because a pull outside Ansible stops
   that step from running (itguy `pre_deploy.py`, #140).
   `itguy restart lintarr` will exit 1 by design, because every unit is
   `Type=oneshot` and there's nothing long-running to bounce
   (`deploy.py:_restart_systemd_units`). Deploy, don't restart.
5. First real run, checked by hand, then `CONTINUATION.md` updated.

## Out of scope

- Docker image, web UI, HTTP API, SQLite history, webhook (shape B / spec phases)
- New invariants
- Any write access to the arr stack. The read-only guarantee stands
  (`tests/test_readonly_guarantee.py`)

## Risks / open questions

- **Exit 3 (SKIP) under `--strict` pages.** A run that can't reach one service
  exits non-zero. That's probably right, because the tool's whole point is "a
  silent green on an unchecked service is the worst outcome". But if it's
  noisy, it's a one-flag change (`--no-strict`).
- **qBittorrent ban.** One bad password per hour stays well under qBittorrent's
  fail count, and lintarr makes exactly one login attempt per run. The
  host-to-bridge whitelist (homelab#197) may make credentials moot anyway.
  Confirm on the first run.
- **Acceptance fixture.** homelab#393's before/after table must already be a
  test fixture before anyone trusts a PASS in production. Check that it is
  before step 5.

## Plan

| # | Task | Repo | Done when |
|---|---|---|---|
| 0 | Tom picks A or B | — | **done**: A |
| 1 | `restart.sh` + README "Deploying" section | lintarr | PR merged |
| 2 | Vault the two arr API keys | homelab | `ansible-vault view` shows them |
| 3 | Service, timer and env templates + tasks + tests (match the existing `test_syslog_identifiers.py`-style guards) | homelab | PR merged, `itguy list` shows `lintarr` after `git pull` on the box |
| 4 | `itguy deploy lintarr`, then `systemctl --user start lintarr.service` once by hand | homelab | `itguy logs lintarr --level info` shows a full `check` run |
| 5 | Prove the alert path: point one URL at a dead port, see the ntfy, revert | homelab | ntfy received, then green again |
| 6 | Refresh `CONTINUATION.md` | lintarr | merged |

Tasks 1 and 3 are separate PRs in separate repos. Task 3 depends on the vault
entries from task 2.
