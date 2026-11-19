"""Properties of ``hardlink-futility`` over generated clients and arrs.

The oracle is deliberately small: an arr is *excused* when its hardlinks are
known off, or when its download clients are known and none could be this
client. A PASS is allowed only when every arr is excused or every fact the
decision needed about it is ``Known``.
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from lintarr.checks.hardlink_futility import CROSS_MOUNT, INVARIANT_ID, PREMISE, check
from lintarr.facts import Known, is_known
from lintarr.models import ArrInstance, ClientInstance
from lintarr.outcomes import Outcome, Premise
from tests.fixtures.clients import as_client
from tests.fixtures.homelab_paths import arr_with, homelab_arr, homelab_qbt
from tests.strategies import ARR_INSTANCES, qbt_instances

CLIENTS = qbt_instances().map(as_client)
ARRS = st.lists(ARR_INSTANCES, max_size=3).map(tuple)

_NEEDED = ("root_folders", "mounts", "remote_path_mappings")


def _hardlinks_off(arr: ArrInstance) -> bool:
    return is_known(arr.copy_using_hardlinks) and arr.copy_using_hardlinks.value is False


def _could_be_this_client(client: ClientInstance, d: object) -> bool:
    save_paths = client.common.save_paths
    if getattr(d, "enabled", None) is not True:
        return False
    if d.implementation.lower() != client.kind:
        return False
    return not is_known(save_paths) or d.category in save_paths.value


def _excused(client: ClientInstance, arr: ArrInstance) -> bool:
    if _hardlinks_off(arr):
        return True
    clients = arr.download_clients
    if not is_known(clients) or not isinstance(clients.value, tuple):
        return False
    return not any(_could_be_this_client(client, d) for d in clients.value)


def _everything_needed_is_known(client: ClientInstance, arr: ArrInstance) -> bool:
    facts = (client.common.save_paths, *(getattr(arr, name) for name in _NEEDED))
    return all(is_known(f) for f in facts)


def _pass_is_allowed(client: ClientInstance, arrs: tuple[ArrInstance, ...]) -> bool:
    return all(_excused(client, a) or _everything_needed_is_known(client, a) for a in arrs)


@settings(max_examples=400)
@given(CLIENTS, ARRS)
def test_the_check_never_raises_and_keeps_its_identity(client, arrs):
    finding = check(client, arrs)
    assert (finding.invariant, finding.conflict) == (INVARIANT_ID, CROSS_MOUNT)
    assert finding.instance == f"qbittorrent[{client.name}]"


@settings(max_examples=400)
@given(CLIENTS, ARRS)
def test_every_fail_carries_exactly_the_one_premise_holding(client, arrs):
    finding = check(client, arrs)
    if finding.outcome is Outcome.FAIL:
        assert finding.premises == (Premise(PREMISE, True),)
        assert finding.detail


@settings(max_examples=400)
@given(CLIENTS, ARRS)
def test_never_pass_while_a_needed_input_was_unreadable(client, arrs):
    finding = check(client, arrs)
    if finding.outcome is Outcome.PASS:
        assert _pass_is_allowed(client, arrs)


# Reachability controls: each property above quantifies over one outcome, and
# would pass vacuously if that outcome never occurred. These hand-built cases
# prove the outcome is reachable and that the oracle rejects the PASS it guards.


def test_control_fail_is_reachable_and_carries_the_premise():
    qbt = homelab_qbt()
    arr = arr_with(mounts=("/", "/data", "/data/media"))
    finding = check(as_client(qbt), (arr,))
    assert finding.outcome is Outcome.FAIL
    assert finding.premises == (Premise(PREMISE, True),)


def test_control_oracle_rejects_pass_over_an_unreadable_mount():
    from lintarr.facts import Unknown

    arr = arr_with(mounts=Unknown("field-absent", "x"))
    assert not _pass_is_allowed(as_client(homelab_qbt()), (arr,))
    assert check(as_client(homelab_qbt()), (arr,)).outcome is Outcome.SKIP


def test_control_oracle_allows_pass_when_hardlinks_are_off():
    from lintarr.facts import Unknown

    arr = arr_with(copy_using_hardlinks=False, mounts=Unknown("field-absent", "x"))
    assert _pass_is_allowed(as_client(homelab_qbt()), (arr,))
    assert check(as_client(homelab_qbt()), (arr,)).outcome is Outcome.PASS


def test_control_homelab_is_a_known_pass():
    arrs = (homelab_arr("sonarr", "tv"), homelab_arr("radarr", "movies"))
    assert all(isinstance(a.mounts, Known) for a in arrs)
    assert check(as_client(homelab_qbt()), arrs).outcome is Outcome.PASS
