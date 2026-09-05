"""Resolving finished transfers, the download link, and the "ready" email.

The two put.io failure modes here are not hypothetical: probing the live
account on 2026-09-05 found one row whose file had been deleted (GET
/files/{id} 404s) and one whose transfer had been purged from put.io's history
(GET /transfers/{id} 404s). Both must leave the row alone rather than crash the
cron for every row behind it.
"""

import pytest
import responses
from django.contrib.auth.models import Permission, User
from django.core.management import call_command
from django.urls import reverse

from core import mail, putio
from core.models import DownloadRequest
from core.tests.test_views import MAGNET

pytestmark = pytest.mark.django_db

TRANSFER_ID = 555
FOLDER_ID = 900
FILE_ID = 901


@pytest.fixture
def requester():
    return User.objects.create_user("kane", email="kane@example.com", password="pw")


@pytest.fixture
def row(requester):
    return DownloadRequest.objects.create(
        user=requester,
        info_hash="a" * 40,
        title="Red Sky Mourning",
        magnet_uri=MAGNET,
        destination="unclassified/kane",
        content_type="ebook",
        putio_transfer_id=TRANSFER_ID,
    )


def _mock_finished_transfer(status="SEEDING", files=None):
    """put.io wraps single-file torrents in a folder, so the transfer's file_id
    is a FOLDER and has to be walked."""
    responses.get(
        putio.TRANSFER_URL.format(id=TRANSFER_ID),
        json={"status": "OK", "transfer": {"status": status, "file_id": FOLDER_ID}},
    )
    responses.get(
        putio.FILE_URL.format(id=FOLDER_ID),
        json={"status": "OK", "file": {"id": FOLDER_ID, "file_type": "FOLDER", "name": "pack"}},
    )
    responses.get(
        putio.FILES_LIST_URL,
        json={
            "status": "OK",
            "files": files
            if files is not None
            else [
                {
                    "id": FILE_ID,
                    "file_type": "FILE",
                    "name": "Red Sky Mourning.epub",
                    "size": 5_242_000,
                },
            ],
        },
    )


def _configure_email(settings):
    settings.TELNYX_API_KEY = "test-key"
    settings.TELNYX_EMAIL_FROM = "dragnet@mail.closient.com"
    settings.DRAGNET_PUBLIC_URL = "https://dragnet.example.com"


# --- the picker ------------------------------------------------------------


@responses.activate
def test_picker_descends_the_folder_and_prefers_a_readable_format(settings):
    """The EPUB matters; the cover art and readme shipped beside it do not."""
    settings.PUTIO_OAUTH_TOKEN = "t"
    responses.get(
        putio.FILE_URL.format(id=FOLDER_ID),
        json={"status": "OK", "file": {"id": FOLDER_ID, "file_type": "FOLDER", "name": "pack"}},
    )
    responses.get(
        putio.FILES_LIST_URL,
        json={
            "status": "OK",
            "files": [
                {"id": 1, "file_type": "FILE", "name": "cover.jpg", "size": 9_000_000},
                {"id": 2, "file_type": "FILE", "name": "readme.txt", "size": 100},
                {"id": 3, "file_type": "FILE", "name": "book.epub", "size": 4_000_000},
            ],
        },
    )
    assert putio.pick_downloadable_file(FOLDER_ID)["id"] == 3


@responses.activate
def test_picker_falls_back_to_the_largest_file(settings):
    """No readable format present — a video or a lone archive. Biggest wins."""
    settings.PUTIO_OAUTH_TOKEN = "t"
    responses.get(
        putio.FILE_URL.format(id=FOLDER_ID),
        json={"status": "OK", "file": {"id": FOLDER_ID, "file_type": "FOLDER", "name": "pack"}},
    )
    responses.get(
        putio.FILES_LIST_URL,
        json={
            "status": "OK",
            "files": [
                {"id": 1, "file_type": "FILE", "name": "sample.mkv", "size": 1_000},
                {"id": 2, "file_type": "FILE", "name": "movie.mkv", "size": 9_000_000},
            ],
        },
    )
    assert putio.pick_downloadable_file(FOLDER_ID)["id"] == 2


