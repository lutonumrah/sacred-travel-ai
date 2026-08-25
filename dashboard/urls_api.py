from django.urls import path
from rest_framework import generics
from rest_framework.views import APIView

from bookings import selectors as booking_selectors
from bookings.serializers import NotificationSerializer
from core.api import EnvelopeMixin, SuccessResponse

from . import selectors, services

app_name = "api_dashboard"


def _days(request, default=30):
    try:
        return max(1, min(int(request.query_params.get("days", default)), 365))
    except (TypeError, ValueError):
        return default


class OverviewAPI(APIView):
    def get(self, request):
        return SuccessResponse(selectors.overview_kpis(days=_days(request)))


class NotificationsAPI(EnvelopeMixin, generics.ListAPIView):
    serializer_class = NotificationSerializer

    def get_queryset(self):
        return booking_selectors.list_notifications(
            self.request.user,
            unread_only=self.request.query_params.get("unread") == "1",
            notification_type=self.request.query_params.get("type", ""),
        )

    def post(self, request, *args, **kwargs):
        """Mark notifications read: pass `id`, or omit it for all of them."""
        count = services.mark_read(user=request.user, notification_id=request.data.get("id"))
        return SuccessResponse({"marked_read": count})


class ReportsAPI(APIView):
    def get(self, request):
        days = _days(request)
        return SuccessResponse(
            {
                "days": days,
                "kpis": selectors.overview_kpis(days=days),
                "leads_by_source": selectors.leads_by_source(days=days),
                "leads_by_status": selectors.leads_by_status(),
                "leads_by_day": selectors.leads_by_day(days=days),
                "revenue_by_day": selectors.revenue_by_day(days=days),
                "bookings": selectors.booking_report(days=days),
                "top_destinations": selectors.top_destinations(days=days),
            }
        )


class AnalyticsAPI(APIView):
    def get(self, request):
        days = _days(request, 90)
        return SuccessResponse(
            {
                "days": days,
                "websites": [
                    {
                        "id": row["website"].pk,
                        "name": row["website"].name,
                        "source_identifier": row["website"].source_identifier,
                        "leads": row["leads"],
                        "converted": row["converted"],
                        "conversion_rate": row["conversion_rate"],
                        "conversations": row["conversations"],
                        "bookings": row["bookings"],
                        "revenue": row["revenue"],
                    }
                    for row in selectors.website_performance(days=days)
                ],
                "employees": [
                    {
                        "id": row["user"].pk,
                        "username": row["user"].get_username(),
                        "leads": row["leads"],
                        "converted": row["converted"],
                        "conversion_rate": row["conversion_rate"],
                        "open_follow_ups": row["open_follow_ups"],
                        "revenue": row["revenue"],
                    }
                    for row in selectors.employee_performance(days=days)
                ],
            }
        )


urlpatterns = [
    path("overview/", OverviewAPI.as_view(), name="overview"),
    path("notifications/", NotificationsAPI.as_view(), name="notifications"),
    path("reports/", ReportsAPI.as_view(), name="reports"),
    path("analytics/", AnalyticsAPI.as_view(), name="analytics"),
]
