"""The single code path for sending a torrent to put.io.

Both the web UI and the JSON API go through send_download() so that folder
routing, duplicate detection, and history recording can never diverge.
"""

from django.conf import settings

from . import putio
from .models import DownloadRequest


class DuplicateDownload(Exception):
    """This info hash was already sent; .existing is the prior DownloadRequest."""

    def __init__(self, existing: DownloadRequest):
        self.existing = existing
        super().__init__(
            f"already sent to put.io by {existing.user.username} on {existing.created_at:%Y-%m-%d}"
        )


def destination_folders(content_type: str) -> list[str]:
    """put.io folder path for a bitmagnet content type — the routing rules.

    Classified movies/TV go under the base (rclone-watched) folder; anything
    else goes to a root-level folder that rclone does NOT ship to the server.
    """
    subfolder = settings.PUTIO_CONTENT_TYPE_FOLDERS.get(content_type)
    if subfolder:
        return [settings.PUTIO_BASE_FOLDER, subfolder]
    return [settings.PUTIO_UNCLASSIFIED_FOLDER]


def send_download(
    user,
    *,
    info_hash: str,
    title: str,
    magnet_uri: str,
    content_type: str = "",
    size: int | None = None,
    force: bool = False,
) -> DownloadRequest:
    """Send a magnet to put.io and record it.

    Raises DuplicateDownload if already sent (unless force), and PutioError
    on transfer failure — after recording a FAILED row.
    """
    if not force:
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

    return DownloadRequest.objects.create(
        user=user,
        info_hash=info_hash,
        title=title,
        size=size,
        magnet_uri=magnet_uri,
        destination=destination,
        putio_transfer_id=transfer.get("id"),
        status=DownloadRequest.Status.SENT,
    )
