from django.conf import settings
from django.db import models


class DownloadRequest(models.Model):
    """A torrent a family member sent to put.io."""

    class Status(models.TextChoices):
        SENT = "sent", "Sent to put.io"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="download_requests",
    )
    info_hash = models.CharField(max_length=40, db_index=True)
    title = models.CharField(max_length=1024)
    size = models.BigIntegerField(null=True, blank=True)
    magnet_uri = models.TextField()
    destination = models.CharField(max_length=255, blank=True, default="")
    # The bitmagnet contentType this was sent as. Stored because `destination`
    # alone can't answer "who downloaded which book" — every unclassified type
    # shares one folder root, so ebooks, music and games look identical in the
    # log. Blank for rows written before this field existed, and for torrents
    # bitmagnet could not classify.
    content_type = models.CharField(max_length=32, blank=True, default="")
    putio_transfer_id = models.BigIntegerField(null=True, blank=True)
    # The single downloadable file chosen out of the finished transfer. Null
    # until the transfer completes and notify_ready resolves it — put.io wraps
    # even single-file torrents in a folder, so this is not the transfer's own
    # file id. Stays null forever if put.io has since dropped the file.
    putio_file_id = models.BigIntegerField(null=True, blank=True)
    # Set once the "your download is ready" email has gone out, so a re-run of
    # notify_ready cannot mail the same person twice.
    notified_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SENT)
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        permissions = [
            ("view_adult_content", "Can browse and send adult content"),
        ]

    def __str__(self):
        return f"{self.title} ({self.get_status_display()})"
