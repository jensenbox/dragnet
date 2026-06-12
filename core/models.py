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
    putio_transfer_id = models.BigIntegerField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SENT)
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} ({self.get_status_display()})"
