from django.urls import path
from rest_framework import generics

from core.api import EnvelopeMixin
from core.permissions import IsManager

from . import selectors
from .serializers import WebsiteSerializer

app_name = "api_websites"


class WebsiteListAPI(EnvelopeMixin, generics.ListAPIView):
    permission_classes = [IsManager]
    serializer_class = WebsiteSerializer

    def get_queryset(self):
        params = self.request.query_params
        return selectors.list_websites(
            q=params.get("q", ""), status=params.get("status", "")
        ).prefetch_related("api_keys")


class WebsiteDetailAPI(EnvelopeMixin, generics.RetrieveAPIView):
    permission_classes = [IsManager]
    serializer_class = WebsiteSerializer

    def get_queryset(self):
        return selectors.list_websites().prefetch_related("api_keys")


urlpatterns = [
    path("", WebsiteListAPI.as_view(), name="list"),
    path("<int:pk>/", WebsiteDetailAPI.as_view(), name="detail"),
]
