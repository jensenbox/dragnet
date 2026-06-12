from django.contrib import admin

from .models import DownloadRequest


@admin.register(DownloadRequest)
class DownloadRequestAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "status", "size", "putio_transfer_id", "created_at")
    list_filter = ("status", "user")
    search_fields = ("title", "info_hash")
    readonly_fields = ("created_at",)
