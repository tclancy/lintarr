"""Which of an arr's download clients is a given client, shared by arr-aware checks.

Moved out of ``hardlink_futility`` so other checks can match arr to client the
same way. Behaviour and strings are unchanged.
"""

from collections.abc import Mapping

from lintarr.facts import Fact, is_known
from lintarr.models import ArrDownloadClient, ArrInstance, ClientInstance

NO_ARR_SENDS_HERE = "no collected arr sends downloads to this client"


def tuple_of[T](fact: Fact[object], kind: type[T]) -> tuple[T, ...] | None:
    """*fact*'s value when it is a readable tuple of *kind*, else ``None``."""
    if not is_known(fact) or not isinstance(fact.value, tuple):
        return None
    if not all(isinstance(item, kind) for item in fact.value):
        return None
    return fact.value


def save_paths_of(instance: ClientInstance) -> Mapping[str, object] | None:
    fact = instance.common.save_paths
    if not is_known(fact) or not isinstance(fact.value, Mapping):
        return None
    return fact.value


def arr_label(arr: ArrInstance) -> str:
    return f"{arr.kind}[{arr.name}]"


def is_this_client(
    d: ArrDownloadClient, instance: ClientInstance, save_paths: Mapping[str, object] | None
) -> bool:
    """Spec step 1. With ``save_paths`` unread, any enabled ``D`` of this kind may be ``C``."""
    if not d.enabled:
        return False
    if d.implementation.lower() != instance.kind:
        return False
    return save_paths is None or d.category in save_paths


def is_well_formed(d: ArrDownloadClient) -> bool:
    """Every field ``is_this_client`` and ``translate`` read has the type they assume."""
    return (
        isinstance(d.enabled, bool)
        and isinstance(d.implementation, str)
        and isinstance(d.category, str)
        and (d.host is None or isinstance(d.host, str))
    )


def clients_that_are(
    arr: ArrInstance, instance: ClientInstance, save_paths: Mapping[str, object] | None
) -> tuple[ArrDownloadClient, ...] | str:
    """The arr's download clients that are this client, or why they can't be told."""
    label = arr_label(arr)
    clients = tuple_of(arr.download_clients, ArrDownloadClient)
    if clients is None:
        return f"{label}: its download clients could not be read"
    malformed = [d.name for d in clients if not is_well_formed(d)]
    if malformed:
        return f"{label}: download client {malformed[0]!r} has fields that could not be read"
    return tuple(d for d in clients if is_this_client(d, instance, save_paths))
