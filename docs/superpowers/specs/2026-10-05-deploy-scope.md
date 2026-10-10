# Scope — deploying lintarr to homelab

**Date:** 2026-10-05 (UTC)
**Status:** DONE 2026-10-07. Shape A deployed (lintarr#30, #31; homelab#534,
#542). First live check PASSed, and the alert path was proven end to end.
Follow-up: the systemd state-dir symlink quirk (see CONTINUATION.md).

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
3. Credentials (decided: `config.xml`). Read `<ApiKey>` from each app's
   `config.xml` at play time (`slurp`, `no_log`, gated on `stat`), the way
   `cleanuparr-config.yml:47-52` does and argues for. A vaulted copy goes stale
   silently when the app regenerates its key on a rebuild. The briefly vaulted
   copies (homelab 8972c77) are reverted in homelab#534. qBittorrent: the vaulted password plus `qbittorrent_webui_username`
   (`vars.yml:545`). Both are required, because `config.py` raises on
   `QBIT_URL` without them, whatever the auth-subnet whitelist does.
4. homelab tasks: a `Clone or pull lintarr source` git task that triggers
   a `restart lintarr` step, plus rendering the env file and enabling the timer.
   That git task is the **only** thing that runs `restart.sh`. itguy's
   pre-deploy deliberately doesn't pull, because a pull outside Ansible stops
   that step from running (itguy `pre_deploy.py`, #140).
   `itguy restart lintarr` will exit 1 by design, because every unit is
   `Type=oneshot` and there's nothing long-running to bounce
   (`deploy.py:_restart_systemd_units`). Deploy, don't restart.
   The catalog only makes `lintarr` a subject when its tag is registered in
   `apps.yml` (itguy `catalog.py:112-121`), so every task needs
   `tags: [native-apps, lintarr]`, plus a `lintarr_enabled` gate and a
   keyless-https `lintarr_repo` var like `sandy_repo` (`vars.yml:908`).
   The handler goes after `reload user systemd` and copies the
   `XDG_RUNTIME_DIR` / `DBUS_SESSION_BUS_ADDRESS` env from `resync
   parsons-pulse`. Env file: `~/.config/lintarr/environment` at 0600 in a
   0700 dir, loaded with `EnvironmentFile=`, never `Environment=`. Changes to
   the env file or unit alone don't run `restart.sh`; the next tick picks
   them up. uv and linger come from sandy-gated tasks, which already ran on
   the box but wouldn't on a rebuild that deploys only lintarr.
   Correction: systemd subjects never run `pre_deploy.py` (itguy
   `cli.py:369-375`). The conclusion, that only Ansible pulls, still holds.
5. Timer: `OnCalendar=` must be a shape that
   `tests/test_unit_failure_alert_escalation.py:700-714` parses, e.g.
   `*-*-* *:17:00`. A literal `hourly` raises.
6. First real run, checked by hand, then `CONTINUATION.md` updated.

## Out of scope

- Docker image, web UI, HTTP API, SQLite history, webhook (shape B / spec phases)
- New invariants
- Any write access to the arr stack. The read-only guarantee stands
  (`tests/test_readonly_guarantee.py`)

## Alerting: the blocker (adversarial review, 2026-10-05)

The original claim, that streak dedup stops a persistent failure paging every
tick, was **wrong for an hourly unit**:

- `unit_failure_alert_min_interval: 3600` (`vars.yml:118`) is exactly
  lintarr's cadence, so a persistent FAIL pushes every 1–2 hours.
- From failure #3 every push is `urgent` and copied to the `claude` topic,
  which is Tom's phone (`vars.yml:122,135`). That includes overnight.
- The 8-day streak window re-arms on every failure, so it never resets.
- The wedge axiom is still unvalidated against a live client
  (`queue_liveness.py:31-38`). A false-positive FAIL would page urgent
  indefinitely.

Options:

- **(a)** A per-unit `min_interval` override in the shared alert script
  (homelab).
- **(b)** lintarr edge-triggers: persist the last run's outcome and exit
  non-zero only on a transition into FAIL/ERROR. This is the spec's own model
  ("edge-triggered with dedup", Runtime section), and it's back-end code with
  unit tests.
- **(c)** Run less often. That only dilutes the problem.

Must be settled before task 4 (first deploy).

## Risks / open questions

- **Exit 3 under `--strict` pages, for SKIP and for N/A** (`outcomes.py`
  `exit_code`; N/A comes from `run.py:102-110`). A run that can't reach one
  service exits non-zero. That's intended ("a silent green on an unchecked
  service is the worst outcome"), but combined with the alerting blocker any
  structurally permanent SKIP/N/A becomes a permanent urgent page.
- **qBittorrent ban.** One bad password per hour stays well under qBittorrent's
  fail count, and lintarr makes exactly one login attempt per run. Host
  callers come from the bridge gateway, which the #197 whitelist covers. If
  the whitelist ever broke, a ban would also lock out Ansible's
  qbt-preferences tasks and `itguy arr delete`.
- **itguy discovery fallback.** If `systemctl --user list-unit-files`/`show`
  fails, itguy treats `lintarr.service` as a daemon (`units.py:154-160`) and
  may report a false restart failure on a good deploy. Rare.
- ~~Acceptance fixture~~: already present. `tests/fixtures/homelab.py`
  (wedged + repaired) is used by `tests/invariants/test_queue_liveness.py`
  and `tests/test_check_cli.py:82-99`.

## Plan

| # | Task | Repo | Done when |
|---|---|---|---|
| 0 | Tom picks A or B | — | **done**: A |
| 1 | `restart.sh` + README "Deploying" section | lintarr | PR merged |
| 2 | Arr API keys | homelab | **superseded**: read from `config.xml` in task 3; vault copies reverted (homelab#534) |
| 2b | Edge-triggered `check --state-file` (option b) | lintarr | lintarr#31 merged; ExecStart passes `--state-file %S/lintarr/state.json` |
| 3 | Service, timer and env templates + tasks + tests | homelab | homelab#542 open; merged, and `itguy list` shows `lintarr` after `git pull` on the box |
| 4 | **done** 2026-10-07. (after 2b) `itguy deploy lintarr`, then `systemctl --user start lintarr.service` once by hand | homelab | `itguy logs lintarr --level info` shows a full `check` run |
| 5 | Prove the alert path: point one URL at a dead port, see the ntfy, revert | homelab | **done** 2026-10-07 |
| 6 | Refresh `CONTINUATION.md` | lintarr | **done** (#37) |

Tasks 1, 2b and 3 are separate PRs. Task 3 needs #30 and #31 merged first, so
the unit runs a `restart.sh` and `--state-file` that exist on `main`.
