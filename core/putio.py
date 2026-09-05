"""Minimal put.io API client: send magnet links into the right folder."""

from typing import Any

import requests
from django.conf import settings

API_BASE = "https://api.put.io/v2"
TRANSFERS_ADD_URL = f"{API_BASE}/transfers/add"
FILES_LIST_URL = f"{API_BASE}/files/list"
CREATE_FOLDER_URL = f"{API_BASE}/files/create-folder"


class PutioError(Exception):
    """put.io rejected the request or was unreachable."""


def _request(method: str, url: str, **params: Any) -> dict[str, Any]:
    token = settings.PUTIO_OAUTH_TOKEN
    if not token:
        raise PutioError("PUTIO_OAUTH_TOKEN is not configured")
    try:
        response = requests.request(
            method,
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
            **params,
        )
    except requests.RequestException as exc:
        raise PutioError(f"put.io is unreachable: {exc}") from exc
    if response.status_code != 200:
        raise PutioError(f"put.io returned HTTP {response.status_code}: {response.text[:500]}")
    payload = response.json()
    if payload.get("status") != "OK":
        raise PutioError(f"put.io returned non-OK payload: {payload}")
    return payload


def find_or_create_folder(name: str, parent_id: int) -> int:
    """Return the id of the named folder under parent_id, creating it if absent."""
    listing = _request("GET", FILES_LIST_URL, params={"parent_id": parent_id, "per_page": 1000})
    for entry in listing["files"]:
        if entry["file_type"] == "FOLDER" and entry["name"] == name:
            return entry["id"]
    created = _request("POST", CREATE_FOLDER_URL, data={"name": name, "parent_id": parent_id})
    return created["file"]["id"]


def resolve_folder_path(names: list[str]) -> int:
    """Walk/create a folder path from the account root, returning the final folder id."""
    parent_id = 0
    for name in names:
        parent_id = find_or_create_folder(name, parent_id)
    return parent_id


def add_transfer(magnet_uri: str, save_parent_id: int | None = None) -> dict[str, Any]:
    """Add a transfer on put.io and return the transfer object."""
    data: dict[str, Any] = {"url": magnet_uri}
    if save_parent_id is not None:
        data["save_parent_id"] = save_parent_id
    payload = _request("POST", TRANSFERS_ADD_URL, data=data)
    return payload["transfer"]


TRANSFER_URL = f"{API_BASE}/transfers/{{id}}"
FILE_URL = f"{API_BASE}/files/{{id}}"
FILE_DOWNLOAD_URL = f"{API_BASE}/files/{{id}}/url"

# put.io transfer statuses that mean the bytes have landed. SEEDING counts:
# the download is complete and the client is now uploading back to the swarm.
FINISHED_STATUSES = {"COMPLETED", "SEEDING"}

# Extensions worth handing to someone who asked for a book, best first. Used to
# pick one file out of a torrent that contains several.
READABLE_EXTENSIONS = (".epub", ".azw3", ".mobi", ".pdf", ".cbz", ".cbr")


def get_transfer(transfer_id: int) -> dict[str, Any]:
    """Return the transfer object, which carries its status and saved file id."""
    return _request("GET", TRANSFER_URL.format(id=transfer_id))["transfer"]


def get_file(file_id: int) -> dict[str, Any]:
    return _request("GET", FILE_URL.format(id=file_id))["file"]


def list_folder(parent_id: int) -> list[dict[str, Any]]:
    listing = _request("GET", FILES_LIST_URL, params={"parent_id": parent_id, "per_page": 1000})
    return listing["files"]


def pick_downloadable_file(file_id: int) -> dict[str, Any] | None:
    """Resolve a transfer's saved file id to a single file someone can download.

    put.io wraps even single-file torrents in a folder, so the id a transfer
    reports is usually a FOLDER and /files/{id}/url returns NotFile for it. Walk
    down and choose: a readable format first (an EPUB beats the cover art and
    the readme that ship beside it), otherwise the largest file, which is the
    right answer for a video or a single archive.

    Returns None for an empty folder. Multi-book bundles necessarily return only
    one file — the folder link in the UI is the honest answer for those.
    """
    entry = get_file(file_id)
    if entry["file_type"] != "FOLDER":
        return entry

    candidates: list[dict[str, Any]] = []
    stack = [file_id]
    while stack:
        for child in list_folder(stack.pop()):
            if child["file_type"] == "FOLDER":
                stack.append(child["id"])
            else:
                candidates.append(child)
    if not candidates:
        return None

    def rank(f: dict[str, Any]) -> tuple[int, int]:
        name = f["name"].lower()
        for i, ext in enumerate(READABLE_EXTENSIONS):
            if name.endswith(ext):
                return (i, -(f.get("size") or 0))
        return (len(READABLE_EXTENSIONS), -(f.get("size") or 0))

    return sorted(candidates, key=rank)[0]


def download_url(file_id: int) -> str:
    """A short-lived signed URL for the file. put.io 400s if the id is a folder."""
    return _request("GET", FILE_DOWNLOAD_URL.format(id=file_id))["url"]
