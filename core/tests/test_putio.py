import pytest
import responses

from core import putio

MAGNET = "magnet:?xt=urn:btih:" + "a" * 40


@responses.activate
def test_add_transfer_success(settings):
    settings.PUTIO_OAUTH_TOKEN = "test-token"
    responses.post(
        putio.TRANSFERS_ADD_URL,
        json={"status": "OK", "transfer": {"id": 42, "name": "Westworld"}},
    )
    transfer = putio.add_transfer(MAGNET)
    assert transfer["id"] == 42
    request = responses.calls[0].request
    assert request.headers["Authorization"] == "Bearer test-token"
    assert "url=magnet" in request.body


def test_missing_token_raises(settings):
    settings.PUTIO_OAUTH_TOKEN = ""
    with pytest.raises(putio.PutioError, match="not configured"):
        putio.add_transfer(MAGNET)


@responses.activate
def test_http_error_raises(settings):
    settings.PUTIO_OAUTH_TOKEN = "test-token"
    responses.post(putio.TRANSFERS_ADD_URL, status=401, body="Unauthorized")
    with pytest.raises(putio.PutioError, match="401"):
        putio.add_transfer(MAGNET)


@responses.activate
def test_non_ok_payload_raises(settings):
    settings.PUTIO_OAUTH_TOKEN = "test-token"
    responses.post(putio.TRANSFERS_ADD_URL, json={"status": "ERROR", "error_type": "Whatever"})
    with pytest.raises(putio.PutioError, match="non-OK"):
        putio.add_transfer(MAGNET)
