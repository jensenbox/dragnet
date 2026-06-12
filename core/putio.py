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
