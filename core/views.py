from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import bitmagnet, putio, services
from .models import DownloadRequest

ADULT_PERMISSION = "core.view_adult_content"


@login_required
def search(request):
    """Family-facing search. Adult content is excluded at the query level."""
    return _render_search(request, adult=False)


@login_required
@permission_required(ADULT_PERMISSION, raise_exception=True)
def adult_search(request):
    """The adult section: a separate URL that only ever returns xxx results."""
    return _render_search(request, adult=True)


def _render_search(request, *, adult: bool):
    query = request.GET.get("q", "").strip()
    content_type = request.GET.get("content_type", "")
    resolution = request.GET.get("resolution", "")
    video_source = request.GET.get("video_source", "")
    year = request.GET.get("year", "").strip()
    order = request.GET.get("order", "seeders")
    try:
        page = max(1, int(request.GET.get("page", "1")))
    except ValueError:
        page = 1
    if not year.isdigit():
        year = ""

    result = None
    error = None
    try:
        search_input = bitmagnet.build_search_input(
            query=query,
            content_type=content_type,
            resolution=resolution,
            video_source=video_source,
            year=year,
            order=order,
            page=page,
            adult=adult,
        )
        result = bitmagnet.search(search_input)
    except bitmagnet.BitmagnetError as exc:
        error = str(exc)

    if result:
        sent = {
            dr.info_hash: dr
            for dr in DownloadRequest.objects.filter(
                info_hash__in=[item["infoHash"] for item in result["items"]],
                status=DownloadRequest.Status.SENT,
            ).select_related("user")
        }
        for item in result["items"]:
            item["sentRequest"] = sent.get(item["infoHash"])

    context = {
        "adult": adult,
        "adult_folder": settings.PUTIO_ADULT_FOLDER,
        "query": query,
        "content_type": content_type,
        "resolution": resolution,
        "video_source": video_source,
        "year": year,
        "order": order,
        "page": page,
        "result": result,
        "error": error,
        "content_types": bitmagnet.CONTENT_TYPES,
        "video_resolutions": bitmagnet.VIDEO_RESOLUTIONS,
        "video_sources": bitmagnet.VIDEO_SOURCES,
        "order_options": [
            ("seeders", "Most seeders"),
            ("newest", "Newest"),
            ("size", "Largest"),
            ("relevance", "Relevance"),
            ("name", "Name"),
        ],
    }
    return render(request, "core/search.html", context)


def _send(request, *, adult: bool):
    """Shared POST handler for both sections; `adult` only picks the redirect target."""

    info_hash = request.POST.get("info_hash", "")
    title = request.POST.get("title", "")
    magnet_uri = request.POST.get("magnet_uri", "")
    content_type = request.POST.get("content_type", "")
    size = request.POST.get("size") or None
    next_url = request.POST.get("next", "")
    fallback = "adult_search" if adult else "search"
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = fallback

    if not magnet_uri.startswith("magnet:?") or not info_hash or not title:
        messages.error(request, "Invalid download request.")
        return redirect(next_url)

    try:
        result = services.send_download(
            request.user,
            info_hash=info_hash,
            title=title,
            magnet_uri=magnet_uri,
            content_type=content_type,
            size=size,
            force=bool(request.POST.get("force")),
        )
    except services.AdultContentNotPermitted:
        messages.error(request, "You do not have permission to send adult content.")
        return redirect(next_url)
    except services.DuplicateDownload as exc:
        messages.warning(
            request,
            f"“{exc.existing.title}” was {exc}. Use the re-send button to send it again.",
        )
        return redirect(next_url)
    except putio.PutioError as exc:
        messages.error(request, f"put.io rejected the transfer: {exc}")
        return redirect(next_url)

    messages.success(request, f"Sent “{title}” to put.io → {result.destination}/")
    return redirect(next_url)


@require_POST
@login_required
def download(request):
    return _send(request, adult=False)


@require_POST
@login_required
@permission_required(ADULT_PERMISSION, raise_exception=True)
def adult_download(request):
    return _send(request, adult=True)


@login_required
def status(request):
    crawler = None
    error = None
    try:
        crawler = bitmagnet.status(since=timezone.now() - timedelta(hours=24))
    except bitmagnet.BitmagnetError as exc:
        error = str(exc)
    # bitmagnet's dashboard has no auth and is LAN-only; deriving it from the
    # request host would render a dead http://dragnet.jensenbox.com:3333 link
    # for anyone arriving through the tunnel. Show it to staff only, and only
    # when an explicit LAN URL is configured.
    dashboard_url = settings.BITMAGNET_DASHBOARD_URL if request.user.is_staff else ""
    return render(
        request,
        "core/status.html",
        {"crawler": crawler, "error": error, "dashboard_url": dashboard_url},
    )


@login_required
def history(request):
    """The shared download log.

    Adult sends are not recorded at all (see services.send_download), so nothing
    routed to the adult folder should ever reach this page. The exclusion below
    stays as defence in depth: it covers rows written before adult sends stopped
    being logged, and any future regression that starts writing them again.

    Filterable by content type and by sender, which together answer the question
    the log exists for: who asked for which book.
    """
    visible = DownloadRequest.objects.select_related("user")
    if not request.user.has_perm(ADULT_PERMISSION):
        visible = visible.exclude(destination=services.adult_destination())

    # Built from everything this viewer may see, deliberately before the filters
    # below — otherwise choosing a sender would collapse the dropdown to that
    # one person and there'd be no way back.
    senders = sorted(set(visible.values_list("user__username", flat=True)))

    content_type = request.GET.get("content_type", "")
    sender = request.GET.get("sender", "")
    rows = visible
    if content_type:
        rows = rows.filter(content_type=content_type)
    if sender:
        rows = rows.filter(user__username=sender)

    return render(
        request,
        "core/history.html",
        {
            "download_requests": rows[:500],
            "content_types": bitmagnet.CONTENT_TYPES,
            "content_type": content_type,
            "senders": senders,
            "sender": sender,
        },
    )
