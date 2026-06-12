import pytest
import responses

from core import putio

MAGNET = "magnet:?xt=urn:btih:" + "a" * 40


@responses.activate
def test_add_transfer_success(settings):
    settings.PUTIO_OAUTH_TOKEN = "test-token"
    responses.post(
        putio.TRANSFERS_ADD_URL,
        json={"status": "OK", "transfer": {"id": 42, "name": "Westworld"}},
    )
    transfer = putio.add_transfer(MAGNET)
    assert transfer["id"] == 42
    request = responses.calls[0].request
    assert request.headers["Authorization"] == "Bearer test-token"
    assert "url=magnet" in request.body


def test_missing_token_raises(settings):
    settings.PUTIO_OAUTH_TOKEN = ""
    with pytest.raises(putio.PutioError, match="not configured"):
        putio.add_transfer(MAGNET)


@responses.activate
def test_http_error_raises(settings):
    settings.PUTIO_OAUTH_TOKEN = "test-token"
    responses.post(putio.TRANSFERS_ADD_URL, status=401, body="Unauthorized")
    with pytest.raises(putio.PutioError, match="401"):
        putio.add_transfer(MAGNET)


@responses.activate
def test_non_ok_payload_raises(settings):
    settings.PUTIO_OAUTH_TOKEN = "test-token"
    responses.post(putio.TRANSFERS_ADD_URL, json={"status": "ERROR", "error_type": "Whatever"})
    with pytest.raises(putio.PutioError, match="non-OK"):
        putio.add_transfer(MAGNET)


@responses.activate
def test_add_transfer_with_parent_folder(settings):
    settings.PUTIO_OAUTH_TOKEN = "test-token"
    responses.post(putio.TRANSFERS_ADD_URL, json={"status": "OK", "transfer": {"id": 1}})
    putio.add_transfer(MAGNET, save_parent_id=42)
    assert "save_parent_id=42" in responses.calls[0].request.body


@responses.activate
def test_find_or_create_folder_returns_existing(settings):
    settings.PUTIO_OAUTH_TOKEN = "test-token"
    responses.get(
        putio.FILES_LIST_URL,
        json={
            "status": "OK",
            "files": [
                {"id": 8, "file_type": "VIDEO", "name": "plex"},
                {"id": 7, "file_type": "FOLDER", "name": "plex"},
            ],
        },
    )
    assert putio.find_or_create_folder("plex", 0) == 7


@responses.activate
def test_find_or_create_folder_creates_when_missing(settings):
    settings.PUTIO_OAUTH_TOKEN = "test-token"
    responses.get(putio.FILES_LIST_URL, json={"status": "OK", "files": []})
    responses.post(putio.CREATE_FOLDER_URL, json={"status": "OK", "file": {"id": 9}})
    assert putio.find_or_create_folder("curated_movies", 100) == 9
    assert "parent_id=100" in responses.calls[1].request.body


@responses.activate
def test_resolve_folder_path_chains_parents(settings):
    settings.PUTIO_OAUTH_TOKEN = "test-token"
    responses.get(
        putio.FILES_LIST_URL,
        json={"status": "OK", "files": [{"id": 7, "file_type": "FOLDER", "name": "plex"}]},
    )
    responses.get(
        putio.FILES_LIST_URL,
        json={"status": "OK", "files": [{"id": 20, "file_type": "FOLDER", "name": "tv_series"}]},
    )
    assert putio.resolve_folder_path(["plex", "tv_series"]) == 20
    assert "parent_id=0" in responses.calls[0].request.url
    assert "parent_id=7" in responses.calls[1].request.url
