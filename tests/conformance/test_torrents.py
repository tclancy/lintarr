from tests.conformance.torrents import HarnessTorrent, bencode, make_torrent


def test_bencode_known_vectors():
    assert bencode({"a": 1, "b": [b"x", "y"]}) == b"d1:ai1e1:bl1:x1:yee"
    assert bencode({"b": 1, "a": 2}) == b"d1:ai2e1:bi1ee"


def test_make_torrent_hash_is_stable():
    assert make_torrent("one").info_hash == make_torrent("one").info_hash
    assert make_torrent("one").info_hash != make_torrent("two").info_hash


def test_metainfo_has_private_flag_and_no_announce():
    torrent = make_torrent("one")
    assert isinstance(torrent, HarnessTorrent)
    assert b"7:privatei1e" in torrent.metainfo
    assert b"announce" not in torrent.metainfo
    assert len(torrent.info_hash) == 40
    assert torrent.info_hash == torrent.info_hash.lower()
    assert len(torrent.data) == 16384
