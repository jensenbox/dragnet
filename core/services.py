"""The single code path for sending a torrent to put.io.

Both the web UI and the JSON API go through send_download() so that folder
routing, duplicate detection, and history recording can never diverge.
"""

import re
from dataclasses import dataclass

from django.conf import settings

from . import putio
from .bitmagnet import ADULT_CONTENT_TYPE
from .models import DownloadRequest


@dataclass(frozen=True)
class SendResult:
    """What a caller needs to report a send, independent of whether it was logged.

    Adult sends deliberately leave no DownloadRequest behind, so there is no row
    to hand back. Returning this for *every* send keeps both paths identical for
    callers, and means no caller ever holds a model instance it could persist by
    accident — which is the whole point for the adult path.
    """

    title: str
    destination: str
    putio_transfer_id: int | None


class AdultContentNotPermitted(Exception):
    """This user may not send adult content."""


class DuplicateDownload(Exception):
    """This info hash was already sent; .existing is the prior DownloadRequest."""

    def __init__(self, existing: DownloadRequest):
        self.existing = existing
        super().__init__(
            f"already sent to put.io by {existing.user.username} on {existing.created_at:%Y-%m-%d}"
        )


def person_folder(user) -> str:
    """The folder segment naming whoever asked for a download.

    Cloudflare Access provisions family accounts with the email address as the
    username, so the local part is both readable and stable — kanequinton, not
    kanequinton@gmail.com. Falls back to the username for accounts with no email
    (the API's `claude` user), and to the pk if neither yields anything a folder
    name can safely be made of.
    """
    source = (getattr(user, "email", "") or getattr(user, "username", "") or "").strip()
    local = source.split("@")[0]
    # put.io folder names are free-form; keep them boring so they stay easy to
    # type on the server and can't introduce a path separator.
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", local).strip("-.")
    return slug or f"user-{user.pk}"


def destination_folders(content_type: str, user) -> list[str]:
    """put.io folder path for a bitmagnet content type — the routing rules.

    Classified movies/TV go under the base (rclone-watched) folder. Everything
    else goes to root-level folders that rclone does NOT ship to the server, so
    neither can reach the family Plex library.

    Unclassified content — ebooks, audiobooks, comics, music, software, games,
    and anything bitmagnet could not type — is additionally split per person, so
    it is obvious whose book is whose without reading titles.

    `user` is required rather than optional on purpose: a caller that forgot it
    would silently fall back to the old shared folder, which is exactly the bug
    this routing exists to prevent. Adult is the one path that ignores it — see
    below.
    """
    if content_type == ADULT_CONTENT_TYPE:
        # Deliberately NOT per person. Adult sends leave no database row (see
        # send_download); giving them a per-person folder would rebuild that
        # same record on put.io, where the whole family can see it.
        return [settings.PUTIO_ADULT_FOLDER]
    subfolder = settings.PUTIO_CONTENT_TYPE_FOLDERS.get(content_type)
    if subfolder:
        return [settings.PUTIO_BASE_FOLDER, subfolder]
    return [settings.PUTIO_UNCLASSIFIED_FOLDER, person_folder(user)]


def adult_destination() -> str:
    """The stored `destination` of an adult send.

    History identifies adult rows by where they were routed. Deriving it from
    destination_folders() keeps the two from drifting apart if the routing rules
    change. Passing user=None is safe because the adult path returns before the
    per-person segment is reached.
    """
    return "/".join(destination_folders(ADULT_CONTENT_TYPE, None))


def send_download(
    user,
    *,
    info_hash: str,
    title: str,
    magnet_uri: str,
    content_type: str = "",
    size: int | None = None,
    force: bool = False,
) -> SendResult:
    """Send a magnet to put.io and, for everything but adult content, record it.

    Raises DuplicateDownload if already sent (unless force), PutioError on
    transfer failure (after recording a FAILED row), and AdultContentNotPermitted
    if the user lacks the adult permission. The permission is enforced here
    rather than only in the view so the web UI and the JSON API can't diverge.

    Adult sends write nothing to the database — no row on success, no row on
    failure, not even the title. Two behaviours follow directly from keeping no
    record, and are intended rather than missing: adult content has no duplicate
    detection (sending the same torrent twice makes two put.io transfers, and
    the API never answers 409 for it), and the adult section never shows an
    "already sent" badge.
    """
    is_adult = content_type == ADULT_CONTENT_TYPE

    # Refuse before anything reaches put.io.
    if is_adult and not user.has_perm("core.view_adult_content"):
        raise AdultContentNotPermitted("you do not have permission to send adult content")

    # An adult send leaves no row, so there is never one to find. Skipping the
    # query outright also keeps the adult path from matching — and reporting —
    # a *family* row that happens to share an info hash.
    if not force and not is_adult:
        existing = (
            DownloadRequest.objects.filter(info_hash=info_hash, status=DownloadRequest.Status.SENT)
            .select_related("user")
            .first()
        )
        if existing:
            raise DuplicateDownload(existing)

    folder_names = destination_folders(content_type, user)
    destination = "/".join(folder_names)

    try:
        parent_id = putio.resolve_folder_path(folder_names)
        transfer = putio.add_transfer(magnet_uri, save_parent_id=parent_id)
    except putio.PutioError as exc:
        if not is_adult:
            DownloadRequest.objects.create(
                user=user,
                info_hash=info_hash,
                title=title,
                size=size,
                magnet_uri=magnet_uri,
                destination=destination,
                content_type=content_type,
                status=DownloadRequest.Status.FAILED,
                error=str(exc),
            )
        raise

    if not is_adult:
        DownloadRequest.objects.create(
            user=user,
            info_hash=info_hash,
            title=title,
            size=size,
            magnet_uri=magnet_uri,
            destination=destination,
            content_type=content_type,
            putio_transfer_id=transfer.get("id"),
            status=DownloadRequest.Status.SENT,
        )

    return SendResult(
        title=title,
        destination=destination,
        putio_transfer_id=transfer.get("id"),
    )
