#!/usr/bin/env python3
"""Measure qBittorrent's real WebUI authentication-ban response. Issue #8.

**DISPOSABLE INSTANCES ONLY.** The method is deliberate repeated authentication
failure, which earns the caller an IP ban on whatever box receives it. Never
aim this at a qBittorrent anyone depends on.

Bring up a throwaway instance, then run the probe::

    docker run -d --name lintarr-qbt-probe \\
      -e PUID=1000 -e PGID=1000 -e WEBUI_PORT=8080 -p 8080:8080 \\
      -v /tmp/qbt-probe/config:/config -v /tmp/qbt-probe/downloads:/downloads \\
      linuxserver/qbittorrent:latest
    PW=$(docker logs lintarr-qbt-probe 2>&1 \\
         | sed -nE 's/.*temporary password[^:]*: ([A-Za-z0-9]+).*/\\1/p' | tail -1)
    uv run python tools/probe_qbt_ban.py "$PW"
    docker rm -f lintarr-qbt-probe

**Publish on the port qBittorrent listens on** — ``-p 8080:8080``, not
``-p 18080:8080``. A remapped publish makes the ``Host`` header disagree with
``WebUI\\Port``, and qBittorrent then answers *every* request 401
"Unauthorized", indistinguishable from a wrong password. That confound is row
A6 and it is issue #17.

``docker port`` resolves the target so the probe cannot be aimed at a bystander,
and the pre-flight ``assert_same_instance`` then refuses a remapped publish
instead of measuring the confound — measured, not assumed. It fixes where the
probe points, not the port mismatch itself.

Two things make the measurement possible at all:

* **The detection channel is ``GET /api/v2/log/main``, not the ``banned_IPs``
  preference.** ``banned_IPs`` is the BitTorrent *peer* filter and stays empty
  through a WebUI auth ban, so the confirmation step issue #8 prescribed cannot
  confirm anything. The server log says ``Reason: IP has been banned`` outright.
* **The log is read from 127.0.0.1 inside the container.** The ban is per source
  IP, so localhost stays usable after the host IP is banned. It is the only
  read channel that survives the measurement.
"""

import json
import os
import subprocess
import sys
import time

import httpx

CONTAINER = "lintarr-qbt-probe"
PORT = 8080  # the port qBittorrent listens on *inside* the container
USER = "admin"
WRONG = "definitely-not-the-password"
LOG_PATH = "/api/v2/log/main?normal=true&info=true&warning=true&critical=true"

ROWS: list[dict] = []


def _host(args: list[str]) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


def _exec(args: list[str]) -> str:
    return _host(["docker", "exec", CONTAINER, *args])


def published_base() -> str:
    """The address *CONTAINER* actually publishes — never a hardcoded guess.

    Every destructive request goes to this address and every confirmation comes
    from ``docker exec CONTAINER``. If those are two different instances the
    probe bans a bystander and then reads a clean log from the container,
    concluding nothing happened. Asking docker where the container is published
    is what makes them the same box by construction.

    This does *not* make a remapped publish work: the Host header carries the
    *published* port and qBittorrent validates it against its own
    ``WebUI\\Port``, so ``-p 18080:8080`` still fails every request. What it
    does is hand that case to ``assert_same_instance``, which refuses rather
    than measuring it.
    """
    mapping = _host(["docker", "port", CONTAINER, f"{PORT}/tcp"]).strip().splitlines()
    if not mapping:
        raise SystemExit(f"{CONTAINER} does not publish {PORT}/tcp — nothing to probe")
    return f"http://127.0.0.1:{mapping[0].rsplit(':', 1)[1]}"


BASE = ""  # set by main() from published_base()


def inside_login(password: str) -> str:
    """Log in from 127.0.0.1 inside the container; return the cookie header."""
    out = _exec(
        [
            "curl",
            "-s",
            "-i",
            "-X",
            "POST",
            "--data",
            f"username={USER}&password={password}",
            f"http://127.0.0.1:{PORT}/api/v2/auth/login",
        ]
    )
    for line in out.splitlines():
        if line.lower().startswith("set-cookie"):
            return line.split(":", 1)[1].split(";")[0].strip()
    raise SystemExit(f"inside-container login failed:\n{out}")


def inside_get(path: str, sid: str) -> str:
    return _exec(["curl", "-s", "-H", f"Cookie: {sid}", f"http://127.0.0.1:{PORT}{path}"])


def server_log(sid: str) -> list[str]:
    return [r["message"] for r in json.loads(inside_get(LOG_PATH, sid))]


def banned_ips(sid: str) -> str:
    return json.loads(inside_get("/api/v2/app/preferences", sid))["banned_IPs"]


def temp_password() -> str:
    """The password linuxserver/qbittorrent prints on each cold start."""
    logs = subprocess.run(["docker", "logs", CONTAINER], capture_output=True, text=True).stdout
    hits = [ln.split(": ")[-1].strip() for ln in logs.splitlines() if "temporary password" in ln]
    if not hits:
        raise SystemExit("no temporary password in the container log")
    return hits[-1]


