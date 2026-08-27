# lintarr P0b — queue-liveness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decide, from configuration alone, whether a qBittorrent queue can ever start another download — and report the specific settings responsible when it cannot.

**Architecture:** Invariants are plain predicates over `StackFacts`, built from labelled premises so the premise set that fired *is* the explanation. No solver. The one temporal argument (`queue-liveness` is a claim about traces) is discharged offline by an exhaustive state-machine sweep in the test suite; the runtime check is the validated closed form.

**Tech Stack:** Python 3.13, click, pytest, Hypothesis, ruff, uv. No new runtime dependencies.

**Spec:** `docs/superpowers/specs/2026-08-26-lintarr-design.md`

**Closes:** #4 (acceptance fixtures), #5 (this phase)

## Global Constraints

- Python `>=3.13`. PEP 695 syntax (`type X = ...`, `class C[T]`) is correct; never rewrite as `TypeVar`/`Generic`.
- Use `uv` for everything. Never `pip`. `uv` is at `/opt/homebrew/bin/uv` if not on PATH.
- ruff `line-length = 100`, `target-version = "py313"`, lint select `["E", "F", "I"]`. Both `uv run ruff check .` and `uv run ruff format --check .` must be clean before every commit.
- **A fact is either read or unknown, never defaulted.** Any invariant whose required fact is `Unknown` reports `SKIP`, never `PASS` and never `FAIL`.
- **Nothing is asserted anonymously.** Every premise carries a unique label naming its origin.
- **No new runtime dependency.** Hypothesis and pytest are dev-only.
- Credentials never appear in logs, findings, exception messages, or CLI output.
- All timestamps UTC.

## Facts about qBittorrent this phase depends on

Recorded here because they are the axioms the whole check rests on, and because P0a shipped a bug from an assumed-but-unchecked API shape.

- Three independent active limits exist: `max_active_downloads`, `max_active_uploads`, `max_active_torrents`. A queue can wedge on any one of them.
- A limit value of `-1` means unlimited — and **only** exactly `-1`. `0` and any
  positive integer bind, and so does any other negative value. Do not write
  `limit < 0` for "unlimited": the model treats only `-1` that way, and the two
  would disagree on every negative-but-not-`-1` value. `0` is legal and
  immediately catastrophic.
- Seeding torrents count toward `max_active_torrents`.
- With no share limit enabled, a completed torrent seeds indefinitely and never releases its slot.
- `dont_count_slow_torrents` exempts torrents by transfer rate, not by state, so it can prevent the wedge.
- Seed criteria live on `/api/v3/indexer` (per indexer), not on the download client.

**Measured against the live homelab instance on 2026-08-26 (qBittorrent 5.2.3):**

- `max_active_uploads` is a real preference and read `3` there. #393 did not record it.
- **Per-category share limits exist and have a three-way encoding.**
  `GET /api/v2/torrents/categories` returns, per category, `ratio_limit`,
  `seeding_time_limit`, `inactive_seeding_time_limit` and `share_limit_action`.
  Observed: `-2` means **use the global limit**, `-1` means **unlimited**, and
  `>= 0` is the category's own limit. Both homelab categories (`tv`, `movies`)
  are `-2`, so they inherit.

**Status of the rest: `assumed`.** The slot-accounting axioms are not
conformance-tested — that is P2. Until then this check rests on documentation
plus one observed production incident. Three assumptions in this project have
already turned out wrong when finally measured (a non-existent `enable` key,
Sonarr omitting `value` rather than sending null, and the entire qBittorrent
login protocol), so treat the untested ones with suspicion.

---

### Task 1: Outcome and Finding types

**Files:**
- Create: `src/lintarr/outcomes.py`
- Create: `tests/test_outcomes.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `class Outcome(StrEnum)` — `PASS`, `FAIL`, `SKIP`, `ERROR`, `NOT_APPLICABLE` (value `"N/A"`)
  - `Premise` — frozen dataclass: `label: str`, `state: bool | None` (`None` means the input was unknown)
  - `Finding` — frozen dataclass: `invariant: str`, `instance: str`, `outcome: Outcome`, `premises: tuple[Premise, ...]`, `detail: str = ""`
  - `RUN_PRECEDENCE: tuple[Outcome, ...]` — `(ERROR, NOT_APPLICABLE, SKIP, FAIL, PASS)`
  - `def worst(outcomes: Iterable[Outcome]) -> Outcome` — the run's outcome; `PASS` for an empty iterable
  - `def exit_code(outcomes: Iterable[Outcome], *, strict: bool = True) -> int` — `1` any FAIL, `2` any ERROR, `3` any SKIP/N-A under strict, else `0`. ERROR outranks FAIL.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_outcomes.py
from lintarr.outcomes import Outcome, Premise, exit_code, worst


def test_outcome_na_renders_as_slash_form():
    assert Outcome.NOT_APPLICABLE == "N/A"


def test_worst_is_empty_pass():
    assert worst([]) is Outcome.PASS


def test_worst_follows_precedence():
    assert worst([Outcome.PASS, Outcome.FAIL]) is Outcome.FAIL
    assert worst([Outcome.FAIL, Outcome.SKIP]) is Outcome.SKIP
    assert worst([Outcome.SKIP, Outcome.ERROR]) is Outcome.ERROR
    assert worst([Outcome.ERROR, Outcome.NOT_APPLICABLE]) is Outcome.ERROR


def test_exit_code_error_outranks_fail():
    assert exit_code([Outcome.FAIL, Outcome.ERROR]) == 2


def test_exit_code_fail_is_one():
    assert exit_code([Outcome.PASS, Outcome.FAIL]) == 1


def test_exit_code_incomplete_is_three_under_strict():
    assert exit_code([Outcome.PASS, Outcome.SKIP]) == 3
    assert exit_code([Outcome.PASS, Outcome.NOT_APPLICABLE]) == 3


def test_exit_code_incomplete_is_zero_without_strict():
    assert exit_code([Outcome.PASS, Outcome.SKIP], strict=False) == 0


def test_all_pass_is_zero():
    assert exit_code([Outcome.PASS, Outcome.PASS]) == 0


def test_premise_state_none_means_unknown():
    assert Premise(label="x", state=None).state is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_outcomes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lintarr.outcomes'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/lintarr/outcomes.py
"""Outcomes, and the arithmetic that collapses many of them into one exit code.

Five outcomes rather than two, because collapsing them produces silent green at
exactly the wrong moment: a run where every check SKIPped because a service was
unreachable must not look like a clean bill of health.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum


class Outcome(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"
    ERROR = "ERROR"
    NOT_APPLICABLE = "N/A"


# Worst-first. A run reports the worst outcome any of its findings reached.
RUN_PRECEDENCE: tuple[Outcome, ...] = (
    Outcome.ERROR,
    Outcome.NOT_APPLICABLE,
    Outcome.SKIP,
    Outcome.FAIL,
    Outcome.PASS,
)


@dataclass(frozen=True, slots=True)
class Premise:
    """One labelled input to an invariant. ``state=None`` means it was unknown."""

    label: str
    state: bool | None


@dataclass(frozen=True, slots=True)
class Finding:
    invariant: str
    instance: str
    outcome: Outcome
    premises: tuple[Premise, ...] = field(default_factory=tuple)
    detail: str = ""


def worst(outcomes: Iterable[Outcome]) -> Outcome:
    """The run's outcome: the worst any finding reached. Empty means PASS."""
    seen = set(outcomes)
    for outcome in RUN_PRECEDENCE:
        if outcome in seen:
            return outcome
    return Outcome.PASS


def exit_code(outcomes: Iterable[Outcome], *, strict: bool = True) -> int:
    """Collapse outcomes to a process exit code.

    CI needs to tell "found a conflict" apart from "could not look", so ERROR
    gets its own code and outranks FAIL: a run that could not read the config
    has not established that the config is wrong.
    """
    seen = set(outcomes)
    if Outcome.ERROR in seen:
        return 2
    if Outcome.FAIL in seen:
        return 1
    if strict and (Outcome.SKIP in seen or Outcome.NOT_APPLICABLE in seen):
        return 3
    return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_outcomes.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/lintarr/outcomes.py tests/test_outcomes.py
git commit -m "feat(outcomes): five-outcome lattice with exit-code arithmetic"
```

