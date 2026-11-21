# lintarr#64: one pinned Sonarr version per verified claim

*2026-11-21T14:37:38Z by Showboat 0.6.1*
<!-- showboat-id: 429a199f-fd52-4f89-9a00-51536d34d3d3 -->

The complaint: `share_limits.py` read Sonarr twice on the same day and cited a
different version each time. The module docstring credited `v5-develop` for the
"what the arr recognises" rule; `_season_pack_arm`, two hundred lines further
down, credited `main` (v4) for the season-pack rule. The #58 PR body and the
design spec said `v5-develop` as well.

Both readings were probably true. That is the problem: a reader could not tell
which Sonarr the check is actually right about, and homelab runs exactly one of
them.

Here is the drift, read off the base commit rather than described.

```bash
git show 061f628:src/lintarr/clients/qbittorrent/share_limits.py |
  grep -n "Sonarr \`\`v5-develop\`\`\|Sonarr \`\`main\`\` (v4)"
```

```output
13:**What the arr recognises**, read from Sonarr ``v5-develop`` (Radarr shares
288:    Verified in Sonarr ``main`` (v4) ``src/NzbDrone.Core/Indexers/SeedConfigProvider.cs``,
295:    Known limit: Sonarr ``v5-develop`` adds a ``SeasonPackSeedGoal`` toggle and a
```

Line 13 and line 288 are the two halves of one reading, each naming a
different Sonarr. Line 295 then uses `v5-develop` a third time, for the one
claim that really is about v5.

The fix picks the version the check runs against — Sonarr `v4.0.20.3014`, the
latest stable release and the build on homelab — and re-reads every claim on
**release tags** rather than moving branches, the way
`docs/measurements/2026-10-05-sonarr-seed-ratio-range.md` already does. After:

```bash
grep -n "Sonarr \`\`v4.0.20.3014\`\`\|Sonarr \`\`v5.0.0.2997\`\`\|Radarr \`\`v6.4.4.10685\`\`" \
  src/lintarr/clients/qbittorrent/share_limits.py \
  src/lintarr/clients/qbittorrent/orphan_inevitability.py
```

```output
src/lintarr/clients/qbittorrent/share_limits.py:13:**What the arr recognises**, read from Sonarr ``v4.0.20.3014`` — the build
src/lintarr/clients/qbittorrent/share_limits.py:24:claims it of. Radarr ``v6.4.4.10685`` and Sonarr ``v5.0.0.2997`` carry
src/lintarr/clients/qbittorrent/share_limits.py:296:    Verified on Sonarr ``v4.0.20.3014`` — the same single version the module
src/lintarr/clients/qbittorrent/share_limits.py:305:    Sonarr ``v5.0.0.2997`` the two season-pack fields sit behind a new
src/lintarr/clients/qbittorrent/orphan_inevitability.py:9:How the arr removes a download (Sonarr ``v4.0.20.3014``, the build homelab
src/lintarr/clients/qbittorrent/orphan_inevitability.py:10:runs; Radarr ``v6.4.4.10685`` carries the same members byte-identical): only
src/lintarr/clients/qbittorrent/orphan_inevitability.py:24:qBittorrent 5's ``stoppedUP`` are not modelled; Sonarr ``v5.0.0.2997``'s
```

