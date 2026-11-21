# `hardlink-futility`: design

Status: design, 2026-11-19. Third P3 slice after pluggable backends
(lintarr#56). Supersedes the `hardlink-futility` paragraph in
`2026-08-26-lintarr-design.md` (see "What changed from the original design").

## Intent

Sonarr and Radarr can import a finished download into the library by
hardlinking it rather than copying it. A hardlink costs no extra disk and lets
the torrent keep seeding the same bytes. It only works when the download and the
library are on the same filesystem **as the arr sees them**. When they are not,
the arr falls back to copying without saying so. Each import then doubles its
disk use, and that is noticed weeks later when the disk fills.

The commonest cause is not two disks. It is two Docker volumes:
`-v /mnt/x/torrents:/downloads -v /mnt/x/media:/tv`. Both are on one host
filesystem, but `link(2)` refuses to cross a mount point and returns `EXDEV`, so
every "hardlink" is a copy. TRaSH's guides exist largely because of this.

The check answers one question: **with hardlinks turned on, would an import from
this download client into this arr's library actually be a hardlink?**

## Goals

1. FAIL when an arr has hardlinks enabled and a download client's save path for
   that arr's category is on a different mount, in the arr's own view, from one
   of the arr's root folders.
2. PASS when every such pair is on the same mount, or hardlinks are off.
3. SKIP, never guess, when any input needed to decide is unreadable.
4. Run in today's deployment (host systemd timer, shape A), with no access to
   the arr's container.
5. Be the first shared check: written once over client-neutral facts, and run
   for every backend.

## Non-goals

- Proving `link(2)` succeeds. Same mount point is necessary for a hardlink,
  not sufficient: mergerfs across branches and btrfs subvolumes under one mount
  can still fail. Those cases are documented limitations, not checked.
- Measuring wasted space (that's `orphan-inevitability`'s link-count-aware
  sizing).
- Usenet download clients. The arr may have them; lintarr collects none.
- Windows paths. Arrs on Windows report `C:\…`; those inputs SKIP.

## Anti-goals

- **A FAIL on a working homelab.** Tom's stack (one `/data` volume in every
  container) must PASS. A false FAIL here pages him hourly.
- **A verdict from the host's view.** lintarr runs on the host. A host-side
  `stat` or `os.link()` cannot see container bind mounts, which are exactly
  the failure that matters. Every path comparison happens in the arr's
  namespace, from what the arr reports.

## What changed from the original design

The original paragraph planned a free-space "mount contract" (arr
`freeSpace` and qBittorrent `free_space_on_disk` against the sidecar's own
`statvfs`) plus a real `os.link()` probe. Both assumed a sidecar sharing the
arr's mounts. The deploy-scope doc already noted host-run lintarr can't do that.

A live probe on 2026-11-19 found a better source. **Sonarr and Radarr's
`GET /api/v3/diskspace` lists the mount points in their own namespace.** On
homelab it returns `/`, `/config` and `/data`. `/config` is a bind mount on the
same disk as `/`, and it is listed separately. A mount-point comparison inside
that list detects the bind-mount case that host statting cannot, and it needs
nothing but the API. The original approach is dropped:

| Approach | Sees container bind mounts | Works from the host | Verdict |
|---|---|---|---|
| Host `statvfs` + `os.link()` (original) | No | Only on host paths | Dropped |
| Free-space cross-check (original) | No (same fs, same free bytes) | Yes | Dropped |
| Arr's own `/api/v3/diskspace` mounts (this design) | Yes | Yes | **Chosen** |

## Inputs

Measured live on 2026-11-19 against Sonarr 4.0.20.3014, Radarr 6.4.4.10685 and
qBittorrent 5.2.3:

| Fact | Source | Homelab value |
|---|---|---|
| Hardlinks on | arr `GET /api/v3/config/mediamanagement` → `copyUsingHardlinks` | `true` (both) |
| Root folders | arr `GET /api/v3/rootfolder` → `[].path` | `/data/media/tv`, `/data/media/movies` |
| Mounts | arr `GET /api/v3/diskspace` → `[].path` | `/`, `/config`, `/data` |
| Remote path mappings | arr `GET /api/v3/remotepathmapping` → `[].{host, remotePath, localPath}` | none |
| Download clients | arr `GET /api/v3/downloadclient` → `implementation`, `enable`, `fields[host]`, `fields[tvCategory\|movieCategory]` | `QBittorrent`, enabled, `gluetun`, `tv` / `movies` |
| Save path per category | qBittorrent prefs `save_path`, `auto_tmm_enabled`; `GET /api/v2/torrents/categories` → `savePath` | `/data/torrents/tv`, `/data/torrents/movies` |

## The check

For one download-client instance `C`, over every collected arr `A`:

