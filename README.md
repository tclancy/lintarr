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
exit (FAIL, ERROR, or SKIP under the default `--strict`) triggers the shared
`unit-failure-alert-user@` ntfy alert. The units, env file and vaulted
credentials live in the homelab repo (`roles/native-apps/templates/lintarr.*`).

```bash
itguy deploy lintarr        # Ansible pulls ~/sources/lintarr, then runs ./restart.sh
itguy logs lintarr --level info
```

`restart.sh` syncs locked runtime deps and kicks one check with `--no-block`, so
a FAIL finding pages without failing the deploy. `itguy restart lintarr` exits 1
by design, because there is no long-running unit to bounce. Use `deploy`.

Scope and rationale: `docs/superpowers/specs/2026-10-05-deploy-scope.md`.
