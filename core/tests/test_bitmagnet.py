from datetime import UTC, datetime

import pytest
import responses
from django.conf import settings

from core import bitmagnet

GRAPHQL_URL = f"{settings.BITMAGNET_URL}/graphql"


def make_item(**overrides):
    item = {
        "infoHash": "a" * 40,
        "title": "Westworld S01",
        "contentType": "tv_show",
        "videoResolution": "V2160p",
        "videoSource": "WEBDL",
        "videoCodec": None,
        "releaseGroup": None,
        "seeders": 100,
        "leechers": 5,
        "publishedAt": "2026-01-01T00:00:00Z",
        "languages": [{"name": "English"}],
        "episodes": {"label": "S01"},
        "content": {"title": "Westworld", "releaseYear": 2016},
        "torrent": {
            "name": "Westworld.S01.2160p.WEB-DL",
            "size": 50_000_000_000,
            "filesCount": 10,
            "magnetUri": "magnet:?xt=urn:btih:" + "a" * 40,
        },
    }
    item.update(overrides)
    return item


def search_payload(items, total_count=1, has_next_page=False):
    return {
        "data": {
            "torrentContent": {
                "search": {
                    "totalCount": total_count,
                    "hasNextPage": has_next_page,
                    "items": items,
                }
            }
        }
    }


def test_build_search_input_defaults():
    search_input = bitmagnet.build_search_input(query="westworld")
    assert search_input["queryString"] == "westworld"
    assert search_input["limit"] == bitmagnet.PAGE_SIZE
    assert search_input["offset"] == 0
    assert search_input["orderBy"] == [{"field": "seeders", "descending": True}]
    assert "facets" not in search_input


def test_build_search_input_empty_query_is_none():
    search_input = bitmagnet.build_search_input(query="")
    assert search_input["queryString"] is None


def test_build_search_input_filters_and_paging():
    search_input = bitmagnet.build_search_input(
        query="westworld",
        content_type="tv_show",
        resolution="V2160p",
        video_source="WEBDL",
        year="2016",
        page=3,
    )
    assert search_input["facets"]["contentType"]["filter"] == ["tv_show"]
    assert search_input["facets"]["videoResolution"]["filter"] == ["V2160p"]
    assert search_input["facets"]["videoSource"]["filter"] == ["WEBDL"]
    assert search_input["facets"]["releaseYear"]["filter"] == [2016]
    assert search_input["offset"] == 2 * bitmagnet.PAGE_SIZE


def test_relevance_without_query_falls_back_to_newest():
    search_input = bitmagnet.build_search_input(order="relevance")
    assert search_input["orderBy"][0]["field"] == "published_at"


def test_relevance_with_query_is_kept():
    search_input = bitmagnet.build_search_input(query="westworld", order="relevance")
    assert search_input["orderBy"][0]["field"] == "relevance"


def test_name_sorts_ascending():
    search_input = bitmagnet.build_search_input(order="name")
    assert search_input["orderBy"] == [{"field": "name", "descending": False}]


def test_unknown_order_falls_back_to_seeders():
    search_input = bitmagnet.build_search_input(order="nonsense")
    assert search_input["orderBy"][0]["field"] == "seeders"


@responses.activate
def test_search_success_parses_published_at():
    responses.post(GRAPHQL_URL, json=search_payload([make_item()]))
    result = bitmagnet.search(bitmagnet.build_search_input(query="westworld"))
    assert result["totalCount"] == 1
    assert result["items"][0]["publishedAt"].year == 2026


@responses.activate
def test_search_graphql_errors_raise():
    responses.post(GRAPHQL_URL, json={"errors": [{"message": "boom"}], "data": None})
    with pytest.raises(bitmagnet.BitmagnetError, match="boom"):
        bitmagnet.search(bitmagnet.build_search_input(query="x"))


@responses.activate
def test_search_http_error_raises():
    responses.post(GRAPHQL_URL, status=502, body="bad gateway")
    with pytest.raises(bitmagnet.BitmagnetError, match="502"):
        bitmagnet.search(bitmagnet.build_search_input(query="x"))


@responses.activate
def test_search_connection_error_raises():
    responses.post(GRAPHQL_URL, body=responses.ConnectionError())
    with pytest.raises(bitmagnet.BitmagnetError, match="unreachable"):
        bitmagnet.search(bitmagnet.build_search_input(query="x"))


def status_payload():
    return {
        "data": {
            "torrentContent": {"search": {"totalCount": 5000, "totalCountIsEstimate": True}},
            "torrent": {
                "metrics": {
                    "buckets": [
                        # new torrents, two sources in the same hour bucket
                        {"bucket": "2026-06-12T01:00:00Z", "updated": False, "count": 300},
                        {"bucket": "2026-06-12T01:00:00Z", "updated": False, "count": 100},
                        {"bucket": "2026-06-12T02:00:00Z", "updated": False, "count": 200},
                        # updates are not "new" and must be excluded
                        {"bucket": "2026-06-12T02:00:00Z", "updated": True, "count": 999},
                    ]
                }
            },
            "queue": {
                "jobs": {
                    "totalCount": 42,
                    "aggregations": {
                        "queue": [{"label": "process_torrent", "count": 42}],
                    },
                }
            },
        }
    }


@responses.activate
def test_status_aggregates_metrics():
    responses.post(GRAPHQL_URL, json=status_payload())
    result = bitmagnet.status(since=datetime(2026, 6, 11, 2, 0, tzinfo=UTC))
    assert result["totalTorrents"] == 5000
    assert result["totalIsEstimate"] is True
    # sources summed within a bucket; updated=True excluded; oldest first
    assert [point["count"] for point in result["hourly"]] == [400, 200]
    assert result["lastHour"] == 200
    assert result["last24h"] == 600
    assert result["hourly"][0]["percent"] == 100
    assert result["hourly"][1]["percent"] == 50
    assert result["queueBacklog"] == 42
    assert result["backlogByQueue"][0]["label"] == "process_torrent"
