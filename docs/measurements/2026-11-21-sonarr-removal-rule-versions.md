# Sonarr's removal rule — which version each lintarr claim was read on

**Read 2026-11-21** against **Sonarr `v4.0.20.3014`** (the latest stable
release, and the build homelab runs), **Sonarr `v5.0.0.2997`** (the latest v5
prerelease) and **Radarr `v6.4.4.10685`** (homelab's build). Every line quoted
below was re-read on those release tags, not on a moving branch.

Issue [#64](https://github.com/tclancy/lintarr/issues/64). `share_limits`
cited two different Sonarr versions for one reading — the module docstring said
`v5-develop`, `_season_pack_arm` said `main` (v4) — so neither claim named the
version it was actually true of. This document is the single read both now
cite.

## TL;DR

- **The removal rule is version-independent across v4 and v5.**
  `CanBeRemoved`, `HasReachedSeedLimit`, `HasReachedSeedingTimeLimit` and
  `HasReachedInactiveSeedingTimeLimit` are **byte-identical** on
  `v4.0.20.3014` and `v5.0.0.2997`, and identical again in Radarr
  `v6.4.4.10685`. One citation is enough, and lintarr cites the version it
  runs against: **v4.0.20.3014**.
- **The season-pack rule is not.** v4 always gives a full season
  `SeasonPackSeedTime`. v5 gates both the season-pack time *and* a new
  season-pack ratio behind a per-indexer toggle that is **off by default**, so
  a default-configured v5 season pack uses the ordinary `SeedTime` and
  `SeedRatio`.
- **lintarr models the v4 rule**, because that is what homelab runs. The v5
  divergence is a named known limit, not a modelled case.

## This is a source read, not a wire measurement

Same distinction as
[`2026-10-05-sonarr-seed-ratio-range.md`](2026-10-05-sonarr-seed-ratio-range.md):
this document is a read of open-source code paths at pinned tags, not bytes off
a running container. It is authoritative about mechanism and cannot see a build
difference or a patched package.

## The removal rule, read on `v4.0.20.3014`

`src/NzbDrone.Core/Download/Clients/QBittorrent/QBittorrent.cs`:

```csharp
item.CanMoveFiles = item.CanBeRemoved =
    item.DownloadClientInfo.RemoveCompletedDownloads &&
    torrent.State is "pausedUP" or "stoppedUP" &&
    HasReachedSeedLimit(torrent, config);
```

```csharp
protected bool HasReachedSeedLimit(QBittorrentTorrent torrent, QBittorrentPreferences config)
{
    if (torrent.RatioLimit >= 0)
    {
        if (torrent.RatioLimit - torrent.Ratio <= 0.001f) { return true; }
    }
    else if (torrent.RatioLimit == -2 && config.MaxRatioEnabled)
    {
        if (config.MaxRatio - torrent.Ratio <= 0.001f) { return true; }
    }

    if (HasReachedSeedingTimeLimit(torrent, config) || HasReachedInactiveSeedingTimeLimit(torrent, config))
    {
        return true;
    }

    return false;
}
```

The seeding-time and inactive siblings have the same two-arm shape — the
torrent's own limit when it is `>= 0`, else the global preference when the
torrent's limit is exactly `-2` and the global's `*Enabled` flag is on, else
`false`:

```csharp
if (torrent.SeedingTimeLimit >= 0)            { seedingTimeLimit = torrent.SeedingTimeLimit * 60; }
else if (torrent.SeedingTimeLimit == -2 && config.MaxSeedingTimeEnabled)
                                              { seedingTimeLimit = config.MaxSeedingTime * 60; }
else                                          { return false; }
```

```csharp
if (torrent.InactiveSeedingTimeLimit >= 0)    { inactiveSeedingTimeLimit = torrent.InactiveSeedingTimeLimit * 60; }
else if (torrent.InactiveSeedingTimeLimit == -2 && config.MaxInactiveSeedingTimeEnabled)
                                              { inactiveSeedingTimeLimit = config.MaxInactiveSeedingTime * 60; }
else                                          { return false; }

return DateTimeOffset.UtcNow.ToUnixTimeSeconds() - torrent.LastActivity > inactiveSeedingTimeLimit;
```

**No arm reads a category limit.** That is the whole basis of
`share_limits`'s `_global_covers`: a torrent qBittorrent stopped at a
*category* limit still carries `-2` as its own limit, so the arr checks the
same-arm global instead.

`QBittorrentProxyV2.GetTorrents` adds `category=TvCategory` to
`/api/v2/torrents/info` when the setting is non-blank, which is why a
post-import category hides the torrent from the arr:

```csharp
var request = BuildRequest(settings).Resource("/api/v2/torrents/info");
if (settings.TvCategory.IsNotNullOrWhiteSpace())
{
    request.AddQueryParam("category", settings.TvCategory);
}
```

### Identical on v5, and in Radarr

| Member | `v4.0.20.3014` | `v5.0.0.2997` | Radarr `v6.4.4.10685` |
|---|---|---|---|
| `CanBeRemoved` assignment | — | identical | identical |
| `HasReachedSeedLimit` | — | identical | identical |
| `HasReachedSeedingTimeLimit` | — | identical | identical |
| `HasReachedInactiveSeedingTimeLimit` | — | identical | identical |

Compared body-for-body, whitespace included. The `develop` and `v5-develop`
branches served byte-identical copies of both files when read, with the same
four bodies again — so the earlier `v5-develop` citation was not *false* for
the removal rule, only unpinned, and inconsistent with the other citation in
the same file.

## The season-pack rule, where the versions actually diverge

`src/NzbDrone.Core/Indexers/SeedConfigProvider.cs` on **`v4.0.20.3014`**:

```csharp
var seedConfig = new TorrentSeedConfiguration
{
    Ratio = seedCriteria.SeedRatio
};

var seedTime = fullSeason ? seedCriteria.SeasonPackSeedTime : seedCriteria.SeedTime;
if (seedTime.HasValue)
{
    seedConfig.SeedTime = TimeSpan.FromMinutes(seedTime.Value);
}
```

So on v4: a full season takes `SeasonPackSeedTime` and **never** falls back to
`SeedTime`; unset leaves the torrent at `-2`, deferring to the category then the
global. The ratio is always the ordinary `SeedRatio`.

The same file on **`v5.0.0.2997`**:

```csharp
var useSeasonPackSeedGoal = (SeasonPackSeedGoal)seedCriteria.SeasonPackSeedGoal == SeasonPackSeedGoal.UseSeasonPackSeedGoal;

var seedConfig = new TorrentSeedConfiguration
{
    Ratio = (fullSeason && useSeasonPackSeedGoal)
        ? seedCriteria.SeasonPackSeedRatio
        : seedCriteria.SeedRatio
};

var seedTime = (fullSeason && useSeasonPackSeedGoal) ? seedCriteria.SeasonPackSeedTime : seedCriteria.SeedTime;
```

`SeedCriteriaSettings` gains two fields on v5 (`SeedRatio`, `SeedTime`,
**`SeasonPackSeedGoal`**, **`SeasonPackSeedRatio`**, `SeasonPackSeedTime`),
and the toggle defaults to the *standard* goals:

```csharp
public enum SeasonPackSeedGoal
{
    UseStandardSeedGoal = 0,
    UseSeasonPackSeedGoal = 1
}
```

`SeasonPackSeedGoal` is a plain `int`, so an unset indexer reads `0`.

| | v4 (`v4.0.20.3014`) | v5 (`v5.0.0.2997`) |
|---|---|---|
| Season-pack seeding time | `SeasonPackSeedTime`, always | `SeasonPackSeedTime` only when the toggle is `UseSeasonPackSeedGoal`; otherwise `SeedTime` |
| Season-pack ratio | `SeedRatio`, always | `SeasonPackSeedRatio` when the toggle is on; otherwise `SeedRatio` |
| Toggle default | no toggle exists | `UseStandardSeedGoal` (0) |

### What that means for lintarr, stated rather than modelled

`_season_pack_arm` implements the v4 rule, and homelab runs v4, so the check is
right about the stack it runs against. Against a **v5** arr it can read the
wrong field two ways, and lintarr collects no `seasonPackSeedGoal` to tell
which:

1. **Toggle at its default.** lintarr treats a set `seasonPackSeedTime` as the
   season pack's seeding-time goal; v5 ignores it and uses `seedTime`, so the
   arm lintarr credits to an indexer goal may really defer to the category and
   the global (or the reverse, when `seedTime` is the one that is set).
2. **Toggle on.** v5's season-pack *ratio* is a fourth arm. `_ratio_arm` only
   ever reads `seedRatio`.

Both are recorded as known limits in `share_limits`, `orphan_inevitability`
and the `orphan-inevitability` spec. Neither can be closed by reading source:
it needs a `seasonPackSeedGoal` field on `IndexerFacts`, which is a collection
change, and there is no v5 arr in the stack to read it from.

## How to re-derive this

Every quote above comes from a pinned raw URL; no checkout is needed.

```bash
SONARR_V4=v4.0.20.3014
SONARR_V5=v5.0.0.2997
RADARR=v6.4.4.10685

base_sonarr=https://raw.githubusercontent.com/Sonarr/Sonarr
base_radarr=https://raw.githubusercontent.com/Radarr/Radarr

curl -sS "$base_sonarr/$SONARR_V4/src/NzbDrone.Core/Indexers/SeedConfigProvider.cs"
curl -sS "$base_sonarr/$SONARR_V5/src/NzbDrone.Core/Indexers/SeedConfigProvider.cs"
curl -sS "$base_sonarr/$SONARR_V5/src/NzbDrone.Core/Indexers/SeasonPackSeedGoal.cs"
curl -sS "$base_sonarr/$SONARR_V4/src/NzbDrone.Core/Download/Clients/QBittorrent/QBittorrent.cs"
curl -sS "$base_radarr/$RADARR/src/NzbDrone.Core/Download/Clients/QBittorrent/QBittorrent.cs"
```

`v4.0.20.3014` is `Sonarr/Sonarr`'s latest non-prerelease release
(`gh api repos/Sonarr/Sonarr/releases/latest --jq .tag_name`); `v5.0.0.2997` is
the newest `v5.0.0.*` prerelease tag.
