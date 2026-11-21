"""Render one client whose backend reports a field named like a neutral one.

For ``docs/66-common-fact-namespace.md``. No registered backend does this
today -- ``ClientFacts.save_paths`` against qBittorrent's ``save_path`` is the
near miss -- so the payload is built by hand rather than collected. The point
is what ``_render_human`` does on the day a second backend names a field
``save_paths`` for its own purposes.

Run from a checkout's root, which decides whose renderer answers::

    uv run --quiet python3 docs/66-collision-probe.py

Demo scaffolding: nothing in ``src/`` or ``tests/`` imports it. The same
collision is asserted as a test in ``tests/test_render_human.py``.
"""

from lintarr.cli import _render_human


def fact(value, source):
    return {"known": True, "value": value, "source": source, "read_at": "", "service_version": "v1"}


payload = {
    "clients": [
        {
            "name": "main",
            "kind": "fake",
            "version": "1.0.0",
            # Collected by the shared collector, read by shared checks.
            "common": {"save_paths": fact({"": "/neutral"}, "GET /neutral")},
            # The backend's own field, which happens to share the name.
            "save_paths": fact({"": "/backend"}, "GET /backend"),
        }
    ],
    "arrs": [],
    "errors": [],
}

out = _render_human(payload)
print(out.rstrip())
print("---")
printed = sum("save_paths" in line for line in out.splitlines())
print(f"facts collected: 2    fact lines printed: {printed}")