---

### Task 2: The premise combinator

**Files:**
- Create: `src/lintarr/invariants/__init__.py`
- Create: `src/lintarr/invariants/combinator.py`
- Create: `tests/invariants/__init__.py`
- Create: `tests/invariants/test_combinator.py`

**Interfaces:**
- Consumes: `Fact`, `is_known` (P0a `facts.py`); `Outcome`, `Premise`, `Finding` (Task 1)
- Produces:
  - `def premise(label: str, value: Fact[bool] | bool | None) -> Premise` — an `Unknown` fact yields `state=None`
  - `def conflict_if(invariant: str, instance: str, *premises: Premise) -> Finding`
  - `DuplicatePremiseLabel(ValueError)`

Semantics, in order:

1. Any premise with `state is None` → `SKIP`, carrying **only the unknown premises** so the report names what could not be read.
2. All premises `True` → `FAIL`, carrying **all** premises: for a conjunction the whole set is the minimal explanation.
3. Otherwise → `PASS`, carrying no premises.

Duplicate labels raise — a label is an identifier in the report, and two premises sharing one makes a finding unreadable.

- [ ] **Step 1: Write the failing test**

```python
# tests/invariants/test_combinator.py
from datetime import UTC, datetime

import pytest

from lintarr.facts import Known, Unknown
from lintarr.invariants.combinator import DuplicatePremiseLabel, conflict_if, premise
from lintarr.outcomes import Outcome


def _known(value: bool) -> Known[bool]:
    return Known(value=value, source="GET /x", read_at=datetime.now(UTC), service_version="v1")


def test_all_premises_true_is_a_conflict():
    f = conflict_if("inv", "qbt[main]", premise("a", True), premise("b", True))
    assert f.outcome is Outcome.FAIL
    assert [p.label for p in f.premises] == ["a", "b"]


def test_one_false_premise_is_a_pass():
    f = conflict_if("inv", "qbt[main]", premise("a", True), premise("b", False))
    assert f.outcome is Outcome.PASS
    assert f.premises == ()


def test_unknown_premise_skips_even_when_others_would_conflict():
    """A missing input must never be read as agreement."""
    f = conflict_if(
        "inv", "qbt[main]", premise("a", True), premise("b", Unknown("field-absent", "b"))
    )
    assert f.outcome is Outcome.SKIP


def test_skip_names_only_the_unknown_premises():
    f = conflict_if(
        "inv",
        "qbt[main]",
        premise("a", True),
        premise("b", Unknown("field-absent", "b")),
        premise("c", Unknown("service-absent", "c")),
    )
    assert [p.label for p in f.premises] == ["b", "c"]


def test_premise_unwraps_a_known_fact():
    assert premise("a", _known(True)).state is True
    assert premise("a", _known(False)).state is False


def test_duplicate_labels_are_rejected():
    with pytest.raises(DuplicatePremiseLabel, match="a"):
        conflict_if("inv", "qbt[main]", premise("a", True), premise("a", False))


def test_a_conflict_carries_every_premise_not_just_the_true_ones():
    """For a conjunction the whole set is the explanation; a partial set would mislead."""
    f = conflict_if("inv", "qbt[main]", premise("a", True), premise("b", True), premise("c", True))
    assert len(f.premises) == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/invariants/test_combinator.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lintarr.invariants'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/lintarr/invariants/__init__.py
"""Invariants: plain predicates over StackFacts, built from labelled premises."""
```

```python
# src/lintarr/invariants/combinator.py
"""Build findings from labelled premises.

The premise set that fired IS the explanation — minimal by construction,
deterministic, and ordered as written. That is why this project needs no
solver: an SMT unsat core would supply the same thing less reliably.
"""

from lintarr.facts import Fact, Known, is_known
from lintarr.outcomes import Finding, Outcome, Premise


class DuplicatePremiseLabel(ValueError):
    """Two premises in one finding shared a label."""


def premise(label: str, value: Fact[bool] | bool | None) -> Premise:
    """Wrap *value* as a labelled premise. An Unknown fact becomes ``state=None``."""
    match value:
        case Known():
            return Premise(label=label, state=bool(value.value))
        case bool() | None:
            return Premise(label=label, state=value)
        case _ if not is_known(value):
            return Premise(label=label, state=None)
        case _:
            raise TypeError(f"premise {label!r} got an unsupported value: {type(value).__name__}")


def conflict_if(invariant: str, instance: str, *premises: Premise) -> Finding:
    """FAIL when every premise holds; SKIP when any input was unknown."""
    labels = [p.label for p in premises]
    duplicates = {label for label in labels if labels.count(label) > 1}
    if duplicates:
        raise DuplicatePremiseLabel(f"duplicate premise labels: {sorted(duplicates)}")

    unknown = tuple(p for p in premises if p.state is None)
    if unknown:
        return Finding(
            invariant=invariant,
            instance=instance,
            outcome=Outcome.SKIP,
            premises=unknown,
            detail="required inputs could not be read",
        )
    if premises and all(p.state for p in premises):
        return Finding(
            invariant=invariant, instance=instance, outcome=Outcome.FAIL, premises=tuple(premises)
        )
    return Finding(invariant=invariant, instance=instance, outcome=Outcome.PASS)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/invariants/test_combinator.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/lintarr/invariants/ tests/invariants/
git commit -m "feat(invariants): premise combinator whose fired set is the explanation"
```

---

### Task 3: Acceptance fixtures from the real incident

**Files:**
- Create: `tests/fixtures/__init__.py`
- Create: `tests/fixtures/homelab.py`
- Create: `tests/fixtures/test_fixtures.py`

