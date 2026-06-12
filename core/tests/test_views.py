import pytest
import responses
from django.contrib.auth.models import User
from django.urls import reverse

from core import putio
from core.models import DownloadRequest
from core.tests.test_bitmagnet import GRAPHQL_URL, make_item, search_payload

pytestmark = pytest.mark.django_db

MAGNET = "magnet:?xt=urn:btih:" + "a" * 40


@pytest.fixture
def user(client):
    user = User.objects.create_user("alice", password="test-password")
    client.force_login(user)
    return user


def download_post_data(**overrides):
    data = {
        "info_hash": "a" * 40,
        "title": "Westworld.S01.2160p.WEB-DL",
        "size": "50000000000",
        "magnet_uri": MAGNET,
        "content_type": "tv_show",
    }
    data.update(overrides)
    return data


def mock_putio_folders():
    """Register folder listings: root (plex) then plex's children, in call order."""
    responses.get(
        putio.FILES_LIST_URL,
        json={"status": "OK", "files": [{"id": 100, "file_type": "FOLDER", "name": "plex"}]},
    )
    responses.get(
        putio.FILES_LIST_URL,
        json={
            "status": "OK",
            "files": [
                {"id": 200, "file_type": "FOLDER", "name": "curated_movies"},
                {"id": 300, "file_type": "FOLDER", "name": "tv_series"},
            ],
        },
    )


def test_search_requires_login(client):
    response = client.get(reverse("search"))
    assert response.status_code == 302
    assert "/accounts/login/" in response["Location"]


@responses.activate
def test_search_renders_results(client, user):
    responses.post(GRAPHQL_URL, json=search_payload([make_item()]))
    response = client.get(reverse("search"), {"q": "westworld", "resolution": "V2160p"})
    assert response.status_code == 200
    content = response.content.decode()
    assert "Westworld.S01.2160p.WEB-DL" in content
    assert "Send to put.io" in content


@responses.activate
def test_search_shows_sent_badge_for_already_sent(client, user):
    DownloadRequest.objects.create(
        user=user,
        info_hash="a" * 40,
        title="Westworld.S01.2160p.WEB-DL",
        magnet_uri=MAGNET,
        status=DownloadRequest.Status.SENT,
    )
    responses.post(GRAPHQL_URL, json=search_payload([make_item()]))
    response = client.get(reverse("search"), {"q": "westworld"})
    assert "✓ Sent" in response.content.decode()


@responses.activate
def test_search_shows_error_when_bitmagnet_down(client, user):
    responses.post(GRAPHQL_URL, status=502, body="bad gateway")
    response = client.get(reverse("search"), {"q": "westworld"})
    assert response.status_code == 200
    assert "502" in response.content.decode()


@responses.activate
def test_download_tv_show_goes_to_tv_series(client, user, settings):
    settings.PUTIO_OAUTH_TOKEN = "test-token"
    mock_putio_folders()
    responses.post(putio.TRANSFERS_ADD_URL, json={"status": "OK", "transfer": {"id": 7}})
    response = client.post(reverse("download"), download_post_data(content_type="tv_show"))
    assert response.status_code == 302
    download_request = DownloadRequest.objects.get()
    assert download_request.user == user
    assert download_request.putio_transfer_id == 7
    assert download_request.status == DownloadRequest.Status.SENT
    assert download_request.destination == "plex/tv_series"
    assert "save_parent_id=300" in responses.calls[-1].request.body


@responses.activate
def test_download_movie_goes_to_curated_movies(client, user, settings):
    settings.PUTIO_OAUTH_TOKEN = "test-token"
    mock_putio_folders()
    responses.post(putio.TRANSFERS_ADD_URL, json={"status": "OK", "transfer": {"id": 7}})
    client.post(reverse("download"), download_post_data(content_type="movie"))
    assert DownloadRequest.objects.get().destination == "plex/curated_movies"
    assert "save_parent_id=200" in responses.calls[-1].request.body


@responses.activate
def test_download_unclassified_goes_to_root_unclassified_folder(client, user, settings):
    settings.PUTIO_OAUTH_TOKEN = "test-token"
    # Root listing has no "unclassified" folder yet, so it gets created.
    responses.get(
        putio.FILES_LIST_URL,
        json={"status": "OK", "files": [{"id": 100, "file_type": "FOLDER", "name": "plex"}]},
    )
    responses.post(putio.CREATE_FOLDER_URL, json={"status": "OK", "file": {"id": 500}})
    responses.post(putio.TRANSFERS_ADD_URL, json={"status": "OK", "transfer": {"id": 7}})
    client.post(reverse("download"), download_post_data(content_type=""))
    assert DownloadRequest.objects.get().destination == "unclassified"
    assert "save_parent_id=500" in responses.calls[-1].request.body


def test_download_rejects_non_magnet(client, user):
    response = client.post(reverse("download"), download_post_data(magnet_uri="http://evil"))
    assert response.status_code == 302
    assert DownloadRequest.objects.count() == 0


def test_download_duplicate_warns_without_resending(client, user):
    DownloadRequest.objects.create(
        user=user,
        info_hash="a" * 40,
        title="Westworld.S01.2160p.WEB-DL",
        magnet_uri=MAGNET,
        status=DownloadRequest.Status.SENT,
    )
    # No responses mock active: a real put.io call would raise, so reaching
    # the redirect proves nothing was sent.
    response = client.post(reverse("download"), download_post_data())
    assert response.status_code == 302
    assert DownloadRequest.objects.count() == 1


@responses.activate
def test_download_duplicate_with_force_resends(client, user, settings):
    settings.PUTIO_OAUTH_TOKEN = "test-token"
    DownloadRequest.objects.create(
        user=user,
        info_hash="a" * 40,
        title="Westworld.S01.2160p.WEB-DL",
        magnet_uri=MAGNET,
        status=DownloadRequest.Status.SENT,
    )
    mock_putio_folders()
    responses.post(putio.TRANSFERS_ADD_URL, json={"status": "OK", "transfer": {"id": 8}})
    client.post(reverse("download"), download_post_data(force="1"))
    assert DownloadRequest.objects.count() == 2


@responses.activate
def test_download_putio_failure_recorded(client, user, settings):
    settings.PUTIO_OAUTH_TOKEN = "test-token"
    mock_putio_folders()
    responses.post(putio.TRANSFERS_ADD_URL, status=500, body="boom")
    client.post(reverse("download"), download_post_data())
    download_request = DownloadRequest.objects.get()
    assert download_request.status == DownloadRequest.Status.FAILED
    assert "500" in download_request.error


@responses.activate
def test_status_renders(client, user):
    from core.tests.test_bitmagnet import status_payload

    responses.post(GRAPHQL_URL, json=status_payload())
    response = client.get(reverse("status"))
    content = response.content.decode()
    assert "torrents indexed" in content
    assert "5000" in content
    assert "queue backlog" in content


@responses.activate
def test_status_shows_error_when_bitmagnet_down(client, user):
    responses.post(GRAPHQL_URL, status=502, body="bad gateway")
    response = client.get(reverse("status"))
    assert response.status_code == 200
    assert "502" in response.content.decode()


def test_history_lists_requests(client, user):
    DownloadRequest.objects.create(
        user=user,
        info_hash="a" * 40,
        title="Westworld.S01.2160p.WEB-DL",
        magnet_uri=MAGNET,
    )
    response = client.get(reverse("history"))
    assert "Westworld.S01.2160p.WEB-DL" in response.content.decode()
