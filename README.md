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
fires `lintarr.service` (`Type=oneshot`, `lintarr check`) hourly. A non-zero
exit (FAIL, ERROR, or SKIP/N/A under the default `--strict`) triggers the shared
`unit-failure-alert-user@` ntfy alert. The units, env file and vaulted
credentials live in the homelab repo (`roles/native-apps/templates/lintarr.*`).

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