**Interfaces:**
- Consumes: `Known`, `Unknown` (P0a); `QbtInstance` (P0a `models.py`)
- Produces:
  - `def wedged_qbt() -> QbtInstance` — the configuration that froze the real stack
  - `def repaired_qbt() -> QbtInstance` — the configuration that fixed it
  - `def qbt_with(**overrides) -> QbtInstance` — builder for one-fact variations

**Closes #4.** These values are transcribed from `tclancy/homelab#393`, which is the only surviving record of the broken state — the live stack has since been repaired. They must not be edited to make a test pass.

Incident date 2026-08-25. Symptom: 52 torrents, 21 incomplete, **0 kB/s**, no error anywhere, for weeks. 29 torrents seeding indefinitely, some 45+ days.

| Setting | Documented | Found (wedged) |
|---|---|---|
| `max_active_downloads` | 6 | **3** |
| `max_active_torrents` | 10 | **5** |
| `dont_count_slow_torrents` | true | **false** |
| `max_ratio_enabled` | true (1.5, then remove) | **false** |
| `max_seeding_time_enabled` | true (14d, then remove) | **false** |

`max_active_uploads` was **not recorded** in #393. Both fixtures use qBittorrent's default of `3`; the docstring says so, because inventing a number and staying quiet about it is the exact failure this project exists to catch.

- [ ] **Step 1: Write the failing test**

```python
# tests/fixtures/test_fixtures.py
"""The fixtures are evidence. These tests stop them drifting into convenience."""

from lintarr.facts import is_known
from tests.fixtures.homelab import qbt_with, repaired_qbt, wedged_qbt


def test_wedged_matches_the_values_recorded_in_homelab_393():
    q = wedged_qbt()
    assert q.max_active_downloads.value == 3
    assert q.max_active_torrents.value == 5
    assert q.dont_count_slow_torrents.value is False
    assert q.max_ratio_enabled.value is False
    assert q.max_seeding_time_enabled.value is False
    assert q.queueing_enabled.value is True


def test_repaired_matches_the_documented_values():
    q = repaired_qbt()
    assert q.max_active_downloads.value == 6
    assert q.max_active_torrents.value == 10
    assert q.dont_count_slow_torrents.value is True
    assert q.max_ratio_enabled.value is True
    assert q.max_ratio.value == 1.5
    assert q.max_seeding_time_enabled.value is True


def test_every_fact_in_both_fixtures_is_known():
    """A fixture with an accidental Unknown would silently turn FAIL into SKIP."""
    for build in (wedged_qbt, repaired_qbt):
        q = build()
        for name in (
            "queueing_enabled",
            "max_active_downloads",
            "max_active_uploads",
            "max_active_torrents",
            "dont_count_slow_torrents",
            "max_ratio_enabled",
            "max_seeding_time_enabled",
        ):
            assert is_known(getattr(q, name)), f"{build.__name__}.{name} is not Known"


def test_qbt_with_overrides_one_fact_and_leaves_the_rest():
    q = qbt_with(max_active_torrents=99)
    assert q.max_active_torrents.value == 99
    assert q.max_active_downloads.value == 6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/fixtures/test_fixtures.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tests.fixtures'`

- [ ] **Step 3: Write minimal implementation**

```python
# tests/fixtures/__init__.py
```

```python
# tests/fixtures/homelab.py
"""Configurations from a real incident, kept as evidence.

Transcribed from tclancy/homelab#393. On 2026-08-25 the whole arr stack sat at
0 kB/s: 52 torrents, 21 of them incomplete, some stalled for weeks, with no
error, no health warning and no alert anywhere. 29 torrents were seeding
indefinitely, several for 45+ days, and every active slot was permanently
occupied by one of them.

The live stack has since been repaired, so #393 is the ONLY surviving record of
the broken configuration. Do not edit these values to make a test pass — change
the code instead, or the project loses the one configuration it knows for
certain produced a silent total stall in production.

`max_active_uploads` was not recorded in #393. Both builders use qBittorrent's
default of 3, stated here rather than passed off as observed.
"""

from datetime import UTC, datetime
from typing import Any

from lintarr.facts import Known
from lintarr.models import QbtInstance

_VERSION = "v5.2.3"
_SOURCE = "GET /api/v2/app/preferences"

_REPAIRED: dict[str, Any] = {
    "queueing_enabled": True,
    "max_active_downloads": 6,
    "max_active_uploads": 3,
    "max_active_torrents": 10,
    "dont_count_slow_torrents": True,
    "max_ratio_enabled": True,
    "max_ratio": 1.5,
    "max_ratio_act": 3,
    "max_seeding_time_enabled": True,
    "max_seeding_time": 20160,
}

# The five drifted values. Everything else matched.
_WEDGED: dict[str, Any] = _REPAIRED | {
    "max_active_downloads": 3,
    "max_active_torrents": 5,
    "dont_count_slow_torrents": False,
    "max_ratio_enabled": False,
    "max_seeding_time_enabled": False,
}


def _known(value: Any) -> Known[Any]:
    return Known(value=value, source=_SOURCE, read_at=datetime.now(UTC), service_version=_VERSION)


def _build(values: dict[str, Any], name: str) -> QbtInstance:
    return QbtInstance(
        name=name,
        version=_VERSION,
        categories=_known({}),
        **{key: _known(value) for key, value in values.items()},
    )


def wedged_qbt(name: str = "main") -> QbtInstance:
    """The configuration that froze the stack on 2026-08-25. Must report FAIL."""
    return _build(_WEDGED, name)


def repaired_qbt(name: str = "main") -> QbtInstance:
    """The documented configuration that unfroze it. Must report PASS."""
    return _build(_REPAIRED, name)


def qbt_with(name: str = "main", **overrides: Any) -> QbtInstance:
    """A repaired instance with individual preferences overridden."""
    return _build(_REPAIRED | overrides, name)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/fixtures/test_fixtures.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/
git commit -m "test(fixtures): preserve homelab#393's wedged configuration as evidence

Closes #4."
```

---

### Task 4: The queue state-machine model

**Files:**
- Create: `tests/model/__init__.py`
- Create: `tests/model/queue.py`
- Create: `tests/model/test_queue_model.py`

**Interfaces:**
- Consumes: nothing (deliberately independent of `src/` — it is a second opinion, not a reuse)
- Produces:
  - `QueueConfig` — frozen dataclass: `queueing_enabled: bool`, `max_active_downloads: int`, `max_active_uploads: int`, `max_active_torrents: int`, `dont_count_slow_torrents: bool`, `share_limit_enabled: bool`
  - `def simulate(cfg: QueueConfig, n_torrents: int) -> bool` — `True` when the queue reaches a state where at least one torrent is still queued and **no further download can ever start**

This is a deliberately naive executable model of qBittorrent's queue, written from the documented behaviour. It exists so the closed-form predicate in Task 5 can be checked against something that actually steps through states.

**What it proves, and what it does not.** It catches errors in *deriving* the closed form. It **cannot** catch a wrong model: a belief held by the author enters both this file and the predicate, and the sweep reports perfect agreement while both are wrong together. That is what P2's container differential test is for. Do not treat a green sweep as fidelity.

