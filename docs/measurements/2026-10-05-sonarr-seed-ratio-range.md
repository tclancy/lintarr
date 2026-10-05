# Sonarr seed criteria — what a negative or zero goal actually does

**Measured 2026-10-05** against **Sonarr `v4.0.20.3014`** and **qBittorrent
`release-5.2.4`**, by reading the code paths end to end. Every line quoted
below was re-read on the release tag, not only on `develop`/`master`.

Issue [#14](https://github.com/tclancy/lintarr/issues/14). This settles the
range question that [#10](https://github.com/tclancy/lintarr/issues/10) /
PR #13 deliberately left open after deciding *type*.

## TL;DR

- **A negative seed goal means "seed forever", and it used to clear the wedge
  check.** `seedCriteria.seedRatio: -1` reaches qBittorrent verbatim, where any
  negative ratio limit is never enforced. That is the homelab#393 wedge
  disarming the check built to find it.
- **Every negative, not just `-1`.** The ticket guessed `-1` was a sentinel and
  filed `-5` as meaningless junk. Measured: qBittorrent gates enforcement on
  `ratioLimit >= 0`, so `-5` and `-0.5` seed forever in exactly the way `-1`
  does. The rule is a range rule, not a sentinel check.
- **`0` is a real goal and stays one.** `ratio >= 0` is satisfied the moment
  the torrent finishes, so the slot is released immediately. The ticket
  guessed this right.
- **`-2` is a third case: "defer to the category/global setting".** Still not an
  *indexer* goal, so the premise must stay armed — but it is not "no limit"
  either, and the distinction matters for the remedy text.
- **A cleared field stores no number, so `0` never arrives from a cleared
  goal** — only from an operator who typed it. `SeedRatio` is a `double?` and
  the validator skips a null entirely. Whether the *API* then reports that as
  `"value": null` or by omitting the key is **not** something source can settle,
  and lintarr reads both as "no goal" regardless — see the open question below.
- **Sonarr does not refuse any of this.** Its own validator calls a
  non-positive ratio `AsWarning()`, which saves.

## This is a source measurement, not a wire measurement

Different evidence from
[`2026-10-02-qbittorrent-5.2.4-webui-ban.md`](2026-10-02-qbittorrent-5.2.4-webui-ban.md),
and worth naming so nobody over-reads it. That document is bytes off the wire
from a running container. This one is a read of two open-source code paths.

What that buys: it is authoritative about *intent and mechanism* in a way one
observation is not — `ratioLimit >= 0` is the rule for all values at once,
whereas a live poke tells you about the values you happened to type.

What it does not buy: it cannot see a build difference, a patched package, or a
libtorrent-level behaviour the C++ above does not describe. Two residual gaps,
both narrow:

1. **Sonarr's UI may clamp before the API does.** The validator is
   warning-only, so the *API* accepts a negative; whether the React form lets
   an operator type one is not read here. It does not matter for lintarr —
   lintarr reads what the API reports, and a value already stored reports
   regardless of how it got there.
2. **The `-2`-means-defer path is read, not exercised.** `effectiveRatioLimit()`
   falls back to `categoryRatioLimit(category())`, and the category/global
   chain beyond that is this file's existing `USE_GLOBAL` territory.

Neither gap can turn a negative back into a goal, which is the only thing the
code change depends on.

## One open question this does NOT settle

Ticket question 1 was "what is stored when the operator clears the field —
`null`, absent, or `0`?" Source answers the part the range rule needed: the
field is a `double?`, so a cleared goal is the absence of a number and is
**never `0`**. `0` only ever arrives because somebody typed it.

It cannot answer the rest. How a null `double?` is rendered into the `fields`
array of `/api/v3/indexer` is a serializer question, and `SeedCriteriaSettings.cs`
says nothing about it. This repo describes **two** states, in
`_lacks_seed_criteria`'s own docstring:

- *never set* → "Sonarr reports 'unset' by omitting the value" (also
  `CONTINUATION.md` P0a lesson 3 and the module docstring, both from a live
  4.0.19 stack: the `value` key is absent, not `null`).
- *set, then cleared* → "a criterion read back as `null` … Sonarr reports a
  cleared criterion that way" (also
  `test_a_null_seed_ratio_is_not_a_seed_goal`).

Read quickly those look like a contradiction, and the first draft of this
document filed them as one. They are not: a settings blob that has never held a
key and one whose key was emptied are plausibly different on the wire, which
would make **both** sentences true. What is actually unmeasured is whether that
distinction is real — nobody has cleared a previously-set ratio on a live
Sonarr and read the field back.

**Nothing is broken either way, which is why this is a note and not a ticket.**
`_lacks_seed_criteria` reads absent and null identically, both as "no goal", and
the suite pins both shapes. The open question is about the prose, and settling
it needs a live stack rather than this chair.

## The path, in four hops

### 1. Sonarr stores it as nullable and warns rather than refuses

`src/NzbDrone.Core/Indexers/SeedCriteriaSettings.cs`:

```csharp
public double? SeedRatio { get; set; }      // nullable: a cleared field is null
public int? SeedTime { get; set; }

RuleFor(c => c.SeedRatio).GreaterThan(0.0)
    .When(c => c.SeedRatio.HasValue)
    .AsWarning().WithMessage("Should be greater than zero");
```

`AsWarning()` is the whole story for question 2 of the ticket. Sonarr's own
position is that a non-positive ratio is wrong — and it *saves it anyway*, as a
yellow warning. There is no sentinel, no clamp, and no documented meaning for
`-1`.

### 2. Sonarr passes the value through untouched

`src/NzbDrone.Core/Indexers/SeedConfigProvider.cs`:

```csharp
var seedConfig = new TorrentSeedConfiguration
{
    Ratio = seedCriteria.SeedRatio      // verbatim. no clamp, no sentinel.
};
```

### 3. The qBittorrent client maps only `null`

`src/NzbDrone.Core/Download/Clients/QBittorrent/QBittorrentProxyV2.cs`:

```csharp
var ratioLimit = seedConfiguration.Ratio.HasValue ? seedConfiguration.Ratio : -2;

if (ratioLimit != -2 || always)
{
    request.AddFormParameter("ratioLimit", ratioLimit);
}
```

Unset becomes `-2`. **Everything else travels as typed** — including a `-2` the
operator typed themselves, which is why `-2` is indistinguishable from unset on
the add path.

### 4. qBittorrent never enforces a negative limit

`src/base/bittorrent/sharelimits.h`:

```cpp
inline const qreal DEFAULT_RATIO_LIMIT = -2;   // "use category/global"
inline const qreal NO_RATIO_LIMIT      = -1;   // "no limit"
```

`src/base/bittorrent/torrentimpl.cpp` — only `-2` is redirected:

```cpp
qreal TorrentImpl::effectiveRatioLimit() const
{
    if (m_ratioLimit == DEFAULT_RATIO_LIMIT)
        return m_session->categoryRatioLimit(category());

    return m_ratioLimit;          // -1, -5, -0.5 all arrive here unchanged
}
```

`src/base/bittorrent/sessionimpl.cpp::processTorrentShareLimits` — the gate:

```cpp
const qreal ratioLimit = torrent->effectiveRatioLimit();

if (const qreal ratio = torrent->realRatio();
        (ratioLimit >= 0) && (ratio >= ratioLimit))
{
    reached = true;   // ... only ever reachable for a non-negative limit
}
```

`ratioLimit >= 0` is the measurement. `-1` and `-5` are the same value to this
code: both fail the test, both mean the torrent is never stopped. The WebUI API
adds no validation of its own — `TorrentsController::setShareLimitsAction` does
`params()[u"ratioLimit"_s].toDouble()` and hands it straight on.

`seedingTimeLimit` is gated by the identical `>= 0` test in the same function,
so `seedCriteria.seedTime` needs the same rule. That is why the code change
applies to both criteria rather than to the ratio alone.

## The table

| `seedCriteria.seedRatio` | Sonarr validator | → qBittorrent `ratioLimit` | enforced? | slot released? | an indexer goal? |
|---|---|---|---|---|---|
| absent | n/a (`.When(HasValue)`) | `-2` | defers to category/global | only if the global says so | **no** |
| `null` (cleared) | skipped | `-2` | defers to category/global | only if the global says so | **no** |
| `-5` | warning | `-5` | `>= 0` fails — never | **never** | **no** |
| `-1` | warning | `-1` | `>= 0` fails — never | **never** | **no** |
| `-0.5` | warning | `-0.5` | `>= 0` fails — never | **never** | **no** |
| `-2` | warning | `-2` | defers to category/global | only if the global says so | **no** |
| `0` | warning | `0` | yes | **immediately** | **yes** |
| `0.5` | accepted | `0.5` | yes | at ratio 0.5 | yes |
| `2.0` | accepted | `2.0` | yes | at ratio 2.0 | yes — the control |

## What changed in the code

`_is_a_seed_goal` was type-only. It is now type **then** range, with the type
half split out as `_is_a_readable_seed_criterion`:

```python
def _is_a_seed_goal(fact):
    return _is_a_readable_seed_criterion(fact) and fact.value >= 0
```

The split is load-bearing, not cosmetic. `_is_an_unusable_seed_criterion` —
which drives the "could not be read" note on the finding — stays wired to the
**type** predicate. Pointed at `_is_a_seed_goal` instead, a `-1` would be
reported as a value nobody could read, sending an operator to re-examine a
field lintarr understood perfectly. Both wirings arm the check, so only
`test_a_negative_seed_ratio_is_not_reported_as_unreadable` can tell them apart;
it was confirmed to be the single failure under that mutation.

## Re-deriving this

```bash
# Sonarr: nullable + warning-only validator, and the verbatim pass-through
curl -sL https://raw.githubusercontent.com/Sonarr/Sonarr/v4.0.20.3014/src/NzbDrone.Core/Indexers/SeedCriteriaSettings.cs
curl -sL https://raw.githubusercontent.com/Sonarr/Sonarr/v4.0.20.3014/src/NzbDrone.Core/Indexers/SeedConfigProvider.cs
curl -sL https://raw.githubusercontent.com/Sonarr/Sonarr/v4.0.20.3014/src/NzbDrone.Core/Download/Clients/QBittorrent/QBittorrentProxyV2.cs

# qBittorrent: the sentinels, the -2 redirect, and the >= 0 gate
curl -sL https://raw.githubusercontent.com/qbittorrent/qBittorrent/release-5.2.4/src/base/bittorrent/sharelimits.h
curl -sL https://raw.githubusercontent.com/qbittorrent/qBittorrent/release-5.2.4/src/base/bittorrent/torrentimpl.cpp
curl -sL https://raw.githubusercontent.com/qbittorrent/qBittorrent/release-5.2.4/src/base/bittorrent/sessionimpl.cpp
```

`develop` (`cab419ade8ac`) and `master` (`4df3aff93799`) were read first and
agree with the release tags on every line above. The only shape difference is
that qBittorrent `master` has since folded the three limits into a
`ShareLimits` struct and added an early return when all three are `< 0`; the
`>= 0` gate per limit is unchanged, so the conclusion is version-stable across
both.
