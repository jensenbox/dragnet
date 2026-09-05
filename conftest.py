"""Test-wide fixtures.

Static storage is CompressedManifestStaticFilesStorage in production, which
stamps a content hash into every filename so a changed stylesheet gets a new
URL (see settings.py). The cost is that `{% static %}` then refuses to resolve
anything absent from the collectstatic manifest — and tests that render a
template have no reason to have run collectstatic.

Locally this hid itself: the manifest was left over from a manual collectstatic
run, so the suite passed while CI failed 21 tests with "Missing staticfiles
manifest entry for 'vendor/bootstrap.min.css'". Pin the plain storage for tests
instead, so the result does not depend on whether a build artefact happens to
be lying around.
"""

import pytest


@pytest.fixture(autouse=True)
def _plain_static_storage(settings):
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
