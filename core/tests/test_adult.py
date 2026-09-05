"""The adult section: gating, isolation, and put.io routing.

Two invariants matter here. Adult content must never appear in, or be sendable
from, the family-facing section; and adult sends must never land under the
rclone-watched put.io folder, because rclone ships that folder to Plex.
"""

import pytest
import responses
from django.contrib.auth.models import Permission, User
from django.urls import reverse

from core import putio, services
from core.models import DownloadRequest
from core.tests.test_bitmagnet import GRAPHQL_URL, make_item, search_payload
from core.tests.test_views import MAGNET, download_post_data

pytestmark = pytest.mark.django_db

ADULT_PERM = "core.view_adult_content"


@pytest.fixture
def plain_user(client):
    user = User.objects.create_user("bob", password="test-password")
    client.force_login(user)
    return user


@pytest.fixture
def adult_user(client):
    user = User.objects.create_user("carol", password="test-password")
    user.user_permissions.add(Permission.objects.get(codename="view_adult_content"))
    client.force_login(user)
    return user


# --- routing ---------------------------------------------------------------


def test_adult_sends_route_outside_the_plex_folder(settings):
    """rclone watches PUTIO_BASE_FOLDER; adult must not be under it."""
    folders = services.destination_folders("xxx")
    assert folders == [settings.PUTIO_ADULT_FOLDER]
    assert settings.PUTIO_BASE_FOLDER not in folders


def test_normal_routing_is_unchanged(settings):
    assert services.destination_folders("movie") == [settings.PUTIO_BASE_FOLDER, "curated_movies"]
    assert services.destination_folders("tv_show") == [settings.PUTIO_BASE_FOLDER, "tv_series"]
    assert services.destination_folders("music") == [settings.PUTIO_UNCLASSIFIED_FOLDER]


# --- permission gate -------------------------------------------------------


def test_adult_section_is_hidden_from_users_without_permission(client, plain_user):
    assert client.get(reverse("adult_search")).status_code == 403
    assert client.post(reverse("adult_download"), download_post_data()).status_code == 403


def test_adult_section_requires_login(client):
    response = client.get(reverse("adult_search"))
    assert response.status_code == 302
    assert "/accounts/login/" in response["Location"]


def test_adult_nav_link_only_renders_with_permission(client, plain_user):
    responses.start()
    try:
        responses.post(GRAPHQL_URL, json=search_payload([]))
        assert reverse("adult_search").encode() not in client.get(reverse("search")).content
    finally:
        responses.stop()
        responses.reset()


@responses.activate
def test_adult_nav_link_renders_for_permitted_users(client, adult_user):
    responses.post(GRAPHQL_URL, json=search_payload([]))
    assert reverse("adult_search").encode() in client.get(reverse("search")).content


# --- the service layer is the real gate ------------------------------------


def test_service_layer_refuses_adult_sends_without_permission(plain_user):
    """Enforced below the view so the JSON API can't bypass it."""
    with pytest.raises(services.AdultContentNotPermitted):
        services.send_download(
            plain_user,
            info_hash="b" * 40,
            title="something",
            magnet_uri=MAGNET,
            content_type="xxx",
        )
    assert not DownloadRequest.objects.exists()


def test_family_download_endpoint_refuses_adult_content(client, plain_user):
    """Posting content_type=xxx to the family endpoint must not send anything."""
    response = client.post(reverse("download"), download_post_data(content_type="xxx"), follow=True)
    assert response.status_code == 200
    assert not DownloadRequest.objects.exists()


def _mock_adult_putio(settings, transfer_id=77, add_status=200):
    """Wire up a put.io that resolves the adult folder and accepts the transfer."""
    settings.PUTIO_OAUTH_TOKEN = "test-token"
    responses.get(
        putio.FILES_LIST_URL,
        json={
            "status": "OK",
            "files": [{"id": 900, "file_type": "FOLDER", "name": settings.PUTIO_ADULT_FOLDER}],
        },
    )
    if add_status == 200:
        responses.post(
            putio.TRANSFERS_ADD_URL, json={"status": "OK", "transfer": {"id": transfer_id}}
        )
    else:
        responses.post(putio.TRANSFERS_ADD_URL, json={"status": "ERROR"}, status=add_status)


@responses.activate
def test_permitted_user_sends_adult_content_to_the_adult_folder(client, adult_user, settings):
    """The send still happens and still routes correctly — it is only unlogged."""
    _mock_adult_putio(settings)
    responses.post(GRAPHQL_URL, json=search_payload([]))

    client.post(reverse("adult_download"), download_post_data(content_type="xxx"), follow=True)

    add_call = [c for c in responses.calls if c.request.url == putio.TRANSFERS_ADD_URL][0]
    assert "save_parent_id=900" in add_call.request.body


# --- adult sends are never recorded ----------------------------------------


