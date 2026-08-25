from django.urls import path
from rest_framework import generics

from core.api import EnvelopeMixin
from core.views import HealthCheckAPIView

from . import selectors
from .serializers import TeamSerializer, UserSerializer

app_name = "api_accounts"


class UserListAPI(EnvelopeMixin, generics.ListAPIView):
    serializer_class = UserSerializer

    def get_queryset(self):
        params = self.request.query_params
        return selectors.list_users(
            q=params.get("q", ""),
            role=params.get("role", ""),
            status=params.get("status", ""),
        )


class UserDetailAPI(EnvelopeMixin, generics.RetrieveAPIView):
    serializer_class = UserSerializer

    def get_queryset(self):
        return selectors.list_users()


class TeamListAPI(EnvelopeMixin, generics.ListAPIView):
    serializer_class = TeamSerializer

    def get_queryset(self):
        return selectors.list_teams()


urlpatterns = [
    path("health/", HealthCheckAPIView.as_view(), name="health"),
    path("users/", UserListAPI.as_view(), name="users"),
    path("users/<int:pk>/", UserDetailAPI.as_view(), name="user_detail"),
    path("teams/", TeamListAPI.as_view(), name="teams"),
]
