# Remote path mapping, at the three edges lintarr was guessing at

**Read 2026-11-21** against **Sonarr `v4.0.20.3014`** (the latest stable
release, and the build homelab runs), **Sonarr `v5.0.0.2997`** (the latest v5
prerelease) and **Radarr `v6.4.4.10685`** (homelab's build). Release tags, not
a moving branch — the rule
[`2026-11-21-sonarr-removal-rule-versions.md`](2026-11-21-sonarr-removal-rule-versions.md)
set.

Issue [#68](https://github.com/tclancy/lintarr/issues/68). `checks/paths.py`
`translate` models `RemotePathMappingService.RemapRemoteToLocal`, and three
edges of it were never read: whether a Windows remote path matches
case-insensitively, what a blank `localPath` does, and what a remote path of
`/` does.

## TL;DR

- **A Windows path matches case-insensitively, and `/` and `\` are one
  separator.** A Windows qBittorrent reporting `c:\downloads\tv` matches a
  mapping for `C:\Downloads\`, and `c:/downloads` matches
  `C:\DOWNLOADS\tv\s1`. lintarr's prefix test was case- and separator-sensitive
  and missed both.
- **A POSIX path matches case-sensitively.** `/DL` and `/dl` are two
  directories. That was already lintarr's behaviour, and it is now pinned.
- **A remote path of `/` swallows the whole path.** `/` → `/mnt` does *not*
  turn `/data` into `/mnt/data`, which is what #68 assumed. It turns `/data`,
  and every other path the client reports, into **`/`**. lintarr now does the
  same.
- **A blank `localPath` is unconfigurable**, because the arr's own API
  validates it. The arr's arithmetic would turn `/downloads/tv` into `/tv` — an
  absolute path that reads as real. lintarr returns `''`, which no caller can
  place, so the path is reported unknown rather than guessed. This is the one
  deliberate divergence.
- **An unrooted path on either side never matches**, so an empty or relative
  `remotePath` applies to nothing.
- **One citation covers all three apps.** `OsPath.cs` is byte-identical on
  Sonarr `v5.0.0.2997` and Radarr `v6.4.4.10685`
  (`sha256 bfb2e92e…`) and differs from Sonarr `v4.0.20.3014`
  (`sha256 34233761…`) by one space in `new (null)`. The probe below prints the
  same 17 lines on all three.

## This is a source read, and an executed one

Like [`2026-10-05-sonarr-seed-ratio-range.md`](2026-10-05-sonarr-seed-ratio-range.md),
this is a read of open-source code at pinned tags rather than bytes off a
running container, so it is authoritative about mechanism and blind to a build
difference or a patched package. It is a step past a read, though:
[`docs/68-ospath-probe.sh`](../68-ospath-probe.sh) fetches **Sonarr's own
`OsPath.cs`** at the tag, compiles it with
[`docs/68-ospath-probe.cs`](../68-ospath-probe.cs) and runs the mapping
arithmetic. Every answer below is that code's output, not a transcription of it
into Python and then believed.

## The code that decides it

`src/NzbDrone.Core/RemotePathMappings/RemotePathMappingService.cs`, the whole
mapping loop:

```csharp
foreach (var mapping in mappings)
{
    _logger.Trace("Checking configured remote path mapping: {0} - {1}", mapping.Host, mapping.RemotePath);
    if (host.Equals(mapping.Host, StringComparison.InvariantCultureIgnoreCase) && new OsPath(mapping.RemotePath).Contains(remotePath))
    {
        var localPath = new OsPath(mapping.LocalPath) + (remotePath - new OsPath(mapping.RemotePath));
        _logger.Debug("Remapped remote path [{0}] to local path [{1}] for host [{2}]", remotePath, localPath, host);

        return localPath;
    }
}
```

So three `OsPath` members answer everything: `Contains` (does this mapping
apply?), `operator -` (what is left of the path?) and `operator +` (where does
that land?).

### Case, and separators, come from the path's *kind*

`OsPath.DetectPathKind` reads the kind off the string, and a leading `/` wins
before any backslash does:

```csharp
if (path.StartsWith("/")) { return OsPathKind.Unix; }
if (HasWindowsDriveLetter(path) || path.Contains('\\')) { return OsPathKind.Windows; }
if (path.Contains('/')) { return OsPathKind.Unix; }
return OsPathKind.Unknown;
```

A Windows path then has its slashes flipped to `\` (`FixSlashes`), and every
comparison — in `Contains`, in `operator -`, in `Equals` — folds case when
**either** side is Windows:

```csharp
var stringComparison = (Kind == OsPathKind.Windows || other.Kind == OsPathKind.Windows) ? StringComparison.InvariantCultureIgnoreCase : StringComparison.InvariantCulture;
```

Sonarr's own tests say the same, in
`src/NzbDrone.Common.Test/OsPathFixture.cs`: `should_compare_windows_ospathkind_case_insensitive`
and `should_compare_unix_ospathkind_case_sensitive`.

`Contains` also refuses unless both paths are rooted, which is what makes an
empty or relative `remotePath` match nothing:

```csharp
if (!IsRooted || !other.IsRooted) { return false; }
```

### Why `/` swallows the path

`operator -` compares the two paths' fragments, and `GetFragments` splits on
both separators with `StringSplitOptions.RemoveEmptyEntries` — so `/` has **no
fragments at all**. The loop therefore never runs, `i` stays `0`, and:

```csharp
if (i == 0)
{
    return right;
}
```

`right` is the mapping's own remote path, `/` — which is rooted. `operator +`
hands a rooted right-hand side straight back:

```csharp
if (right.IsRooted)
{
    return right;
}
```

`/mnt + / == /`. There is no upstream test for this case; the code is the only
statement of it, which is exactly why lintarr pins it.

## What the code prints

`docs/68-ospath-probe.sh`, verbatim (`v4.0.20.3014`; `v5.0.0.2997` and Radarr
`v6.4.4.10685` print these same lines):

```
A Windows remote path folds case AND separators:
  remote 'C:\Downloads\'    local '/data/torrents' raw 'c:\downloads\tv'          -> [/data/torrents/tv]
  remote 'c:/downloads'     local '/data/torrents' raw 'C:\DOWNLOADS\tv\s1'       -> [/data/torrents/tv/s1]
  remote 'C:\Downloads'     local '/data/torrents' raw 'c:\downloads'             -> [/data/torrents]
A POSIX remote path folds neither:
  remote '/DL'              local '/a'             raw '/dl/x'                    -> no match, path unchanged: [/dl/x]
  remote '/dl'              local '/a'             raw '/DL/x'                    -> no match, path unchanged: [/DL/x]
  remote '/dl'              local '/a'             raw '/dl/x'                    -> [/a/x]
  remote '/DL'              local '/a'             raw '/dl\x'                    -> no match, path unchanged: [/dl/x]
A root remote path swallows the whole path:
  remote '/'                local '/mnt'           raw '/data'                    -> [/]
  remote '/'                local '/mnt'           raw '/data/tv/x'               -> [/]
  remote '//'               local '/mnt'           raw '/data'                    -> [/]
  remote '\\'               local '/mnt'           raw '/data'                    -> THROWS Exception: Cannot combine OsPaths of different platforms ('/mnt' + '\\')
A blank local path (which the arr's own API refuses):
  remote '/dl'              local ''               raw '/dl/x'                    -> [/x]
  remote '/dl'              local '   '            raw '/dl/x'                    -> [   /x]
  remote '/dl'              local ''               raw '/dl'                      -> []
An unrooted path on either side never matches:
  remote ''                 local '/a'             raw '/dl/x'                    -> no match, path unchanged: [/dl/x]
  remote 'dl'               local '/a'             raw '/dl/x'                    -> no match, path unchanged: [/dl/x]
  remote '/dl'              local '/a'             raw 'dl/x'                     -> no match, path unchanged: [dl/x]
```

Two rows are worth reading twice. `local '   '` → `[   /x]`: whitespace is
`IsEmpty` to `OsPath` but not to `string.Join`, so the arr would build a path
beginning with three spaces. And `remote '\\'` **throws**: a UNC root is a
Windows path, the local path is POSIX, and `operator +` refuses to combine two
kinds. An arr that reaches that line fails the import with an unhandled
exception.

## What lintarr does with each answer

| Edge | The arr | `checks/paths.py` `translate` |
| --- | --- | --- |
| Windows remote path, different case | matches | matches (`_as_compared` folds case) |
| Windows remote path, `/` for `\` | matches | matches (`_as_compared` folds separators) |
| POSIX remote path, different case | no match | no match |
| `remotePath` `/` → `/mnt`, path `/data` | `/` | `/` |
| `remotePath` empty or relative | no match | no match |
| `localPath` blank, path `/downloads/tv` | `/tv` | **`''`**, which no caller can place |
| `remotePath` `\\` (UNC root) | throws | `/`, as for any root — **not modelled** |

The last two rows are the places lintarr does not copy the arr, both on purpose:

- **Blank `localPath`.** `RemotePathMappingController` validates `localPath`
  with `IsValidPath()`, `MappedNetworkDriveValidator` and
  `PathExistsValidator`, while `remotePath` gets only `NotEmpty` ("We cannot
  use IsValidPath here, because it's a remote path, possibly other OS"). So a
  blank local path cannot be configured through the API or the UI; it takes a
  hand-edited database. Copying the arr there would have lintarr place a file
  on a mount from a mapping that names no directory — a confident answer built
  on nothing. Measured: with the old code a blank `localPath` turned
  `/downloads/tv` into `/tv`, and `hardlink-futility` **PASSED** the stack
  because `/tv` happened to share a mount with the root folder `/tv/shows`.
  `''` fails the POSIX test instead, and the check reports it SKIP, naming both
  paths.
- **UNC-root `remotePath`.** lintarr treats any separators-only remote path as
  root and returns `/`; the arr throws for the `\\` form. Modelling a thrown
  exception needs a verdict shape `translate` does not have, and the
  configuration is nonsense (a UNC path with no server or share).

## How to re-derive it

```bash
brew install mono                     # a C# compiler and runtime

docs/68-ospath-probe.sh                                  # Sonarr v4.0.20.3014
docs/68-ospath-probe.sh v5.0.0.2997                      # Sonarr v5
docs/68-ospath-probe.sh v6.4.4.10685 Radarr/Radarr       # Radarr
```

The sources the quotes above come from, at the same tags:

```bash
SONARR_V4=v4.0.20.3014
SONARR_V5=v5.0.0.2997
RADARR=v6.4.4.10685

base_sonarr=https://raw.githubusercontent.com/Sonarr/Sonarr
base_radarr=https://raw.githubusercontent.com/Radarr/Radarr

curl -sS "$base_sonarr/$SONARR_V4/src/NzbDrone.Common/Disk/OsPath.cs"
curl -sS "$base_sonarr/$SONARR_V4/src/NzbDrone.Common.Test/OsPathFixture.cs"
curl -sS "$base_sonarr/$SONARR_V4/src/NzbDrone.Core/RemotePathMappings/RemotePathMappingService.cs"
curl -sS "$base_sonarr/$SONARR_V4/src/Sonarr.Api.V3/RemotePathMappings/RemotePathMappingController.cs"
curl -sS "$base_sonarr/$SONARR_V5/src/NzbDrone.Common/Disk/OsPath.cs"
curl -sS "$base_radarr/$RADARR/src/NzbDrone.Common/Disk/OsPath.cs"
```

`v4.0.20.3014` is `Sonarr/Sonarr`'s latest non-prerelease release
(`gh api repos/Sonarr/Sonarr/releases/latest --jq .tag_name`); `v5.0.0.2997` is
its latest prerelease; `v6.4.4.10685` is the Radarr homelab runs.
`RemapRemoteToLocal` is identical on both Sonarr tags apart from one v5 guard
that rejects a `remotePath` starting with a space.

## Pinned by

- `tests/checks/test_paths.py`:
  `test_translate_folds_case_and_separators_for_a_windows_client_path`,
  `test_translate_keeps_a_posix_remote_path_case_sensitive`,
  `test_a_root_remote_mapping_swallows_the_whole_path`,
  `test_a_blank_local_path_translates_to_nothing_rather_than_a_plausible_path`,
  `test_an_unrooted_remote_or_client_path_never_matches`.
- `tests/checks/test_hardlink_futility.py`:
  `test_a_windows_client_path_matches_a_mapping_whose_case_differs`,
  `test_a_root_remote_mapping_leaves_the_download_on_root_not_under_the_local_path`,
  `test_a_mapping_with_a_blank_local_path_is_unplaceable_not_invented`.
