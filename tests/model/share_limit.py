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

Two clauses, not one. The first draft of this section named only the first and
the review found the second.

1. **``UNREADABLE``.** Deciding that a Python ``str`` is not a number is
   necessarily the same one-line test on both sides, so for that state this
   model is a restatement rather than a second opinion. It is included anyway
   because the *partition* is what the property checks — a value misfiled
   between UNREADABLE and any other state is exactly lintarr#10 — but a reader
   should not credit this file with having re-derived it.
2. **The ``float`` narrowing that used to be inside ``gate_can_fire`` — now
   gone, and recorded because the borrowing is what retired it.** The ``>= 0``
   gate and the ``== -2`` redirect are re-derived from quoted C++ and owe
   nothing to ``queue_liveness``. ``isinstance(value, float)`` was different: a
   Python artifact whose justification was an ``OverflowError`` in
   ``math.isfinite``, appearing nowhere in any measurement document, and
   character-for-character the implementation's own guard. lintarr#43 replaced
   both sides' finiteness test with an **upper bound** read off the C++ types
   (see ``gate_can_fire``), which subsumes it. So this file no longer borrows
   anything, and clause 1 is the only reservation left.

### The question that borrowing left pinned — measured, and the pin flipped

Both sides used to file a **huge but finite** limit — ``1e308``, ``3.4e38`` — as
``GOAL``, and ``st.floats(width=32)`` really draws that range, so the property
*pinned* that agreement. This section called it an open question, on the grounds
that under the 10-05 document's reasoning for ``inf`` ("a limit ``realRatio()``
never reaches") a ratio limit of ``1e308`` is unreachable in the same way, which
would make it ``OVERRIDING`` — lintarr#14 wearing a finite sign. It refused to
prescribe a fix, because the number a fix needs had not been measured.

**lintarr#43 measured it, and the question resolved against the pin.** See
``docs/measurements/2026-10-12-qbittorrent-real-ratio-ceiling.md``. For every
torrent that can wedge a download queue the reachable range is
``[0, (double)INT64_MAX]``, so ``1e308`` is ``OVERRIDING`` after all and
``gate_can_fire`` carries the bound. **This is the assertion this file's
docstring promised would go red once someone did the measurement, and it going
red was the question resolving rather than a regression** — which was the whole
point of recording it instead of leaving the value quietly pinned.

The 10-05 reasoning was also wrong in its own terms, which is worth keeping
because it is what made the question look unanswerable: ``realRatio()`` *does*
reach ``inf``, by the named constant ``Torrent::MAX_RATIO``, and ``inf >= inf``
is True. What saves the ``inf`` exclusion is not unreachability-in-principle but
the bound — the branch returning ``MAX_RATIO`` needs a finished torrent holding
no pieces at all, which is not a torrent that can wedge anything.

## Where the controls for this file live

Not beside it. ``queue.py`` has ``tests/model/test_queue_model.py`` next to it;
this model's controls are in ``tests/invariants/test_seed_criteria_states.py``,
because they are the same tests that compare it against ``queue_liveness`` and
splitting them would put two halves of one argument in two files. Noted here
because someone editing this module would otherwise run ``tests/model/`` and
see nothing.
"""

from enum import Enum
from typing import Any, Final

#: ``DEFAULT_RATIO_LIMIT`` / ``DEFAULT_SEEDING_TIME_LIMIT`` in
#: ``src/base/bittorrent/sharelimits.h``. The *only* value ``effectiveRatioLimit()``
#: redirects, which is what makes it the only value that defers.
DEFAULT_LIMIT: Final = -2

#: ``(double)INT64_MAX``, the supremum of ``realRatio()``'s finite branch
#: (``all_time_upload / (qreal)download``, both ``int64_t``, ``download >= 1``)
#: and therefore the largest ratio limit a torrent holding data can satisfy.
#: Exactly ``2.0**63`` as a double, and attained rather than approached —
#: ``INT64_MAX / 1.0`` *is* that double — so the bound using it is inclusive.
#: From ``docs/measurements/2026-10-12-qbittorrent-real-ratio-ceiling.md``,
#: which reads the C++ types; deliberately not imported from
#: ``queue_liveness``'s constant of the same value, which is the point of this
#: file.
MAX_REACHABLE_RATIO: Final = 2.0**63

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
    #: Any other negative, ``nan``, and anything above ``MAX_REACHABLE_RATIO``
    #: — which includes ``inf``. Returned verbatim into a gate it can never
    #: pass, and the redirect never happens — so the global is never consulted
    #: and *cannot* release the slot.
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
    """Models ``processTorrentShareLimits``' gate **and what can satisfy it**.

    .. code-block:: cpp

        const qreal ratioLimit = torrent->effectiveRatioLimit();
        if (const qreal ratio = torrent->realRatio();
                (ratioLimit >= 0) && (ratio >= ratioLimit))
            reached = true;

    The bare ``>= 0`` is only half the question. ``reached`` also needs an
    attainable ``ratio`` on the left, so the gate has an upper end as well as a
    lower one, and ``MAX_REACHABLE_RATIO`` is where it sits.

    Both ends are re-derived from the C++ rather than from ``queue_liveness``,
    which is what the module docstring's independence section now says — the one
    borrowed clause was a ``math.isfinite`` workaround that this bound replaced:

    * The lower end is the quoted ``ratioLimit >= 0``, from the 10-05
      measurement.
    * The upper end is ``realRatio()``'s own codomain, from the 10-12 one. Its
      finite branch is ``all_time_upload / (qreal)download``, both ``int64_t``
      with ``download >= 1``, so it cannot exceed ``(double)INT64_MAX``. Its
      other branch returns ``Torrent::MAX_RATIO`` — genuinely infinity, which is
      what the 10-05 document had wrong — but needs ``total_done == 0`` with
      ``all_time_upload > 0``: a finished torrent holding no pieces, which is
      not a torrent that can wedge a queue.

    ``nan`` needs no special case: every comparison against it is False, so it
    fails both ends and lands in ``OVERRIDING`` where it belongs. Nor does a
    401-digit ``int``, which ``json`` really does decode from an arr payload: a
    Python ``int`` compares against a ``float`` exactly and never raises, which
    the ``math.isfinite`` spelling could not manage.
    """
    return 0 <= value <= MAX_REACHABLE_RATIO


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
