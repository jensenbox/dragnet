"""Cloudflare Access single sign-on.

Public traffic reaches dragnet through a Cloudflare tunnel sitting behind an
Access application. Access authenticates the visitor (Google, email OTP, …) and
forwards the request with a signed JWT in `Cf-Access-Jwt-Assertion`. This module
validates that JWT and logs the matching Django user in.

The signature check is the whole point: port 9180 is still open on the LAN and
bypasses Cloudflare entirely, so a LAN client could trivially set the plaintext
`Cf-Access-Authenticated-User-Email` header. That header is never read here —
only the RS256-signed assertion, verified against the team's public keys and
pinned to this application's audience tag.

Requests without a valid assertion are left untouched, so Django's own login
page keeps working for LAN access on :9180.
"""

import logging

import jwt
from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger(__name__)

COOKIE_NAME = "CF_Authorization"
# Session key holding the Access identity this session was opened for.
SESSION_KEY = "cf_access_email"
HEADER_NAME = "HTTP_CF_ACCESS_JWT_ASSERTION"

_jwk_client: jwt.PyJWKClient | None = None


def is_configured() -> bool:
    return bool(settings.CF_ACCESS_TEAM_DOMAIN and settings.CF_ACCESS_AUD)


def issuer() -> str:
    return f"https://{settings.CF_ACCESS_TEAM_DOMAIN}"


def certs_url() -> str:
    return f"{issuer()}/cdn-cgi/access/certs"


def logout_url() -> str:
    """Cloudflare's own logout endpoint — clears the Access session, not just Django's."""
    return f"{issuer()}/cdn-cgi/access/logout"


def _get_jwk_client() -> jwt.PyJWKClient:
    """Lazily build the JWKS client; it caches keys and refetches on an unknown kid."""
    global _jwk_client
    if _jwk_client is None:
        _jwk_client = jwt.PyJWKClient(certs_url(), cache_keys=True, lifespan=3600)
    return _jwk_client


def decode_assertion(token: str) -> dict | None:
    """Verify an Access JWT and return its claims, or None if it isn't valid for us."""
    try:
        signing_key = _get_jwk_client().get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.CF_ACCESS_AUD,
            issuer=issuer(),
            options={"require": ["exp", "iat", "aud", "iss"]},
        )
    except jwt.PyJWTError as exc:
        logger.warning("Rejected Cloudflare Access assertion: %s", exc)
        return None
    except Exception as exc:  # JWKS fetch failure, etc. — fail closed, never fail open.
        logger.error("Could not verify Cloudflare Access assertion: %s", exc)
        return None


def user_for_email(email: str):
    """Find or provision the Django user for an Access-verified email address.

    Matches an existing account on email first (case-insensitively) so the
    accounts already created in /admin/ keep their username, history and staff
    flag; only genuinely new visitors get an account created for them.
    """
    User = get_user_model()
    user = User.objects.filter(email__iexact=email).order_by("pk").first()
    if user is None:
        user = User.objects.filter(username__iexact=email).first()
        if user is not None and not user.email:
            # Matched on username; record the address so admin shows the identity.
            user.email = email
            user.save(update_fields=["email"])
    if user is None:
        user = User(username=email[: User._meta.get_field("username").max_length], email=email)
        user.set_unusable_password()
        user.save()
        logger.info("Provisioned Django user for Access identity %s", email)
    return user


class CloudflareAccessMiddleware(MiddlewareMixin):
    """Log in the visitor named by a valid Cloudflare Access assertion.

    Must run after AuthenticationMiddleware (it needs request.user and the
    session). Does nothing at all when Access isn't configured, or when the
    request carries no assertion — that is the LAN path.
    """

    def process_request(self, request):
        if not is_configured():
            return None

        token = request.META.get(HEADER_NAME) or request.COOKIES.get(COOKIE_NAME)
        if not token:
            return None

        claims = decode_assertion(token)
        if not claims:
            return None

        email = claims.get("email") or claims.get("common_name")
        if not email:
            return None

        request.cf_access_email = email

        # The session records which Access identity it was opened for, so the
        # common case (already logged in as this person) costs no queries and
        # doesn't cycle the session key on every request.
        if request.user.is_authenticated and request.session.get(SESSION_KEY) == email:
            return None

        user = user_for_email(email)
        if not user.is_active:
            return None
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        request.session[SESSION_KEY] = email
        return None
