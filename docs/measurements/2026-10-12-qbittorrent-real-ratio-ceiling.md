# How large a seed ratio can qBittorrent ever reach?

**Measured 2026-10-12** against **qBittorrent `release-5.2.4`** and **Sonarr
`v4.0.20.3014`**, by reading the code paths end to end on the release tags.

Issue [#43](https://github.com/tclancy/lintarr/issues/43). That ticket asked one
question and declined to prescribe a fix until it was answered:

> **What is the largest ratio `realRatio()` can return?**

This settles it, and the answer inverts the ticket's hypothesis.

## TL;DR

- **There is no finite ceiling. `realRatio()`'s maximum is `inf`, by an
  explicit named constant** — `Torrent::MAX_RATIO`, which is
  `std::numeric_limits<qreal>::infinity()`. So the ticket's conditional ("if it
  is bounded … the range rule wants an upper bound") resolves to *not bounded*,
  and **`_is_a_seed_goal` does not grow an upper bound.**
- **`1e308` as a `GOAL` is therefore correct**, and so is PR #42's pin of it.
  The ticket read `1e308` as "#14 wearing a finite sign"; it is not — the value
  is reachable by the same single branch that reaches every other value.
- **The real defect is the one the ticket used as its *premise*.** Both
  `_is_a_seed_goal` and PR #42's `gate_can_fire` say a ratio limit of `inf` is
  excluded because "`realRatio()` never reaches it". **It does reach it**, by
  name, and `inf >= inf` is True. The behaviour stays; the stated reason was
  false and is corrected below.
- **`1e308`, `3.4e38` and `inf` are reachability-equivalent**, and that is the
  finding that disposes of any candidate bound: all three are satisfied by, and
  *only* by, the `MAX_RATIO` branch. A bound `B` placed anywhere would file
  `B+1` as unreachable while the one branch that reaches `1e308` reaches `B+1`
  too.
- **No magnitude rule could be right for `seed_time` anyway, and the predicate
  is shared.** `seedingTimeLimit` is compared against `finishedTime() / 60`,
  which grows with the wall clock, and the limit's own type caps it **4085
  years** below where the comparison's type caps — so *every* representable
  seed-time limit is eventually satisfied. §5.
- **Neither side bounds the magnitude on the way in.** Sonarr's validator is
  `GreaterThan(0.0).AsWarning()` and nothing else; `setRatioLimit` floors and
  never ceilings. §4.

## What changed in the code

Nothing in the behaviour. Three docstrings and one test docstring carried the
false "never reaches it" claim, plus PR #42's module docstring carried this as
an open question; all are corrected to the measured reason, and two tests now
pin the huge-finite decision explicitly rather than leaving it to a `st.floats`
draw. See "The decision" below for why *the same behaviour* follows from a
different premise.

## 1. The two branches of `realRatio()`

`src/base/bittorrent/torrentimpl.cpp:1516`:

```cpp
qreal TorrentImpl::realRatio() const
{
    const int64_t upload = m_nativeStatus.all_time_upload;
    // special case for a seeder who lost its stats, also assume nobody will import a 99% done torrent
    const int64_t download = (m_nativeStatus.all_time_download < (m_nativeStatus.total_done * 0.01))
        ? m_nativeStatus.total_done
        : m_nativeStatus.all_time_download;

    if (download == 0)
        return (upload == 0) ? 0 : MAX_RATIO;

    const qreal ratio = upload / static_cast<qreal>(download);
    Q_ASSERT(ratio >= 0);

    return ratio;
}
```

Two branches, and they have different codomains:

- **the finite branch** — `upload / (qreal)download`, both `int64_t`, with
  `download >= 1`. Its supremum is `(double)INT64_MAX`.
- **the `MAX_RATIO` branch** — `download == 0 && upload != 0`.

`src/base/bittorrent/torrent.cpp:47`:

```cpp
const qreal Torrent::MAX_RATIO = std::numeric_limits<qreal>::infinity();
```

That line is the whole answer to #43. The maximum is `inf`.

### The finite branch's supremum, computed

`INT64_MAX` is not representable as a `double`; the division rounds up to
exactly `2^63`:

```
INT64_MAX            = 9223372036854775807
float(INT64_MAX)     = 9.223372036854776e+18
float(INT64_MAX) == 2.0**63   -> True
```

So the finite branch cannot reach `1e308` or `3.4e38`:

```
float(INT64_MAX) >= 1e308    -> False
float(INT64_MAX) >= 3.4e38   -> False
```

IEEE-754 double semantics are identical in C++ `qreal` and in Python `float`,
so the Python above is a faithful check of the C++ comparison rather than an
analogy.

### Is the `MAX_RATIO` branch reachable at all?

`download == 0` requires `total_done == 0` **and** `all_time_download == 0`,
because `0 < 0 * 0.01` is false and the ternary then selects
`all_time_download`. Combined with `upload != 0` and with
`processTorrentShareLimits`' own `isFinished()` precondition, that is a torrent
which is *finished*, has uploaded payload, and has **no wanted data on disk**.

Reachable, and the qBittorrent source treats it as occurring rather than as
dead code: a torrent added as a seed (so `all_time_download` stays `0`), seeded
for a while, then with every file set to "do not download" — `wantedSize()`
becomes 0, so `total_done` becomes 0 and `isFinished()` stays true.
`src/webui/api/torrentscontroller.cpp:787` exists for exactly this state:

```cpp
{KEY_PROP_RATIO, ((ratio >= BitTorrent::Torrent::MAX_RATIO) ? -1 : ratio)},
```

i.e. the WebUI API reports `share_ratio: -1` rather than an infinity, which is
also why an API consumer — Sonarr included — can never observe the `inf`.

**What this measurement does not establish** is how a torrent gets into that
state in practice, or how often. It is a source measurement; the sequence above
is read off the preconditions, not observed on a running client. That gap does
not affect the decision, because the decision turns on the comparison in §3
rather than on the frequency.

## 2. Nothing clamps a large limit on the way in

`torrentimpl.cpp:2635` floors and does not ceiling:

```cpp
void TorrentImpl::setRatioLimit(qreal limit)
{
    if (limit < DEFAULT_RATIO_LIMIT)   // -2
        limit = NO_RATIO_LIMIT;        // -1
    ...
}
```

`src/webui/api/torrentscontroller.cpp:1559` reads it verbatim:

```cpp
const qreal ratioLimit = params()[u"ratioLimit"_s].toDouble();
```

So `1e308` arrives at `m_ratioLimit` intact, and `effectiveRatioLimit()` returns
it unchanged (only `-2` redirects).

## 3. The gate, and why no bound can be right

`src/base/bittorrent/sessionimpl.cpp:2374`:

```cpp
if (const qreal ratio = torrent->realRatio();
        (ratioLimit >= 0) && (ratio >= ratioLimit))
```

With `ratio == inf` from the `MAX_RATIO` branch, the right-hand comparison is
True for **every** `ratioLimit`:

```
inf >= 0.0                     -> True
inf >= 2.0                     -> True
inf >= 9.223372036854776e+18   -> True
inf >= 1e+308                  -> True
inf >= 3.4e+38                 -> True
inf >= inf                     -> True
```

Read the last two lines together: **`1e308` and `inf` have identical
reachability.** Each is satisfied by the `MAX_RATIO` branch and by nothing else.

That is what disposes of an upper bound, and it does so twice over:

1. Any finite `B` would classify `B+1` as unreachable. But the single branch
   that reaches `1e308` reaches `B+1` as well, so `B` would be drawing a line
   where the mechanism draws none. The only non-arbitrary candidate, `2^63`, is
   the supremum of *one branch*, not of the function.
2. Applied consistently, a bound at `2^63` would also have to move `inf` —
   which is on the other side of it — and `inf` is *already* excluded. So the
   bound's only effect would be to make `1e308` agree with `inf` by moving
   `1e308`, when the measurement says the two agree *already* and the
   disagreement is in lintarr.

## 4. Sonarr accepts it, which was #43's second question

`SeedCriteriaSettings.cs` — the field is a `double?`, and the only rule on it is
warning-only and about sign, not magnitude:

```csharp
RuleFor(c => c.SeedRatio).GreaterThan(0.0)
    .When(c => c.SeedRatio.HasValue)
    .AsWarning().WithMessage("Should be greater than zero");
```

`SeedConfigProvider.cs` passes it through verbatim (`Ratio =
seedCriteria.SeedRatio`), and `QBittorrentProxyV2.cs:227` puts the double
straight on the wire:

```csharp
var ratioLimit = seedConfiguration.Ratio.HasValue ? seedConfiguration.Ratio : -2;
if (ratioLimit != -2 || always)
{
    request.AddFormParameter("ratioLimit", ratioLimit);
```

**So yes: `1e308` saves in Sonarr and travels to qBittorrent intact.** `1e309`
does not — it is `Infinity` as a `double` — which is the same boundary the JSON
decode on lintarr's side already crosses.

## 5. The seed-time half has no ceiling at all, and the predicate is shared

`_is_a_seed_goal` is applied to `seed_ratio` **and** `seed_time`
(`_seed_criteria` returns both), so any magnitude rule added to it has to be
right for both. It cannot be.

The seeding-time arm of the same `if`/`else if` chain is:

```cpp
else if (const qlonglong seedingTimeInMinutes = torrent->finishedTime() / 60;
        (seedingTimeLimit >= 0) && (seedingTimeInMinutes >= seedingTimeLimit))
```

`seedingTimeLimit` here is an `int` — `processTorrentShareLimits` declares it
`const int seedingTimeLimit = torrent->effectiveSeedingTimeLimit();` and that
accessor returns `int`, so `int` is the type of the operand actually compared.
(`setShareLimitsAction` reads the parameter as a `qlonglong` and then narrows it
into `setSeedingTimeLimit(int)`; Sonarr's own `SeedTime` is an `int?` of
minutes, so nothing wider ever arrives from the arr side.) `finishedTime() / 60`
is a `qlonglong` that grows with the wall clock. The limit's own type caps it far
below where the comparison's type does:

```
INT32_MAX minutes            = 2147483647          = 4085.8 years
int64 seconds / 60 (minutes) = 153722867280912930  = 292471208678 years
limit cap < comparison cap   -> True
```

So **every representable seed-time limit is eventually satisfied** — there is no
magnitude above which a seed time stops being a goal. A bound derived from the
ratio and applied to the shared predicate would be wrong for half its callers,
and splitting the predicate per criterion would give up the protection this
file buys deliberately: `test_a_zero_seed_time_is_still_a_seed_goal` exists
because a rule can be got wrong one field at a time.

## The decision

`_is_a_seed_goal` keeps its rule: readable type, then finite, then `>= 0`. No
upper bound. Three independent reasons, each sufficient:

1. **There is no finite ceiling to bound against** (§1). The answer to #43's
   question is `inf`.
2. **`1e308` and `inf` are reachability-equivalent** (§3), so a bound cannot
   separate them, and the only line a bound could draw is one the mechanism
   does not.
3. **The predicate is shared with `seed_time`, which has no ceiling** (§5).

### What the measurement *does* change: the reason for excluding `inf`

Excluding `inf` is still right, and now for a stated reason rather than a false
one. The false one, in two places verbatim — "a ratio limit of `inf` can never
be reached by `realRatio()`" — is refuted by §1 and §3.

The reason that survives measurement: the `MAX_RATIO` branch requires a
finished torrent **holding no wanted data**, and a torrent holding no data is
not the homelab#393 wedge. lintarr's flagship check asks whether a *seeding*
torrent will ever release its slot; for every torrent that can wedge a queue,
`download >= 1` and the reachable range is the finite branch's `[0, 2^63]`.
`inf` sits outside it, so excluding `inf` **arms** the check, which is the
direction this project takes under doubt.

That same argument would move `1e308` out of `GOAL` if it were pushed one step
further — and it is deliberately not pushed, because at that point the rule
stops being a measurement and becomes a judgement about which torrents are
"real", which is exactly the kind of invented threshold #14 taught this project
to refuse. The asymmetry is recorded here rather than resolved by invention,
and the two tests added with this document pin it so it cannot drift silently.

## The table

Measured by running both the shipped predicate and PR #42's independent model
over each value. They agree on every row; the right-hand column is the one #43
asked about.

| `seedCriteria.seedRatio` | `_is_a_seed_goal` | model `classify` | reachable by |
|---|---|---|---|
| `2.0` | `True` | `goal` | finite branch |
| `0` | `True` | `goal` | finite branch, immediately |
| `-1`, `-5`, `-0.5` | `False` | `overriding` | nothing — gate needs `>= 0` |
| `-2` | `False` | `deferring` | redirected to the category |
| `9.223372036854776e+18` (`2^63`) | `True` | `goal` | finite branch, at its supremum |
| `1e308` | `True` | `goal` | `MAX_RATIO` branch only |
| `3.4e38` | `True` | `goal` | `MAX_RATIO` branch only |
| `10**400` (a Python `int`) | `True` | `goal` | `MAX_RATIO` branch only |
| `inf` | `False` | `overriding` | `MAX_RATIO` branch only |
| `nan` | `False` | `overriding` | nothing — every comparison is False |

The last two rows are the asymmetry: identical reachability, different
classification. Argued above; pinned in
`tests/invariants/test_queue_liveness.py`.

## Re-deriving this

```bash
# qBittorrent: MAX_RATIO, the two branches, the gate, and the API's -1 display
curl -sL https://raw.githubusercontent.com/qbittorrent/qBittorrent/release-5.2.4/src/base/bittorrent/torrent.cpp | grep -n 'MAX_RATIO'
curl -sL https://raw.githubusercontent.com/qbittorrent/qBittorrent/release-5.2.4/src/base/bittorrent/torrentimpl.cpp | sed -n '1516,1531p;2635,2645p'
curl -sL https://raw.githubusercontent.com/qbittorrent/qBittorrent/release-5.2.4/src/base/bittorrent/sessionimpl.cpp | sed -n '2374,2404p'
curl -sL https://raw.githubusercontent.com/qbittorrent/qBittorrent/release-5.2.4/src/webui/api/torrentscontroller.cpp | sed -n '787p;1557,1562p'
curl -sL https://raw.githubusercontent.com/qbittorrent/qBittorrent/release-5.2.4/src/base/bittorrent/sharelimits.h | grep -n 'RATIO_LIMIT\|SEEDING_TIME_LIMIT'

# Sonarr: the warning-only validator, the pass-through, the form parameter
curl -sL https://raw.githubusercontent.com/Sonarr/Sonarr/v4.0.20.3014/src/NzbDrone.Core/Indexers/SeedCriteriaSettings.cs
curl -sL https://raw.githubusercontent.com/Sonarr/Sonarr/v4.0.20.3014/src/NzbDrone.Core/Indexers/SeedConfigProvider.cs
curl -sL https://raw.githubusercontent.com/Sonarr/Sonarr/v4.0.20.3014/src/NzbDrone.Core/Download/Clients/QBittorrent/QBittorrentProxyV2.cs | sed -n '227,240p'

# The arithmetic in this document
python3 -c 'print(repr(float(2**63 - 1)), float(2**63-1) == 2.0**63, float(2**63-1) >= 1e308)'
python3 -c 'import math; print([math.inf >= v for v in (0.0, 2.0, 2.0**63, 1e308, 3.4e38, math.inf)])'
python3 -c 'print(2**31 - 1 < (2**63 - 1)//60)'
```
