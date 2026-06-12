"""Minimal put.io API client: send a magnet link to the transfer queue."""

from typing import Any

import requests
from django.conf import settings

TRANSFERS_ADD_URL = "https://api.put.io/v2/transfers/add"


class PutioError(Exception):
    """put.io rejected the transfer or was unreachable."""


def add_transfer(magnet_uri: str) -> dict[str, Any]:
    """Add a transfer on put.io and return the transfer object."""
    token = settings.PUTIO_OAUTH_TOKEN
    if not token:
        raise PutioError("PUTIO_OAUTH_TOKEN is not configured")
    try:
        response = requests.post(
            TRANSFERS_ADD_URL,
            data={"url": magnet_uri},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
    except requests.RequestException as exc:
        raise PutioError(f"put.io is unreachable: {exc}") from exc
    if response.status_code != 200:
        raise PutioError(f"put.io returned HTTP {response.status_code}: {response.text[:500]}")
    payload = response.json()
    if payload.get("status") != "OK":
        raise PutioError(f"put.io returned non-OK payload: {payload}")
    return payload["transfer"]
