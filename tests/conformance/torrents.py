"""Build tiny deterministic private torrents for the qBittorrent harness."""

import hashlib
from dataclasses import dataclass


def bencode(value: int | bytes | str | list | dict) -> bytes:
    """Encode a value as bencode; dict keys are sorted as bytes."""
    if isinstance(value, bool):
        raise TypeError("bool is not bencodable")
    if isinstance(value, int):
        return b"i%de" % value
    if isinstance(value, str):
        value = value.encode()
    if isinstance(value, bytes):
        return b"%d:%s" % (len(value), value)
    if isinstance(value, list):
        return b"l" + b"".join(bencode(v) for v in value) + b"e"
    if isinstance(value, dict):
        items = sorted((_key_bytes(k), v) for k, v in value.items())
        return b"d" + b"".join(bencode(k) + bencode(v) for k, v in items) + b"e"
    raise TypeError(f"cannot bencode {type(value).__name__}")


def _key_bytes(key: bytes | str) -> bytes:
    return key.encode() if isinstance(key, str) else key


@dataclass(frozen=True)
class HarnessTorrent:
    name: str
    data: bytes
    metainfo: bytes
    info_hash: str


def _content(name: str, size: int) -> bytes:
    """Deterministic bytes derived from the name."""
    seed = hashlib.sha256(name.encode()).digest()
    return (seed * (size // len(seed) + 1))[:size]


def _pieces(data: bytes, piece_length: int) -> bytes:
    return b"".join(
        hashlib.sha1(data[i : i + piece_length]).digest() for i in range(0, len(data), piece_length)
    )


def make_torrent(name: str, size: int = 16384, piece_length: int = 16384) -> HarnessTorrent:
    """Single-file, private, announce-less torrent so nothing looks for peers."""
    data = _content(name, size)
    info = {
        "name": name,
        "length": size,
        "piece length": piece_length,
        "pieces": _pieces(data, piece_length),
        "private": 1,
    }
    return HarnessTorrent(
        name=name,
        data=data,
        metainfo=bencode({"info": info}),
        info_hash=hashlib.sha1(bencode(info)).hexdigest(),
    )
