import csv

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect
from django.views import View
from django.views.generic import TemplateView

from bookings import selectors as booking_selectors
from core.mixins import ManagerRequiredMixin, PageMixin
from core.selectors import paginate
from crm import selectors as crm_selectors

from . import selectors, services


def _selected_website(request):
    from websites.selectors import list_websites

    website_id = request.GET.get("website")
    if not website_id:
        return None
    return list_websites().filter(pk=website_id).first()


def _days(request, default=30):
    try:
        days = int(request.GET.get("days", default))
    except (TypeError, ValueError):
        return default
    return max(1, min(days, 365))


class OverviewView(PageMixin, TemplateView):
    template_name = "dashboard/overview.html"
    page_title = "Dashboard Overview"
    page_subtitle = "Leads, conversations, bookings and revenue at a glance."
    active_nav = "dashboard"

    def get_context_data(self, **kwargs):
        from websites.selectors import list_websites

        ctx = super().get_context_data(**kwargs)
        website = _selected_website(self.request)
        days = _days(self.request)
        ctx["kpis"] = selectors.overview_kpis(website=website, days=days)
        ctx["leads_by_day"] = selectors.leads_by_day(days=days, website=website)
        ctx["leads_by_status"] = selectors.leads_by_status(website=website)
        ctx["pipeline"] = crm_selectors.lead_status_counts(website=website)
        ctx["activity"] = selectors.recent_activity(user=self.request.user)
        ctx["websites"] = list_websites(status="active")
        ctx["selected_website"] = website
        ctx["days"] = days
        ctx["reminders"] = services.follow_up_reminders_due(user=self.request.user)[:5]
        return ctx


class NotificationsView(PageMixin, TemplateView):
    template_name = "dashboard/notifications.html"
    page_title = "Notifications"
    page_subtitle = "Lead, handoff, payment and follow-up alerts."
    active_nav = "notifications"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        unread_only = self.request.GET.get("unread") == "1"
        queryset = booking_selectors.list_notifications(
            self.request.user,
            unread_only=unread_only,
            notification_type=self.request.GET.get("type", ""),
        )
        ctx["page_obj"] = paginate(queryset, self.request.GET.get("page"), 30)
        ctx["unread_only"] = unread_only
        ctx["unread_count"] = booking_selectors.unread_notifications(
            self.request.user
        ).count()
        return ctx


class NotificationReadView(PageMixin, View):
    def post(self, request, pk=None):
        services.mark_read(user=request.user, notification_id=pk)
        if pk is None:
            messages.success(request, "All notifications marked as read.")
        return redirect(request.META.get("HTTP_REFERER") or "dashboard:notifications")


class ReportsView(ManagerRequiredMixin, TemplateView):
    template_name = "dashboard/reports.html"
    page_title = "Reports"
    page_subtitle = "Lead, booking, revenue and conversion reporting."
    active_nav = "reports"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        days = _days(self.request, 30)
        ctx["days"] = days
        ctx["kpis"] = selectors.overview_kpis(days=days)
        ctx["leads_by_source"] = selectors.leads_by_source(days=days)
        ctx["leads_by_status"] = selectors.leads_by_status()
        ctx["leads_by_day"] = selectors.leads_by_day(days=days)
        ctx["revenue_by_day"] = selectors.revenue_by_day(days=days)
        ctx["booking_report"] = selectors.booking_report(days=days)
        ctx["top_destinations"] = selectors.top_destinations(days=days)
        return ctx


class AnalyticsView(ManagerRequiredMixin, TemplateView):
    template_name = "dashboard/analytics.html"
    page_title = "Website & Employee Analytics"
    page_subtitle = "Source and team performance."
    active_nav = "analytics"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        days = _days(self.request, 90)
        ctx["days"] = days
        ctx["websites"] = selectors.website_performance(days=days)
        ctx["employees"] = selectors.employee_performance(days=days)
        ctx["top_destinations"] = selectors.top_destinations(days=days)
        return ctx


class ReportExportView(ManagerRequiredMixin, View):
    """Download the lead or booking report as CSV."""

    def get(self, request, kind):
        days = _days(request, 30)
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{kind}-report.csv"'
        writer = csv.writer(response)

        if kind == "leads":
            from crm.selectors import list_leads

            writer.writerow(
                ["ID", "Title", "Customer", "Status", "Source", "Destination", "Score", "Created"]
            )
            for lead in list_leads()[:5000]:
                writer.writerow(
                    [
                        lead.pk,
                        lead.title,
                        lead.customer.full_name if lead.customer else "",
                        lead.get_status_display(),
                        lead.get_source_display(),
                        lead.destination,
                        lead.score,
                        lead.created_at.strftime("%Y-%m-%d %H:%M"),
                    ]
                )
        elif kind == "bookings":
            writer.writerow(
                ["Number", "Customer", "Product", "Status", "Total", "Currency", "Created"]
            )
            for booking in booking_selectors.list_bookings()[:5000]:
                writer.writerow(
                    [
                        booking.booking_number,
                        booking.customer.full_name,
                        booking.product_name,
                        booking.get_status_display(),
                        booking.total_amount,
                        booking.currency,
                        booking.created_at.strftime("%Y-%m-%d %H:%M"),
                    ]
                )
        else:
            writer.writerow(["Unknown report type"])
        return response
