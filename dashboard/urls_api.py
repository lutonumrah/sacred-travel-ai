from django.urls import path
from rest_framework import generics
from rest_framework.views import APIView

from bookings import selectors as booking_selectors
from bookings.serializers import NotificationSerializer
from core.api import EnvelopeMixin, SuccessResponse
from core.permissions import IsManager

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
    """Same filters as the reports page: website (id), days, date_from / date_to."""

    permission_classes = [IsManager]

    def get(self, request):
        from .views import report_filters

        filters = report_filters(request, 30)
        scope = {"website": filters["website"], "start": filters["start"], "end": filters["end"]}
        return SuccessResponse(
            {
                "days": filters["days"],
                "start": filters["start"],
                "end": filters["end"],
                "website": filters["website"].pk if filters["website"] else None,
                "kpis": selectors.overview_kpis(**scope),
                "leads_by_source": selectors.leads_by_source(**scope),
                "leads_by_status": selectors.leads_by_status(**scope),
                "leads_by_day": selectors.leads_by_day(**scope),
                "revenue_by_day": selectors.revenue_by_day(**scope),
                "bookings": selectors.booking_report(**scope),
                "top_destinations": selectors.top_destinations(**scope),
                "conversion": selectors.conversion_funnel(
                    group_by=("website", "source"), **scope
                ),
                "campaigns": selectors.conversion_funnel(group_by=("campaign",), **scope),
            }
        )


class AnalyticsAPI(APIView):
    """Per-website funnel and per-employee performance; days or date_from / date_to."""

    permission_classes = [IsManager]

    def get(self, request):
        from .views import report_filters

        filters = report_filters(request, 90)
        scope = {"start": filters["start"], "end": filters["end"]}
        return SuccessResponse(
            {
                "days": filters["days"],
                "start": filters["start"],
                "end": filters["end"],
                "websites": [
                    {
                        "id": row["website"].pk,
                        "name": row["website"].name,
                        "source_identifier": row["website"].source_identifier,
                        "conversations": row["conversations"],
                        "chats_with_lead": row["chats_with_lead"],
                        "chat_to_lead_rate": row["chat_to_lead_rate"],
                        "leads": row["leads"],
                        "converted": row["converted"],
                        "conversion_rate": row["conversion_rate"],
                        "bookings": row["bookings"],
                        "paid_bookings": row["paid_bookings"],
                        "revenue": row["revenue"],
                    }
                    for row in selectors.website_performance(**scope)
                ],
                "employees": [
                    {
                        "id": row["user"].pk,
                        "username": row["user"].get_username(),
                        "leads": row["leads"],
                        "converted": row["converted"],
                        "conversion_rate": row["conversion_rate"],
                        "open_follow_ups": row["open_follow_ups"],
                        "conversations": row["conversations"],
                        "handoffs": row["handoffs"],
                        "responded_handoffs": row["responded_handoffs"],
                        "avg_first_response_seconds": row["avg_first_response_seconds"],
                        "revenue": row["revenue"],
                    }
                    for row in selectors.employee_performance(**scope)
                ],
                "definitions": {
                    "avg_first_response_seconds": selectors.FIRST_RESPONSE_DEFINITION,
                    "conversion_rate": (
                        "Converted leads / leads assigned and created in the period."
                    ),
                    "chat_to_lead_rate": "Chats started in the period that produced a lead.",
                },
            }
        )


urlpatterns = [
    path("overview/", OverviewAPI.as_view(), name="overview"),
    path("notifications/", NotificationsAPI.as_view(), name="notifications"),
    path("reports/", ReportsAPI.as_view(), name="reports"),
    path("analytics/", AnalyticsAPI.as_view(), name="analytics"),
]