- [ ] **Step 1: Write the failing test**

```python
# tests/model/test_queue_model.py
from tests.model.queue import QueueConfig, simulate

WEDGED = QueueConfig(
    queueing_enabled=True,
    max_active_downloads=3,
    max_active_uploads=3,
    max_active_torrents=5,
    dont_count_slow_torrents=False,
    share_limit_enabled=False,
)
REPAIRED = QueueConfig(
    queueing_enabled=True,
    max_active_downloads=6,
    max_active_uploads=3,
    max_active_torrents=10,
    dont_count_slow_torrents=True,
    share_limit_enabled=True,
)


def test_the_real_incident_wedges():
    assert simulate(WEDGED, n_torrents=52) is True


def test_the_repaired_config_does_not_wedge():
    assert simulate(REPAIRED, n_torrents=52) is False


def test_a_share_limit_alone_prevents_the_wedge():
    """Seeders leaving is what frees the slot."""
    import dataclasses

    assert simulate(dataclasses.replace(WEDGED, share_limit_enabled=True), 52) is False


def test_slow_torrent_exemption_alone_prevents_the_wedge():
    import dataclasses

    assert simulate(dataclasses.replace(WEDGED, dont_count_slow_torrents=True), 52) is False


def test_queueing_disabled_never_wedges():
    import dataclasses

    assert simulate(dataclasses.replace(WEDGED, queueing_enabled=False), 52) is False


def test_zero_active_downloads_wedges_immediately():
    import dataclasses

    cfg = dataclasses.replace(REPAIRED, max_active_downloads=0)
    assert simulate(cfg, n_torrents=2) is True


def test_unlimited_sentinel_never_wedges():
    import dataclasses

    cfg = dataclasses.replace(
        WEDGED, max_active_downloads=-1, max_active_uploads=-1, max_active_torrents=-1
    )
    assert simulate(cfg, 52) is False


def test_fewer_torrents_than_slots_never_wedges():
    assert simulate(WEDGED, n_torrents=1) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/model/test_queue_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tests.model'`

- [ ] **Step 3: Write minimal implementation**

```python
# tests/model/__init__.py
```

```python
# tests/model/queue.py
"""A small executable model of qBittorrent's download queue.

Deliberately independent of src/ — it is a second opinion on the same
behaviour, not a reuse of the implementation. If it imported the predicate it
would agree with it by construction and prove nothing.

Scope note: this models the ONE property queue-liveness reasons about — whether
a queued torrent can ever start. It is not a general qBittorrent simulator.
"""

from dataclasses import dataclass

UNLIMITED = -1


@dataclass(frozen=True, slots=True)
class QueueConfig:
    queueing_enabled: bool
    max_active_downloads: int
    max_active_uploads: int
    max_active_torrents: int
    dont_count_slow_torrents: bool
    share_limit_enabled: bool


def _binds(limit: int) -> bool:
    """A limit constrains the queue unless it is the unlimited sentinel."""
    return limit != UNLIMITED


def simulate(cfg: QueueConfig, n_torrents: int) -> bool:
    """Step the queue to a fixpoint. True if it ends wedged.

    Wedged means: at least one torrent is still queued, and no further download
    can ever start no matter how long you wait.
    """
    if not cfg.queueing_enabled:
        return False

    downloading = 0
    seeding = 0
    queued = n_torrents

    def slots_free() -> bool:
        active = downloading + seeding
        if _binds(cfg.max_active_torrents) and active >= cfg.max_active_torrents:
            return False
        if _binds(cfg.max_active_downloads) and downloading >= cfg.max_active_downloads:
            return False
        return True

    # A torrent exempted by the slow-torrent rule stops consuming a slot, so the
    # queue always drains eventually.
    if cfg.dont_count_slow_torrents:
        return False

    while True:
        started = 0
        while queued > 0 and slots_free():
            queued -= 1
            downloading += 1
            started += 1

        if queued == 0:
            return False

        if downloading == 0:
            # Nothing in flight and something still queued: nothing will ever
            # complete to free a slot.
            return True

        # Every in-flight download completes and becomes a seeder.
        completed, downloading = downloading, 0
        if cfg.share_limit_enabled:
            # Seeders eventually hit the limit and leave, releasing their slots.
            pass
        else:
            seeding += completed

        if started == 0 and not slots_free():
            return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/model/test_queue_model.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add tests/model/
git commit -m "test(model): executable queue model, independent of the predicate"
```

---

### Task 5: The `queue-liveness` predicate and its exhaustive sweep

**Files:**
- Create: `src/lintarr/invariants/queue_liveness.py`
- Create: `tests/invariants/test_queue_liveness.py`
- Create: `tests/invariants/test_queue_liveness_sweep.py`

**Interfaces:**
- Consumes: `premise`, `conflict_if` (Task 2); `QbtInstance`, `ArrInstance` (P0a); `Fact`, `is_known` (P0a); fixtures (Task 3); `simulate`, `QueueConfig` (Task 4)
- Produces:
  - `INVARIANT_ID: str = "queue-liveness"`
  - `NEEDS: tuple[str, ...]` — the fact paths this invariant reads
  - `def check(qbt: QbtInstance, arrs: tuple[ArrInstance, ...]) -> Finding`

The premises, in report order:

| Label | Holds when |
|---|---|
| `qbt.queueing_enabled` | queueing is on — with it off there is no queue to wedge |
| `qbt.a_limit_binds` | any of the three active limits is not `-1` |
| `qbt.slow_exempt_off` | `dont_count_slow_torrents` is false |
| `qbt.no_global_ratio` | `max_ratio_enabled` is false |
| `qbt.no_global_seed_time` | `max_seeding_time_enabled` is false |
| `qbt.no_category_limits` | no category sets its own share limit (all inherit or are unlimited) |
| `arr.indexer_without_seed_criteria` | at least one enabled torrent indexer has no seed criteria set |

The last premise is why P0a read `/api/v3/indexer`. Seed criteria are **per indexer**, so a stack wedges if *any one* enabled torrent indexer lacks them — torrents grabbed from it seed forever and fill the slots. Requiring *every* indexer to lack them would miss the mixed case, which is the common one.

- [ ] **Step 1: Write the failing test**

