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


def search(search_input: dict[str, Any]) -> dict[str, Any]:
    """Run a torrentContent search and return the result payload.

    Returns the dict at data.torrentContent.search, with each item's
    publishedAt parsed into a datetime for template rendering.
    """
    try:
        response = requests.post(
            f"{settings.BITMAGNET_URL}/graphql",
            json={"query": SEARCH_QUERY, "variables": {"input": search_input}},
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

    result = payload["data"]["torrentContent"]["search"]
    for item in result["items"]:
        published_at = item.get("publishedAt")
        if published_at:
            item["publishedAt"] = datetime.fromisoformat(published_at)
    return result
