"""An executable model of what a seed criterion means to qBittorrent.

Deliberately independent of ``src/``, for the same reason ``queue.py`` is: if it
imported ``queue_liveness`` it would agree with the predicates by construction
and prove nothing. ``queue.py`` is the second opinion on the *slot-count* axis —
"does this many torrents against these limits wedge". This is the second opinion
on the **value** axis: "what does this one number mean, and can it ever release
the slot".

That axis is where lintarr's whole false-PASS family lived. Each of
lintarr#10, #14 and #28 was a seed-criterion value filed under the wrong one of
the four states below, and each was found by the code review on the previous
fix rather than by a test:

* **#10** — a junk value (``"not-a-number"``, ``{"a": 1}``, ``False``) was filed
  as ``GOAL``. It belongs in ``UNREADABLE``.
* **#14** — ``0``, ``-1`` and ``-5`` were filed as ``GOAL``. ``0`` really is a
  goal; the negatives belong in ``DEFERRING`` (``-2``) or ``OVERRIDING``.
* **#28** — every negative was filed as ``DEFERRING``, so the global limit was
  credited with releasing a slot it is never consulted about. Only ``-2``
  defers.

The four states are not an invention of this module. They are the module
docstring of ``queue_liveness`` read back as data, and they come from two
mechanisms in qBittorrent, each modelled below by the function named after it.

Source of every rule here: ``docs/measurements/2026-10-05-sonarr-seed-ratio-range.md``,
measured against Sonarr ``v4.0.20.3014`` and qBittorrent ``release-5.2.4``. The
C++ is quoted in that document; this file is that document executable.

## Where this model is NOT independent, stated rather than implied

The GOAL/DEFERRING/OVERRIDING split is derived from the two quoted C++ hops and
owes nothing to ``queue_liveness``. ``UNREADABLE`` is different: deciding that a
Python ``str`` is not a number is necessarily the same one-line test on both
sides, so for that state this model is a restatement rather than a second
opinion. It is included anyway because the *partition* is what the property
checks — a value misfiled between UNREADABLE and any other state is exactly
lintarr#10 — but a reader should not credit this file with having re-derived it.
"""

import math
from enum import Enum
from typing import Any, Final

#: ``DEFAULT_RATIO_LIMIT`` / ``DEFAULT_SEEDING_TIME_LIMIT`` in
#: ``src/base/bittorrent/sharelimits.h``. The *only* value ``effectiveRatioLimit()``
#: redirects, which is what makes it the only value that defers.
DEFAULT_LIMIT: Final = -2

#: ``NO_RATIO_LIMIT`` in the same header. Named because the measurement doc's
#: readers expect it to be load-bearing and it is not: ``setRatioLimit`` rewrites
#: anything below ``-2`` to exactly this, so ``-1`` is where ``-5`` lands rather
#: than a sentinel the gate treats specially. The gate below never mentions it.
NO_LIMIT: Final = -1


class CriterionState(Enum):
    """What an indexer's seed criterion is, as qBittorrent resolves it.

    Total and mutually exclusive by construction — ``classify`` is a chain of
    early returns — which is the property worth testing. Every one of lintarr's
    three historical false PASSes was a value in the wrong member.
    """

    #: Read as a number the gate can satisfy. The slot is released.
    GOAL = "goal"
    #: ``-2``, absent, or null. ``effectiveRatioLimit()`` hands the question to
    #: ``categoryRatioLimit()``, the only route to ``globalMaxRatio()``, so a
    #: global or category setting still releases the slot.
    DEFERRING = "deferring"
    #: Any other negative, plus ``inf`` and ``nan``. Returned verbatim into a
    #: gate it can never pass, and the redirect never happens — so the global is
    #: never consulted and *cannot* release the slot.
    OVERRIDING = "overriding"
    #: Present, not null, and not a number. Sonarr's field is a ``double?`` /
    #: ``int?``, so no such value can be a limit the operator set; what the
    #: client actually received is unknowable from here.
    UNREADABLE = "unreadable"


def is_a_limit_value(value: Any) -> bool:
    """True when *value* is a number a ``double?`` seed criterion could hold.

    JSON's grammar is the rule, not Python's type lattice: ``true`` is a JSON
    boolean and not a JSON number, so it cannot arrive in a ``double?`` field
    however much ``isinstance(True, int)`` says otherwise. The explicit ``bool``
    exclusion is therefore the model being faithful to the wire, not a copy of
    the implementation's own guard — though see the module docstring: on this
    one state the two are the same line whatever each is derived from.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def redirects_to_the_category(value: float) -> bool:
    """Models ``TorrentImpl::effectiveRatioLimit`` — a single equality.

    .. code-block:: cpp

        if (m_ratioLimit == DEFAULT_RATIO_LIMIT)       // -2, and only -2
            return m_session->categoryRatioLimit(category());
        return m_ratioLimit;                            // -1, -5, -0.5 unchanged

    ``effectiveSeedingTimeLimit()`` redirects ``DEFAULT_SEEDING_TIME_LIMIT`` in
    the identical shape, which is why one model serves both criteria.

    Written as ``== DEFAULT_LIMIT`` rather than ``< 0``, which is the whole of
    lintarr#28: a ``< 0`` spelling reads every negative as deferring and so
    credits the global limit with releasing a slot it is never asked about.
    """
    return value == DEFAULT_LIMIT


def gate_can_fire(value: float) -> bool:
    """Models the ``(limit >= 0)`` half of ``processTorrentShareLimits``.

    .. code-block:: cpp

        const qreal ratioLimit = torrent->effectiveRatioLimit();
        if (const qreal ratio = torrent->realRatio();
                (ratioLimit >= 0) && (ratio >= ratioLimit))
            reached = true;

    Two things the bare comparison gets wrong, both from the measurement doc:

    * ``inf`` passes ``>= 0`` and is still unreachable — ``realRatio()`` never
      reaches it — so finiteness is a separate test rather than a consequence.
    * ``math.isfinite`` raises ``OverflowError`` on a large enough ``int``, and
      ``json`` decodes a 401-digit literal to exactly that. A Python ``int`` is
      always finite, so asking only about ``float`` both answers correctly and
      avoids the crash.

    ``nan`` needs no special case: every comparison against it is False, so it
    fails ``>= 0`` on its own and lands in ``OVERRIDING`` where it belongs.
    """
    if isinstance(value, float) and not math.isfinite(value):
        return False
    return value >= 0


def classify(value: Any, *, was_read: bool = True) -> CriterionState:
    """The state qBittorrent resolves *value* to.

    *was_read* is False for a criterion the arr never reported — Sonarr omits
    the ``value`` key entirely when no goal is set. ``QBittorrentProxyV2.cs``
    maps exactly that case to ``-2``::

        var ratioLimit = seedConfiguration.Ratio.HasValue ? seedConfiguration.Ratio : -2;

    so an unread criterion and a null one are both ``DEFERRING``, and reach it
    through the measured substitution rather than by assertion. Note this is the
    one place the model and lintarr differ in *vocabulary* without differing in
    meaning: ``_lacks_seed_criteria`` calls an unread criterion "no goal", which
    is true of ``DEFERRING`` and ``OVERRIDING`` alike.
    """
    if not was_read or value is None:
        return CriterionState.DEFERRING
    if not is_a_limit_value(value):
        return CriterionState.UNREADABLE
    if redirects_to_the_category(value):
        return CriterionState.DEFERRING
    if gate_can_fire(value):
        return CriterionState.GOAL
    return CriterionState.OVERRIDING
