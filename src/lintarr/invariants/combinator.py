"""Build findings from labelled premises.

The premise set that fired IS the explanation — minimal by construction,
deterministic, and ordered as written. That is why this project needs no
solver: an SMT unsat core would supply the same thing less reliably.
"""

from lintarr.facts import Fact, Known, Unknown
from lintarr.outcomes import Finding, Outcome, Premise


class DuplicatePremiseLabel(ValueError):
    """Two premises in one finding shared a label."""


def premise(label: str, value: Fact[bool] | bool | None) -> Premise:
    """Wrap *value* as a labelled premise.

    An Unknown fact becomes ``state=None``, and so does a Known fact whose
    value is ``None``: a null we read and a field we never read both mean
    "cannot decide" at this layer, even though ``facts.py`` keeps them apart
    at the fact layer where that distinction carries meaning.
    """
    match value:
        case Known():
            return Premise(label=label, state=None if value.value is None else bool(value.value))
        case Unknown():
            return Premise(label=label, state=None)
        case bool() | None:
            return Premise(label=label, state=value)
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