```python
# tests/invariants/test_queue_liveness.py
from datetime import UTC, datetime

from lintarr.facts import Known, Unknown
from lintarr.invariants.queue_liveness import check
from lintarr.models import ArrInstance, IndexerFacts
from lintarr.outcomes import Outcome
from tests.fixtures.homelab import qbt_with, repaired_qbt, wedged_qbt


def _fact(value):
    return Known(value=value, source="GET /x", read_at=datetime.now(UTC), service_version="v1")


def _indexer(*, seed_ratio, protocol="torrent", enabled=True):
    return IndexerFacts(
        name="1337x",
        protocol=_fact(protocol),
        enable_rss=_fact(enabled),
        enable_automatic_search=_fact(enabled),
        enable_interactive_search=_fact(enabled),
        seed_ratio=seed_ratio,
        seed_time=Unknown("field-absent", "seed_time"),
        season_pack_seed_time=Unknown("field-absent", "season_pack_seed_time"),
    )


def _arrs(*indexers):
    return (ArrInstance(name="main", kind="sonarr", version="4.0.0", indexers=indexers),)


NO_GOALS = _arrs(_indexer(seed_ratio=Unknown("field-absent", "seed_ratio")))
WITH_GOALS = _arrs(_indexer(seed_ratio=_fact(2.0)))


def test_the_real_incident_is_reported_as_a_conflict():
    """homelab#393. This is the whole reason the project exists."""
    f = check(wedged_qbt(), NO_GOALS)
    assert f.outcome is Outcome.FAIL


def test_the_repaired_configuration_passes():
    assert check(repaired_qbt(), NO_GOALS).outcome is Outcome.PASS


def test_a_conflict_names_the_settings_responsible():
    labels = {p.label for p in check(wedged_qbt(), NO_GOALS).premises}
    assert "qbt.no_global_ratio" in labels
    assert "qbt.no_global_seed_time" in labels
    assert "qbt.a_limit_binds" in labels


def test_per_indexer_seed_goals_prevent_the_conflict():
    """Global share limits off does not mean seeding is unlimited."""
    assert check(wedged_qbt(), WITH_GOALS).outcome is Outcome.PASS


def test_one_indexer_without_goals_is_enough_to_wedge():
    mixed = _arrs(
        _indexer(seed_ratio=_fact(2.0)),
        _indexer(seed_ratio=Unknown("field-absent", "seed_ratio")),
    )
    assert check(wedged_qbt(), mixed).outcome is Outcome.FAIL


def test_a_usenet_indexer_without_goals_is_irrelevant():
    usenet = _arrs(_indexer(seed_ratio=Unknown("field-absent", "s"), protocol="usenet"))
    assert check(wedged_qbt(), usenet).outcome is Outcome.PASS


def test_a_disabled_indexer_without_goals_is_irrelevant():
    off = _arrs(_indexer(seed_ratio=Unknown("field-absent", "s"), enabled=False))
    assert check(wedged_qbt(), off).outcome is Outcome.PASS


def test_zero_active_downloads_still_conflicts():
    """0 is a legal and immediately catastrophic value."""
    assert check(qbt_with(max_active_downloads=0), NO_GOALS).outcome is Outcome.FAIL


def test_all_limits_unlimited_passes():
    q = qbt_with(max_active_downloads=-1, max_active_uploads=-1, max_active_torrents=-1)
    assert check(q, NO_GOALS).outcome is Outcome.PASS


def test_an_unknown_required_fact_skips_rather_than_passing():
    q = qbt_with()
    object.__setattr__(q, "max_ratio_enabled", Unknown("field-absent", "max_ratio_enabled"))
    f = check(q, NO_GOALS)
    assert f.outcome is Outcome.SKIP
    assert [p.label for p in f.premises] == ["qbt.no_global_ratio"]
```

