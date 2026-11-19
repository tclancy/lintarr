from pathlib import PurePosixPath

from hypothesis import given
from hypothesis import strategies as st

from lintarr.checks.paths import mount_of, posix_abs, translate
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
