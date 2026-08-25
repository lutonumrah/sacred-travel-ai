from django.urls import path
from rest_framework import generics

from core.api import EnvelopeMixin

from . import selectors
from .serializers import WebsiteSerializer

app_name = "api_websites"


class WebsiteListAPI(EnvelopeMixin, generics.ListAPIView):
    serializer_class = WebsiteSerializer

    def get_queryset(self):
        params = self.request.query_params
        return selectors.list_websites(
            q=params.get("q", ""), status=params.get("status", "")
        ).prefetch_related("api_keys")


class WebsiteDetailAPI(EnvelopeMixin, generics.RetrieveAPIView):
    serializer_class = WebsiteSerializer

    def get_queryset(self):
        return selectors.list_websites().prefetch_related("api_keys")


urlpatterns = [
    path("", WebsiteListAPI.as_view(), name="list"),
    path("<int:pk>/", WebsiteDetailAPI.as_view(), name="detail"),
]