```python
# tests/invariants/test_queue_liveness_sweep.py
"""Level 1 validation: does the closed form agree with an executable model?

This catches errors in DERIVING the closed form. It cannot catch a wrong model
— a sweep over the predicate's own parameters cannot discover a parameter the
predicate is missing. That is P2's job.

The magnitude range exists to confirm the predicate is INSENSITIVE to
magnitude, since it tests only whether a limit binds. If any premise ever
becomes magnitude-sensitive, this box must widen.
"""

import itertools

from hypothesis import given, settings
from hypothesis import strategies as st

from lintarr.invariants.queue_liveness import check
from lintarr.outcomes import Outcome
from tests.fixtures.homelab import qbt_with
from tests.invariants.test_queue_liveness import NO_GOALS
from tests.model.queue import QueueConfig, simulate

LIMITS = (-1, 0, 1, 2, 3, 4, 5, 6)

# The model answers "does this wedge with N torrents"; the predicate answers
# "can this configuration wedge at all". They are comparable only at an N large
# enough to exceed every limit in LIMITS — below that the model correctly says
# "not wedged" for a config that certainly can wedge, and the sweep would report
# a wall of spurious disagreements that someone would then "fix" by breaking the
# model. Wedging is monotone in N, so one sufficiently large N suffices.
SWEEP_TORRENTS = 100
assert SWEEP_TORRENTS > max(LIMITS), (
    "N must exceed every limit for this comparison to mean anything"
)


def _model(dl: int, ul: int, tot: int, slow: bool, share: bool) -> QueueConfig:
    return QueueConfig(
        queueing_enabled=True,
        max_active_downloads=dl,
        max_active_uploads=ul,
        max_active_torrents=tot,
        dont_count_slow_torrents=slow,
        share_limit_enabled=share,
    )


def _predicate(dl: int, ul: int, tot: int, slow: bool, share: bool) -> bool:
    q = qbt_with(
        max_active_downloads=dl,
        max_active_uploads=ul,
        max_active_torrents=tot,
        dont_count_slow_torrents=slow,
        max_ratio_enabled=share,
        max_seeding_time_enabled=False,
    )
    return check(q, NO_GOALS).outcome is Outcome.FAIL


def test_closed_form_matches_the_model_exhaustively():
    mismatches = []
    for dl, ul, tot, slow, share in itertools.product(
        LIMITS, LIMITS, LIMITS, (False, True), (False, True)
    ):
        predicted = _predicate(dl, ul, tot, slow, share)
        observed = simulate(_model(dl, ul, tot, slow, share), n_torrents=SWEEP_TORRENTS)
        if predicted != observed:
            mismatches.append((dl, ul, tot, slow, share, predicted, observed))
    assert not mismatches, f"{len(mismatches)} disagreements, first: {mismatches[0]}"


@given(
    dl=st.sampled_from(LIMITS),
    ul=st.sampled_from(LIMITS),
    tot=st.sampled_from(LIMITS),
    slow=st.booleans(),
    share=st.booleans(),
    n=st.integers(min_value=max(LIMITS) + 1, max_value=500),
)
@settings(max_examples=300, deadline=None)
def test_closed_form_matches_the_model_on_random_queues(dl, ul, tot, slow, share, n):
    """The sweep's comparison over random large N.

    N starts above max(LIMITS) for the reason recorded at SWEEP_TORRENTS: below
    that the two are answering different questions, not disagreeing.
    """
    assert _predicate(dl, ul, tot, slow, share) == simulate(_model(dl, ul, tot, slow, share), n)


def test_the_model_is_n_dependent_and_the_predicate_is_not():
    """Documents WHY the sweep pins N, so nobody later simplifies it away."""
    wedging = _model(3, 3, 5, slow=False, share=False)
    assert simulate(wedging, n_torrents=5) is False
    assert simulate(wedging, n_torrents=6) is True
    assert _predicate(3, 3, 5, False, False) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/invariants/test_queue_liveness.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lintarr.invariants.queue_liveness'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/lintarr/invariants/queue_liveness.py
"""Can any queued download ever start?

The motivating incident, homelab#393: qBittorrent had max_active_torrents=5
with both share limits disabled. Seeding torrents count against that limit, so
once five torrents completed they held every slot permanently. 52 torrents,
zero active downloads, zero kB/s, no error anywhere, for weeks.

This is a claim about traces, but its reasoning is a monotone quantity with an
absorbing state — completed count only rises, seeders never release slots, and
past a threshold no slot is ever free again — so it collapses to a closed form.
The derivation is validated against an executable model in
tests/invariants/test_queue_liveness_sweep.py.
"""

from lintarr.facts import Fact, is_known
from lintarr.invariants.combinator import conflict_if, premise
from lintarr.models import ArrInstance, IndexerFacts, QbtInstance
from lintarr.outcomes import Finding, Premise

INVARIANT_ID = "queue-liveness"

NEEDS: tuple[str, ...] = (
    "qbt.queueing_enabled",
    "qbt.max_active_downloads",
    "qbt.max_active_uploads",
    "qbt.max_active_torrents",
    "qbt.dont_count_slow_torrents",
    "qbt.max_ratio_enabled",
    "qbt.max_seeding_time_enabled",
    "qbt.categories",
    "arr.indexer_seed_criteria",
)

UNLIMITED = -1


def _not(fact: Fact[bool]) -> Fact[bool] | None:
    """Negate a boolean fact, preserving unknown-ness."""
    if not is_known(fact):
        return None
    return not bool(fact.value)


def _a_limit_binds(qbt: QbtInstance) -> bool | None:
    """True when any of the three active limits is not the unlimited sentinel."""
    limits = (qbt.max_active_downloads, qbt.max_active_uploads, qbt.max_active_torrents)
    if any(not is_known(limit) for limit in limits):
        return None
    return any(limit.value != UNLIMITED for limit in limits)


def _is_relevant(indexer: IndexerFacts) -> bool:
    """Only an enabled torrent indexer can put seeding torrents in the queue."""
    if not is_known(indexer.protocol) or indexer.protocol.value != "torrent":
        return False
    toggles = (indexer.enable_rss, indexer.enable_automatic_search)
    return any(is_known(t) and bool(t.value) for t in toggles)


def _lacks_seed_criteria(indexer: IndexerFacts) -> bool:
    """No usable seed goal — either unreadable, or read and unset."""
    for fact in (indexer.seed_ratio, indexer.seed_time):
        if is_known(fact) and fact.value is not None:
            return False
    return True


def _indexer_without_seed_criteria(arrs: tuple[ArrInstance, ...]) -> bool:
    """Any ONE enabled torrent indexer lacking goals is enough to wedge.

    Torrents grabbed from it seed forever and accumulate in the slots.
    Requiring every indexer to lack them would miss the mixed case.
    """
    return any(
        _lacks_seed_criteria(indexer)
        for arr in arrs
        for indexer in arr.indexers
        if _is_relevant(indexer)
    )


USE_GLOBAL = -2


def _no_category_sets_its_own_limit(qbt: QbtInstance) -> bool | None:
    """True when no category overrides the global share limits.

    Measured on 5.2.3: each category carries ``ratio_limit`` and
    ``seeding_time_limit`` where ``-2`` means inherit the global setting, ``-1``
    means unlimited, and ``>= 0`` is the category's own limit. A category with
    its own limit releases its torrents' slots even when the global limits are
    off, so it breaks the wedge for anything filed under it.
    """
    if not is_known(qbt.categories):
        return None
    categories = qbt.categories.value or {}
    if not isinstance(categories, dict):
        return None
    for category in categories.values():
        if not isinstance(category, dict):
            continue
        for key in ("ratio_limit", "seeding_time_limit"):
            value = category.get(key, USE_GLOBAL)
            if isinstance(value, (int, float)) and value >= 0:
                return False
    return True


def check(qbt: QbtInstance, arrs: tuple[ArrInstance, ...]) -> Finding:
    """FAIL when this configuration can reach a state with no startable download."""
    premises: tuple[Premise, ...] = (
        premise("qbt.queueing_enabled", qbt.queueing_enabled),
        premise("qbt.a_limit_binds", _a_limit_binds(qbt)),
        premise("qbt.slow_exempt_off", _not(qbt.dont_count_slow_torrents)),
        premise("qbt.no_global_ratio", _not(qbt.max_ratio_enabled)),
        premise("qbt.no_global_seed_time", _not(qbt.max_seeding_time_enabled)),
        premise("qbt.no_category_limits", _no_category_sets_its_own_limit(qbt)),
        premise("arr.indexer_without_seed_criteria", _indexer_without_seed_criteria(arrs)),
    )
    return conflict_if(INVARIANT_ID, f"qbittorrent[{qbt.name}]", *premises)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/invariants/ tests/model/ -v`
Expected: PASS. If the sweep reports disagreements, **do not adjust the fixtures or the model to agree** — work out which of the two is wrong and fix that. The model is the second opinion; silencing it defeats the purpose.

- [ ] **Step 5: Commit**

```bash
git add src/lintarr/invariants/queue_liveness.py tests/invariants/test_queue_liveness.py tests/invariants/test_queue_liveness_sweep.py
git commit -m "feat(invariants): queue-liveness, validated against an executable queue model"
```

---

### Task 6: Running invariants over a whole stack

**Files:**
- Create: `src/lintarr/run.py`
- Create: `tests/test_run.py`

**Interfaces:**
- Consumes: `StackFacts` (P0a); `check`, `INVARIANT_ID` (Task 5); `Outcome`, `Finding`, `worst` (Task 1)
- Produces:
  - `def run_checks(facts: StackFacts) -> tuple[Finding, ...]` — one finding **per qBittorrent instance**, plus one `ERROR` finding per entry in `facts.errors`
  - `def run_outcome(findings: Iterable[Finding]) -> Outcome`

Findings are emitted **per instance** so a per-instance verdict is never hidden behind an aggregate. `StackFacts.errors`, populated in P0a when a configured service is unreachable, becomes `ERROR` findings here — that is how a service that could not be read stops the run looking clean.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_run.py
from lintarr.models import StackFacts
from lintarr.outcomes import Outcome
from lintarr.run import run_checks, run_outcome
from tests.fixtures.homelab import repaired_qbt, wedged_qbt
from tests.invariants.test_queue_liveness import NO_GOALS


def test_one_finding_per_qbittorrent_instance():
    facts = StackFacts(qbits=(wedged_qbt("main"), repaired_qbt("vpn")), arrs=NO_GOALS)
    findings = run_checks(facts)
    by_instance = {f.instance: f.outcome for f in findings}
    assert by_instance["qbittorrent[main]"] is Outcome.FAIL
    assert by_instance["qbittorrent[vpn]"] is Outcome.PASS


def test_collect_errors_become_error_findings():
    facts = StackFacts(qbits=(), arrs=(), errors=(("qbittorrent[main]", "banned"),))
    findings = run_checks(facts)
    assert [f.outcome for f in findings] == [Outcome.ERROR]
    assert "banned" in findings[0].detail