# --- notify_ready ----------------------------------------------------------


@responses.activate
def test_notify_ready_resolves_the_file_and_emails_once(row, settings):
    settings.PUTIO_OAUTH_TOKEN = "t"
    _configure_email(settings)
    _mock_finished_transfer()
    responses.post(mail.SEND_URL, json={"data": {"id": "msg-1"}}, status=202)

    call_command("notify_ready")
    row.refresh_from_db()
    assert row.putio_file_id == FILE_ID
    assert row.notified_at is not None

    sent = [c for c in responses.calls if c.request.url == mail.SEND_URL]
    assert len(sent) == 1
    assert "https://dragnet.example.com/files/" in sent[0].request.body.decode()

    # A second run must not mail the same person again.
    call_command("notify_ready")
    assert len([c for c in responses.calls if c.request.url == mail.SEND_URL]) == 1


@responses.activate
def test_unfinished_transfer_is_left_alone(row, settings):
    settings.PUTIO_OAUTH_TOKEN = "t"
    _configure_email(settings)
    responses.get(
        putio.TRANSFER_URL.format(id=TRANSFER_ID),
        json={"status": "OK", "transfer": {"status": "DOWNLOADING", "file_id": None}},
    )
    call_command("notify_ready")
    row.refresh_from_db()
    assert row.putio_file_id is None
    assert row.notified_at is None


@responses.activate
def test_a_purged_transfer_does_not_crash_the_run(row, settings):
    """GET /transfers/{id} 404s once put.io drops it from history."""
    settings.PUTIO_OAUTH_TOKEN = "t"
    responses.get(putio.TRANSFER_URL.format(id=TRANSFER_ID), json={"status": "ERROR"}, status=404)
    call_command("notify_ready")
    row.refresh_from_db()
    assert row.putio_file_id is None


@responses.activate
def test_a_deleted_file_does_not_crash_the_run(row, settings):
    """GET /files/{id} 404s once the file itself is gone."""
    settings.PUTIO_OAUTH_TOKEN = "t"
    responses.get(
        putio.TRANSFER_URL.format(id=TRANSFER_ID),
        json={"status": "OK", "transfer": {"status": "COMPLETED", "file_id": FOLDER_ID}},
    )
    responses.get(putio.FILE_URL.format(id=FOLDER_ID), json={"status": "ERROR"}, status=404)
    call_command("notify_ready")
    row.refresh_from_db()
    assert row.putio_file_id is None


@responses.activate
def test_resolving_still_happens_when_email_is_not_configured(row, settings):
    """The Download button must appear even with no mail set up."""
    settings.PUTIO_OAUTH_TOKEN = "t"
    settings.TELNYX_API_KEY = ""
    settings.TELNYX_EMAIL_FROM = ""
    _mock_finished_transfer()
    call_command("notify_ready")
    row.refresh_from_db()
    assert row.putio_file_id == FILE_ID
    assert row.notified_at is None


@responses.activate
def test_a_failed_send_leaves_notified_at_unset_so_it_retries(row, settings):
    settings.PUTIO_OAUTH_TOKEN = "t"
    _configure_email(settings)
    _mock_finished_transfer()
    responses.post(mail.SEND_URL, json={"errors": [{"detail": "nope"}]}, status=422)
    call_command("notify_ready")
    row.refresh_from_db()
    assert row.putio_file_id == FILE_ID
    assert row.notified_at is None


@responses.activate
def test_dry_run_writes_nothing(row, settings):
    settings.PUTIO_OAUTH_TOKEN = "t"
    _configure_email(settings)
    _mock_finished_transfer()
    call_command("notify_ready", "--dry-run")
    row.refresh_from_db()
    assert row.putio_file_id is None
    assert row.notified_at is None
    assert not [c for c in responses.calls if c.request.url == mail.SEND_URL]


