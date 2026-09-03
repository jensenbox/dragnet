"""Login/logout that stay correct whether the visitor came via Cloudflare Access
or over the LAN.

Two things need special handling once Access is in front of the tunnel:

* Logging out of Django alone is misleading — the Access session cookie is still
  valid, so the next request would silently log the visitor straight back in.
  Public logouts are therefore handed off to Cloudflare's own logout endpoint.
* The Django login form is meaningless to an Access visitor (they are already
  authenticated, and their account has an unusable password), so it is only
  worth showing on the LAN path.
"""

from django.contrib.auth import logout
from django.contrib.auth import views as django_auth_views
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from . import cfaccess


class LoginView(django_auth_views.LoginView):
    """Standard Django login, with a hint when Access should have handled it."""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["cf_access_active"] = bool(getattr(self.request, "cf_access_email", None))
        return context


@require_POST
def logout_view(request):
    came_via_access = bool(getattr(request, "cf_access_email", None))
    logout(request)
    if came_via_access:
        # Clear the Cloudflare session too, or the visitor is instantly back in.
        return redirect(cfaccess.LOGOUT_PATH)
    return redirect("login")