def test_an_unreachable_service_does_not_let_the_run_look_clean():
    facts = StackFacts(
        qbits=(repaired_qbt(),), arrs=NO_GOALS, errors=(("sonarr[main]", "unreachable"),)
    )
    assert run_outcome(run_checks(facts)) is Outcome.ERROR


def test_a_wedged_instance_alongside_a_healthy_one_fails_the_run():
    facts = StackFacts(qbits=(repaired_qbt("a"), wedged_qbt("b")), arrs=NO_GOALS)
    assert run_outcome(run_checks(facts)) is Outcome.FAIL


def test_no_qbittorrent_configured_yields_no_findings():
    assert run_checks(StackFacts(qbits=(), arrs=())) == ()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_run.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lintarr.run'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/lintarr/run.py
"""Run every invariant over a collected snapshot.

Findings are per-instance: a stack with one healthy and one wedged download
client must show both, not an aggregate that hides either.
"""

from collections.abc import Iterable

from lintarr.invariants import queue_liveness
from lintarr.models import StackFacts
from lintarr.outcomes import Finding, Outcome, worst


def run_checks(facts: StackFacts) -> tuple[Finding, ...]:
    findings = [queue_liveness.check(qbt, facts.arrs) for qbt in facts.qbits]
    findings += [
        Finding(
            invariant="collect",
            instance=instance,
            outcome=Outcome.ERROR,
            detail=f"could not read this service: {kind}",
        )
        for instance, kind in facts.errors
    ]
    return tuple(findings)


def run_outcome(findings: Iterable[Finding]) -> Outcome:
    return worst(f.outcome for f in findings)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_run.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/lintarr/run.py tests/test_run.py
git commit -m "feat(run): per-instance findings, with collect errors surfaced as ERROR"
```

---

### Task 7: The `check` command

**Files:**
- Modify: `src/lintarr/cli.py`
- Create: `tests/test_check_cli.py`

**Interfaces:**
- Consumes: `run_checks`, `run_outcome` (Task 6); `exit_code` (Task 1); `collect_stack`, `load_config` (P0a)
- Produces: `lintarr check` — `--json`, `--no-strict`; exits per the code table

Human output, one block per finding:

```
FAIL  queue-liveness  [qbittorrent[main]]

  Your settings — read from your stack, check these yourself:
    qbt.queueing_enabled                holds
    qbt.a_limit_binds                   holds
    qbt.slow_exempt_off                 holds
    qbt.no_global_ratio                 holds
    qbt.no_global_seed_time             holds
    arr.indexer_without_seed_criteria   holds

  Therefore: completed torrents hold every active slot and no queued
  download can start.

3 checked: 1 FAIL, 2 PASS
```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_check_cli.py
import json
import os

import httpx
from click.testing import CliRunner

from lintarr.cli import cli

_PREFIXES = ("QBIT_", "SONARR_", "RADARR_", "LINTARR_")
_CLEARED = {k: None for k in os.environ if k.startswith(_PREFIXES)}
ENV = {"QBIT_URL": "http://qbt:8080", "QBIT_USER": "admin", "QBIT_PASS": "pw"}

WEDGED_PREFS = {
    "queueing_enabled": True,
    "max_active_downloads": 3,
    "max_active_uploads": 3,
    "max_active_torrents": 5,
    "dont_count_slow_torrents": False,
    "max_ratio_enabled": False,
    "max_ratio": -1,
    "max_ratio_act": 0,
    "max_seeding_time_enabled": False,
    "max_seeding_time": -1,
}


def _transport(prefs):
    def handle(request: httpx.Request) -> httpx.Response:
        match request.url.path:
            case "/api/v2/auth/login":
                return httpx.Response(200, text="Ok.")
            case "/api/v2/app/version":
                return httpx.Response(200, text="v5.2.3")
            case "/api/v2/app/preferences":
                return httpx.Response(200, json=prefs)
            case "/api/v2/torrents/categories":
                return httpx.Response(200, json={})
        return httpx.Response(404)

    return httpx.MockTransport(handle)


def _run(args, prefs=WEDGED_PREFS):
    return CliRunner().invoke(
        cli, args, env={**_CLEARED, **ENV}, obj={"transport": _transport(prefs)}
    )


def test_wedged_config_exits_one_and_names_the_settings():
    result = _run(["check"])
    assert result.exit_code == 1
    assert "queue-liveness" in result.output
    assert "qbt.no_global_ratio" in result.output


def test_repaired_config_exits_zero():
    repaired = WEDGED_PREFS | {
        "max_active_downloads": 6,
        "max_active_torrents": 10,
        "dont_count_slow_torrents": True,
        "max_ratio_enabled": True,
        "max_ratio": 1.5,
        "max_seeding_time_enabled": True,
    }
    result = _run(["check"], prefs=repaired)
    assert result.exit_code == 0


def test_json_mode_emits_findings_with_premises():
    result = _run(["check", "--json"])
    payload = json.loads(result.output)
    finding = payload["findings"][0]
    assert finding["invariant"] == "queue-liveness"
    assert finding["outcome"] == "FAIL"
    assert any(p["label"] == "qbt.no_global_ratio" for p in finding["premises"])


def test_summary_line_counts_by_outcome():
    assert "1 FAIL" in _run(["check"]).output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_check_cli.py -v`
Expected: FAIL — `No such command 'check'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/lintarr/cli.py`, and add `from lintarr.outcomes import Outcome, exit_code` plus `from lintarr.run import run_checks, run_outcome` to the imports **at the top of the file** (ruff `E402` is enforced):

```python
_THEREFORE = {
    "queue-liveness": (
        "completed torrents hold every active slot and no queued\n  download can start."
    ),
}


def _finding_to_dict(finding) -> dict[str, Any]:
    return {
        "invariant": finding.invariant,
        "instance": finding.instance,
        "outcome": str(finding.outcome),
        "detail": finding.detail,
        "premises": [{"label": p.label, "state": p.state} for p in finding.premises],
    }


def _render_findings(findings) -> str:
    lines: list[str] = []
    for f in findings:
        lines.append(f"{f.outcome:<5} {f.invariant}  [{f.instance}]")
        if f.premises:
            header = (
                "  Your settings — read from your stack, check these yourself:"
                if f.outcome is Outcome.FAIL
                else "  Could not read:"
            )
            lines.append("")
            lines.append(header)
            for p in f.premises:
                state = "holds" if p.state else "unknown" if p.state is None else "does not hold"
                lines.append(f"    {p.label:<36} {state}")
        if f.detail:
            lines.append(f"  {f.detail}")
        therefore = _THEREFORE.get(f.invariant)
        if therefore and f.outcome is Outcome.FAIL:
            lines.append("")
            lines.append(f"  Therefore: {therefore}")
        lines.append("")
    counts: dict[str, int] = {}
    for f in findings:
        counts[str(f.outcome)] = counts.get(str(f.outcome), 0) + 1
    summary = ", ".join(f"{n} {name}" for name, n in sorted(counts.items()))
    lines.append(f"{len(findings)} checked: {summary}" if findings else "nothing to check")
    return "\n".join(lines)


@cli.command("check")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.option(
    "--no-strict",
    "strict",
    flag_value=False,
    default=True,
    help="Do not treat SKIP or N/A as a non-zero exit.",
)
@click.pass_context
def check_command(ctx: click.Context, as_json: bool, strict: bool) -> None:
    """Check whether this stack's settings can coexist."""
    facts = collect_stack(load_config(os.environ), transport=ctx.obj.get("transport"))
    findings = run_checks(facts)
    if as_json:
        click.echo(
            jsonlib.dumps(
                {
                    "schema": 1,
                    "outcome": str(run_outcome(findings)),
                    "findings": [_finding_to_dict(f) for f in findings],
                },
                indent=2,
            )
        )
    else:
        click.echo(_render_findings(findings))
    ctx.exit(exit_code((f.outcome for f in findings), strict=strict))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_check_cli.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/lintarr/cli.py tests/test_check_cli.py
git commit -m "feat(cli): check command with per-outcome exit codes"
```

