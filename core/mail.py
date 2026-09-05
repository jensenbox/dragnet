"""Outbound email via the Telnyx Email API.

Deliberately not Django's email backend: dragnet sends exactly one kind of
message, and a direct client is smaller than configuring anymail for it.

⚠️ Telnyx enforces a hard 1,048,576-byte cap on the whole request — the error
is "Kafka payload exceeds size limit", and it applies to verified custom
domains, not just the shared ones. Their own rate-limits page documents 25 MB,
which is wrong; measured on 2026-09-05, a 1,014,175-byte request sent and a
1,066,948-byte one was rejected. That is why dragnet mails a *link* to the
finished file rather than attaching it: every ebook in the library is larger
than the roughly 786 KB of payload that cap leaves after base64. Do not add
attachments here without re-testing the limit.
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

SEND_URL = "https://api.telnyx.com/v2/email_messages"
TIMEOUT = 30


class EmailError(Exception):
    """Telnyx rejected the message or was unreachable."""


def is_configured() -> bool:
    return bool(settings.TELNYX_API_KEY and settings.TELNYX_EMAIL_FROM)


def send(*, to: str, subject: str, text_body: str, html_body: str = "") -> str:
    """Send one message, returning the Telnyx message id.

    Raises EmailError on anything that isn't a 2xx, including a missing
    configuration, so a caller that means to send can't silently do nothing.
    """
    if not is_configured():
        raise EmailError("TELNYX_API_KEY and TELNYX_EMAIL_FROM are not both set")

    payload = {
        "from": {
            "email": settings.TELNYX_EMAIL_FROM,
            "name": settings.TELNYX_EMAIL_FROM_NAME,
        },
        "to": [{"email": to}],
        "subject": subject,
        "text_body": text_body,
    }
    if html_body:
        payload["html_body"] = html_body

    try:
        response = requests.post(
            SEND_URL,
            json=payload,
            headers={"Authorization": f"Bearer {settings.TELNYX_API_KEY}"},
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise EmailError(f"Telnyx is unreachable: {exc}") from exc

    if response.status_code >= 300:
        raise EmailError(f"Telnyx returned HTTP {response.status_code}: {response.text[:500]}")

    message_id = response.json().get("data", {}).get("id", "")
    logger.info("Sent %r to %s (telnyx id %s)", subject, to, message_id)
    return message_id
