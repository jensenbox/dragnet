from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import bitmagnet, putio, services
from .models import DownloadRequest


@login_required
def search(request):
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


@require_POST
@login_required
def download(request):
    info_hash = request.POST.get("info_hash", "")
    title = request.POST.get("title", "")
    magnet_uri = request.POST.get("magnet_uri", "")
    content_type = request.POST.get("content_type", "")
    size = request.POST.get("size") or None
    next_url = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = "search"

    if not magnet_uri.startswith("magnet:?") or not info_hash or not title:
        messages.error(request, "Invalid download request.")
        return redirect(next_url)

    try:
        download_request = services.send_download(
            request.user,
            info_hash=info_hash,
            title=title,
            magnet_uri=magnet_uri,
            content_type=content_type,
            size=size,
            force=bool(request.POST.get("force")),
        )
    except services.DuplicateDownload as exc:
        messages.warning(
            request,
            f"“{exc.existing.title}” was {exc}. Use the re-send button to send it again.",
        )
        return redirect(next_url)
    except putio.PutioError as exc:
        messages.error(request, f"put.io rejected the transfer: {exc}")
        return redirect(next_url)

    messages.success(request, f"Sent “{title}” to put.io → {download_request.destination}/")
    return redirect(next_url)


@login_required
def status(request):
    crawler = None
    error = None
    try:
        crawler = bitmagnet.status(since=timezone.now() - timedelta(hours=24))
    except bitmagnet.BitmagnetError as exc:
        error = str(exc)
    dashboard_url = f"http://{request.get_host().split(':')[0]}:3333"
    return render(
        request,
        "core/status.html",
        {"crawler": crawler, "error": error, "dashboard_url": dashboard_url},
    )


@login_required
def history(request):
    requests_list = DownloadRequest.objects.select_related("user").all()[:500]
    return render(request, "core/history.html", {"download_requests": requests_list})
