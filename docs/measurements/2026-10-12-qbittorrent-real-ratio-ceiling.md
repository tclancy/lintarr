# How large a seed ratio can qBittorrent ever reach?

**Measured 2026-10-12** against **qBittorrent `release-5.2.4`**, **libtorrent
`RC_2_0`** and **Sonarr `v4.0.20.3014`**, by reading the code paths end to end
on those tags.

Issue [#43](https://github.com/tclancy/lintarr/issues/43). That ticket asked one
question and refused to prescribe a fix until it was answered:

> **What is the largest ratio `realRatio()` can return?**

Because a threshold guessed rather than measured is the thing this project calls
its cardinal sin, and [#14](https://github.com/tclancy/lintarr/issues/14) is the
precedent: that ticket guessed `-1` was a sentinel, and the measurement showed
the rule was a *range* rule covering every negative.

## TL;DR

- **There is a measurable ceiling, and `_is_a_seed_goal` now carries it.** For
  any torrent that holds data — the only kind that can wedge a download queue —
  `realRatio()` is `all_time_upload / (qreal)download` with both `int64_t` and
  `download >= 1`, so it cannot exceed `(double)INT64_MAX`, which is exactly
  `2.0**63`. The range rule is now `0 <= value <= MAX_REACHABLE_SEED_RATIO`.
- **#43's hypothesis was right.** `seedCriteria.seedRatio: 1e308` was filed as a
  reachable `GOAL` and disarmed the flagship wedge check byte-identically to a
  real ratio of `2.0`. That is lintarr#14 wearing a large positive sign, and it
  was a live false PASS on `main`.
- **The bound is inclusive, and that is measured too.** `(double)INT64_MAX` is
  *attained*, not approached: `INT64_MAX / 1.0` **is** that double. A `<` would
  file the one attainable extreme as unreachable.
- **The premise everything rested on was false.** Four docstrings and a prior
  measurement document said a ratio limit of `inf` is excluded because
  "`realRatio()` never reaches it". `Torrent::MAX_RATIO` **is**
  `std::numeric_limits<qreal>::infinity()`, `realRatio()` returns it on its
  other branch, and `inf >= inf` is True. All five are corrected; `inf` is still
  an override, now for the same reason `1e308` is.
- **One comparison replaced three lines.** The bound subsumes the `math.isfinite`
  guard *and* that guard's own `isinstance(..., float)` workaround — which
  existed because `math.isfinite` raises `OverflowError` on an `int` too large
  for a `float`, and `json` decodes a 401-digit literal to exactly that. A
  Python `int` compares against a `float` exactly and never raises. `import math`
  is gone from both files.
- **The bound is safe for `seed_time`, which shares the predicate — by
  arithmetic, not by design.** Sonarr's `SeedTime` is an `int?` of minutes, so
  `2**31 - 1` is the most that field can carry — the ratio bound is 4.3e9 times
  larger. Asserted rather than assumed — §5.

## What changed in the code

- `_is_a_seed_goal` gains an upper end: `0 <= fact.value <= MAX_REACHABLE_SEED_RATIO`,
  replacing `>= 0` plus the `math.isfinite` special case.
- `tests/model/share_limit.py`'s `gate_can_fire` gains the same bound,
  re-derived here from the C++ types rather than imported — and that retires
  the one clause its module docstring admitted was borrowed from the
  implementation.
- `1e308`, `3.4e38` and a 401-digit `int` move from `GOAL` to `OVERRIDING` on
  both sides. **This is the assertion PR #42 pinned as an open question and
  predicted would go red when someone measured the ceiling** — it going red is
  the question resolving, not a regression, exactly as that file said.

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

Two branches, with different codomains:

- **the finite branch** — `upload / (qreal)download`, both `int64_t`, with
  `download >= 1`. Supremum `(double)INT64_MAX`.
- **the `MAX_RATIO` branch** — `download == 0 && upload != 0`.
  `src/base/bittorrent/torrent.cpp:47`:

  ```cpp
  const qreal Torrent::MAX_RATIO = std::numeric_limits<qreal>::infinity();
  ```

### The finite branch's supremum, computed

`INT64_MAX` is not representable as a `double`; the division rounds up to
exactly `2^63`:

```
INT64_MAX                     = 9223372036854775807
float(INT64_MAX)              = 9.223372036854776e+18
float(INT64_MAX) == 2.0**63   -> True
float(INT64_MAX) >= 1e308     -> False
float(INT64_MAX) >= 3.4e38    -> False
```

IEEE-754 double semantics are identical in C++ `qreal` and Python `float`, so
the Python above checks the C++ comparison rather than analogising to it.

The second line is why the bound is **inclusive**: `2^63` is the value a torrent
reaches having uploaded `INT64_MAX` bytes against one byte downloaded, so it is
attained. The last two lines are why `1e308` and `3.4e38` are outside it.

### The `MAX_RATIO` branch: when, and whether it happens at all

`download == 0` requires `total_done == 0` **and** `all_time_download == 0`.
Both ternary arms give that: the condition `all_time_download < total_done * 0.01`
is true only when `all_time_download` is below a non-negative quantity, and when
it is false the selected `all_time_download` is itself `0` only if
`total_done * 0.01 <= 0`. Combined with `upload != 0` and with
`processTorrentShareLimits`' `isFinished()` precondition, the state is: a
*finished* torrent that has uploaded payload and whose `total_done` is zero.

**Whether any sequence of operations reaches that state is NOT established
here, and an earlier draft of this document got it wrong.** That draft claimed
you could get there by adding a torrent as a seed, seeding it, then setting
every file to "do not download". libtorrent refutes it — `bytes_done()` in
`src/torrent.cpp:4109-4116`:

```cpp
if (m_seed_mode || is_seed())
{
    // once we're a seed and remove the piece picker, we stop tracking
    // piece- and file priority. We consider everything as being
    // "wanted"
    st.total_done = m_size_on_disk;
```

`m_size_on_disk` is `aux::size_on_disk(files)`, which sums every non-pad file
regardless of priority (`src/file_storage.cpp:1615-1624`) and is computed at
add/metadata time. So a seed's `total_done` is the full payload and cannot be
driven to zero by priorities. The draft had conflated `total_done` with
`total_wanted` / `total_wanted_done`, which qBittorrent keeps distinct in the
same file it reads: `torrentimpl.cpp:466` (`wantedSize() → total_wanted`) versus
`:473` (`completedSize() → total_wanted_done`). `realRatio()` reads
`total_done`, and in the non-seed branch (`torrent.cpp:4131`) that is
`calc_bytes(files, m_picker->have())` — bytes of pieces *held*, filtered ones
included. So `total_done == 0` means **holding no pieces at all**.

What *is* established is that qBittorrent treats the branch as occurring rather
than as dead code: `src/webui/api/torrentscontroller.cpp:787` exists to keep the
value off the wire,

```cpp
{KEY_PROP_RATIO, ((ratio >= BitTorrent::Torrent::MAX_RATIO) ? -1 : ratio)},
```

so an API consumer — Sonarr included — reports `share_ratio: -1` and never
observes the infinity.

**The decision does not depend on settling this**, which is why the question is
left open rather than guessed at. Either way the branch describes a finished
torrent holding no pieces, and such a torrent is not occupying a download slot
— so it is not the homelab#393 wedge this check is about, and the reachable
range over the torrents that *can* wedge is the finite branch's.

## 2. Nothing clamps a large limit on the way in

`torrentimpl.cpp:2635` floors and does not ceiling (comments added here, values
from `sharelimits.h:36-37`):

```cpp
void TorrentImpl::setRatioLimit(qreal limit)
{
    if (limit < DEFAULT_RATIO_LIMIT)   // -2
        limit = NO_RATIO_LIMIT;        // -1
    ...
}
```

`torrentscontroller.cpp:1559` reads it verbatim:

```cpp
const qreal ratioLimit = params()[u"ratioLimit"_s].toDouble();
```

So a huge limit arrives at `m_ratioLimit` unclamped, and
`effectiveRatioLimit()` returns it unchanged — only `-2` redirects.

## 3. The gate, and what the gate needs on its left

`src/base/bittorrent/sessionimpl.cpp:2374` (`processTorrentShareLimits`); the
gate itself is at `:2386-2387`:

```cpp
if (const qreal ratio = torrent->realRatio();
        (ratioLimit >= 0) && (ratio >= ratioLimit))
```

The 10-05 measurement read the left-hand conjunct and gave lintarr its `>= 0`
rule. **The right-hand conjunct is the half #43 is about**, and it is the one
that bounds the limit from above: `reached` needs some *attainable* `ratio`, and
§1 says what is attainable.

For the record, with `ratio == inf` from the `MAX_RATIO` branch the comparison
is True for every limit:

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
lintarr classified them *differently* — `1e308` a `GOAL`, `inf` an override —
and that asymmetry, not the ticket's suspicion, is what the measurement actually
convicts. The bound removes it by moving `1e308` to join `inf`, which is the
arming direction; the alternative, moving `inf` to join `1e308`, would disarm
the check on the strength of a branch §1 cannot even show is reachable.

## 4. Sonarr accepts it, which was #43's second question

`SeedCriteriaSettings.cs` — the field is a `double?` and both rules on it are
warning-only and about sign or a lower bound, never magnitude:

```csharp
RuleFor(c => c.SeedRatio).GreaterThan(0.0)
    .When(c => c.SeedRatio.HasValue)
    .AsWarning().WithMessage("Should be greater than zero");
...
if (seedRatioMinimum != 0.0)
{
    RuleFor(c => c.SeedRatio).GreaterThanOrEqualTo(seedRatioMinimum)
        .When(c => c.SeedRatio > 0.0)
        .AsWarning()
        .WithMessage($"Under {seedRatioMinimum} leads to H&R");
}
```

`SeedConfigProvider.cs` passes it through verbatim (`Ratio = seedCriteria.SeedRatio`),
and `QBittorrentProxyV2.cs:229-234` puts the double straight on the wire:

```csharp
var ratioLimit = seedConfiguration.Ratio.HasValue ? seedConfiguration.Ratio : -2;
if (ratioLimit != -2 || always)
{
    request.AddFormParameter("ratioLimit", ratioLimit);
```

**So yes: `1e308` saves in Sonarr and leaves it unclamped.** `1e309` does not —
it is `inf` as a `double` — the same boundary the JSON decode on lintarr's side
already crosses.

Not read here: how .NET renders that `double?` into the form parameter and
whether the rendering round-trips through `QString::toDouble()`. "Unclamped by
either side's own logic" is what this section establishes; the exact wire bytes
would need a capture, as in
[`2026-10-02-qbittorrent-5.2.4-webui-ban.md`](2026-10-02-qbittorrent-5.2.4-webui-ban.md).
It does not matter for lintarr, which reads the value from Sonarr's API rather
than off the wire to qBittorrent.

## 5. The seed-time half has no ceiling of its own, and the predicate is shared

`_is_a_seed_goal` is applied to `seed_ratio` **and** `seed_time`
(`_seed_criteria` returns both), so a bound measured from the ratio lands on the
time too. The seeding-time arm of the same `if`/`else if` chain is:

```cpp
else if (const qlonglong seedingTimeInMinutes = torrent->finishedTime() / 60;
        (seedingTimeLimit >= 0) && (seedingTimeInMinutes >= seedingTimeLimit))
```

`finishedTime() / 60` grows with the wall clock, so there is no magnitude above
which a seed time stops being satisfiable. Two separate caps make the shared
bound safe anyway, and they are both well below it:

| cap | value | source |
|---|---|---|
| what Sonarr's field can hold | `2**31 - 1` minutes ≈ 4085 years | `SeedTime` is `int?` (`SeedCriteriaSettings.cs`) |
| what qBittorrent compares | `int` | `const int seedingTimeLimit = torrent->effectiveSeedingTimeLimit();`, and that accessor returns `int` |
| the ratio bound | `2**63` ≈ 9.2e18 | §1 |

`2**63 / (2**31 - 1)` is `4294967298.0`, so the ratio bound sits more than
**nine** orders of magnitude above the largest seed time Sonarr can report and
every representable seed-time limit stays a `GOAL`. That is an arithmetic coincidence rather than a design, so
`test_the_largest_representable_seed_time_is_still_a_seed_goal` asserts it at
the real cap: if the ratio bound ever has to drop near `2**31`, that test is
what goes red, and the answer then is to split the predicate per criterion.

(`setShareLimitsAction` reads the parameter as a `qlonglong` and narrows it into
`setSeedingTimeLimit(int)`. A value outside `int` therefore lands below
`DEFAULT_SEEDING_TIME_LIMIT` and becomes `NO_SEEDING_TIME_LIMIT` — *no limit at
all*, i.e. a wedge. That is one more reason `FAIL` is the right answer for an
out-of-range seed time, and it is what
`test_an_unreachable_seed_time_is_not_a_seed_goal_either` records.)

## The decision

`_is_a_seed_goal` grows an upper bound, inclusive, at `(double)INT64_MAX`:

```python
if not _is_a_readable_seed_criterion(fact):
    return False
return 0 <= fact.value <= MAX_REACHABLE_SEED_RATIO
```

Why this is a measurement and not a guess — the distinction #43 insisted on:

1. **The number is read off the C++ types**, not off anyone's sense of what
   ratio is plausible. `int64_t / (qreal)int64_t` with a divisor of at least one
   has exactly one supremum, and it is this.
2. **It is the frontier the mechanism itself draws.** Below it a torrent holding
   data can satisfy the limit; above it only the `MAX_RATIO` branch can, and
   that branch describes a torrent holding no pieces.
3. **Both ends of the rule now come from the same two conjuncts of one `if`.**
   `>= 0` is the left conjunct (10-05); the ceiling is what the right conjunct
   can supply (this document). The rule is no longer half-measured.

And why the direction is the safe one: a limit above the ceiling cannot release
a slot, so treating it as "no goal" **arms** the wedge check. The failure it
closes is a false PASS, which this project ranks worst. The residual risk is the
opposite error — a bound set too low would FAIL a stack that is fine and send an
operator to re-set a goal that was already correct — which is why
`test_the_largest_reachable_seed_ratio_is_still_a_seed_goal` pins the attainable
extreme as a `GOAL`.

### What this does *not* claim

- Not that anyone has seen a `1e308` seed ratio in the wild. Exposure is an
  operator typing an absurd number, an API client, or a corrupted payload; the
  value of the fix is that the verdict is now defensible either way, where
  `test_an_integer_too_large_for_a_float_does_not_crash_the_check` previously
  described its own PASS as "not very defensible".
- Not that the `MAX_RATIO` branch is reachable or unreachable. §1 says why that
  is still open and why the decision does not turn on it.
- Not that `realRatio()`'s *codomain* is bounded. It is not — `inf` is in it by
  a named constant. The bound is on what a **wedgeable** torrent can reach,
  which is the only population lintarr's check is about.

## The table

Measured by running the shipped predicate and PR #42's independent model over
each value, before and after. They agree on every row in both columns.

| `seedCriteria.seedRatio` | before | after | reachable by |
|---|---|---|---|
| `2.0` | `goal` | `goal` | finite branch |
| `0` | `goal` | `goal` | finite branch, immediately |
| `-1`, `-5`, `-0.5` | `overriding` | `overriding` | nothing — gate needs `>= 0` |
| `-2` | `deferring` | `deferring` | redirected to the category |
| `9.223372036854776e+18` (`2^63`) | `goal` | `goal` | finite branch, at its attained maximum |
| `1e308` | **`goal`** | **`overriding`** | `MAX_RATIO` branch only |
| `3.4e38` | **`goal`** | **`overriding`** | `MAX_RATIO` branch only |
| `10**400` (a Python `int`) | **`goal`** | **`overriding`** | `MAX_RATIO` branch only |
| `inf` | `overriding` | `overriding` | `MAX_RATIO` branch only |
| `nan` | `overriding` | `overriding` | nothing — every comparison is False |

The three bold rows are the false PASS #43 found. The `inf` and `nan` rows are
unchanged in verdict and changed in *reason*: both now fail the range rule
rather than a dedicated finiteness guard.

## Where the false premise had spread

"`realRatio()` never reaches `inf`" or a paraphrase, all corrected in the same
change as the bound. Recorded because the count is the point — one sentence in
one measurement document became the stated justification in four places, and a
`grep` for the exact wording would have found only three of them:

| site | wording |
|---|---|
| `docs/measurements/2026-10-05-sonarr-seed-ratio-range.md` | "`realRatio() >= inf` is never true" — the origin; now carries a supersession note |
| `src/lintarr/invariants/queue_liveness.py`, `_is_a_seed_goal` | "can never be reached by `realRatio()`" |
| `src/lintarr/invariants/queue_liveness.py`, `_overrides_the_share_limit` | "`realRatio()` never reaches it" |
| `tests/invariants/test_queue_liveness.py`, `_NO_LIMIT_SEED_VALUES` | "is never reached by `realRatio()`" |
| `tests/invariants/test_share_limit_override.py` | "`realRatio()` never reaches `inf`" |
| `tests/invariants/test_seed_criteria_states.py` | "`realRatio() >= inf` is never true" |

## Re-deriving this

```bash
# qBittorrent: MAX_RATIO, the two branches, the gate, the sentinels, the -1 display
curl -sL https://raw.githubusercontent.com/qbittorrent/qBittorrent/release-5.2.4/src/base/bittorrent/torrent.cpp | grep -n 'MAX_RATIO'
curl -sL https://raw.githubusercontent.com/qbittorrent/qBittorrent/release-5.2.4/src/base/bittorrent/torrentimpl.cpp | sed -n '1516,1531p;2635,2641p'
curl -sL https://raw.githubusercontent.com/qbittorrent/qBittorrent/release-5.2.4/src/base/bittorrent/sessionimpl.cpp | sed -n '2374,2404p'
curl -sL https://raw.githubusercontent.com/qbittorrent/qBittorrent/release-5.2.4/src/webui/api/torrentscontroller.cpp | sed -n '787p;1557,1562p'
curl -sL https://raw.githubusercontent.com/qbittorrent/qBittorrent/release-5.2.4/src/base/bittorrent/sharelimits.h | grep -n 'RATIO_LIMIT\|SEEDING_TIME_LIMIT'

# libtorrent: total_done is NOT total_wanted, and is priority-independent for a seed
curl -sL https://raw.githubusercontent.com/arvidn/libtorrent/RC_2_0/src/torrent.cpp | sed -n '4094,4135p'
curl -sL https://raw.githubusercontent.com/arvidn/libtorrent/RC_2_0/src/file_storage.cpp | sed -n '1615,1624p'

# Sonarr: both warning-only rules, the pass-through, the form parameter
curl -sL https://raw.githubusercontent.com/Sonarr/Sonarr/v4.0.20.3014/src/NzbDrone.Core/Indexers/SeedCriteriaSettings.cs
curl -sL https://raw.githubusercontent.com/Sonarr/Sonarr/v4.0.20.3014/src/NzbDrone.Core/Indexers/SeedConfigProvider.cs
curl -sL https://raw.githubusercontent.com/Sonarr/Sonarr/v4.0.20.3014/src/NzbDrone.Core/Download/Clients/QBittorrent/QBittorrentProxyV2.cs | sed -n '227,240p'

# The arithmetic in this document
python3 -c 'print(repr(float(2**63 - 1)), float(2**63-1) == 2.0**63, float(2**63-1) >= 1e308)'
python3 -c 'import math; print([math.inf >= v for v in (0.0, 2.0, 2.0**63, 1e308, 3.4e38, math.inf)])'
python3 -c 'print((2**63) / (2**31 - 1))'   # 4294967298.0 -- nine orders of magnitude
```
