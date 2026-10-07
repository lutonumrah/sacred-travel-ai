import csv
from urllib.parse import urlencode

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
from .forms import ReportFilterForm


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


def report_filters(request, default_days=30):
    """Website, `days` and an optional From / To range, as the reports pages use them."""
    form = ReportFilterForm(request.GET or None)
    data = form.cleaned_data if form.is_bound and form.is_valid() else {}
    days = _days(request, default_days)
    start, end = selectors.period(days, data.get("date_from"), data.get("date_to"))
    return {
        "form": form,
        "website": data.get("website"),
        "days": days,
        "start": start,
        "end": end,
        "custom_range": bool(data.get("date_from") or data.get("date_to")),
        # Whether the caller narrowed the period at all (the exports default to everything).
        "has_period": any(request.GET.get(name) for name in ("days", "date_from", "date_to")),
        "querystring": urlencode([(key, value) for key, value in request.GET.items() if value]),
    }


class OverviewView(PageMixin, TemplateView):
    """Business-wide for managers, "My figures" for employees, stock for inventory."""

    template_name = "dashboard/overview.html"
    page_title = "Dashboard Overview"
    active_nav = "dashboard"
    SUBTITLES = {
        selectors.BUSINESS: "Leads, conversations, bookings and revenue at a glance.",
        selectors.MINE: "My figures: the leads, chats and bookings you can work on.",
        selectors.INVENTORY: "Hotels, cars, packages and destinations at a glance.",
    }

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            self.scope = selectors.overview_scope(request.user)
        return super().dispatch(request, *args, **kwargs)

    def get_page_subtitle(self):
        return self.SUBTITLES[self.scope]

    def get_template_names(self):
        if self.scope == selectors.INVENTORY:
            return ["dashboard/overview_inventory.html"]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        from inventory.selectors import inventory_overview
        from websites.selectors import list_websites

        ctx = super().get_context_data(**kwargs)
        ctx["scope"] = self.scope
        if self.scope == selectors.INVENTORY:
            ctx["inventory"] = inventory_overview()
            return ctx
        # Employees count only what they can open; managers everything.
        user = self.request.user if self.scope == selectors.MINE else None
        website = _selected_website(self.request)
        days = _days(self.request)
        ctx["kpis"] = selectors.overview_kpis(website=website, days=days, user=user)
        ctx["leads_by_day"] = selectors.leads_by_day(days=days, website=website, user=user)
        ctx["pipeline"] = crm_selectors.lead_status_counts(website=website, user=user)
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
        filters = report_filters(self.request, 30)
        scope = {"website": filters["website"], "start": filters["start"], "end": filters["end"]}
        ctx["filters"] = filters
        ctx["filter_form"] = filters["form"]
        ctx["days"] = filters["days"]
        ctx["kpis"] = selectors.overview_kpis(**scope)
        ctx["leads_by_source"] = selectors.leads_by_source(**scope)
        ctx["leads_by_status"] = selectors.leads_by_status(**scope)
        ctx["leads_by_day"] = selectors.leads_by_day(**scope)
        ctx["revenue_by_day"] = selectors.revenue_by_day(**scope)
        ctx["booking_report"] = selectors.booking_report(**scope)
        ctx["top_destinations"] = selectors.top_destinations(**scope)
        ctx["funnel"] = selectors.conversion_funnel(group_by=("website", "source"), **scope)
        ctx["campaigns"] = selectors.conversion_funnel(group_by=("campaign",), **scope)
        return ctx


class AnalyticsView(ManagerRequiredMixin, TemplateView):
    template_name = "dashboard/analytics.html"
    page_title = "Website & Employee Analytics"
    page_subtitle = "Source and team performance."
    active_nav = "analytics"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        filters = report_filters(self.request, 90)
        scope = {"start": filters["start"], "end": filters["end"]}
        ctx["filters"] = filters
        ctx["days"] = filters["days"]
        ctx["websites"] = selectors.website_performance(**scope)
        ctx["employees"] = selectors.employee_performance(**scope)
        ctx["top_destinations"] = selectors.top_destinations(**scope)
        ctx["first_response_definition"] = selectors.FIRST_RESPONSE_DEFINITION
        return ctx


def _money(value):
    return f"{value or 0:.2f}"