One version is only honest if it is *enough*. So the four members the removal
rule rests on were fetched from three pinned release tags and compared
body-for-body: Sonarr `v4.0.20.3014`, Sonarr `v5.0.0.2997` (the newest v5
prerelease) and Radarr `v6.4.4.10685` (homelab's Radarr).

```bash
set -eu
d=$(mktemp -d)
sonarr=https://raw.githubusercontent.com/Sonarr/Sonarr
radarr=https://raw.githubusercontent.com/Radarr/Radarr
qbt=src/NzbDrone.Core/Download/Clients/QBittorrent/QBittorrent.cs

curl -sS -o "$d/sonarr-v4.cs" "$sonarr/v4.0.20.3014/$qbt"
curl -sS -o "$d/sonarr-v5.cs" "$sonarr/v5.0.0.2997/$qbt"
curl -sS -o "$d/radarr-v6.cs" "$radarr/v6.4.4.10685/$qbt"

DIR="$d" python3 -I - <<'PY'
import os

def method(src, name):
    """The full body of `protected bool <name>(...)`, braces balanced."""
    i = src.index(f"protected bool {name}(")
    depth, k = 0, src.index("{", i)
    while True:
        depth += {"{": 1, "}": -1}.get(src[k], 0)
        if depth == 0:
            return src[i:k + 1]
        k += 1

def statement(src, start):
    """One statement, from `start` to its semicolon."""
    i = src.index(start)
    return src[i:src.index(";", i) + 1]

d = os.environ["DIR"]
text = {
    name: open(f"{d}/{slug}.cs", encoding="utf-8").read()
    for name, slug in (
        ("sonarr v4.0.20.3014", "sonarr-v4"),
        ("sonarr v5.0.0.2997", "sonarr-v5"),
        ("radarr v6.4.4.10685", "radarr-v6"),
    )
}

claims = {
    "CanBeRemoved assignment": lambda s: statement(s, "item.CanMoveFiles"),
    "HasReachedSeedLimit": lambda s: method(s, "HasReachedSeedLimit"),
    "HasReachedSeedingTimeLimit": lambda s: method(s, "HasReachedSeedingTimeLimit"),
    "HasReachedInactiveSeedingTimeLimit": lambda s: method(s, "HasReachedInactiveSeedingTimeLimit"),
}

for label, read in claims.items():
    bodies = {k: read(v) for k, v in text.items()}
    base = bodies["sonarr v4.0.20.3014"]
    same = all(b == base for b in bodies.values())
    print(f"{label:36s} {'identical in all three' if same else 'DIFFERS':22s} {len(base):>5} bytes")

print()
print("the v4 rule, quoted:")
print(claims["CanBeRemoved assignment"](text["sonarr v4.0.20.3014"]))
PY
rm -rf "$d"
```

```output
CanBeRemoved assignment              identical in all three   235 bytes
HasReachedSeedLimit                  identical in all three   751 bytes
HasReachedSeedingTimeLimit           identical in all three  2298 bytes
HasReachedInactiveSeedingTimeLimit   identical in all three   733 bytes

the v4 rule, quoted:
item.CanMoveFiles = item.CanBeRemoved =
                    item.DownloadClientInfo.RemoveCompletedDownloads &&
                    torrent.State is "pausedUP" or "stoppedUP" &&
                    HasReachedSeedLimit(torrent, config);
```

So the removal rule needs one citation, not two, and v4 is the honest one to
give: it is what the check runs against.

The season pack is the one reading that genuinely **does** differ by version —
which is what the issue asked to be said out loud.

```bash
set -eu
sonarr=https://raw.githubusercontent.com/Sonarr/Sonarr
provider=src/NzbDrone.Core/Indexers/SeedConfigProvider.cs

echo "### Sonarr v4.0.20.3014 — a full season always takes SeasonPackSeedTime"
curl -sS "$sonarr/v4.0.20.3014/$provider" | grep -n "Ratio = \|var seedTime\|seedTime.HasValue"

echo
echo "### Sonarr v5.0.0.2997 — both fields gated on a toggle, and a new ratio"
curl -sS "$sonarr/v5.0.0.2997/$provider" | grep -n "useSeasonPackSeedGoal\|SeasonPackSeedRatio\|: seedCriteria.SeedRatio"

echo
echo "### and the toggle's default"
curl -sS "$sonarr/v5.0.0.2997/src/NzbDrone.Core/Indexers/SeasonPackSeedGoal.cs" | grep -n "UseStandardSeedGoal\|UseSeasonPackSeedGoal ="
```

```output
### Sonarr v4.0.20.3014 — a full season always takes SeasonPackSeedTime
54:                Ratio = seedCriteria.SeedRatio
57:            var seedTime = fullSeason ? seedCriteria.SeasonPackSeedTime : seedCriteria.SeedTime;
58:            if (seedTime.HasValue)

### Sonarr v5.0.0.2997 — both fields gated on a toggle, and a new ratio
52:            var useSeasonPackSeedGoal = (SeasonPackSeedGoal)seedCriteria.SeasonPackSeedGoal == SeasonPackSeedGoal.UseSeasonPackSeedGoal;
56:                Ratio = (fullSeason && useSeasonPackSeedGoal)
57:                    ? seedCriteria.SeasonPackSeedRatio
58:                    : seedCriteria.SeedRatio
61:            var seedTime = (fullSeason && useSeasonPackSeedGoal) ? seedCriteria.SeasonPackSeedTime : seedCriteria.SeedTime;

### and the toggle's default
8:    UseStandardSeedGoal = 0,
10:    UseSeasonPackSeedGoal = 1
```

Read together: on v4 a full season always takes `SeasonPackSeedTime` and its
ratio is always `SeedRatio`. On v5 both of those are gated on
`useSeasonPackSeedGoal`, and the toggle defaults to `UseStandardSeedGoal = 0`
— so a default-configured v5 season pack uses the ordinary `seedTime` and
`seedRatio`, the *opposite* of v4.

`_season_pack_arm` implements the v4 rule, and lintarr collects no
`seasonPackSeedGoal`, so the v5 behaviour is named as a known limit in three
places rather than guessed at:

```bash
sed -n "/^def _season_pack_arm/,/^    \"\"\"$/p" src/lintarr/clients/qbittorrent/share_limits.py | tail -n +2
```

```output
    """A sonarr season pack's seed time, which is ``seasonPackSeedTime`` alone.

    Verified on Sonarr ``v4.0.20.3014`` — the same single version the module
    docstring names — in ``src/NzbDrone.Core/Indexers/SeedConfigProvider.cs``:
    ``var seedTime = fullSeason ? seedCriteria.SeasonPackSeedTime
    : seedCriteria.SeedTime`` and the limit is set only ``if (seedTime.HasValue)``.
    So a season pack never uses ``seedTime``: unset, the torrent keeps ``-2`` and
    defers to the category and then the global, like any other criterion. Its
    ratio is the ordinary ``seedRatio``, which ``_ratio_arm`` already covers.

    Known limit — **this is where v5 differs, and it is not modelled.** On
    Sonarr ``v5.0.0.2997`` the two season-pack fields sit behind a new
    per-indexer ``SeasonPackSeedGoal`` toggle, and a new ``SeasonPackSeedRatio``
    joins them: ``(fullSeason && useSeasonPackSeedGoal) ? SeasonPackSeedTime
    : SeedTime``, and the same condition on the ratio. The toggle defaults to
    ``UseStandardSeedGoal = 0``, so a default-configured v5 season pack uses the
    ordinary ``seedTime`` and ``seedRatio`` — the opposite of v4. lintarr
    collects no ``seasonPackSeedGoal``, so against a v5 arr this arm can read
    the wrong field, and v5's season-pack ratio is a fourth arm nothing reads.
    Homelab runs v4, so neither gap is live here.
    """
```

The same limit is now named in `orphan_inevitability`'s known limits and in the
`orphan-inevitability` spec, so the three places that describe this check agree
on one version apiece.

Last check: no file under `src/` cites `v5-develop` as the source of anything
any more, and the only surviving mentions anywhere are the two documents that
explain the correction (plus this walkthrough, excluded below).

```bash
echo "src/ mentions of v5-develop:"
grep -rn "v5-develop" src/ || echo "  (none)"

echo
echo "elsewhere, excluding this walkthrough:"
grep -rln "v5-develop" --include="*.md" . |
  sed "s|^\./||" |
  grep -v "^docs/64-"
```

```output
src/ mentions of v5-develop:
  (none)

elsewhere, excluding this walkthrough:
docs/superpowers/specs/2026-11-19-orphan-inevitability-design.md
docs/measurements/2026-11-21-sonarr-removal-rule-versions.md
CONTINUATION.md
```

Nothing executable changed — the diff is docstrings and documents — so the
suite is the regression check, unchanged and still green at the same count as
the base commit (2855 passed, 40 container tests deselected). Timings are
stripped so `showboat verify` does not diff on them.

```bash
uv run --frozen ruff check src tests tools
uv run --frozen ruff format --check src tests tools
uv run --frozen pytest -q 2>&1 | tail -1 | sed "s/ in [0-9.]*s$//"
```

```output
All checks passed!
133 files already formatted
2855 passed, 40 deselected
```

And the repo's own pre-push battery, which is what actually gates a push:

```bash
uv run --frozen pre-commit run --all-files 2>&1 | sed "s/\.\{3,\}/ ... /"
```

```output
trim trailing whitespace ... Passed
fix end of files ... Passed
check yaml ... Passed
check for merge conflicts ... Passed
ruff check ... Passed
ruff format ... Passed
pytest ... Passed
```

## What a reader gets now

| Claim | Version it names | Where |
|---|---|---|
| The arr only recognises the torrent's own limit or an inherited global | Sonarr `v4.0.20.3014` | `share_limits` module docstring |
| A full season takes `seasonPackSeedTime` alone | Sonarr `v4.0.20.3014` | `_season_pack_arm` |
| The arr's removal conditions | Sonarr `v4.0.20.3014` | `orphan_inevitability` module docstring |
| Where v5 diverges: a `SeasonPackSeedGoal` toggle, off by default, and a `SeasonPackSeedRatio` | Sonarr `v5.0.0.2997` | `_season_pack_arm`, `orphan_inevitability`, spec "Known limits" |

The read behind all four, with the C# quoted and the pinned `curl` commands to
re-derive it, is `docs/measurements/2026-11-21-sonarr-removal-rule-versions.md`
— so the next reader can check the citation instead of trusting it.
