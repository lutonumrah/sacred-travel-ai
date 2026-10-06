from django.urls import path
from rest_framework import generics
from rest_framework.views import APIView

from core.api import EnvelopeMixin, SuccessResponse

from . import selectors
from .serializers import (
    CarRentalSerializer,
    DestinationSerializer,
    HotelSerializer,
    InventorySearchResultSerializer,
    TourPackageSerializer,
    VisibilitySerializer,
)

app_name = "api_inventory"

SEARCH_LIMIT_DEFAULT = 30
SEARCH_LIMIT_MAX = 50


def _search_limit(raw):
    try:
        return max(1, min(int(raw), SEARCH_LIMIT_MAX))
    except (TypeError, ValueError):
        return SEARCH_LIMIT_DEFAULT


class DestinationListAPI(EnvelopeMixin, generics.ListAPIView):
    serializer_class = DestinationSerializer

    def get_queryset(self):
        return selectors.list_destinations(
            q=self.request.query_params.get("q", ""),
            status=self.request.query_params.get("status", "active"),
        )


class _InventoryListAPI(EnvelopeMixin, generics.ListAPIView):
    lister = None

    def get_queryset(self):
        params = self.request.query_params
        return type(self).lister(
            q=params.get("q", ""),
            status=params.get("status", ""),
            min_price=params.get("min_price"),
            max_price=params.get("max_price"),
        )


class HotelListAPI(_InventoryListAPI):
    serializer_class = HotelSerializer
    lister = staticmethod(selectors.list_hotels)

    def get_queryset(self):
        return super().get_queryset().prefetch_related("offers")


class CarListAPI(_InventoryListAPI):
    serializer_class = CarRentalSerializer
    lister = staticmethod(selectors.list_cars)


class PackageListAPI(_InventoryListAPI):
    serializer_class = TourPackageSerializer
    lister = staticmethod(selectors.list_packages)


class VisibilityListAPI(EnvelopeMixin, generics.ListAPIView):
    serializer_class = VisibilitySerializer

    def get_queryset(self):
        return selectors.list_visibility(
            inventory_type=self.request.query_params.get("inventory_type", "")
        )


class InventorySearchAPI(APIView):
    """Structured search used by the dashboard and the AI recommendation engine."""

    def get(self, request):
        from websites.selectors import list_websites

        params = request.query_params
        website = None
        if params.get("website", "").isdigit():
            website = list_websites().filter(pk=params["website"]).first()
        results = selectors.search_inventory(
            q=params.get("q", ""),
            inventory_type=params.get("inventory_type", ""),
            destination=params.get("destination", ""),
            min_price=params.get("min_price"),
            max_price=params.get("max_price"),
            website=website,
            limit=_search_limit(params.get("limit", SEARCH_LIMIT_DEFAULT)),
        )
        return SuccessResponse(
            {
                "results": InventorySearchResultSerializer(results, many=True).data,
                "count": len(results),
            }
        )


urlpatterns = [
    path("destinations/", DestinationListAPI.as_view(), name="destinations"),
    path("hotels/", HotelListAPI.as_view(), name="hotels"),
    path("cars/", CarListAPI.as_view(), name="cars"),
    path("packages/", PackageListAPI.as_view(), name="packages"),
    path("visibility/", VisibilityListAPI.as_view(), name="visibility"),
    path("search/", InventorySearchAPI.as_view(), name="search"),
]
