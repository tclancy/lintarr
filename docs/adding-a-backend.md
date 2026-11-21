# Adding a download-client backend

A backend is what teaches lintarr to read one kind of download client. This
guide says what one must provide and how to prove it works. qBittorrent is the
worked example: `src/lintarr/clients/qbittorrent/`.

Backends live in the tree. There are no entry points and no out-of-tree
plugins.

## The shape

A backend is one package under `src/lintarr/clients/<kind>/`. Adding it needs no
edit outside that package except one line in the registry,
`src/lintarr/clients/__init__.py`:

```python
BACKENDS: dict[str, Backend] = {
    qbittorrent.BACKEND.kind: qbittorrent.BACKEND,
    mykind.BACKEND.kind: mykind.BACKEND,  # the one line
}
```

The package exports one `BACKEND`, a frozen `Backend` from
`lintarr/clients/base.py`. Import `Backend` from there and from nowhere else in
`clients/`: `base.py` must not import any backend, or the registry becomes a
cycle.

## The `Backend` fields

- `kind`: the value of `DOWNLOAD_CLIENT_KIND`. It is also the label prefix.
- `credentials`: which of `"user"`, `"pass"`, `"api_key"` this client uses.
- `collect`: called as `collect(cfg, *, transport)`. It reads the client and
  returns a `ClientInstance`. It raises `ServiceError` for anything it cannot
  read, never a default. All HTTP goes through `ReadOnlyClient`, so the
  read-only guarantee holds. The returned `ClientInstance.kind` must equal
  `BACKEND.kind`: checks are dispatched by looking that kind up in
  `BACKENDS`, so a mismatch is a `KeyError`.
- `checks`: this backend's own invariants. Each is a `Check`, a function
  `(ClientInstance, tuple[ArrInstance, ...]) -> Finding`. It runs only on
  instances of this kind, and reads `inst.specific`, the backend's own facts.
- `therefore`: the operator-facing "Therefore" line for each
  `(invariant, conflict)` pair that this backend's checks can FAIL on. It may
  be empty. It lives here so a new backend never edits `cli.py`. A finding's
  line is looked up in the producing backend's table only (the kind in its
  `<kind>[<name>]` label), so reusing another backend's invariant id or
  conflict name cannot pick up that backend's text.
- `arr_dependent`: which of this backend's invariant ids need arr data, and what
  their SKIP says when an arr could not be read. It may be empty. The value is
  an `ArrDependency(premise, detail)`: `run` turns that check's PASS or N/A into
  a SKIP carrying `premise`, and prints `detail`. Write the sentence your own
  check needs — `hardlink-futility` could not check *imports*,
  `orphan-inevitability` could not check *downloads* — because nothing else
  knows what your check reads the arr for. The ids must not appear in
  `lintarr.checks.SHARED_ARR_DEPENDENT` either: a shared check already owns
  those, and `run` refuses the pair rather than pick one.

## Credential rules

Config checks each client against its backend's `credentials`:

- A credential the backend requires, but is missing, is an error.
- A credential the backend does not use is an error too. It most likely means
  the wrong client was configured.

Both are `ValueError` from `load_config`, which the CLI reports as a usage
error with exit 2.

## Facts

Report what you read. A value you could not read is `Unknown(reason, detail)`,
never a default.

`ClientInstance.common` holds client-neutral facts. Client facts grow with
their consumers: a neutral field is added in the same slice as the first shared
check that reads it, and that slice must populate it in every registered
backend. Do not add a neutral field because your backend happens to have the
data. Put it in `specific` until a shared check needs it.

Today there is one, `save_paths: Fact[Mapping[str, str]]`. Every backend must
fill it: per category, the absolute directory, in the client's own namespace,
where a new download in that category is saved. The key `""` is the
uncategorised location. If you cannot work it out, return `Unknown`, never a
guess. qBittorrent is the worked example (`clients/qbittorrent/save_paths.py`):
`""` is the `save_path` preference; a category uses its own `savePath` when
`auto_tmm_enabled` is on (or it is off and `use_category_paths_in_manual_mode`
is on), and `save_path` otherwise; an empty `savePath` is `<save_path>/<name>`
and a relative one is joined to `save_path`; any input it needs that was not
read makes the whole mapping `Unknown`.

Shared checks live in `lintarr/checks/` (`SHARED_CHECKS`, none yet). They read
only neutral facts and run on every backend.

## Labels

Every finding and error about a client is labelled `<kind>[<name>]`, for
example `qbittorrent[main]`. Collection errors are labelled for you. Findings
are not: each check builds its own label, `f"{inst.kind}[{inst.name}]"`, as
`queue_liveness` and the fake backend's check do. The label is part of the edge-trigger alert key.
Change one and every persisting finding pages again, so labels are stable once
shipped.

## The proof bar

A backend is done when all of these hold:

1. **Conformance tests against the real client**, from a pinned image, as in
   `tests/conformance/`. Facts you read must match what the real client reports.
2. **A measurements doc** under `docs/measurements/` for any queue behaviour a
   check assumes. If a check says "this setting starves the queue", the doc
   says how that was measured.
3. **The fake-backend suite is green.** `tests/clients/test_fake_backend.py`
   registers a test-only backend and shows config, collection, checks and
   rendering treat any registered kind the same. It must stay green with your
   backend registered too.
