"""qBittorrent's answer to ``ClientFacts.save_paths``.

``""`` maps to ``save_path``. A category's own path applies when
``auto_tmm_enabled`` is true, or when it is false and
``use_category_paths_in_manual_mode`` is true; otherwise the category saves to
``save_path``. A category's own path is its ``savePath``: empty means
``<save_path>/<name>`` and a relative one is joined to ``save_path`` (assumed
from ``Session::categorySavePath``, not measured).

Any input the mapping needs that is unknown, or of the wrong type, makes the
whole mapping ``Unknown``: a partial mapping would let a check conclude from a
category it never read.
"""

from collections.abc import Mapping
from pathlib import PurePosixPath

from lintarr.facts import Fact, Known, Unknown, is_known
from lintarr.models import QbtInstance


def _unknown(detail: str) -> Unknown:
    return Unknown("field-absent", f"save_paths: {detail}")


def _wants_own_path(qbt: QbtInstance) -> Fact[bool]:
    """Whether categories use their own path, or an Unknown naming what blocked it."""
    auto = qbt.auto_tmm_enabled
    if not is_known(auto) or not isinstance(auto.value, bool):
        return _unknown("auto_tmm_enabled is unknown or not a bool")
    if auto.value:
        return auto
    manual = qbt.use_category_paths_in_manual_mode
    if not is_known(manual) or not isinstance(manual.value, bool):
        return _unknown("use_category_paths_in_manual_mode is unknown or not a bool")
    return manual


def _own_path(default: str, name: str, entry: object) -> str | None:
    """A category's own directory, or None when its ``savePath`` can't be read."""
    if not isinstance(entry, dict):
        return None
    raw = entry.get("savePath")
    if not isinstance(raw, str):
        return None
    if raw == "":
        return str(PurePosixPath(default) / name)
    return str(PurePosixPath(default) / raw)


def save_paths(qbt: QbtInstance) -> Fact[Mapping[str, str]]:
    """The per-category save directory, or Unknown when an input is missing."""
    default = qbt.save_path
    if not is_known(default) or not isinstance(default.value, str):
        return _unknown("save_path is unknown or not a string")
    categories = qbt.categories
    if not is_known(categories) or not isinstance(categories.value, dict):
        return _unknown("categories are unknown or not an object")
    own = _wants_own_path(qbt)
    if not is_known(own):
        return own
    mapping = {"": default.value}
    for name, entry in categories.value.items():
        path = _own_path(default.value, name, entry) if own.value else default.value
        if path is None:
            return _unknown(f"category {name!r} has no readable savePath")
        mapping[name] = path
    return Known(
        mapping,
        source="; ".join(dict.fromkeys((default.source, categories.source))),
        read_at=min(default.read_at, categories.read_at),
        service_version=default.service_version,
    )