@responses.activate
def test_adult_send_writes_no_row(adult_user, settings):
    """The whole point: no title, no magnet, no row — not even for the sender."""
    _mock_adult_putio(settings)

    result = services.send_download(
        adult_user,
        info_hash="a" * 40,
        title="Some Adult Title That Must Not Be Stored",
        magnet_uri=MAGNET,
        content_type="xxx",
    )

    assert not DownloadRequest.objects.exists()
    # The caller still gets everything it needs to report the send.
    assert result.destination == settings.PUTIO_ADULT_FOLDER
    assert result.putio_transfer_id == 77
    assert result.title == "Some Adult Title That Must Not Be Stored"


@responses.activate
def test_failed_adult_send_writes_no_row(adult_user, settings):
    """The FAILED branch records a row for family sends; it must not for adult."""
    _mock_adult_putio(settings, add_status=500)

    with pytest.raises(putio.PutioError):
        services.send_download(
            adult_user,
            info_hash="a" * 40,
            title="Some Adult Title That Must Not Be Stored",
            magnet_uri=MAGNET,
            content_type="xxx",
        )

    assert not DownloadRequest.objects.exists()


@responses.activate
def test_adult_sends_are_never_duplicates(adult_user, settings):
    """No row means no memory: the same hash sends twice, making two transfers."""
    _mock_adult_putio(settings)

    for _ in range(2):
        services.send_download(
            adult_user,
            info_hash="a" * 40,
            title="Same Thing Twice",
            magnet_uri=MAGNET,
            content_type="xxx",
        )

    add_calls = [c for c in responses.calls if c.request.url == putio.TRANSFERS_ADD_URL]
    assert len(add_calls) == 2
    assert not DownloadRequest.objects.exists()


@responses.activate
def test_family_sends_are_still_recorded(adult_user, settings):
    """Guard the inverse: only adult content is exempt from logging."""
    settings.PUTIO_OAUTH_TOKEN = "test-token"
    responses.get(
        putio.FILES_LIST_URL,
        json={
            "status": "OK",
            "files": [
                {
                    "id": 901,
                    "file_type": "FOLDER",
                    "name": settings.PUTIO_UNCLASSIFIED_FOLDER,
                }
            ],
        },
    )
    responses.post(putio.TRANSFERS_ADD_URL, json={"status": "OK", "transfer": {"id": 78}})

    # music routes to the single root-level unclassified folder; the multi-level
    # plex/* paths are covered by test_normal_routing_is_unchanged.
    services.send_download(
        adult_user,
        info_hash="c" * 40,
        title="Some Album",
        magnet_uri=MAGNET,
        content_type="music",
    )

    row = DownloadRequest.objects.get()
    assert row.title == "Some Album"
    assert row.putio_transfer_id == 78


# --- search isolation ------------------------------------------------------


@responses.activate
def test_family_search_query_excludes_adult_content(client, plain_user):
    responses.post(GRAPHQL_URL, json=search_payload([make_item()]))
    client.get(reverse("search"), {"q": "westworld"})
    sent = responses.calls[0].request.body.decode()
    assert '"xxx"' not in sent


@responses.activate
def test_adult_search_query_requests_only_adult_content(client, adult_user):
    responses.post(GRAPHQL_URL, json=search_payload([make_item()]))
    client.get(reverse("adult_search"), {"q": "anything"})
    sent = responses.calls[0].request.body.decode()
    assert '"filter": ["xxx"]' in sent.replace("'", '"')


# --- history isolation -----------------------------------------------------


def _make_adult_row(user, settings):
    return DownloadRequest.objects.create(
        user=user,
        info_hash="e" * 40,
        title="Some Adult Title That Must Not Leak",
        magnet_uri=MAGNET,
        destination=settings.PUTIO_ADULT_FOLDER,
    )


def _make_family_row(user, settings):
    return DownloadRequest.objects.create(
        user=user,
        info_hash="f" * 40,
        title="Westworld S01",
        magnet_uri=MAGNET,
        destination=f"{settings.PUTIO_BASE_FOLDER}/tv_series",
    )


def test_history_hides_adult_sends_from_the_rest_of_the_family(client, plain_user, settings):
    """Defence in depth: adult sends are no longer logged, but if a row exists
    (written before that change, or by a future regression) it must not surface
    on a page the whole family reads."""
    _make_adult_row(plain_user, settings)
    _make_family_row(plain_user, settings)

    body = client.get(reverse("history")).content.decode()
    assert "Some Adult Title That Must Not Leak" not in body
    assert "Westworld S01" in body


def test_history_shows_adult_sends_to_permitted_users(client, adult_user, settings):
    _make_adult_row(adult_user, settings)
    _make_family_row(adult_user, settings)

    body = client.get(reverse("history")).content.decode()
    assert "Some Adult Title That Must Not Leak" in body
    assert "Westworld S01" in body


def test_adult_destination_tracks_the_routing_rules(settings):
    """The history filter must keep matching wherever adult sends actually go."""
    assert services.adult_destination() == "/".join(services.destination_folders("xxx"))
