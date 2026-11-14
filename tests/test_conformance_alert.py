"""tools/conformance_alert.sh, run for real with a fake `curl` on PATH."""

import json
import os
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "conformance_alert.sh"
_URL = "https://github.com/tclancy/lintarr/actions/runs/1"
_TOKEN = "tk_super_secret_value"


_STDIN: dict[Path, str] = {}


def _fake_curl(bin_dir: Path, exit_code: int) -> Path:
    log = bin_dir / "argv.json"
    stub = bin_dir / "curl"
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "stdin = sys.stdin.read() if '--config' in sys.argv else ''\n"
        f"open({str(log)!r}, 'w').write(json.dumps({{'argv': sys.argv[1:], 'stdin': stdin}}))\n"
        "print('fake curl output mentioning nothing secret')\n"
        f"sys.exit({exit_code})\n"
    )
    stub.chmod(0o755)
    return log


def _run(tmp_path: Path, *, token: str = _TOKEN, result: object = None, curl_exit: int = 0):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = _fake_curl(bin_dir, curl_exit)
    work = tmp_path / "work"
    work.mkdir()
    if result is not None:
        (work / "conformance-result.json").write_text(
            result if isinstance(result, str) else json.dumps(result)
        )
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "NTFY_TOKEN": token,
        "RUN_URL": _URL,
    }
    proc = subprocess.run(
        ["bash", str(_SCRIPT)], cwd=work, env=env, capture_output=True, text=True, check=False
    )
    call = json.loads(log.read_text()) if log.exists() else None
    if call is not None:
        _STDIN[tmp_path] = call["stdin"]
    return proc, call["argv"] if call else None


def _header(argv: list[str], name: str) -> str:
    headers = [argv[i + 1] for i, a in enumerate(argv) if a == "-H"]
    return next(h for h in headers if h.startswith(f"{name}:"))


def _body(argv: list[str]) -> str:
    return argv[argv.index("-d") + 1]


def test_empty_token_exits_1_with_the_exact_message_and_posts_nothing(tmp_path):
    proc, argv = _run(tmp_path, token="")
    assert proc.returncode == 1
    assert "NTFY_TOKEN secret is not set; no alert was sent" in proc.stderr + proc.stdout
    assert argv is None, "curl was invoked despite no token"


def test_missing_result_file_posts_harness_error_and_says_it_was_missing(tmp_path):
    proc, argv = _run(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert _body(argv).startswith("harness-error")
    assert "missing" in _body(argv)


def test_unparseable_result_file_is_a_harness_error(tmp_path):
    proc, argv = _run(tmp_path, result="{not json")
    assert proc.returncode == 0, proc.stderr
    assert _body(argv).startswith("harness-error")


def test_disagreement_result_sets_title_body_and_url(tmp_path):
    result = {
        "kind": "disagreement",
        "failed": ["1-a", "2-b"],
        "kinds": {"1-a": "disagreement", "2-b": "disagreement"},
    }
    proc, argv = _run(tmp_path, result=result)
    assert proc.returncode == 0, proc.stderr
    assert "disagreement" in _header(argv, "Title")
    assert _STDIN[tmp_path] == f'header = "Authorization: Bearer {_TOKEN}"\n'
    assert _TOKEN not in " ".join(argv)
    assert "--config" in argv
    assert _header(argv, "Priority") == "Priority: high"
    assert _header(argv, "Click") == f"Click: {_URL}"
    assert _body(argv).startswith("disagreement: 1-a (disagreement),2-b (disagreement) — ")
    assert _URL in _body(argv)
    assert "--fail-with-body" in argv
    assert argv[-1] == "https://notifications.tomclancy.info/homelab-alerts"


@pytest.mark.parametrize("kind", ["pass", "disagreement\nPriority: min", "", "DISAGREEMENT", 7])
def test_an_unknown_kind_is_a_harness_error(tmp_path, kind):
    proc, argv = _run(tmp_path, result={"kind": kind, "failed": ["a"]})
    assert proc.returncode == 0, proc.stderr
    assert _body(argv).startswith("harness-error")
    assert "harness-error" in _header(argv, "Title")


def _listed(argv: list[str], kind: str) -> str:
    return _body(argv).removeprefix(f"{kind}: ").split(" — ")[0]


def test_each_failed_id_carries_its_own_kind(tmp_path):
    result = {
        "kind": "harness-error",
        "failed": ["1-a", "2-b"],
        "kinds": {"1-a": "disagreement", "2-b": "harness-error"},
    }
    _, argv = _run(tmp_path, result=result)
    assert "harness-error" in _header(argv, "Title")
    assert _listed(argv, "harness-error") == "1-a (disagreement),2-b (harness-error)"


@pytest.mark.parametrize("kind", ["pass", "disagreement\nPriority: min", "", "DISAGREEMENT", 7])
def test_an_unknown_per_id_kind_is_a_harness_error(tmp_path, kind):
    result = {"kind": "disagreement", "failed": ["1-a"], "kinds": {"1-a": kind}}
    _, argv = _run(tmp_path, result=result)
    assert _listed(argv, "disagreement") == "1-a (harness-error)"


@pytest.mark.parametrize("kinds", [None, "not-a-dict", {}])
def test_a_missing_per_id_kind_is_a_harness_error(tmp_path, kinds):
    result = {"kind": "disagreement", "failed": ["1-a"]}
    if kinds is not None:
        result["kinds"] = kinds
    _, argv = _run(tmp_path, result=result)
    assert _listed(argv, "disagreement") == "1-a (harness-error)"


def test_failed_ids_are_capped_at_300_chars_on_a_pair_boundary(tmp_path):
    ids = [f"scenario-{i:03d}-{'x' * 20}" for i in range(40)]
    kinds = dict.fromkeys(ids, "disagreement")
    _, argv = _run(tmp_path, result={"kind": "disagreement", "failed": ids, "kinds": kinds})
    listed = _listed(argv, "disagreement")
    assert 0 < len(listed) <= 300
    assert listed.endswith(",…")
    pairs = listed.removesuffix(",…").split(",")
    assert pairs == [f"{i} (disagreement)" for i in ids[: len(pairs)]]
    next_pair = f",{ids[len(pairs)]} (disagreement)"
    assert len(listed.removesuffix(",…")) + len(next_pair) + len(",…") > 300, "trimmed too early"


def test_a_list_that_fits_is_not_marked_as_trimmed(tmp_path):
    ids = [f"s{i}" for i in range(3)]
    kinds = dict.fromkeys(ids, "disagreement")
    _, argv = _run(tmp_path, result={"kind": "disagreement", "failed": ids, "kinds": kinds})
    assert "…" not in _listed(argv, "disagreement")


def test_one_id_longer_than_the_cap_is_cut_and_marked(tmp_path):
    long_id = "x" * 400
    result = {"kind": "disagreement", "failed": [long_id], "kinds": {long_id: "disagreement"}}
    _, argv = _run(tmp_path, result=result)
    listed = _listed(argv, "disagreement")
    assert len(listed) <= 300
    assert listed.endswith("…")


def test_non_2xx_from_curl_fails_the_script(tmp_path):
    proc, _ = _run(tmp_path, result={"kind": "disagreement", "failed": ["a"]}, curl_exit=22)
    assert proc.returncode != 0


@pytest.mark.parametrize("curl_exit", [0, 22])
def test_the_token_never_reaches_stdout_or_stderr(tmp_path, curl_exit):
    proc, _ = _run(tmp_path, result={"kind": "disagreement", "failed": ["a"]}, curl_exit=curl_exit)
    assert _TOKEN not in proc.stdout
    assert _TOKEN not in proc.stderr
