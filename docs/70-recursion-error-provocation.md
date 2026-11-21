# lintarr #70: provoking RecursionError without guessing a parse depth

*2026-11-21T04:06:24Z by Showboat 0.6.1*
<!-- showboat-id: f1d582b2-80a7-4c50-a1f8-6490146a0ce4 -->

`collect/http.py`'s `get_json` catches `RecursionError` alongside `ValueError`,
because `json.loads` raises it on a deeply nested body and it is **not** a
`ValueError`. Uncaught it sails past `collect_stack`'s `except ServiceError`
and aborts every healthy instance's collection.

The guard is right. The *test* provoked it by nesting 20,000 deep — and how
deep CPython nests before its stack guard fires is the interpreter's business.
It moved, so the pre-push gate went red on a clean `main` for anyone on
3.14.10. Nothing in `src/` changes here.

## Why depth is not a provocation you can rely on

```bash
unset VIRTUAL_ENV
for v in 3.13 3.14; do
  uv run -q --python $v --no-project python -I -c '
import json, sys
print(f"CPython {sys.version.split()[0]}   sys.getrecursionlimit() = {sys.getrecursionlimit()}")
for d in (20_000, 100_000, 200_000):
    body = "[" * d + "]" * d
    try:
        json.loads(body)
        print(f"   {d:>7,} deep: parsed, no error")
    except RecursionError:
        print(f"   {d:>7,} deep: RecursionError")
'
done

```

```output
CPython 3.13.7   sys.getrecursionlimit() = 1000
    20,000 deep: RecursionError
   100,000 deep: RecursionError
   200,000 deep: RecursionError
CPython 3.14.10   sys.getrecursionlimit() = 1000
    20,000 deep: parsed, no error
   100,000 deep: RecursionError
   200,000 deep: RecursionError
```

Same default recursion limit, different answers. 3.14.10 parses the depth the
old test was built on. That is the whole bug.

## Before: the test on `main` at 4623438, under 3.14.10

```bash
unset VIRTUAL_ENV
before=$(mktemp -d)
git show 4623438:tests/collect/test_http.py > "$before/test_http_before.py"
sed -n '/def test_deeply_nested_body_is_bad_response/,/RecursionError" in e.value.detail/p' \
  "$before/test_http_before.py"

```

```output
def test_deeply_nested_body_is_bad_response():
    """``json.loads`` raises ``RecursionError``, which is not a ``ValueError``.

    Same escape route as the undecodable body, different base class — so it
    has to be named rather than covered by widening to ``ValueError``.
    """
    body = b"[" * 20_000 + b"]" * 20_000

    def handler(request):
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    with pytest.raises(ServiceError) as e:
        _client(handler).get_json("/x")
    assert e.value.kind == "bad-response"
    assert "RecursionError" in e.value.detail
```

```bash
unset VIRTUAL_ENV
before=$(mktemp -d)
git show 4623438:tests/collect/test_http.py > "$before/test_http_before.py"
uv run --frozen --python 3.14 pytest -q -p no:cacheprovider \
  "$before/test_http_before.py" -k deeply_nested > "$before/out.txt" 2>&1 || true
sed -n '/FAILURES/,$p' "$before/out.txt" | tail -8

```

```output
>       with pytest.raises(ServiceError) as e:
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^
E       Failed: DID NOT RAISE ServiceError

/var/folders/7v/j3klsc_j5kvcl9h24_p35jl00000gn/T/tmp.eWHPc3yGnv/test_http_before.py:150: Failed
=========================== short test summary info ============================
FAILED ../../../var/folders/7v/j3klsc_j5kvcl9h24_p35jl00000gn/T/tmp.eWHPc3yGnv/test_http_before.py::test_deeply_nested_body_is_bad_response
1 failed, 20 deselected in 0.07s
```

## After: two tests, one question each

```bash
unset VIRTUAL_ENV
awk '/^class _RecursionErrorOnDecode/{f=1} /^def _list_depth/{f=0} f' \
  tests/collect/test_http.py

```

