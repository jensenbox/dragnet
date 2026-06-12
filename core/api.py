"""Token-authenticated JSON API for programmatic sends (e.g. Claude Code).

Goes through the same core.services.send_download() path as the web UI, so
folder routing, dedupe, and history recording are identical.
"""

import json
import secrets

from django.conf import settings
from django.contrib.auth.models import User
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import putio, services


def _authenticate(request) -> JsonResponse | None:
    configured = settings.DRAGNET_API_TOKEN
    if not configured:
        return JsonResponse({"error": "DRAGNET_API_TOKEN is not configured"}, status=503)
    provided = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not provided or not secrets.compare_digest(provided, configured):
        return JsonResponse({"error": "invalid or missing bearer token"}, status=401)
    return None


def _api_user() -> User:
    user, created = User.objects.get_or_create(username=settings.DRAGNET_API_USERNAME)
    if created:
        user.set_unusable_password()
        user.save()
    return user


@csrf_exempt
@require_POST
def download(request):
    auth_error = _authenticate(request)
    if auth_error:
        return auth_error

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "request body must be JSON"}, status=400)

    info_hash = body.get("info_hash", "")
    title = body.get("title", "")
    magnet_uri = body.get("magnet_uri", "")
    if not magnet_uri.startswith("magnet:?") or not info_hash or not title:
        return JsonResponse(
            {"error": "info_hash, title and a magnet_uri starting with magnet:? are required"},
            status=400,
        )

    try:
        download_request = services.send_download(
            _api_user(),
            info_hash=info_hash,
            title=title,
            magnet_uri=magnet_uri,
            content_type=body.get("content_type", ""),
            size=body.get("size"),
            force=bool(body.get("force")),
        )
    except services.DuplicateDownload as exc:
        return JsonResponse(
            {
                "status": "duplicate",
                "title": exc.existing.title,
                "sent_by": exc.existing.user.username,
                "sent_at": exc.existing.created_at.isoformat(),
                "destination": exc.existing.destination,
                "hint": "pass force=true to re-send",
            },
            status=409,
        )
    except putio.PutioError as exc:
        return JsonResponse({"status": "failed", "error": str(exc)}, status=502)

    return JsonResponse(
        {
            "status": "sent",
            "title": download_request.title,
            "destination": download_request.destination,
            "putio_transfer_id": download_request.putio_transfer_id,
        },
        status=201,
    )