---

### Task 8: Load-bearing coverage assertion

**Files:**
- Create: `tests/test_needs_are_load_bearing.py`

**Interfaces:**
- Consumes: `NEEDS`, `check` (Task 5); fixtures (Task 3)
- Produces: nothing importable — a CI gate

This replaces the runtime sensitivity sweep an earlier design specified, which could not work: dropping a fact always yields `SKIP`, so every fact looks load-bearing; and on a conjunction with two premises already false, dropping any single one leaves `PASS`, so every premise looks dead. Vacuity is a property of the invariant, not of one user's config, so it belongs in CI and is checked deterministically.

**The rule:** every fact an invariant declares in `NEEDS` must be shown load-bearing by at least one **fixture pair** — two configurations differing only in that fact, producing different verdicts.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_needs_are_load_bearing.py
"""Every declared need must change a verdict somewhere, or the declaration lies.

A `NEEDS` entry nothing depends on is dead weight that makes SKIP fire for no
reason; a fact the predicate reads but does not declare escapes this check
entirely, which is why the pairs below are written by hand rather than derived
from the implementation.
"""

import pytest

from lintarr.invariants.queue_liveness import NEEDS, check
from tests.fixtures.homelab import qbt_with
from tests.invariants.test_queue_liveness import NO_GOALS, WITH_GOALS

# fact -> (overrides that should FAIL, overrides that should PASS)
_PAIRS: dict[str, tuple[dict, dict]] = {
    "qbt.queueing_enabled": ({"queueing_enabled": True}, {"queueing_enabled": False}),
    "qbt.max_active_downloads": ({"max_active_downloads": 3}, {}),
    "qbt.max_active_uploads": ({"max_active_uploads": 3}, {}),
    "qbt.max_active_torrents": ({"max_active_torrents": 5}, {}),
    "qbt.dont_count_slow_torrents": (
        {"dont_count_slow_torrents": False},
        {"dont_count_slow_torrents": True},
    ),
    "qbt.max_ratio_enabled": ({"max_ratio_enabled": False}, {"max_ratio_enabled": True}),
    "qbt.max_seeding_time_enabled": (
        {"max_seeding_time_enabled": False},
        {"max_seeding_time_enabled": True},
    ),
}

_WEDGE = {
    "max_active_downloads": 3,
    "max_active_torrents": 5,
    "dont_count_slow_torrents": False,
    "max_ratio_enabled": False,
    "max_seeding_time_enabled": False,
}


def test_every_declared_need_has_a_pair():
    missing = [n for n in NEEDS if n not in _PAIRS and n != "arr.indexer_seed_criteria"]
    assert not missing, f"NEEDS entries with no load-bearing pair: {missing}"


@pytest.mark.parametrize("need", sorted(_PAIRS))
def test_each_need_changes_the_verdict(need):
    failing, passing = _PAIRS[need]
    a = check(qbt_with(**(_WEDGE | failing)), NO_GOALS).outcome
    b = check(qbt_with(**(_WEDGE | passing)), NO_GOALS).outcome
    if failing == passing:
        pytest.fail(f"{need}: the pair does not differ")
    assert a != b, f"{need} is declared in NEEDS but changes no verdict"


def test_the_arr_need_changes_the_verdict():
    wedged = qbt_with(**_WEDGE)
    assert check(wedged, NO_GOALS).outcome != check(wedged, WITH_GOALS).outcome
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_needs_are_load_bearing.py -v`
Expected: FAIL initially if any `NEEDS` entry has no pair. Add the pair or remove the entry — **do not** loosen the assertion.

- [ ] **Step 3: Reconcile `NEEDS` with reality**

There is no new source file in this task. Either add the missing pair to `_PAIRS`, or delete the unused entry from `NEEDS` in `queue_liveness.py`. A `NEEDS` entry that changes no verdict is a lie that makes `SKIP` fire for no reason.

- [ ] **Step 4: Run the full suite and the linters**

```bash
uv run pytest -v
uv run ruff check .
uv run ruff format --check .
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add tests/test_needs_are_load_bearing.py src/lintarr/invariants/queue_liveness.py
git commit -m "test: assert every declared need is load-bearing

Closes #5."
```

---

## Definition of done for P0b

Two named scenarios, both scripted, both required:

**`scenario/wedged-queue`** — `check` against the configuration recorded in homelab#393 reports `FAIL` for `queue-liveness`, names `qbt.no_global_ratio` and `qbt.no_global_seed_time` among the premises, and exits `1`.

**`scenario/healthy-per-app-goals`** — `check` against a stack whose global share limits are off but whose torrent indexers all set seed criteria reports `PASS` and exits `0`. This is the false-positive case that makes the check trustworthy; without it, `queue-liveness` would flag a large fraction of healthy stacks.

Then, against the real homelab stack once #1 is resolved:

```bash
QBIT_URL=http://192.168.68.67:8080 QBIT_USER=admin QBIT_PASS=... \
SONARR_URL=http://192.168.68.67:8989 SONARR_API_KEY=... \
  uv run lintarr check
```

Expected today: `PASS`, because the stack was repaired on 2026-08-25. A `FAIL` here means either the stack has drifted again — which would be the tool doing its job — or the check has a false positive. Investigate before assuming the former.

## What this phase does NOT establish

Stated plainly so a green suite is not mistaken for a proven check:

- **The model may be wrong.** The sweep proves the closed form was derived correctly from `tests/model/queue.py`. Both encode the same author's beliefs about qBittorrent. A parameter neither knows about is invisible to both. P2's container differential test is the only thing that can catch it.
- **Every axiom is `assumed`.** No conformance test has run against a real qBittorrent. The check rests on documentation plus one observed incident.
- **`0` versus `-1` semantics are unverified** against a live client. The plan treats `-1` as unlimited and `0` as immediately binding; that should be confirmed empirically in P2.
