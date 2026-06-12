import json

import pytest
import responses
from django.contrib.auth.models import User
from django.urls import reverse

from core import putio
from core.models import DownloadRequest
from core.tests.test_views import MAGNET, mock_putio_folders

pytestmark = pytest.mark.django_db

AUTH = {"HTTP_AUTHORIZATION": "Bearer test-api-token"}


@pytest.fixture(autouse=True)
def api_settings(settings):
    settings.DRAGNET_API_TOKEN = "test-api-token"
    settings.PUTIO_OAUTH_TOKEN = "test-putio-token"


def post_download(client, extra_headers=None, **overrides):
    body = {
        "info_hash": "a" * 40,
        "title": "Westworld.S01-S04.2160p.WEB-DL",
        "magnet_uri": MAGNET,
        "content_type": "tv_show",
        "size": 200_000_000_000,
    }
    body.update(overrides)
    headers = AUTH if extra_headers is None else extra_headers
    return client.post(
        reverse("api_download"),
        json.dumps(body),
        content_type="application/json",
        **headers,
    )


def test_missing_token_is_401(client):
    response = post_download(client, extra_headers={})
    assert response.status_code == 401


def test_wrong_token_is_401(client):
    response = post_download(client, extra_headers={"HTTP_AUTHORIZATION": "Bearer wrong"})
    assert response.status_code == 401


def test_unconfigured_api_is_503(client, settings):
    settings.DRAGNET_API_TOKEN = ""
    response = post_download(client)
    assert response.status_code == 503


def test_invalid_body_is_400(client):
    response = client.post(
        reverse("api_download"), "not json", content_type="application/json", **AUTH
    )
    assert response.status_code == 400


def test_non_magnet_is_400(client):
    response = post_download(client, magnet_uri="http://evil")
    assert response.status_code == 400
    assert DownloadRequest.objects.count() == 0


@responses.activate
def test_send_routes_and_attributes_to_api_user(client):
    mock_putio_folders()
    responses.post(putio.TRANSFERS_ADD_URL, json={"status": "OK", "transfer": {"id": 9}})
    response = post_download(client)
    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "sent"
    assert payload["destination"] == "plex/tv_series"
    assert payload["putio_transfer_id"] == 9
    download_request = DownloadRequest.objects.get()
    assert download_request.user.username == "claude"
    assert not download_request.user.has_usable_password()


@responses.activate
def test_duplicate_is_409_with_details(client):
    human = User.objects.create_user("alice", password="x")
    DownloadRequest.objects.create(
        user=human,
        info_hash="a" * 40,
        title="Westworld.S01-S04.2160p.WEB-DL",
        magnet_uri=MAGNET,
        destination="plex/tv_series",
        status=DownloadRequest.Status.SENT,
    )
    response = post_download(client)
    assert response.status_code == 409
    payload = response.json()
    assert payload["status"] == "duplicate"
    assert payload["sent_by"] == "alice"
    assert DownloadRequest.objects.count() == 1


@responses.activate
def test_duplicate_with_force_resends(client):
    human = User.objects.create_user("alice", password="x")
    DownloadRequest.objects.create(
        user=human,
        info_hash="a" * 40,
        title="Westworld.S01-S04.2160p.WEB-DL",
        magnet_uri=MAGNET,
        status=DownloadRequest.Status.SENT,
    )
    mock_putio_folders()
    responses.post(putio.TRANSFERS_ADD_URL, json={"status": "OK", "transfer": {"id": 10}})
    response = post_download(client, force=True)
    assert response.status_code == 201
    assert DownloadRequest.objects.count() == 2


@responses.activate
def test_putio_failure_is_502_and_recorded(client):
    mock_putio_folders()
    responses.post(putio.TRANSFERS_ADD_URL, status=500, body="boom")
    response = post_download(client)
    assert response.status_code == 502
    download_request = DownloadRequest.objects.get()
    assert download_request.status == DownloadRequest.Status.FAILED
