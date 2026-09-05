"""The single code path for sending a torrent to put.io.

Both the web UI and the JSON API go through send_download() so that folder
routing, duplicate detection, and history recording can never diverge.
"""

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


def destination_folders(content_type: str) -> list[str]:
    """put.io folder path for a bitmagnet content type — the routing rules.

    Classified movies/TV go under the base (rclone-watched) folder; adult and
    anything else go to root-level folders that rclone does NOT ship to the
    server, so neither can reach the family Plex library.
    """
    if content_type == ADULT_CONTENT_TYPE:
        return [settings.PUTIO_ADULT_FOLDER]
    subfolder = settings.PUTIO_CONTENT_TYPE_FOLDERS.get(content_type)
    if subfolder:
        return [settings.PUTIO_BASE_FOLDER, subfolder]
    return [settings.PUTIO_UNCLASSIFIED_FOLDER]


def adult_destination() -> str:
    """The stored `destination` of an adult send.

    History has no content type to filter on, so it identifies adult rows by
    where they were routed. Deriving it from destination_folders() keeps the two
    from drifting apart if the routing rules change.
    """
    return "/".join(destination_folders(ADULT_CONTENT_TYPE))


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

    folder_names = destination_folders(content_type)
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
            putio_transfer_id=transfer.get("id"),
            status=DownloadRequest.Status.SENT,
        )

    return SendResult(
        title=title,
        destination=destination,
        putio_transfer_id=transfer.get("id"),
    )
