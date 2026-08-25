from django.urls import path

from . import views

app_name = "websites"

urlpatterns = [
    path("", views.WebsiteListView.as_view(), name="list"),
    path("add/", views.WebsiteCreateView.as_view(), name="create"),
    path("<int:pk>/", views.WebsiteDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.WebsiteUpdateView.as_view(), name="edit"),
    path("<int:pk>/delete/", views.WebsiteDeleteView.as_view(), name="delete"),
    path("<int:pk>/keys/add/", views.WebsiteKeyCreateView.as_view(), name="key_create"),
    path(
        "<int:pk>/keys/<int:key_id>/revoke/",
        views.WebsiteKeyRevokeView.as_view(),
        name="key_revoke",
    ),
]
