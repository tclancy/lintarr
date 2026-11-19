"""Wrap qBittorrent fixtures as the client-neutral instances ``StackFacts`` carries."""

from lintarr.models import ClientFacts, ClientInstance, QbtInstance


def as_client(qbt: QbtInstance) -> ClientInstance:
    """*qbt* as the ``ClientInstance`` the qBittorrent backend's collector returns."""
    return ClientInstance(qbt.name, "qbittorrent", qbt.version, ClientFacts(), qbt)
