"""Connection configuration, read from the environment.

API keys are required; there is no auto-discovery. See the spec's Credentials
section for why.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

_SECRET = "***"


@dataclass(frozen=True, slots=True)
class ClientConfig:
    """One download client. Which credentials it carries is its backend's contract."""

    name: str
    kind: str
    url: str
    username: str | None = field(default=None, repr=False)
    password: str | None = field(default=None, repr=False)
    api_key: str | None = field(default=None, repr=False)

    def __repr__(self) -> str:
        password = None if self.password is None else _SECRET
        api_key = None if self.api_key is None else _SECRET
        return (
            f"ClientConfig(name={self.name!r}, kind={self.kind!r}, url={self.url!r}, "
            f"password={password}, api_key={api_key})"
        )


@dataclass(frozen=True, slots=True)
class ArrConfig:
    """One arr instance. Its kind is not configured: it is read from the app."""

    name: str
    url: str
    api_key: str = field(repr=False)

    def __repr__(self) -> str:
        return f"ArrConfig(name={self.name!r}, url={self.url!r}, api_key={_SECRET})"


@dataclass(frozen=True, slots=True)
class LintarrConfig:
    """``declared_explicit`` is ``LINTARR_SERVICES``, or ``None`` when it is unset.

    The default declared set needs the arr kinds, which only collection knows,
    so it is derived later by ``run.declared_services``.
    """

    clients: tuple[ClientConfig, ...]
    arrs: tuple[ArrConfig, ...]
    declared_explicit: frozenset[str] | None


#: Every variable lintarr used to read, and the client-neutral one that replaced it.
_LEGACY_NAMES = {
    "QBIT_URL": "DOWNLOAD_CLIENT_URL",
    "QBIT_USER": "DOWNLOAD_CLIENT_USER",
    "QBIT_PASS": "DOWNLOAD_CLIENT_PASS",
    "SONARR_URL": "ARR_URL",
    "SONARR_API_KEY": "ARR_API_KEY",
    "RADARR_URL": "ARR_URL",
    "RADARR_API_KEY": "ARR_API_KEY",
}

_CLIENT_KIND = "DOWNLOAD_CLIENT_KIND"
_CLIENT_URL = "DOWNLOAD_CLIENT_URL"

#: The variable each backend credential is read from.
_CREDENTIAL_VARIABLES = {
    "user": "DOWNLOAD_CLIENT_USER",
    "pass": "DOWNLOAD_CLIENT_PASS",
    "api_key": "DOWNLOAD_CLIENT_API_KEY",
}

_ARR_URL = "ARR_URL"
_ARR_API_KEY = "ARR_API_KEY"


def _instances(env: Mapping[str, str], base: str) -> dict[str, str]:
    """Map instance name -> value for ``<BASE>`` and ``<BASE>__<NAME>``."""
    out: dict[str, str] = {}
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


def _legacy_present(env: Mapping[str, str]) -> list[tuple[str, str]]:
    """``(variable, legacy base)`` for every legacy variable set, bare or suffixed."""
    return [
        (key, legacy)
        for key in sorted(env)
        for legacy in _LEGACY_NAMES
        if key == legacy or key.startswith(f"{legacy}__")
    ]


def _reject_legacy_names(env: Mapping[str, str]) -> None:
    """Fail on any pre-rename variable, even beside the new ones.

    A half-migrated environment would otherwise run against whichever half
    lintarr reads, and the silently ignored half is a service nobody checked.
    Every legacy variable is named at once, with its replacement, so one run
    gives the operator the whole migration rather than one name per attempt.
    """
    present = _legacy_present(env)
    if not present:
        return
    renames = "; ".join(
        f"rename {key} to {_LEGACY_NAMES[legacy]}{key.removeprefix(legacy)}"
        for key, legacy in present
    )
    message = f"legacy variables are no longer read: {renames}"
    if any(legacy.startswith("QBIT_") for _, legacy in present):
        message += (
            f"; also set {_CLIENT_KIND}=qbittorrent (with the same __NAME suffix) "
            "for each qBittorrent instance"
        )
    raise ValueError(message)


def _reject_orphaned_credentials(
    env: Mapping[str, str], url_base: str, *credential_bases: str
) -> None:
    """Fail when credentials exist for an instance whose URL does not.

    A single typo — ``DOWNLOAD_CLIENT_URLL`` — otherwise yields no instance
    *and* no declared service, so the run exits clean having never looked at
    the client at all. A silent green on an unchecked service is the worst
    outcome this tool can produce, so orphaned credentials are an error.
    """
    for suffix in sorted(_credential_suffixes(env, *credential_bases)):
        if f"{url_base}{suffix}" not in env:
            present = sorted(f"{b}{suffix}" for b in credential_bases if f"{b}{suffix}" in env)
            raise ValueError(
                f"{', '.join(present)} set but {url_base}{suffix} missing — "
                f"the service would be neither collected nor reported as absent"
            )