1. **Which arr download clients are `C`?** An enabled arr download client `D`
   is `C` when `D.implementation.lower() == C.kind` and `C.common.save_paths`
   has `D`'s category. A blank or absent category is the key `""`, the
   client's uncategorised save location. Sonarr allows a blank category, and
   qBittorrent then saves to its default path. If `C` has no such key, `D` is
   not `C` and is ignored. That covers Usenet clients, disabled clients and
   other kinds.
   A check sees one client instance, not its siblings. So when two collected
   clients of one kind both define the category, both claim `D`. That
   over-reports. It can also give a **false FAIL** in one case: the configured
   client shares a category name with a second, unconfigured client of the same
   kind that the arr actually uses. Port matching was rejected because a
   published-port remap (`-p 18080:8080`) would turn it into a false PASS.
   Disambiguating properly needs a wider `Check` signature, which is out of scope.
2. **Where does `C` save `D`'s downloads?** `C.common.save_paths[category]`, a
   path in `C`'s namespace.
3. **Where is that in the arr's namespace?** Apply the **first** of `A`'s remote
   path mappings, in the order the API lists them, whose `host` equals `D.host`
   (case-insensitive) and whose `remotePath` is a prefix of the save path. This
   mirrors Sonarr's `RemotePathMappingService`, which takes the first match in
   database order, not the longest. The prefix test is on whole path
   components, matching Sonarr's `/`-terminated paths. The translation runs on
   the raw string, *before* the POSIX check, so a Windows qBittorrent
   (`C:\Downloads\tv`) mapped to a Linux arr path still works. With no matching
   mapping the path is unchanged, which is what the arr itself does.
4. **Which mount is it on?** The longest `A.mounts` entry that is a
   component-wise prefix of the path. Do the same for each of `A`'s root folders
   whose `accessible` is true. A root folder whose `accessible` is false is
   left out and named in the detail. A leftover root no series can use is not
   evidence about imports.
5. **The triple `(A, D, root folder R)`** is decided like this:
   - `A.copy_using_hardlinks` known false: **no conflict**, whatever else is
     unreadable. Hardlinks off settles it.
   - Download and root on the same mount, `/` included: **no conflict**. A
     bare-metal arr on one disk has both on `/`, and hardlinks work there.
   - Different mounts, both other than `/`, with hardlinks known true:
     **conflict**.
   - Exactly one side only on `/` while the other is on a real mount: **unknown**.
     In a container, `/` is the image's own filesystem, so a download path
     visible only there means the arr can't see the download at all. That's a
     different failure with a different remedy, and the detail names it.
   - Any other unreadable input: **unknown**.

The finding FAILs if any triple conflicts. A triple with any unreadable input is
unknown. The finding SKIPs if no triple conflicts and at least one is unknown,
so an unknown never hides a FAIL found elsewhere. It PASSes otherwise.

Which arrs are involved also decides the outcome, so that nothing new pages:

- **No arrs collected and none failed:** **N/A**. On a qBittorrent stack
  queue-liveness already reports N/A there, so this adds no new non-zero exit.
  A backend with no N/A check of its own will now exit 3 in strict mode, which
  is the project's rule for N/A.
- **An arr failed to collect:** that arr can't be ruled out, so a run with no
  conflicting triple is **SKIP**, not PASS. The arr's own ERROR is reported
  too.
- **Arrs collected, all their `download_clients` known, none is `C`:** **PASS**,
  with the detail "no collected arr sends downloads to this client". This is
  common with a second client, and paging hourly over it would be wrong.
- **An arr's `download_clients` unknown:** that arr is unknown, so SKIP unless a
  FAIL is found elsewhere.

**Paths** are POSIX and absolute, normalised with `PurePosixPath`. A trailing
slash is dropped, and a leading `//` (which `PurePosixPath` keeps) is collapsed
to `/`. A path that isn't absolute POSIX after translation is unknown. That
covers a Windows drive, a relative path, an empty string and a non-string. A
`..` component is unknown too (#60): the comparison is lexical, so
`/downloads/../media/x` would read as sitting under `/downloads`, and resolving
it lexically is right only when no earlier component is a symlink, which the
string cannot say. Real arr and qBittorrent paths do not contain `..`; the
receipts are in `docs/60-dotdot-paths-are-unplaceable.md`.

### Finding shape

- `invariant="hardlink-futility"`, `instance="<kind>[<name>]"` (the client's
  label, as every client finding has), `conflict="cross-mount"`.
- One premise, `arr.hardlink_import_crosses_a_mount`. The tuple aggregation
  above *is* the predicate, and the combinator settles FAIL, SKIP or PASS from
  its single state.
- `detail` names every conflicting triple on FAIL (and every unknown one on
  SKIP). The format is
  `sonarr[tv]: downloads in /downloads/tv are on mount /downloads, root folder /tv/shows is on mount /tv`.
  This is the line the operator acts on.
- **Therefore**: "every import from this client into that library is a full
  copy, not a hardlink, so each one doubles its disk use. Put downloads and
  library under one volume in the arr's container (TRaSH's `/data` layout), or
  turn hardlinks off to make the copying deliberate."

