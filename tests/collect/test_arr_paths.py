"""The five arr endpoints the hardlink-futility check reads."""

import re

import httpx
import pytest

from lintarr.collect.arr import collect_arr
from lintarr.collect.http import ReadOnlyClient, ServiceError
from lintarr.config import ArrConfig
from lintarr.facts import Known, Unknown
from lintarr.models import ArrDownloadClient, RemotePathMapping, RootFolder
from tests.fixtures import homelab_paths as hp

CFG = ArrConfig(name="main", url="http://sonarr", api_key="k")

PATHS = list(hp.arr_bodies())


def _collect(kind="sonarr", *, replace=None, status_for=None):
    """Collect against homelab bodies; *replace* maps path -> body or Response."""
    bodies = hp.arr_bodies(kind) | (replace or {})

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v3/system/status":
            return httpx.Response(200, json={"version": "4.0.20.3014", "appName": kind.title()})
        if path == "/api/v3/indexer":
            return httpx.Response(200, json=[])
        if path in bodies:
            body = bodies[path]
            return body if isinstance(body, httpx.Response) else httpx.Response(200, json=body)
        return httpx.Response(404, text=path)

    client = ReadOnlyClient("http://sonarr", transport=httpx.MockTransport(handle))
    return collect_arr(client, CFG)


def test_reads_copy_using_hardlinks():
    fact = _collect().copy_using_hardlinks
    assert isinstance(fact, Known)
    assert fact.value is True
    assert fact.source == "GET /api/v3/config/mediamanagement"


def test_non_bool_copy_using_hardlinks_is_bad_response():
    with pytest.raises(ServiceError) as exc:
        _collect(replace={"/api/v3/config/mediamanagement": {"copyUsingHardlinks": "false"}})
    assert exc.value.kind == "bad-response"


def test_absent_copy_using_hardlinks_is_unknown():
    arr = _collect(replace={"/api/v3/config/mediamanagement": {"id": 1}})
    assert isinstance(arr.copy_using_hardlinks, Unknown)
    assert arr.copy_using_hardlinks.reason == "field-absent"


def test_reads_root_folders_with_accessible():
    body = [
        {"path": "/data/media/tv", "accessible": False},
        {"path": "/data/media/anime"},
    ]
    arr = _collect(replace={"/api/v3/rootfolder": body})
    assert arr.root_folders.value == (
        RootFolder("/data/media/tv", False),
        RootFolder("/data/media/anime", None),
    )


def test_reads_mount_paths():
    assert _collect().mounts.value == ("/", "/config", "/data")


def test_reads_remote_path_mappings_verbatim_in_order():
    body = [
        {"id": 2, "host": "b", "remotePath": "/dl/", "localPath": "/data/b"},
        {"id": 1, "host": "a", "remotePath": "/x", "localPath": "/data/a/"},
    ]
    arr = _collect(replace={"/api/v3/remotepathmapping": body})
    assert arr.remote_path_mappings.value == (
        RemotePathMapping("b", "/dl/", "/data/b"),
        RemotePathMapping("a", "/x", "/data/a/"),
    )


@pytest.mark.parametrize(("kind", "category"), [("sonarr", "tv"), ("radarr", "movies")])
def test_reads_download_client_category_for_sonarr_and_radarr(kind, category):
    clients = _collect(kind).download_clients.value
    assert clients == (
        ArrDownloadClient("qBittorrent", "QBittorrent", True, "gluetun", category, False, ""),
    )


@pytest.mark.parametrize(
    "category_field", [{"name": "tvCategory"}, {"name": "tvCategory", "value": ""}]
)
def test_download_client_without_category_has_empty_category(category_field):
    body = hp.download_clients()
    body[0]["fields"] = [f for f in body[0]["fields"] if f["name"] != "tvCategory"] + [
        category_field
    ]
    arr = _collect(replace={"/api/v3/downloadclient": body})
    assert arr.download_clients.value[0].category == ""


def test_download_client_host_is_read_from_fields():
    body = hp.download_clients()
    body[0]["fields"] = [f for f in body[0]["fields"] if f["name"] != "host"]
    arr = _collect(replace={"/api/v3/downloadclient": body})
    assert arr.download_clients.value[0].host is None
    assert _collect().download_clients.value[0].host == "gluetun"


@pytest.mark.parametrize("missing", PATHS)
def test_a_404_on_one_endpoint_is_unknown_and_the_rest_still_read(missing):
    arr = _collect(replace={missing: httpx.Response(404)})
    facts = {
        "/api/v3/config/mediamanagement": arr.copy_using_hardlinks,
        "/api/v3/rootfolder": arr.root_folders,
        "/api/v3/diskspace": arr.mounts,
        "/api/v3/remotepathmapping": arr.remote_path_mappings,
        "/api/v3/downloadclient": arr.download_clients,
    }
    for path, fact in facts.items():
        if path == missing:
            assert isinstance(fact, Unknown)
            assert fact.reason == "field-absent"
        else:
            assert isinstance(fact, Known)


