from pathlib import PurePosixPath

from hypothesis import given
from hypothesis import strategies as st

from lintarr.checks.paths import mount_of, posix_abs, posix_abs_or_reason, translate
from lintarr.models import RemotePathMapping

P = PurePosixPath


def _m(host, remote, local):
    return RemotePathMapping(host=host, remote_path=remote, local_path=local)


def test_trailing_slash_and_leading_double_slash_normalise():
    assert posix_abs("/data/") == P("/data")
    assert posix_abs("//data/x/") == P("/data/x")
    assert posix_abs("///data") == P("/data")
    assert posix_abs("/") == P("/")


def test_windows_relative_empty_and_non_string_are_none():
    for raw in ("C:\\Downloads", "/data\\x", "data/x", "", None, 5, b"/data", ["/a"]):
        assert posix_abs(raw) is None


def test_a_parent_segment_makes_a_path_unplaceable():
    """``..`` is rejected, not resolved: comparison is lexical, so
    ``/downloads/../media/x`` would otherwise read as sitting under ``/downloads``."""
    assert posix_abs("/downloads/../media/x") is None
    assert posix_abs("/data/..") is None
    assert posix_abs("/data/../") is None
    assert posix_abs("/..") is None


def test_a_component_that_merely_starts_with_dots_is_a_real_directory():
    assert posix_abs("/data/..hidden/x") == P("/data/..hidden/x")
    assert posix_abs("/data/.../x") == P("/data/.../x")
    assert posix_abs("/data/a..b") == P("/data/a..b")


def test_the_reason_names_the_parent_segment_and_passes_a_good_path_through():
    assert posix_abs_or_reason("C:\\Downloads") == "is not absolute POSIX"
    assert posix_abs_or_reason(None) == "is not absolute POSIX"
    assert posix_abs_or_reason("/downloads/../media") == (
        "contains a .. segment, so the mount it is on cannot be read"
    )
    assert posix_abs_or_reason("/downloads/media/") == P("/downloads/media")


def test_a_mount_with_a_parent_segment_is_not_a_mount():
    assert mount_of(P("/data/media/x"), ("/data/../data/media",)) is None
    assert mount_of(P("/data/media/x"), ("/", "/data/../data/media")) == P("/")


def test_mount_is_longest_containing_prefix():
    mounts = ("/", "/data", "/data/media", "/config")
    assert mount_of(P("/data/media/tv"), mounts) == P("/data/media")
    assert mount_of(P("/data/torrents"), mounts) == P("/data")
    assert mount_of(P("/other"), mounts) == P("/")
    assert mount_of(P("/other"), ("/data", "bad\\", "")) is None
    assert mount_of(P("/data/x/"), ("/data/",)) == P("/data")


def test_mount_respects_components():
    assert mount_of(P("/data2/x"), ("/", "/data")) == P("/")
    assert mount_of(P("/data2/x"), ("/data",)) is None


def test_translate_takes_the_first_match_not_the_longest():
    ms = [_m("h", "/dl", "/a"), _m("h", "/dl/tv", "/b")]
    assert translate("/dl/tv/x", ms, "h") == "/a/tv/x"


def test_translate_ignores_other_hosts_and_is_case_insensitive_on_host():
    ms = [_m("other", "/dl", "/a"), _m("QBIT.local", "/dl", "/b")]
    assert translate("/dl/x", ms, "qbit.LOCAL") == "/b/x"
    assert translate("/dl/x", ms, "nope") == "/dl/x"
    assert translate("/dl/x", ms, None) == "/dl/x"


def test_translate_respects_components():
    ms = [_m("h", "/dl", "/a")]
    assert translate("/dlx/y", ms, "h") == "/dlx/y"
    assert translate("/dl", ms, "h") == "/a"
    assert translate("/dl/", ms, "h") == "/a"


def test_translate_maps_a_windows_client_path_to_posix():
    ms = [_m("h", "C:\\Downloads\\", "/data/torrents/")]
    assert translate("C:\\Downloads\\tv", ms, "h") == "/data/torrents/tv"
    assert translate("C:\\Downloads\\tv\\s1", ms, "h") == "/data/torrents/tv/s1"


def test_translate_to_root_local_has_no_double_slash():
    assert translate("/dl", [_m("h", "/dl", "/")], "h") == "/"
    assert translate("/dl/x", [_m("h", "/dl", "/")], "h") == "/x"


def test_no_mapping_returns_the_path_unchanged():
    assert translate("/data/x", [], "h") == "/data/x"


