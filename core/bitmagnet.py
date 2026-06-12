"""Thin GraphQL client for the bitmagnet torrent index."""

from datetime import datetime
from typing import Any

import requests
from django.conf import settings

PAGE_SIZE = 25

# Sort options exposed in the UI, mapped to bitmagnet orderBy fields.
# name sorts ascending; everything else descending.
ORDER_FIELDS = {
    "seeders": "seeders",
    "newest": "published_at",
    "size": "size",
    "name": "name",
    "relevance": "relevance",
}

CONTENT_TYPES = [
    ("movie", "Movies"),
    ("tv_show", "TV Shows"),
    ("music", "Music"),
    ("audiobook", "Audiobooks"),
    ("ebook", "Ebooks"),
    ("software", "Software"),
    ("game", "Games"),
]

VIDEO_RESOLUTIONS = [
    ("V2160p", "2160p (4K)"),
    ("V1440p", "1440p"),
    ("V1080p", "1080p"),
    ("V720p", "720p"),
    ("V480p", "480p"),
]

VIDEO_SOURCES = [
    ("BluRay", "BluRay"),
    ("WEBDL", "WEB-DL"),
    ("WEBRip", "WEBRip"),
    ("DVD", "DVD"),
    ("TV", "TV"),
]

SEARCH_QUERY = """
query Search($input: TorrentContentSearchQueryInput!) {
  torrentContent {
    search(input: $input) {
      totalCount
      hasNextPage
      items {
        infoHash
        title
        contentType
        videoResolution
        videoSource
        videoCodec
        releaseGroup
        seeders
        leechers
        publishedAt
        languages { name }
        episodes { label }
        content { title releaseYear }
        torrent { name size filesCount magnetUri }
      }
    }
  }
}
"""


STATUS_QUERY = """
query Status($since: DateTime!) {
  torrentContent {
    search(input: {limit: 0, totalCount: true, cached: true}) {
      totalCount
      totalCountIsEstimate
    }
  }
  torrent {
    metrics(input: {bucketDuration: hour, startTime: $since}) {
      buckets {
        bucket
        updated
        count
      }
    }
  }
  queue {
    jobs(
      input: {
        statuses: [pending, retry]
        limit: 0
        totalCount: true
        facets: {queue: {aggregate: true}}
      }
    ) {
      totalCount
      aggregations {
        queue {
          label
          count
        }
      }
    }
  }
}
"""


class BitmagnetError(Exception):
    """The bitmagnet GraphQL API returned an error or was unreachable."""


def build_search_input(
    query: str = "",
    content_type: str = "",
    resolution: str = "",
    video_source: str = "",
    year: str = "",
    order: str = "seeders",
    page: int = 1,
) -> dict[str, Any]:
    facets: dict[str, Any] = {}
    if content_type:
        facets["contentType"] = {"filter": [content_type]}
    if resolution:
        facets["videoResolution"] = {"filter": [resolution]}
    if video_source:
        facets["videoSource"] = {"filter": [video_source]}
    if year:
        facets["releaseYear"] = {"filter": [int(year)]}

    # Relevance ordering needs a query string to rank against.
    if order == "relevance" and not query:
        order = "newest"
    order_field = ORDER_FIELDS.get(order, "seeders")

    search_input: dict[str, Any] = {
        "queryString": query or None,
        "limit": PAGE_SIZE,
        "offset": (page - 1) * PAGE_SIZE,
        "totalCount": True,
        "hasNextPage": True,
        "orderBy": [{"field": order_field, "descending": order_field != "name"}],
    }
    if facets:
        search_input["facets"] = facets
    return search_input


def execute(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    """POST a GraphQL document to bitmagnet and return the data payload."""
    try:
        response = requests.post(
            f"{settings.BITMAGNET_URL}/graphql",
            json={"query": query, "variables": variables},
            timeout=30,
        )
    except requests.RequestException as exc:
        raise BitmagnetError(f"bitmagnet is unreachable: {exc}") from exc
    if response.status_code != 200:
        raise BitmagnetError(
            f"bitmagnet returned HTTP {response.status_code}: {response.text[:500]}"
        )
    payload = response.json()
    if payload.get("errors"):
        raise BitmagnetError(f"bitmagnet GraphQL errors: {payload['errors']}")
    return payload["data"]


def search(search_input: dict[str, Any]) -> dict[str, Any]:
    """Run a torrentContent search and return the result payload.

    Returns the dict at data.torrentContent.search, with each item's
    publishedAt parsed into a datetime for template rendering.
    """
    data = execute(SEARCH_QUERY, {"input": search_input})
    result = data["torrentContent"]["search"]
    for item in result["items"]:
        published_at = item.get("publishedAt")
        if published_at:
            item["publishedAt"] = datetime.fromisoformat(published_at)
    return result


def status(since: datetime) -> dict[str, Any]:
    """Crawler health snapshot: index size, hourly ingest, queue backlog.

    Returns hourly buckets of *new* torrents (updated=false) summed across
    sources, ordered oldest-first, alongside index totals and the pending+
    retry queue backlog per queue.
    """
    data = execute(STATUS_QUERY, {"since": since.isoformat()})

    hourly: dict[datetime, int] = {}
    for bucket in data["torrent"]["metrics"]["buckets"]:
        if bucket["updated"]:
            continue
        bucket_time = datetime.fromisoformat(bucket["bucket"])
        hourly[bucket_time] = hourly.get(bucket_time, 0) + bucket["count"]
    hourly_series = [{"bucket": b, "count": c} for b, c in sorted(hourly.items())]
    max_hourly = max((point["count"] for point in hourly_series), default=0)
    for point in hourly_series:
        point["percent"] = round(100 * point["count"] / max_hourly) if max_hourly else 0

    queue_jobs = data["queue"]["jobs"]
    search_result = data["torrentContent"]["search"]
    return {
        "totalTorrents": search_result["totalCount"],
        "totalIsEstimate": search_result["totalCountIsEstimate"],
        "hourly": hourly_series,
        "lastHour": hourly_series[-1]["count"] if hourly_series else 0,
        "last24h": sum(point["count"] for point in hourly_series),
        "queueBacklog": queue_jobs["totalCount"],
        "backlogByQueue": queue_jobs["aggregations"]["queue"] or [],
    }