def test_a_user_with_no_email_is_never_mailed(settings):
    """The API's `claude` user has no address."""
    api_user = User.objects.create_user("claude", email="")
    row = DownloadRequest.objects.create(
        user=api_user,
        info_hash="b" * 40,
        title="x",
        magnet_uri=MAGNET,
        destination="unclassified/claude",
        putio_transfer_id=None,
    )
    _configure_email(settings)
    call_command("notify_ready")
    row.refresh_from_db()
    assert row.notified_at is None


# --- the download link -----------------------------------------------------


def test_download_requires_login(client, row):
    response = client.get(reverse("file_download", args=[row.pk]))
    assert response.status_code == 302
    assert "/accounts/login/" in response["Location"]


@responses.activate
def test_download_redirects_to_a_signed_putio_url(client, row, requester, settings):
    settings.PUTIO_OAUTH_TOKEN = "t"
    row.putio_file_id = FILE_ID
    row.save()
    client.force_login(requester)
    responses.get(
        putio.FILE_DOWNLOAD_URL.format(id=FILE_ID),
        json={"status": "OK", "url": "https://s1.put.io/download/901?u=sig"},
    )
    response = client.get(reverse("file_download", args=[row.pk]))
    assert response.status_code == 302
    assert response["Location"] == "https://s1.put.io/download/901?u=sig"


def test_download_of_an_unresolved_row_redirects_with_a_message(client, row, requester):
    client.force_login(requester)
    response = client.get(reverse("file_download", args=[row.pk]), follow=True)
    assert b"isn&#x27;t ready yet" in response.content


@responses.activate
def test_download_reports_a_putio_failure_rather_than_500ing(client, row, requester, settings):
    settings.PUTIO_OAUTH_TOKEN = "t"
    row.putio_file_id = FILE_ID
    row.save()
    client.force_login(requester)
    responses.get(putio.FILE_DOWNLOAD_URL.format(id=FILE_ID), json={"status": "ERROR"}, status=404)
    response = client.get(reverse("file_download", args=[row.pk]), follow=True)
    assert b"could not provide that file" in response.content


def test_download_hides_legacy_adult_rows_from_the_family(client, requester, settings):
    """Adult sends write no row at all now, but a legacy one must not be
    fetchable by someone without the permission."""
    adult_row = DownloadRequest.objects.create(
        user=requester,
        info_hash="c" * 40,
        title="secret",
        magnet_uri=MAGNET,
        destination=settings.PUTIO_ADULT_FOLDER,
        putio_file_id=FILE_ID,
    )
    client.force_login(requester)
    assert client.get(reverse("file_download", args=[adult_row.pk])).status_code == 404

    requester.user_permissions.add(Permission.objects.get(codename="view_adult_content"))
    requester = User.objects.get(pk=requester.pk)  # drop the cached permissions
    client.force_login(requester)
    # Now visible: it gets past the 404 and fails on put.io instead.
    assert client.get(reverse("file_download", args=[adult_row.pk])).status_code != 404


@responses.activate
def test_catch_up_settles_the_backlog_without_sending(row, settings):
    """Run once before enabling the cron. It must also mark the rows notified:
    resolving alone would leave notified_at null and the very next run would
    mail everyone about books they got days ago — the thing the flag exists to
    prevent."""
    settings.PUTIO_OAUTH_TOKEN = "t"
    _configure_email(settings)
    _mock_finished_transfer()
    call_command("notify_ready", "--catch-up")
    row.refresh_from_db()
    assert row.putio_file_id == FILE_ID
    assert row.notified_at is not None
    assert not [c for c in responses.calls if c.request.url == mail.SEND_URL]

    # The follow-up run — the one the cron would do — must stay silent.
    responses.post(mail.SEND_URL, json={"data": {"id": "x"}}, status=202)
    call_command("notify_ready")
    assert not [c for c in responses.calls if c.request.url == mail.SEND_URL]