## Model changes

### Client-neutral fact (first one)

`ClientFacts.save_paths: Fact[Mapping[str, str]]` holds, per category, the
absolute directory in the client's own namespace where a new download in that
category is saved. The key `""` is the uncategorised location. Under "client
facts grow with their consumers", every registered backend populates it in this
slice:

- **qBittorrent** reads three prefs: `save_path`, `auto_tmm_enabled` and
  `use_category_paths_in_manual_mode`. The last was verified live on 5.2.3,
  where it is `false`.
  - `""` maps to `save_path`.
  - A category's own path is used when `auto_tmm_enabled` is true, or when it is
    false and `use_category_paths_in_manual_mode` is true. Otherwise the
    category maps to `save_path`.
  - A category's own path is its `savePath`. An empty `savePath` resolves to
    `<save_path>/<category name>`, and a relative one to
    `<save_path>/<savePath>` (assumed from `Session::categorySavePath`, not
    measured).
  - Any input the mapping needs that is unknown makes the whole mapping `Unknown`.
- **Test fake backend:** a fixed mapping chosen by its URL, like its other
  switches.
- `docs/adding-a-backend.md` gains the obligation.

### Arr facts

`ArrInstance` gains five facts, each a `Fact` so an old arr missing an endpoint
degrades to SKIP rather than ERROR:

- `copy_using_hardlinks: Fact[bool]`. A value that isn't a bool is
  `bad-response`, because `premise()` would read the string `"false"` as true.
- `root_folders: Fact[tuple[RootFolder, ...]]`, where
  `RootFolder(path: str, accessible: bool | None)`. An absent `accessible` is
  `None`, which counts as accessible.
- `mounts: Fact[tuple[str, ...]]`
- `remote_path_mappings: Fact[tuple[RemotePathMapping, ...]]`, where
  `RemotePathMapping(host, remote_path, local_path)`, in API order
- `download_clients: Fact[tuple[ArrDownloadClient, ...]]`, where
  `ArrDownloadClient(name, implementation, enabled, host, category)` and
  `category` is the `tvCategory` or `movieCategory` field value (whichever is
  present). An absent or empty value is normalised to `""`.

**Error policy** (builder's call, recorded here): an HTTP 404 on one of these
endpoints makes that fact `Unknown("field-absent")`, and the arr's other facts
still stand. Any other `ServiceError` behaves as it does today and ERRORs the
arr, consistent with indexers. A malformed body is `bad-response`, as for
indexers.

`dump-facts` shows all of the new facts in both JSON and human output.

## Axioms

| Axiom | Status |
|---|---|
| `link(2)` across two mount points fails with `EXDEV`, even when both are bind mounts of one filesystem | **conformance-tested** (new, see below) |
| Within one mount point on ext4/xfs, `link(2)` succeeds | conformance-tested by the same test |
| Within one mount point, `link(2)` succeeds on mergerfs/btrfs subvolumes | **assumed false-negative risk**: documented limitation |
| Arr `diskspace` lists every mount a download path can lie on | assumed (the arr omits some fs types, e.g. network mounts, so a download on an unlisted mount nested inside a listed one reads as the parent mount) |
| qBittorrent's empty/relative category `savePath` resolution | assumed from source |
| Sonarr/Radarr apply the first matching remote path mapping in API order | assumed from source (`RemotePathMappingService`) |

**Conformance test** (in the nightly suite, `container` marker; seconds, no
services): `docker run` an Alpine container with two bind mounts of two
directories on one host filesystem, then assert `ln` across them exits non-zero
with "cross-device link" in stderr (case-insensitive: musl says
"Cross-device link", glibc "Invalid cross-device link").
Run it once more with one bind mount of their common parent and assert `ln`
succeeds. This tests the axiom the FAIL verdict rests on.

## Deploy impact

None to config: the new GETs use existing credentials. On homelab the check is
expected to PASS (both arrs: downloads and roots both under `/data`). The live
verification step must show that before merge. A SKIP or N/A would page Tom,
because the unit runs `check` strict.

## Approaches considered

1. **Arr-reported mounts (chosen).** API-only, sees bind mounts, works from the
   host. The cost is the documented mergerfs/btrfs blind spot and reliance on
   `diskspace` completeness.
2. **Host-side probe (original).** Tests the real syscall, but on the wrong
   namespace. It misses the commonest failure and needs a container path to
   host path map in config. Dropped.
3. **Wait for the sidecar (shape B), then probe inside a shared mount
   namespace.** Most faithful, but blocked on runtime work no one has started.
   It could later *add* an `os.link()` probe for the mergerfs case on top of (1).

## Left to the builder

- Module placement for the shared check and path helpers (a `checks/`
  submodule is the obvious home).
- Where the shared check's "Therefore" table lives. `cli._therefore` today looks
  only in the producing backend's table. A shared check needs a second table
  keyed the same way, consulted for shared invariants.
- Exact SKIP detail wording.
