from django.urls import path

from . import api, views

urlpatterns = [
    path("", views.search, name="search"),
    path("download/", views.download, name="download"),
    path("history/", views.history, name="history"),
    path("status/", views.status, name="status"),
    # The adult section: a separate URL space, gated by core.view_adult_content.
    path("adult/", views.adult_search, name="adult_search"),
    path("adult/download/", views.adult_download, name="adult_download"),
    path("api/download/", api.download, name="api_download"),
]
