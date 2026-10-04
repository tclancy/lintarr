# qBittorrent 5.2.4 — the WebUI authentication-ban axiom

**Measured 2026-10-02** against a disposable `linuxserver/qbittorrent:latest`
container (qBittorrent **v5.2.4**, WebAPI **2.15.1**, LSIO
`5.2.4_v2.0.15-ls479`). Harness: `tools/probe_qbt_ban.py`. Reproduced three
times with every row identical, the last of those by the committed script.

Issue [#8](https://github.com/tclancy/lintarr/issues/8). This supersedes the
unmeasured guess in `authenticate`'s docstring; it does not supersede the
2026-08-26 measurement against 5.2.3, which it agrees with on every shared row.

## TL;DR

- **`banned` stays.** A banned IP's login attempt is answered HTTP **403** with
  a distinct 78-byte body. It is distinguishable from an ordinary auth failure
  (HTTP 401 `Unauthorized`) on both status and body, so the kind has a real
  producer and does not need dropping.
- **It is distinguishable on the login path only.** Any *other* request from a
  banned IP answers 403 `Forbidden` — byte-identical to an ordinary
  unauthenticated request. That is why the check lives in `authenticate` and
  nowhere else.
- **The refusal is identical whether the password was right or wrong.** That is
  what makes a ban a separate fix: waiting or restarting, never a new password.
- **Three things the ticket, and the code, had wrong** — `banned_IPs` does not
  populate, the fail-count default is 5 and not 3, and the ban engages on the
  attempt *after* the threshold.
- **One new defect fell out of the measurement**: a `Host`-header port mismatch
  is byte-identical to a wrong password. Filed as
  [#17](https://github.com/tclancy/lintarr/issues/17).

## The axiom

Every row below is bytes off the wire, not documentation.

| # | request | status | body | bytes |
|---|---|---|---|---|
| A1 | `GET /api/v2/app/version`, unauthenticated | 403 | `Forbidden` | 9 |
| A2 | login, **correct** password | 204 | *(empty)* | 0 |
| A3 | `GET version` with a valid session | 200 | `v5.2.4` | 6 |
| A4 | login, **wrong** password — **the control** | 401 | `Unauthorized` | 12 |
| A5 | login, unknown username | 401 | `Unauthorized` | 12 |
| A6 | login, correct password, `Host` port ≠ `WebUI\Port` | 401 | `Unauthorized` | 12 |
| C6 | login, 6th consecutive failure — **the ban** | **403** | `Your IP address has been banned after too many failed authentication attempts.` | **78** |
| D1 | login, wrong password, while banned | 403 | *(as C6)* | 78 |
| D2 | login, **correct** password, while banned | 403 | *(as C6)* | 78 |
| D3 | `GET version`, no session, while banned | 403 | `Forbidden` | 9 |
| D4 | `GET version`, **pre-ban** valid session, while banned | **200** | `v5.2.4` | 6 |

A2 sets `QBT_SID_8080`. None of the refusals carries `WWW-Authenticate` or
`Retry-After`; all are `text/plain; charset=UTF-8`. So the ban's recovery window
is **not** advertised in the response — the operator has to know
`WebUI\BanDuration`.

## What the equivalences say

| claim | measured |
|---|---|
| ban response differs from an ordinary auth failure | **True** |
| …even when the password sent was correct | **True** |
| banned-with-wrong-pw == banned-with-correct-pw | **True** |
| unknown username == wrong password | **True** |
| non-login request while banned == plain unauthenticated | **True** |
| `Host` port mismatch == wrong password | **True** ← [#17](https://github.com/tclancy/lintarr/issues/17) |
| a pre-ban session survives the ban | **True** |
| a successful login resets the failure counter | **True** |
| restarting qBittorrent clears an active ban | **True** |
| `banned_IPs` populates | **False** |

## Three corrections

### 1. `banned_IPs` is the wrong place to look, so #8's confirmation step cannot confirm

The ticket said to *"confirm `banned_IPs` becomes populated via
`GET /api/v2/app/preferences`"*. It stays `''` throughout — before, during and
after a confirmed ban. `banned_IPs` is the BitTorrent **peer** filter; the WebUI
auth ban is held separately and is not exposed as a preference at all.

The channel that does work is **`GET /api/v2/log/main`**:

```
WebAPI login failure. Reason: invalid credentials, attempt count: 1, IP: ::ffff:192.168.215.1, username: admin
...
WebAPI login failure. Reason: invalid credentials, attempt count: 5, IP: ::ffff:192.168.215.1, username: admin
WebAPI login failure. Reason: IP has been banned, IP: ::ffff:192.168.215.1, username: admin
```

Had the measurement trusted the prescribed check, it would have concluded no ban
was ever imposed while staring at the ban response.

### 2. `MaxAuthenticationFailCount` defaults to 5, not 3

`qbittorrent.py` said *"default 3, ban 3600s"*. `web_ui_max_auth_fail_count` is
**5**; `web_ui_ban_duration` is 3600, which was right.

### 3. The ban engages on the attempt *after* the threshold

Failures 1–5 are each answered as ordinary refusals. The **6th** attempt gets
the ban body. So the budget is five consecutive failures, not four — and a
success resets the counter, so it is five *per unbroken run of failures* rather
than five for the lifetime of the instance.

## Reading the log channel at all requires a second source IP

The ban is per source IP, so once the probe's own address is banned it can no
longer read the log that proves it. The harness reads from **127.0.0.1 inside
the container** via `docker exec`, which is a different address and never
banned. Without that, the measurement cannot see its own result.

`bypass_local_auth` is `False`, so localhost still authenticates normally — the
inside channel is a different *IP*, not an auth bypass.

## Consequences for lintarr

- `ReadOnlyClient._send` discarded the response body on every 4xx, so the ban was
  **unobservable from inside the adapter** no matter what qBittorrent answered.
  `ServiceError` now carries `status` and `body`.
- `authenticate` classified 403 by testing `"403" in exc.detail` — a substring of
  a prose message, which a path like `/api/403/thing` also satisfies. It now
  branches on `exc.status`.
- The ban body is read with a bounded `errors="replace"` decode, never `.text`
  and never PR #15's `decode_text` (which *raises* on an undecodable body —
  correct for the success path, wrong here). On the error path the status is the
  fact. A declared-but-absent multibyte charset makes `.text` raise
  `UnicodeDecodeError`, which is not an `httpx.HTTPError`, so it would not be
  caught and re-kinded at all: it escapes `_send` **and** `collect_stack`'s
  `except ServiceError`, aborting every healthy service's collection over one
  unreadable error page. The bound and the lenient decode are **coupled, not two
  independent choices**: cutting at a byte offset can halve a multi-byte
  character and manufacture invalid UTF-8 out of a body that was valid on the
  wire, so truncating is what makes `errors="replace"` mandatory.
- `_BAN_BODY_MARKER` matches the leading clause case-insensitively rather than
  all 78 bytes. Pinning the tail would turn a cosmetic upstream reword into a
  silent loss of the `banned` kind — a failure in the misleading direction. The
  prefix narrows that risk and cannot remove it, which is the one failure mode
  only a live run against a new release can catch.
- **The predicate cannot screen out the realistic fronting-proxy confound.** A
  WAF or proxy quoting that sentence would most likely answer 403 as well, and
  the measurement offers no further signal to narrow on — no `WWW-Authenticate`,
  no `Retry-After`, `text/plain` throughout. A 403 carrying that clause does
  mean *some* IP ban is in force, so `banned` is still the right kind to land
  on; the `detail` names qBittorrent as the likely rather than the certain
  source, because "restart qBittorrent" does nothing for a ban imposed upstream.
- `ServiceError.body` is **not** interpolated into the exception message —
  `__init__` formats only `kind` and `detail` — so the 512-byte bound caps what
  a held exception retains rather than what gets printed.
- `collect_stack` records `(label, kind)` and drops `detail`, so the recovery
  advice above currently reaches a debugger and not the CLI. The `kind` is what
  the operator sees, and the `banned`/`unauthorised` split is the deliverable.
  Carrying `detail` through is [#18](https://github.com/tclancy/lintarr/issues/18).

## Re-measuring

See the module docstring of `tools/probe_qbt_ban.py`. Two traps worth repeating.

**Publish on the port qBittorrent listens on.** `-p 18080:8080` fails
host-header validation and every single request, login or not, comes back 401
`Unauthorized` — indistinguishable from a bad password, which is how that
confound was found in the first place. The probe now resolves its target with
`docker port` and refuses a remapped publish in its pre-flight check instead of
measuring it; verified by running it against a `-p 18080:8080` container, where
it exits 1 with `refused the container's own password`.

**The probe must prove it is aimed where it thinks.** Every destructive request
goes to a published address and every confirmation comes from `docker exec`, so
if those are two different instances the probe bans a bystander and then reads a
clean log from the container, concluding nothing happened. `assert_same_instance`
stamps an inert `dyndns_domain` marker from inside and reads it back from
outside before anything destructive runs. The happy path exercises the
comparison (it prints the marker it confirmed); the mismatch *branch* is
unexercised, because manufacturing a bystander that accepts the container's own
temporary password is harder than the risk warrants.
