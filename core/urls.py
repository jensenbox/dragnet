from django.urls import path

from . import api, views

urlpatterns = [
    path("", views.search, name="search"),
    path("download/", views.download, name="download"),
    path("history/", views.history, name="history"),
    path("status/", views.status, name="status"),
    path("api/download/", api.download, name="api_download"),
]
