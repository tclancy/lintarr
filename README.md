# lintarr

Static consistency checker for the *arr stack.

## Development

```bash
uv sync
uv run pytest
pre-commit install   # once per clone — there are no CI workflows, so this is the gate
```

## Running on a schedule

```bash
lintarr check --state-file ~/.local/state/lintarr/state.json
```

`--state-file` makes the exit code edge-triggered. It's non-zero only for
problems the previous run didn't have, so a persistent FAIL pages once under
a failure alert, not every run. The finding still prints every time. A problem
that clears and comes back pages again, and corrupt state is warned about and
pages everything. Without the flag, `check` is level-triggered, as CI wants.