```output
class _RecursionErrorOnDecode(httpx.Response):
    """A 200 whose JSON decode raises ``RecursionError``, by construction.

    The fixture this replaced provoked the error by *depth*: 20,000 nested
    lists, which raised on every interpreter the project had then run on. How
    deep CPython can nest before its stack guard fires is the interpreter's
    business and it moved. Measured here: 3.13.7 raises at 20,000 deep;
    3.14.10 parses 20,000 without complaint and raises at 100,000, with the
    recursion limit at its default 1000 in both. So the old body stopped
    raising and the test read ``DID NOT RAISE ServiceError`` — a red pre-push
    gate on a clean tree, over a guard that was never broken (#70).

    Depth was only ever a *means* of reaching ``response.json()``'s raise, so
    this raises from there directly: ``get_json``'s ``except`` is the subject,
    and this fixture reaches it on any interpreter, at any recursion limit.
    ``test_a_genuinely_deep_body_is_handled_whatever_the_interpreter_does``
    keeps the real nested body, which is a different question.
    """

    def json(self, **kwargs: object) -> object:
        raise RecursionError("maximum recursion depth exceeded while decoding a JSON object")


def test_a_recursion_error_from_the_json_decode_is_bad_response():
    """``RecursionError`` is not a ``ValueError``, so it has to be named.

    Same escape route as the undecodable body, different base class — so
    widening the catch to ``ValueError`` does not cover it. Uncaught it goes
    straight past ``collect_stack``'s ``except ServiceError`` and aborts every
    *healthy* instance's collection.

    Drop ``RecursionError`` from the ``except`` in ``get_json`` and this test
    fails, on 3.13.7 and on 3.14.10 alike. Measured (#70).
    """

    def handler(request):
        return _RecursionErrorOnDecode(
            200, content=b"[]", headers={"content-type": "application/json"}
        )

    with pytest.raises(ServiceError) as e:
        _client(handler).get_json("/x")
    assert e.value.kind == "bad-response"
    assert "RecursionError" in e.value.detail


```

The fixture raises from the decode itself, so it reaches `get_json`'s
`except` on any interpreter at any recursion limit. The genuinely deep body
stays in the suite as a separate test that refuses to pin which way the
interpreter goes — but asserts in both branches, so neither is a free pass.

```bash
unset VIRTUAL_ENV
awk '/^def _list_depth/{f=1} /^def test_declared_charset_with_no_bom/{f=0} f' \
  tests/collect/test_http.py

```

```output
def _list_depth(value: object) -> int:
    """How many nested lists deep ``value`` goes — iteratively, on purpose.

    A recursive walk of a 200,000-deep list would raise the very error the
    test around this is trying to observe the absence of.
    """
    depth = 0
    while isinstance(value, list):
        depth += 1
        value = value[0] if value else None
    return depth


def test_a_genuinely_deep_body_is_handled_whatever_the_interpreter_does():
    """A real deeply nested body never escapes as an uncaught exception.

    The companion to the test above, and the half that keeps a genuine body in
    the suite. It deliberately does **not** pin which way the interpreter goes,
    because that is the thing that moved under #70: at this depth 3.13.7 and
    3.14.10 both raise, a future CPython may parse it, and either is fine. What
    must hold on all of them is that ``get_json`` returns a value or raises
    ``ServiceError`` — never ``RecursionError``, which ``collect_stack`` does
    not catch.

    Both branches assert, so neither is a free pass: the raising branch pins
    the kind, and the parsing branch pins that the body really was 200,000 deep
    and came back whole rather than truncated by a lenient parser.
    """
    depth = 200_000
    body = b"[" * depth + b"]" * depth

    def handler(request):
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    try:
        parsed = _client(handler).get_json("/x")
    except ServiceError as exc:
        assert exc.kind == "bad-response"
    else:
        assert _list_depth(parsed) == depth


```

```bash
unset VIRTUAL_ENV
for v in 3.14 3.13; do
  echo "--- CPython $v"
  uv run --frozen --python $v pytest -p no:cacheprovider -v \
    tests/collect/test_http.py -k "recursion_error or genuinely_deep" 2>&1 \
    | grep -E "PASSED|FAILED|passed|failed"
done

```

```output
--- CPython 3.14
tests/collect/test_http.py::test_a_recursion_error_from_the_json_decode_is_bad_response PASSED [ 50%]
tests/collect/test_http.py::test_a_genuinely_deep_body_is_handled_whatever_the_interpreter_does PASSED [100%]
======================= 2 passed, 20 deselected in 0.07s =======================
--- CPython 3.13
tests/collect/test_http.py::test_a_recursion_error_from_the_json_decode_is_bad_response PASSED [ 50%]
tests/collect/test_http.py::test_a_genuinely_deep_body_is_handled_whatever_the_interpreter_does PASSED [100%]
======================= 2 passed, 20 deselected in 0.49s =======================
```

## The mutation: does the new test still fail if `RecursionError` is removed?

A copy of `src/` is mutated in a temp directory and shadowed onto the import
path with `PYTHONPATH`, so the repository is never edited. The import is
printed to prove the mutant is what ran.

```bash
unset VIRTUAL_ENV
shadow=$(mktemp -d)
cp -R src "$shadow/src"
target="$shadow/src/lintarr/collect/http.py"
sed 's/except (ValueError, RecursionError) as exc:/except ValueError as exc:/' \
  "$target" > "$target.mut" && mv "$target.mut" "$target"
diff src/lintarr/collect/http.py "$target" || true
PYTHONPATH="$shadow/src" uv run --frozen --python 3.14 python -c \
  "import lintarr.collect.http as h; print('imported from:', h.__file__)"
PYTHONPATH="$shadow/src" uv run --frozen --python 3.14 pytest -q -rf -p no:cacheprovider \
  tests/collect/test_http.py 2>&1 | tail -5

```

