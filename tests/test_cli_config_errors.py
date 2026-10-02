"""A misconfigured environment must read as a usage error, not a crash.

``load_config`` raises ``ValueError`` for an environment it refuses — orphaned
credentials, a URL with no credentials — and the CLI handed ``os.environ``
straight to it. Click's default handling for an unexpected exception is to let
it propagate, so the user got a Python traceback for what is an input mistake.
"""

import os
import subprocess
import sys

from click.testing import CliRunner

from lintarr.cli import cli

_PREFIXES = ("QBIT_", "SONARR_", "RADARR_", "LINTARR_")
_CLEARED = {k: None for k in os.environ if k.startswith(_PREFIXES)}

# The typo in the issue: credentials present, URL misspelled, so the service
# would be neither collected nor reported absent.
ORPHANED = {"QBIT_USER": "admin", "QBIT_PASS": "hunter2", "QBIT_URLL": "http://qbt:8080"}
# The other ValueError site in load_config, to prove the guard is at the
# boundary rather than special-cased to the orphan check.
URL_WITHOUT_CREDENTIALS = {"QBIT_URL": "http://qbt:8080"}


def _run(args, extra_env):
    return CliRunner().invoke(cli, args, env={**_CLEARED, **extra_env}, obj={})


def _assert_clean_usage_error(result, *expected_names):
    assert result.exit_code == 2, result.output
    # The specific claim: nothing from load_config escaped as an exception.
    # Asserting on the absence of "Traceback" in `output` would pass even
    # unfixed, because CliRunner captures the exception instead of printing it.
    assert not isinstance(result.exception, ValueError), result.exception
    for name in expected_names:
        assert name in result.output, result.output


def test_dump_facts_reports_orphaned_credentials_as_a_usage_error():
    _assert_clean_usage_error(_run(["dump-facts"], ORPHANED), "QBIT_PASS", "QBIT_USER", "QBIT_URL")


def test_check_reports_orphaned_credentials_as_a_usage_error():
    _assert_clean_usage_error(_run(["check"], ORPHANED), "QBIT_PASS", "QBIT_USER", "QBIT_URL")


def test_a_url_without_credentials_is_also_a_usage_error():
    _assert_clean_usage_error(_run(["dump-facts"], URL_WITHOUT_CREDENTIALS), "QBIT_USER")


def test_the_usage_error_does_not_echo_the_credential_value():
    """It names the variables, so it must not print what is in them."""
    result = _run(["dump-facts"], ORPHANED)
    assert "hunter2" not in result.output


def test_a_real_process_prints_no_traceback():
    """The user-visible claim, proved outside CliRunner.

    CliRunner catches the exception rather than letting the interpreter print
    it, so no in-process assertion can see the traceback this issue is about.
    This runs the CLI as its own process and reads what a terminal would.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith(_PREFIXES)}
    env.update(ORPHANED)
    proc = subprocess.run(
        [sys.executable, "-c", "from lintarr.cli import cli; cli()", "dump-facts"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    combined = proc.stdout + proc.stderr
    assert "Traceback" not in combined, combined
    assert "ValueError" not in combined, combined
    assert "QBIT_URL" in combined, combined