class ReportExportView(ManagerRequiredMixin, View):
    """Download a report as CSV, honouring the reports page's website and period.

    leads / bookings: every row unless a period is given; revenue / conversion:
    the period (default: last 30 days).
    """

    KINDS = ("leads", "bookings", "revenue", "conversion")

    def get(self, request, kind):
        if kind not in self.KINDS:
            return HttpResponse("Unknown report type.", status=404, content_type="text/plain")
        filters = report_filters(request, 30)
        website = filters["website"]
        start, end = filters["start"], filters["end"]
        response = HttpResponse(content_type="text/csv")
        dated = filters["has_period"] or kind in ("revenue", "conversion")
        stamp = f"{start:%Y%m%d}-{end:%Y%m%d}" if dated else "all"
        response["Content-Disposition"] = f'attachment; filename="{kind}-report-{stamp}.csv"'
        writer = csv.writer(response)
        getattr(self, f"write_{kind}")(writer, filters, website, start, end)
        return response

    def write_leads(self, writer, filters, website, start, end):
        from crm.selectors import list_leads

        leads = list_leads(website=website)
        if filters["has_period"]:
            leads = leads.filter(created_at__date__gte=start, created_at__date__lte=end)
        writer.writerow(
            [
                "Reference", "ID", "Title", "Customer", "Status", "Source", "Website",
                "Destination", "Assigned to", "Team", "Score", "Created",
                "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
                "Referrer", "Landing page",
            ]
        )
        for lead in leads[:5000]:
            writer.writerow(
                [
                    lead.reference,
                    lead.pk,
                    lead.title,
                    lead.customer.full_name if lead.customer else "",
                    lead.get_status_display(),
                    lead.get_source_display(),
                    lead.website.name if lead.website else "",
                    lead.destination,
                    lead.assigned_to.get_username() if lead.assigned_to else "",
                    lead.assigned_team.name if lead.assigned_team else "",
                    lead.score,
                    lead.created_at.strftime("%Y-%m-%d %H:%M"),
                    lead.utm_source,
                    lead.utm_medium,
                    lead.utm_campaign,
                    lead.utm_term,
                    lead.utm_content,
                    lead.referrer,
                    lead.landing_page,
                ]
            )

    def write_bookings(self, writer, filters, website, start, end):
        bookings = booking_selectors.list_bookings(
            website=website,
            date_from=start if filters["has_period"] else None,
            date_to=end if filters["has_period"] else None,
        )
        writer.writerow(
            [
                "Number", "Customer", "Product", "Product type", "Website", "Lead", "Status",
                "Total", "Currency", "Created", "utm_source", "utm_medium", "utm_campaign",
            ]
        )
        for booking in bookings[:5000]:
            writer.writerow(
                [
                    booking.booking_number,
                    booking.customer.full_name,
                    booking.product_name,
                    booking.product_type,
                    booking.website.name if booking.website else "",
                    booking.lead.reference if booking.lead else "",
                    booking.get_status_display(),
                    booking.total_amount,
                    booking.currency,
                    booking.created_at.strftime("%Y-%m-%d %H:%M"),
                    booking.utm_source,
                    booking.utm_medium,
                    booking.utm_campaign,
                ]
            )

    def write_revenue(self, writer, filters, website, start, end):
        writer.writerow(["Date", "Website", "Product type", "Payments", "Amount", "Currency"])
        for row in selectors.revenue_rows(website=website, start=start, end=end):
            writer.writerow(
                [
                    row["day"].isoformat() if row["day"] else "",
                    row["booking__website__name"] or "(no website)",
                    row["booking__product_type"],
                    row["payments"],
                    _money(row["total"]),
                    row["currency"],
                ]
            )

    def write_conversion(self, writer, filters, website, start, end):
        writer.writerow(
            [
                "Website", "Source", "utm_source", "utm_medium", "utm_campaign", "Chats",
                "Chats with a lead", "Leads", "Qualified", "Bookings", "Paid bookings",
                "Revenue", "Chat to lead %", "Lead to paid %",
            ]
        )
        for row in selectors.conversion_funnel(website=website, start=start, end=end):
            writer.writerow(
                [
                    row["website"],
                    row["source_label"],
                    row["utm_source"],
                    row["utm_medium"],
                    row["utm_campaign"],
                    "" if row["chats"] is None else row["chats"],
                    "" if row["chats"] is None else row["chats_with_lead"],
                    row["leads"],
                    row["qualified"],
                    row["bookings"],
                    row["paid"],
                    _money(row["revenue"]),
                    "" if row["chat_to_lead_rate"] is None else row["chat_to_lead_rate"],
                    row["lead_to_paid_rate"],
                ]
            )