def _reject_unpaired(have: dict[str, str], partner: dict[str, str], base: str, needs: str) -> None:
    """Fail naming *needs* for the first instance in *have* that *partner* lacks."""
    unpaired = sorted(have.keys() - partner.keys())
    if unpaired:
        s = _suffix(unpaired[0])
        raise ValueError(f"{base}{s} set but {needs}{s} missing")


def _client_kinds(env: Mapping[str, str]) -> dict[str, str]:
    """Instance name -> kind, failing unless every URL has a kind and every kind a URL."""
    urls, kinds = _instances(env, _CLIENT_URL), _instances(env, _CLIENT_KIND)
    _reject_unpaired(urls, kinds, _CLIENT_URL, _CLIENT_KIND)
    _reject_unpaired(kinds, urls, _CLIENT_KIND, _CLIENT_URL)
    return kinds


def _backend_for(raw_kind: str, s: str, backends: Mapping[str, object]) -> str:
    """The registered kind *raw_kind* names, normalised, or fail listing the known ones."""
    kind = raw_kind.strip().lower()
    if kind not in backends:
        raise ValueError(
            f"{_CLIENT_KIND}{s}={raw_kind!r} is not a known download client "
            f"(known: {', '.join(sorted(backends))})"
        )
    return kind


def _client_credentials(
    env: Mapping[str, str], s: str, kind: str, required: frozenset[str]
) -> dict[str, str]:
    """The credentials *kind* requires, failing on a missing one or one it does not use."""
    unused = sorted(
        f"{var}{s}"
        for cred, var in _CREDENTIAL_VARIABLES.items()
        if cred not in required and f"{var}{s}" in env
    )
    if unused:
        raise ValueError(f"{', '.join(unused)} set but a {kind} client does not use it")
    missing = sorted(
        f"{_CREDENTIAL_VARIABLES[c]}{s}"
        for c in required
        if f"{_CREDENTIAL_VARIABLES[c]}{s}" not in env
    )
    if missing:
        raise ValueError(f"{_CLIENT_URL}{s} set but {'/'.join(missing)} missing")
    return {cred: env[f"{_CREDENTIAL_VARIABLES[cred]}{s}"] for cred in required}


def _load_clients(env: Mapping[str, str]) -> list[ClientConfig]:
    from lintarr.clients import BACKENDS

    _reject_orphaned_credentials(env, _CLIENT_URL, *_CREDENTIAL_VARIABLES.values())
    kinds = _client_kinds(env)
    clients = []
    for name, url in sorted(_instances(env, _CLIENT_URL).items()):
        s = _suffix(name)
        kind = _backend_for(kinds[name], s, BACKENDS)
        creds = _client_credentials(env, s, kind, BACKENDS[kind].credentials)
        clients.append(
            ClientConfig(
                name=name,
                kind=kind,
                url=url.rstrip("/"),
                username=creds.get("user"),
                password=creds.get("pass"),
                api_key=creds.get("api_key"),
            )
        )
    return clients


def _load_arrs(env: Mapping[str, str]) -> list[ArrConfig]:
    _reject_orphaned_credentials(env, _ARR_URL, _ARR_API_KEY)
    arrs = []
    for name, url in sorted(_instances(env, _ARR_URL).items()):
        s = _suffix(name)
        api_key = env.get(f"{_ARR_API_KEY}{s}")
        if api_key is None:
            raise ValueError(f"{_ARR_URL}{s} set but {_ARR_API_KEY}{s} missing")
        arrs.append(ArrConfig(name=name, url=url.rstrip("/"), api_key=api_key))
    return arrs


def _declared_explicit(env: Mapping[str, str]) -> frozenset[str] | None:
    raw = env.get("LINTARR_SERVICES")
    if not raw:
        return None
    return frozenset(s.strip().lower() for s in raw.split(",") if s.strip())


def load_config(env: Mapping[str, str]) -> LintarrConfig:
    _reject_legacy_names(env)
    return LintarrConfig(
        clients=tuple(_load_clients(env)),
        arrs=tuple(_load_arrs(env)),
        declared_explicit=_declared_explicit(env),
    )