def test_translate_folds_case_and_separators_for_a_windows_client_path():
    """A Windows qBittorrent reporting ``c:\\downloads`` still matches ``C:\\Downloads\\``.

    Sonarr's ``OsPath`` compares a Windows path case-insensitively and reads
    ``/`` and ``\\`` as one separator. Measured, by running Sonarr's own
    ``OsPath`` at ``v4.0.20.3014``, in
    ``docs/measurements/2026-11-21-arr-path-mapping-edges.md``.
    """
    ms = [_m("h", "C:\\Downloads\\", "/data/torrents")]
    assert translate("c:\\downloads\\tv", ms, "h") == "/data/torrents/tv"
    assert translate("C:/Downloads/tv", ms, "h") == "/data/torrents/tv"
    assert translate("c:\\downloads", ms, "h") == "/data/torrents"
    mixed = [_m("h", "c:/downloads", "/data")]
    assert translate("C:\\DOWNLOADS\\tv\\s1", mixed, "h") == "/data/tv/s1"


def test_translate_keeps_a_posix_remote_path_case_sensitive():
    """Only Windows paths fold case: ``/DL`` and ``/dl`` are two directories.

    Same measurement. A leading ``/`` settles the kind before any backslash
    does, so ``/dl\\x`` is a POSIX path to the arr, not a Windows one.
    """
    assert translate("/dl/x", [_m("h", "/DL", "/a")], "h") == "/dl/x"
    assert translate("/DL/x", [_m("h", "/dl", "/a")], "h") == "/DL/x"
    assert translate("/dl\\x", [_m("h", "/DL", "/a")], "h") == "/dl\\x"


def test_a_root_remote_mapping_swallows_the_whole_path():
    """Do not "fix" this. A ``/`` remote path leaves the arr with ``/`` itself.

    Sonarr subtracts the mapping's remote path from the client's path, and ``/``
    has no components to subtract, so the subtraction returns the mapping's own
    remote path -- which is rooted, and a rooted right-hand side wins the join
    outright. ``/`` -> ``/mnt`` therefore does not prefix anything with
    ``/mnt``: it collapses every path the client reports to ``/``. Measured on
    Sonarr's own ``OsPath`` at ``v4.0.20.3014`` in
    ``docs/measurements/2026-11-21-arr-path-mapping-edges.md``, which is also
    why the ``/``-swallowing is not a lintarr bug to be tidied away.
    """
    ms = [_m("h", "/", "/mnt")]
    assert translate("/data", ms, "h") == "/"
    assert translate("/data/tv/x", ms, "h") == "/"
    assert translate("/data", [_m("h", "//", "/mnt")], "h") == "/"


def test_a_blank_local_path_translates_to_nothing_rather_than_a_plausible_path():
    """A mapping that names no local path cannot place a file, so lintarr does not guess.

    The arr's own API refuses a blank ``localPath`` (``IsValidPath`` plus
    ``PathExistsValidator`` on ``RemotePathMappingController``), so this comes
    only from a hand-edited database. Sonarr's arithmetic would hand back
    ``/tv`` for ``/downloads/tv`` -- an absolute path that reads as real and
    would be placed on a mount. ``''`` is not placeable, so every caller
    reports it unknown instead.
    """
    for local in ("", "   "):
        ms = [_m("h", "/dl", local)]
        assert translate("/dl/x", ms, "h") == ""
        assert translate("/dl", ms, "h") == ""
    assert posix_abs("") is None


def test_an_unrooted_remote_or_client_path_never_matches():
    """``OsPath.Contains`` is false unless both paths are rooted."""
    assert translate("/dl/x", [_m("h", "", "/a")], "h") == "/dl/x"
    assert translate("/dl/x", [_m("h", "dl", "/a")], "h") == "/dl/x"
    assert translate("/dl/x", [_m("h", "dl/", "/a")], "h") == "/dl/x"
    assert translate("dl/x", [_m("h", "/dl", "/a")], "h") == "dl/x"
    assert translate("C:\\dl\\x", [_m("h", "dl", "/a")], "h") == "C:\\dl\\x"


_COMP = st.sampled_from(["data", "media", "tv", "a", "b"])
_ABS = st.lists(_COMP, min_size=0, max_size=4).map(lambda c: "/" + "/".join(c))


@given(path=_ABS, mounts=st.lists(_ABS, max_size=6))
def test_mount_of_is_the_longest_listed_prefix(path, mounts):
    p = posix_abs(path)
    parsed = [posix_abs(m) for m in mounts]
    containing = [m for m in parsed if p.is_relative_to(m)]
    got = mount_of(p, mounts)
    if not containing:
        assert got is None
    else:
        assert got in containing
        assert all(len(m.parts) <= len(got.parts) for m in containing)