def wait_for_webui(timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            httpx.get(f"{BASE}/api/v2/app/version", timeout=3)
            time.sleep(2)  # the WebUI answers a beat before auth is wired up
            return
        except httpx.HTTPError:
            time.sleep(2)
    raise SystemExit("WebUI did not come up")


def record(tag: str, r: httpx.Response) -> dict:
    row = {
        "tag": tag,
        "status": r.status_code,
        "reason": r.reason_phrase,
        "body": r.text,
        "body_len": len(r.content),
        "content_type": r.headers.get("content-type", ""),
        "retry_after": r.headers.get("retry-after"),
        "www_authenticate": r.headers.get("www-authenticate"),
        "sets_cookie": bool(r.headers.get("set-cookie")),
    }
    ROWS.append(row)
    print(
        f"  {tag:<48} {row['status']} {row['reason']:<12} "
        f"len={row['body_len']:<3} body={row['body'][:46]!r}"
    )
    return row


def login(client: httpx.Client, password: str, **kw) -> httpx.Response:
    return client.post("/api/v2/auth/login", data={"username": USER, "password": password}, **kw)


def fresh(**kw) -> httpx.Client:
    """A cookie-less client — each login attempt must stand on its own."""
    return httpx.Client(base_url=BASE, timeout=10, **kw)


def assert_same_instance(password: str, sid: str) -> None:
    """Prove the published address and the exec'd container are one instance.

    ``docker port`` already makes that true unless something is sitting in
    front of the published port — a tunnel, a proxy, a stale forward. So: stamp
    an inert free-text preference from inside and read it back from outside.
    ``dyndns_domain`` is unused while dynamic DNS is disabled, which it is by
    default.

    This runs before anything destructive, and it uses the *correct* password,
    which a successful login resets the failure counter with rather than
    spending it. If the address is a bystander the login fails and the probe
    exits one failure in, instead of six.
    """
    token = f"probe-{os.getpid()}-{int(time.time())}.invalid"
    _exec(
        [
            "curl",
            "-s",
            "-X",
            "POST",
            "-H",
            f"Cookie: {sid}",
            "--data-urlencode",
            f'json={{"dyndns_domain": "{token}"}}',
            f"http://127.0.0.1:{PORT}/api/v2/app/setPreferences",
        ]
    )
    with fresh() as c:
        if login(c, password).status_code not in (200, 204):
            raise SystemExit(
                f"{BASE} refused the container's own password — it is not {CONTAINER}. "
                "Refusing to aim failed logins at it."
            )
        seen = c.get("/api/v2/app/preferences").json().get("dyndns_domain")
    if seen != token:
        raise SystemExit(
            f"{BASE} is not {CONTAINER}: stamped {token!r} inside, read {seen!r} outside. "
            "Refusing to aim failed logins at a bystander."
        )
    print(f"  target confirmed: {BASE} is {CONTAINER} (marker {token})")


def same(a: dict, b: dict) -> bool:
    """Equivalence over everything a caller could branch on."""
    keys = ("status", "reason", "body", "content_type", "retry_after", "www_authenticate")
    return all(a[k] == b[k] for k in keys)


def main() -> int:
    global BASE
    BASE = published_base()
    password = sys.argv[1] if len(sys.argv) > 1 else temp_password()
    sid = inside_login(password)
    assert_same_instance(password, sid)

    app_version = inside_get("/api/v2/app/version", sid).strip()
    api_version = inside_get("/api/v2/app/webapiVersion", sid).strip()
    prefs = json.loads(inside_get("/api/v2/app/preferences", sid))
    threshold = prefs["web_ui_max_auth_fail_count"]

    print(f"qBittorrent {app_version}, WebAPI {api_version}")
    for k in (
        "web_ui_max_auth_fail_count",
        "web_ui_ban_duration",
        "banned_IPs",
        "web_ui_host_header_validation_enabled",
        "web_ui_domain_list",
        "web_ui_csrf_protection_enabled",
        "bypass_local_auth",
    ):
        print(f"  {k:<40} = {prefs[k]!r}")
    if any("banned" in m for m in server_log(sid)):
        raise SystemExit("a ban is already in force — restart the container before measuring")

    print("\nPHASE A — axioms with no ban in force")
    with fresh() as c:
        a_unauth = record("A1 GET version, unauthenticated", c.get("/api/v2/app/version"))
        a_ok = record("A2 POST login, correct password", login(c, password))
        session = str(c.cookies.get(f"QBT_SID_{PORT}")) if a_ok["sets_cookie"] else ""
        record("A3 GET version, valid session", c.get("/api/v2/app/version"))
    with fresh() as c:
        a_wrong = record("A4 POST login, WRONG password  [CONTROL]", login(c, WRONG))
    with fresh() as c:
        a_nouser = record(
            "A5 POST login, unknown username",
            c.post("/api/v2/auth/login", data={"username": "nobody", "password": WRONG}),
        )
    before_host = sum("attempt count" in m for m in server_log(sid))
    with fresh() as c:
        a_host = record(
            "A6 POST login, CORRECT pw, wrong Host port",
            login(c, password, headers={"Host": f"127.0.0.1:{PORT + 1}"}),
        )
    host_counted = sum("attempt count" in m for m in server_log(sid)) > before_host
    print(f"       host-header failure incremented the auth counter? {host_counted}")

    print("\nPHASE B — does a successful login reset the consecutive-failure counter?")
    with fresh() as c:
        record("B1 POST login, correct password", login(c, password))
    with fresh() as c:
        record("B2 POST login, WRONG password", login(c, WRONG))
    counts = [m for m in server_log(sid) if "attempt count" in m]
    last_count = int(counts[-1].split("attempt count: ")[1].split(",")[0])
    reset_by_success = last_count == 1
    print(
        f"       attempt count after success-then-failure = {last_count}"
        f" -> success resets: {reset_by_success}"
    )

    print(f"\nPHASE C — drive consecutive failures (threshold pref = {threshold})")
    observed = None
    with fresh() as c:
        for n in range(2, threshold + 6):
            record(f"C{n} POST login, WRONG password (#{n} consecutive)", login(c, WRONG))
            if any("IP has been banned" in m for m in server_log(sid)):
                observed = n
                print(f"       -> server log says banned on attempt #{n}")
                break
    if observed is None:
        print("  !! never banned — the measurement failed, not qBittorrent")
        return 1

    print("\nPHASE D — responses while the ban is in force")
    with fresh() as c:
        d_wrong = record("D1 POST login, WRONG password, banned", login(c, WRONG))
    with fresh() as c:
        d_right = record("D2 POST login, CORRECT password, banned   [KEY]", login(c, password))
    with fresh() as c:
        d_unauth = record("D3 GET version, no session, banned", c.get("/api/v2/app/version"))
    d_sess = None
    if session:
        with fresh(headers={"Cookie": f"QBT_SID_{PORT}={session}"}) as c:
            d_sess = record(
                "D4 GET version, pre-ban valid session, banned", c.get("/api/v2/app/version")
            )

    print("\nPHASE E — server-side confirmation from 127.0.0.1 (a different IP, never banned)")
    log = server_log(sid)
    banned_ips_while_banned = banned_ips(sid)
    print(
        f"  banned_IPs preference = {banned_ips_while_banned!r}"
        f"   <- issue #8 expected this to populate"
    )
    for m in log:
        if "banned" in m or "Invalid Host" in m or "attempt count: 1," in m:
            print(f"    log: {m}")

    print("\nPHASE F — does restarting qBittorrent clear the ban?")
    subprocess.run(["docker", "restart", CONTAINER], capture_output=True, check=True)
    wait_for_webui()
    with fresh() as c:
        # a cold start mints a new temporary password; the ban is the subject here
        f_after = record(
            "F1 POST login, correct password, after restart", login(c, temp_password())
        )

    verdict = {
        "observed_ban_attempt": observed,
        "threshold_pref": threshold,
        "ban_engages_after_n_consecutive_failures": observed - 1,
        "ban_differs_from_auth_failure": not same(d_wrong, a_wrong),
        "banned_with_correct_pw_differs_from_auth_failure": not same(d_right, a_wrong),
        "banned_wrong_pw_equals_banned_correct_pw": same(d_wrong, d_right),
        "host_mismatch_equals_auth_failure": same(a_host, a_wrong),
        "host_mismatch_increments_counter": host_counted,
        "unknown_user_equals_wrong_password": same(a_nouser, a_wrong),
        "nonlogin_while_banned_equals_plain_unauthenticated": same(d_unauth, a_unauth),
        "pre_ban_session_survives": bool(d_sess and d_sess["status"] == 200),
        "banned_IPs_populated": banned_ips_while_banned != "",
        "success_resets_failure_counter": reset_by_success,
        "restart_clears_ban": f_after["status"] == 204,
    }
    print("\nVERDICT")
    for k, val in verdict.items():
        print(f"  {k:<52} = {val}")

    out = {
        "measured_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "app_version": app_version,
        "webapi_version": api_version,
        "image": subprocess.run(
            ["docker", "inspect", "-f", "{{.Config.Image}} {{.Image}}", CONTAINER],
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "prefs": {
            k: prefs[k]
            for k in (
                "web_ui_max_auth_fail_count",
                "web_ui_ban_duration",
                "banned_IPs",
                "web_ui_host_header_validation_enabled",
                "web_ui_domain_list",
                "web_ui_csrf_protection_enabled",
                "bypass_local_auth",
            )
        },
        "verdict": verdict,
        "rows": ROWS,
        "server_log": log,
    }
    print(json.dumps(out, indent=2), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
