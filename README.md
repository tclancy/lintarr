# lintarr

Static consistency checker for the *arr stack.

## Development

```bash
uv sync
uv run pytest
pre-commit install   # once per clone — there are no CI workflows, so this is the gate
```

## Deploying (homelab)

lintarr runs on homelab as a systemd **timer**, not a daemon: `lintarr.timer`
fires `lintarr.service` (`Type=oneshot`, `lintarr check --state-file ...`)
hourly. A *new* problem (FAIL, ERROR, or SKIP/N/A under the default `--strict`)
exits non-zero and triggers the shared `unit-failure-alert-user@` ntfy alert,
once. See "Running on a schedule" below. The units and env file live in the
homelab repo (`roles/native-apps/templates/lintarr.*`). The Sonarr/Radarr API
keys are read from each app's `config.xml` at deploy time, not vaulted.

```bash
itguy deploy lintarr        # Ansible pulls ~/sources/lintarr; if that changed anything, runs ./restart.sh
itguy logs lintarr --level info
```

`restart.sh` syncs locked deps and kicks one check with `--no-block`, so a FAIL
finding pages without failing the deploy. A deploy that pulls nothing runs no
check. The timer's next tick does that. `itguy restart lintarr` exits 1 by design,
because there's no long-running unit to bounce. Use `deploy`. (If itguy can't
discover units it falls back to treating `lintarr.service` as a daemon and does
a blocking restart. That runs a check and returns the check's exit code.)

Scope and rationale: `docs/superpowers/specs/2026-10-05-deploy-scope.md`.

## Running on a schedule

```bash
lintarr check --state-file ~/.local/state/lintarr/state.json
```

`--state-file` makes the exit code edge-triggered. It's non-zero only for
problems the previous run didn't have, so a persistent FAIL pages once under
a failure alert, not every run. The finding still prints every time. A problem
that clears and comes back pages again, and corrupt state is warned about and
pages everything. Without the flag, `check` is level-triggered, as CI wants.