```output
173c173
<         except (ValueError, RecursionError) as exc:
---
>         except ValueError as exc:
Using CPython 3.14.10
Removed virtual environment at: .venv
Creating virtual environment at: .venv
Installed 26 packages in 17ms
imported from: /var/folders/7v/j3klsc_j5kvcl9h24_p35jl00000gn/T/tmp.lCSUECNoLW/src/lintarr/collect/http.py
/Users/tom/.local/share/uv/python/cpython-3.14.10-macos-aarch64-none/lib/python3.14/json/decoder.py:361: RecursionError
=========================== short test summary info ============================
FAILED tests/collect/test_http.py::test_a_recursion_error_from_the_json_decode_is_bad_response
FAILED tests/collect/test_http.py::test_a_genuinely_deep_body_is_handled_whatever_the_interpreter_does
2 failed, 20 passed in 0.50s
```

Red, and red on the *named* test rather than only on a neighbour.

That one block is the quick version. The full round below ran the **whole
suite** — not a node-id selection, which manufactures survivors — on both
interpreters, with a control between every pair of mutants and a byte-for-byte
restore check, and graded on the `FAILED` lines rather than the exit code.
The mirror mutant `drop-ValueError` is there as the specificity control: if
the new pair killed that one too, it would be covering the whole `except`
rather than the arm it names.

```
CONTROL (unmutated tree)
  control                3.13: 2839 passed, 40 deselected in 32.77s
MUTANT drop-RecursionError
  drop-RecursionError    3.13: 2 failed, 2837 passed, 40 deselected in 32.49s
      FAILED tests/collect/test_http.py::test_a_genuinely_deep_body_is_handled_whatever_the_interpreter_does
      FAILED tests/collect/test_http.py::test_a_recursion_error_from_the_json_decode_is_bad_response
CONTROL after restore
  control                3.13: 2839 passed, 40 deselected in 33.10s
MUTANT drop-ValueError
  drop-ValueError        3.13: 3 failed, 2836 passed, 40 deselected in 32.78s
      FAILED tests/collect/test_http.py::test_non_json_body_is_bad_response
      FAILED tests/collect/test_http.py::test_undecodable_body_is_bad_response
      FAILED tests/collect/test_stack.py::test_undecodable_body_is_recorded_not_raised
CONTROL after restore
  control                3.13: 2839 passed, 40 deselected in 32.56s
CONTROL (unmutated tree)
  control                3.14: 2839 passed, 40 deselected in 33.94s
MUTANT drop-RecursionError
  drop-RecursionError    3.14: 2 failed, 2837 passed, 40 deselected in 33.23s
      FAILED tests/collect/test_http.py::test_a_genuinely_deep_body_is_handled_whatever_the_interpreter_does
      FAILED tests/collect/test_http.py::test_a_recursion_error_from_the_json_decode_is_bad_response
CONTROL after restore
  control                3.14: 2839 passed, 40 deselected in 33.28s
MUTANT drop-ValueError
  drop-ValueError        3.14: 3 failed, 2836 passed, 40 deselected in 33.49s
      FAILED tests/collect/test_http.py::test_non_json_body_is_bad_response
      FAILED tests/collect/test_http.py::test_undecodable_body_is_bad_response
      FAILED tests/collect/test_stack.py::test_undecodable_body_is_recorded_not_raised
CONTROL after restore
  control                3.14: 2839 passed, 40 deselected in 32.99s
tree restored byte-for-byte
```

Read the counts, not just the verdicts: 2 failed + 2837 passed = 2839 on every
row, which is the control's total. No test silently stopped running, and the
baseline before this branch was 2838 — one test became two.

## The whole pre-push gate, on 3.14.10

```bash
unset VIRTUAL_ENV
uv run --frozen python -VV
grep -E '^version_info' .venv/pyvenv.cfg
uv run --frozen pre-commit run --all-files --hook-stage pre-push

```

```output
Python 3.14.10 (main, Nov 11 2026, 00:14:32) [Clang 23.1.3 ]
version_info = 3.14
trim trailing whitespace.................................................Passed
fix end of files.........................................................Passed
check yaml...............................................................Passed
check for merge conflicts................................................Passed
ruff check...............................................................Passed
ruff format..............................................................Passed
pytest...................................................................Passed
```

```bash
unset VIRTUAL_ENV
for v in 3.13 3.14; do
  printf '%-18s' "CPython $v:"
  uv run --frozen --python $v pytest -q -p no:cacheprovider 2>&1 | tail -1
done

```

```output
CPython 3.13:     2839 passed, 40 deselected in 32.56s
CPython 3.14:     2839 passed, 40 deselected in 32.83s
```

Both interpreters green. `git status` is clean: the mutation ran against a
shadow copy, and the only tracked change in this branch is
`tests/collect/test_http.py` plus this document.
