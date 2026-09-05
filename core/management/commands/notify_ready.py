"""Resolve finished put.io transfers and tell the person who asked for them.

Runs on a cron; dragnet has no in-process scheduler. Two jobs, deliberately
separable:

1. Resolve the downloadable file for finished transfers, which is what makes a
   Download button appear in History. This happens whether or not email is
   configured.
2. Email the requester once, if email is configured and they have an address.

Adult sends never appear here because they leave no DownloadRequest at all.
"""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core import mail, putio
from core.models import DownloadRequest

# put.io drops old transfers and files, so rows past this age will never
# resolve and there is no point asking about them every hour, forever.
MAX_AGE = timedelta(days=30)


def _brief(exc: Exception) -> str:
    """put.io returns a 500-character body on a 404; an hourly cron printing
    that for every purged transfer buries the lines that matter."""
    text = str(exc)
    return "gone from put.io (404)" if "404" in text else text[:120]


SUBJECT = "Your download is ready: {title}"

TEXT = """{title} has finished downloading.

Download it here:
  {url}

You'll be asked to sign in the same way you do for Dragnet. Once you have the
file, use Amazon's Send to Kindle app to get it onto your Kindle.
"""

HTML = """<p><strong>{title}</strong> has finished downloading.</p>
<p><a href="{url}">Download it</a></p>
<p>You'll be asked to sign in the same way you do for Dragnet. Once you have
the file, use Amazon's Send to Kindle app to get it onto your Kindle.</p>
"""


class Command(BaseCommand):
    help = "Resolve finished put.io transfers and email whoever requested them."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Resolve and report, but write nothing and send nothing.",
        )
        parser.add_argument(
            "--resolve-only",
            action="store_true",
            help=(
                "Record the downloadable file for finished transfers but send no "
                "email. Use it before enabling the cron so a backlog of already-"
                "finished downloads doesn't mail everyone at once."
            ),
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        resolve_only = options["resolve_only"]
        cutoff = timezone.now() - MAX_AGE
        pending = (
            DownloadRequest.objects.filter(
                status=DownloadRequest.Status.SENT,
                putio_file_id=None,
                created_at__gte=cutoff,
            )
            .exclude(putio_transfer_id=None)
            .select_related("user")
        )

        resolved = notified = skipped = 0
        for row in pending:
            try:
                transfer = putio.get_transfer(row.putio_transfer_id)
            except putio.PutioError as exc:
                # A purged transfer 404s here. Nothing to do but move on.
                self.stderr.write(f"  transfer {row.putio_transfer_id}: {_brief(exc)}")
                skipped += 1
                continue

            if transfer.get("status") not in putio.FINISHED_STATUSES:
                continue
            file_id = transfer.get("file_id")
            if not file_id:
                continue

            try:
                chosen = putio.pick_downloadable_file(file_id)
            except putio.PutioError as exc:
                # A deleted file 404s here.
                self.stderr.write(f"  file {file_id}: {_brief(exc)}")
                skipped += 1
                continue
            if not chosen:
                skipped += 1
                continue

            self.stdout.write(f"  ready: {row.title[:60]} -> {chosen['name'][:50]}")
            resolved += 1
            if not dry_run:
                row.putio_file_id = chosen["id"]
                row.save(update_fields=["putio_file_id"])

            if not resolve_only and self._notify(row, dry_run=dry_run):
                notified += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"resolved={resolved} notified={notified} skipped={skipped}"
                + (" (dry run, nothing written)" if dry_run else "")
                + (" (resolve-only, no email sent)" if resolve_only else "")
            )
        )

    def _notify(self, row, *, dry_run: bool) -> bool:
        if row.notified_at or not row.user.email or not mail.is_configured():
            return False
        url = f"{settings.DRAGNET_PUBLIC_URL.rstrip('/')}/files/{row.pk}/"
        if dry_run:
            self.stdout.write(f"    would email {row.user.email}")
            return True
        try:
            mail.send(
                to=row.user.email,
                subject=SUBJECT.format(title=row.title[:120]),
                text_body=TEXT.format(title=row.title, url=url),
                html_body=HTML.format(title=row.title, url=url),
            )
        except mail.EmailError as exc:
            # Leave notified_at unset so the next run retries.
            self.stderr.write(f"    email to {row.user.email} failed: {exc}")
            return False
        row.notified_at = timezone.now()
        row.save(update_fields=["notified_at"])
        return True
