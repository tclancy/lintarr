"""Connection configuration, read from the environment.

API keys are required; there is no auto-discovery. See the spec's Credentials
section for why.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

type ArrKind = Literal["sonarr", "radarr"]

_SECRET = "***"


@dataclass(frozen=True, slots=True)
class QbtConfig:
    name: str
    url: str
    username: str
    password: str = field(repr=False)

    def __repr__(self) -> str:
        return f"QbtConfig(name={self.name!r}, url={self.url!r}, password={_SECRET})"


@dataclass(frozen=True, slots=True)
class ArrConfig:
    name: str
    kind: ArrKind
    url: str
    api_key: str = field(repr=False)

    def __repr__(self) -> str:
        return (
            f"ArrConfig(name={self.name!r}, kind={self.kind!r}, "
            f"url={self.url!r}, api_key={_SECRET})"
        )


@dataclass(frozen=True, slots=True)
class LintarrConfig:
    qbits: tuple[QbtConfig, ...]
    arrs: tuple[ArrConfig, ...]
    declared: frozenset[str]


def _instances(env: Mapping[str, str], prefix: str) -> dict[str, str]:
    """Map instance name -> URL for ``<PREFIX>_URL`` and ``<PREFIX>_URL__<NAME>``."""
    out: dict[str, str] = {}
    base = f"{prefix}_URL"
    for key, value in env.items():
        if key == base:
            out["main"] = value
        elif key.startswith(f"{base}__"):
            out[key.removeprefix(f"{base}__").lower()] = value
    return out


def _suffix(name: str) -> str:
    return "" if name == "main" else f"__{name.upper()}"


def _credential_suffixes(env: Mapping[str, str], *credential_bases: str) -> set[str]:
    """Every instance suffix (``''`` or ``'__NAME'``) any of *credential_bases* is set for."""
    found: set[str] = set()
    for key in env:
        for base in credential_bases:
            if key == base:
                found.add("")
            elif key.startswith(f"{base}__"):
                found.add(key.removeprefix(base))
    return found


def _reject_orphaned_credentials(
    env: Mapping[str, str], url_base: str, *credential_bases: str
) -> None:
    """Fail when credentials exist for an instance whose URL does not.

    A single typo — ``QBIT_URLL`` — otherwise yields no instance *and* no
    declared service, so the run exits clean having never looked at
    qBittorrent at all. A silent green on an unchecked service is the worst
    outcome this tool can produce, so orphaned credentials are an error.
    """
    for suffix in sorted(_credential_suffixes(env, *credential_bases)):
        if f"{url_base}{suffix}" not in env:
            present = sorted(f"{b}{suffix}" for b in credential_bases if f"{b}{suffix}" in env)
            raise ValueError(
                f"{', '.join(present)} set but {url_base}{suffix} missing — "
                f"the service would be neither collected nor reported as absent"
            )


def load_config(env: Mapping[str, str]) -> LintarrConfig:
    _reject_orphaned_credentials(env, "QBIT_URL", "QBIT_USER", "QBIT_PASS")
    for _kind in ("SONARR", "RADARR"):
        _reject_orphaned_credentials(env, f"{_kind}_URL", f"{_kind}_API_KEY")

    qbits = []
    for name, url in sorted(_instances(env, "QBIT").items()):
        s = _suffix(name)
        user, password = env.get(f"QBIT_USER{s}"), env.get(f"QBIT_PASS{s}")
        if user is None or password is None:
            raise ValueError(f"QBIT_URL{s} set but QBIT_USER{s}/QBIT_PASS{s} missing")
        qbits.append(QbtConfig(name=name, url=url.rstrip("/"), username=user, password=password))

    arrs = []
    for kind in ("sonarr", "radarr"):
        prefix = kind.upper()
        for name, url in sorted(_instances(env, prefix).items()):
            s = _suffix(name)
            api_key = env.get(f"{prefix}_API_KEY{s}")
            if api_key is None:
                raise ValueError(f"{prefix}_URL{s} set but {prefix}_API_KEY{s} missing")
            arrs.append(ArrConfig(name=name, kind=kind, url=url.rstrip("/"), api_key=api_key))

    configured = {"qbittorrent"} if qbits else set()
    configured |= {a.kind for a in arrs}

    raw = env.get("LINTARR_SERVICES")
    declared = (
        frozenset(s.strip().lower() for s in raw.split(",") if s.strip())
        if raw
        else frozenset(configured)
    )
    return LintarrConfig(qbits=tuple(qbits), arrs=tuple(arrs), declared=declared)
