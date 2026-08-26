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
