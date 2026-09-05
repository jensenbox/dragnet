"""Django settings for dragnet.

All deployment-specific values come from environment variables (see .env.example).
"""

from pathlib import Path

from environs import Env

env = Env()
env.read_env()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = env.str("SECRET_KEY", default="django-insecure-dev-only-key")
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
# Must list the public origin (https://dragnet.jensenbox.com) or every POST 403s.
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# Behind the Cloudflare tunnel, cloudflared terminates TLS and forwards over
# plain HTTP; this is how Django learns the request was really HTTPS.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# Cookie flags are opt-in: turning them on means browser sessions only work over
# HTTPS, so plain-http://192.168.16.10:9180 login stops working. That LAN port
# stays open for the bearer-token API, which doesn't use cookies.
SESSION_COOKIE_SECURE = env.bool("SECURE_COOKIES", default=False)
CSRF_COOKIE_SECURE = env.bool("SECURE_COOKIES", default=False)
SESSION_COOKIE_SAMESITE = "Lax"
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False)
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

# Dragnet-specific configuration
BITMAGNET_URL = env.str("BITMAGNET_URL", default="http://bitmagnet:3333")
PUTIO_OAUTH_TOKEN = env.str("PUTIO_OAUTH_TOKEN", default="")
# Transfers land under this put.io folder (rclone moves it to the media server).
PUTIO_BASE_FOLDER = env.str("PUTIO_BASE_FOLDER", default="plex")
# bitmagnet contentType → subfolder under the base folder.
PUTIO_CONTENT_TYPE_FOLDERS = {
    "movie": "curated_movies",
    "tv_show": "tv_series",
}
# Unmapped content types go here, at the account root — deliberately OUTSIDE the
# base folder so rclone doesn't move them to the media server before manual triage.
PUTIO_UNCLASSIFIED_FOLDER = env.str("PUTIO_UNCLASSIFIED_FOLDER", default="unclassified")
# Adult content is a separate section, gated by the core.view_adult_content
# permission. Sends land in a root-level folder deliberately OUTSIDE the
# rclone-watched base folder, so this never reaches the family Plex library.
PUTIO_ADULT_FOLDER = env.str("PUTIO_ADULT_FOLDER", default="adult")
# Bearer token for the JSON API (POST /api/download/); API is disabled while unset.
DRAGNET_API_TOKEN = env.str("DRAGNET_API_TOKEN", default="")
# API sends are attributed to this Django user (auto-created, no password login).
DRAGNET_API_USERNAME = env.str("DRAGNET_API_USERNAME", default="claude")
# bitmagnet's own dashboard has no auth and is LAN-only, so it can't be derived
# from the request host once we're reachable at dragnet.jensenbox.com.
BITMAGNET_DASHBOARD_URL = env.str("BITMAGNET_DASHBOARD_URL", default="")

# --- "your download is ready" email (Telnyx) --------------------------------
# Sending is disabled while either of these is empty, which is the right
# default: notify_ready still resolves finished transfers so the Download
# buttons appear, it just doesn't mail anyone.
TELNYX_API_KEY = env.str("TELNYX_API_KEY", default="")
TELNYX_EMAIL_FROM = env.str("TELNYX_EMAIL_FROM", default="")
TELNYX_EMAIL_FROM_NAME = env.str("TELNYX_EMAIL_FROM_NAME", default="Dragnet")
# Absolute base for links in those emails. The notifier runs on a cron with no
# request to derive a host from, so it has to be configured.
DRAGNET_PUBLIC_URL = env.str("DRAGNET_PUBLIC_URL", default="https://dragnet.jensenbox.com")

# Cloudflare Access SSO. Both must be set for public logins to work; while
# either is empty the Access middleware is inert and only Django login applies.
# Team domain e.g. "jensenbox.cloudflareaccess.com"; AUD is the Access
# application's Audience tag.
CF_ACCESS_TEAM_DOMAIN = env.str("CF_ACCESS_TEAM_DOMAIN", default="")
CF_ACCESS_AUD = env.str("CF_ACCESS_AUD", default="")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Runs after AuthenticationMiddleware: needs request.user and the session.
    "core.cfaccess.CloudflareAccessMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "dragnet.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "dragnet.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": env.path("SQLITE_PATH", default=str(BASE_DIR / "db.sqlite3")),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = env.str("TIME_ZONE", default="UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Auth: family members log in rarely; keep sessions for 90 days.
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "search"
LOGOUT_REDIRECT_URL = "login"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 90