def test_a_500_on_a_new_endpoint_errors_the_arr():
    with pytest.raises(ServiceError):
        _collect(replace={"/api/v3/rootfolder": httpx.Response(500)})


@pytest.mark.parametrize(
    ("fields", "host", "category"),
    [
        ([{"name": "host", "value": None}], None, ""),
        ([{"name": "tvCategory", "value": None}], None, ""),
        ([{"name": "tvCategory", "value": ""}], None, ""),
        ([{"name": "tvCategory", "value": "tv"}], None, "tv"),
    ],
)
def test_null_or_absent_host_and_category_are_read_not_rejected(fields, host, category):
    arr = _collect(replace={"/api/v3/downloadclient": [_dc(fields=fields)]})
    client = arr.download_clients.value[0]
    assert (client.host, client.category) == (host, category)


def _dc(**extra):
    return {"name": "q", "implementation": "Q", "enable": True, **extra}


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/v3/config/mediamanagement", [{"copyUsingHardlinks": True}]),
        ("/api/v3/rootfolder", {"path": "/x"}),
        ("/api/v3/diskspace", {"path": "/x"}),
        ("/api/v3/remotepathmapping", {"host": "h"}),
        ("/api/v3/downloadclient", {"name": "x"}),
        ("/api/v3/rootfolder", [{"accessible": True}]),
        ("/api/v3/rootfolder", [{"path": "/x", "accessible": "yes"}]),
        ("/api/v3/diskspace", [{"label": ""}]),
        ("/api/v3/remotepathmapping", [{"host": "h", "remotePath": "/a"}]),
        ("/api/v3/downloadclient", [{"name": "q", "implementation": "QBittorrent"}]),
        ("/api/v3/downloadclient", [{"name": "q", "implementation": "Q", "enable": "true"}]),
        ("/api/v3/downloadclient", [{"implementation": "Q", "enable": True}]),
        ("/api/v3/downloadclient", [_dc(fields=[{"name": "host", "value": 5}])]),
        ("/api/v3/downloadclient", [_dc(fields=[{"name": "host", "value": ["h"]}])]),
        ("/api/v3/downloadclient", [_dc(fields=[{"name": "tvCategory", "value": 5}])]),
        ("/api/v3/downloadclient", [_dc(fields=[{"name": "movieCategory", "value": []}])]),
    ],
)
def test_wrong_shape_is_bad_response(path, body):
    with pytest.raises(ServiceError) as exc:
        _collect(replace={path: body})
    assert exc.value.kind == "bad-response"


@pytest.mark.parametrize(
    ("kind", "field"),
    [("sonarr", "tvCategory"), ("radarr", "movieCategory")],
)
def test_bad_category_names_the_field_it_read(kind, field):
    """The message must name the field this arr carries, not whichever is listed first.

    A Radarr operator told to fix 'tvCategory' is being pointed at a field
    their app does not have.
    """
    body = [_dc(fields=[{"name": field, "value": []}])]
    with pytest.raises(ServiceError) as exc:
        _collect(kind, replace={"/api/v3/downloadclient": body})
    assert field in exc.value.detail
    other = {"tvCategory", "movieCategory"} - {field}
    assert not (other & set(re.findall(r"\w+", exc.value.detail)))


def _with_top_level(**extra):
    body = hp.download_clients()
    body[0].update(extra)
    return _collect(replace={"/api/v3/downloadclient": body}).download_clients.value[0]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(True, True), (False, False), ("absent", None), (None, None), ("yes", None), (1, None)],
)
def test_reads_remove_completed(raw, expected):
    body = hp.download_clients()
    body[0].pop("removeCompletedDownloads", None)
    if raw != "absent":
        body[0]["removeCompletedDownloads"] = raw
    arr = _collect(replace={"/api/v3/downloadclient": body})
    assert arr.download_clients.value[0].remove_completed is expected


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ({"name": "tvImportedCategory", "value": "tv-done"}, "tv-done"),
        ({"name": "tvImportedCategory"}, ""),
        ({"name": "tvImportedCategory", "value": ""}, ""),
        ({"name": "tvImportedCategory", "value": 5}, ""),
        ({"name": "movieImportedCategory", "value": "m-done"}, "m-done"),
        (None, ""),
    ],
)
def test_reads_imported_category(field, expected):
    body = hp.download_clients()
    if field is not None:
        body[0]["fields"].append(field)
    arr = _collect(replace={"/api/v3/downloadclient": body})
    assert arr.download_clients.value[0].imported_category == expected
