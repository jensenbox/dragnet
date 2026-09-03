"""Cloudflare Access assertion handling.

The threat these tests guard against: port 9180 stays open on the LAN and
bypasses Cloudflare, so anything reachable by spoofing a header is reachable by
anyone on the network. Only a correctly-signed, correctly-audienced JWT may
authenticate a request.
"""

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth.models import User

from core import cfaccess

TEAM = "jensenbox.cloudflareaccess.com"
AUD = "test-audience-tag"

pytestmark = pytest.mark.django_db


@pytest.fixture
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def access_settings(settings, signing_key):
    settings.CF_ACCESS_TEAM_DOMAIN = TEAM
    settings.CF_ACCESS_AUD = AUD
    # Bypass the network JWKS fetch; the signature check itself is what we test.
    cfaccess._jwk_client = None

    class StubKey:
        key = signing_key.public_key()

    class StubClient:
        def get_signing_key_from_jwt(self, token):
            return StubKey()

    cfaccess._jwk_client = StubClient()
    yield settings
    cfaccess._jwk_client = None


def make_token(signing_key, **overrides):
    claims = {
        "email": "family@example.com",
        "aud": AUD,
        "iss": f"https://{TEAM}",
        "iat": 1700000000,
        "exp": 4102444800,  # 2100
    }
    claims.update(overrides)
    return jwt.encode(claims, signing_key, algorithm="RS256")


def test_valid_assertion_is_accepted(access_settings, signing_key):
    claims = cfaccess.decode_assertion(make_token(signing_key))
    assert claims["email"] == "family@example.com"


def test_assertion_signed_by_another_key_is_rejected(access_settings):
    attacker_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert cfaccess.decode_assertion(make_token(attacker_key)) is None


def test_assertion_for_another_application_is_rejected(access_settings, signing_key):
    """A JWT from a different Access app in the same team must not work here."""
    assert cfaccess.decode_assertion(make_token(signing_key, aud="some-other-app")) is None


def test_expired_assertion_is_rejected(access_settings, signing_key):
    assert cfaccess.decode_assertion(make_token(signing_key, exp=1700000001)) is None


def test_unsigned_assertion_is_rejected(access_settings, signing_key):
    """The classic alg=none downgrade."""
    forged = jwt.encode({"email": "attacker@example.com", "aud": AUD}, None, algorithm="none")
    assert cfaccess.decode_assertion(forged) is None


def test_plaintext_email_header_alone_does_not_authenticate(client, access_settings):
    """Cf-Access-Authenticated-User-Email is trivially spoofable and must be ignored."""
    response = client.get("/", HTTP_CF_ACCESS_AUTHENTICATED_USER_EMAIL="attacker@example.com")
    assert response.status_code == 302
    assert "/accounts/login/" in response["Location"]
    assert not User.objects.filter(email="attacker@example.com").exists()


def test_garbage_assertion_does_not_authenticate(client, access_settings):
    response = client.get("/", HTTP_CF_ACCESS_JWT_ASSERTION="not-a-jwt")
    assert response.status_code == 302
    assert "/accounts/login/" in response["Location"]


def test_valid_assertion_provisions_and_logs_in_the_user(client, access_settings, signing_key):
    client.get("/", HTTP_CF_ACCESS_JWT_ASSERTION=make_token(signing_key))
    user = User.objects.get(email="family@example.com")
    assert user.username == "family@example.com"
    assert not user.has_usable_password()
    assert not user.is_staff


def test_existing_account_is_matched_on_email_not_duplicated(client, access_settings, signing_key):
    existing = User.objects.create_user(
        "christian", email="family@example.com", password="x", is_staff=True
    )
    client.get("/", HTTP_CF_ACCESS_JWT_ASSERTION=make_token(signing_key))
    assert User.objects.filter(email="family@example.com").count() == 1
    existing.refresh_from_db()
    assert existing.is_staff  # kept its privileges rather than being re-provisioned


def test_inactive_user_is_not_logged_in(client, access_settings, signing_key):
    User.objects.create_user("blocked", email="family@example.com", is_active=False)
    response = client.get("/", HTTP_CF_ACCESS_JWT_ASSERTION=make_token(signing_key))
    assert response.status_code == 302
    assert "/accounts/login/" in response["Location"]


def test_middleware_is_inert_when_access_is_not_configured(client, settings, signing_key):
    """LAN-only deployments must be unaffected by an attacker-supplied header."""
    settings.CF_ACCESS_TEAM_DOMAIN = ""
    settings.CF_ACCESS_AUD = ""
    response = client.get("/", HTTP_CF_ACCESS_JWT_ASSERTION=make_token(signing_key))
    assert response.status_code == 302
    assert not User.objects.exists()
