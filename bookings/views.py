from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, DetailView, TemplateView

from core.mixins import ManagerRequiredMixin, SalesRequiredMixin
from core.selectors import paginate

from . import payments, selectors, services
from .forms import BookingFilterForm, BookingForm, PaymentFilterForm
from .models import Booking, BookingStatus, Notification, Payment


class BookingListView(SalesRequiredMixin, TemplateView):
    template_name = "bookings/list.html"
    page_title = "Bookings"
    page_subtitle = "Pending, paid, confirmed and cancelled bookings."
    active_nav = "bookings"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form = BookingFilterForm(self.request.GET or None)
        form.is_valid()
        data = form.cleaned_data if form.is_bound and form.is_valid() else {}
        queryset = selectors.list_bookings(
            q=data.get("q", "") or "",
            status=data.get("status", "") or "",
            website=data.get("website"),
            date_from=data.get("date_from"),
            date_to=data.get("date_to"),
            user=self.request.user,
        )
        ctx["filter_form"] = form
        ctx["page_obj"] = paginate(queryset, self.request.GET.get("page"))
        ctx["total"] = queryset.count()
        ctx["status_counts"] = selectors.booking_status_counts()
        return ctx


class BookingCreateView(SalesRequiredMixin, CreateView):
    model = Booking
    form_class = BookingForm
    template_name = "bookings/form.html"
    page_title = "Create Booking"
    page_subtitle = "Select a product and raise a pending booking."
    active_nav = "bookings"

    def form_valid(self, form):
        booking = form.save(commit=False)
        booking.status = BookingStatus.PENDING
        services.create_booking(booking=booking, actor=self.request.user, request=self.request)
        self.object = booking
        messages.success(self.request, f"Booking {booking.booking_number} created.")
        return redirect(self.get_success_url())

    def get_success_url(self):
        return reverse("bookings:detail", args=[self.object.pk])


class BookingDetailView(SalesRequiredMixin, DetailView):
    model = Booking
    template_name = "bookings/detail.html"
    context_object_name = "booking"
    active_nav = "bookings"

    def get_queryset(self):
        return selectors.visible_bookings(self.request.user).select_related(
            "customer", "website", "lead", "created_by"
        )

    def get_page_title(self):
        return self.object.booking_number

    def get_page_subtitle(self):
        return f"{self.object.product_name} · {self.object.get_status_display()}"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        booking = self.object
        ctx["payments"] = booking.payments.all()
        pending = booking.payments.filter(status__in=["created", "pending"]).first()
        ctx["pending_payment"] = pending
        ctx["is_live_gateway"] = payments.is_live()
        ctx["razorpay_key"] = payments.key_id()
        # Rendered with `json_script`, never inline, so customer data can't break out.
        ctx["checkout_payload"] = {
            "key": payments.key_id(),
            "amount": payments.to_paise(booking.total_amount),
            "currency": booking.currency,
            "name": booking.website.name if booking.website else "Scared Travel",
            "description": booking.product_name,
            "order_id": pending.razorpay_order_id if pending else "",
            "prefill": {
                "name": booking.customer.full_name,
                "email": booking.customer.email,
                "contact": booking.customer.phone,
            },
        }
        return ctx


class PaymentOrderCreateView(SalesRequiredMixin, View):
    def post(self, request, pk):
        booking = get_object_or_404(selectors.visible_bookings(request.user), pk=pk)
        payment = services.create_payment_order(
            booking=booking, actor=request.user, request=request
        )
        mode = "live" if payments.is_live() else "simulation"
        messages.success(
            request,
            f"Payment order {payment.razorpay_order_id} created ({mode} mode).",
        )
        return redirect("bookings:detail", pk=pk)


class PaymentSimulateView(ManagerRequiredMixin, View):
    """Complete a simulated checkout so the flow is testable without Razorpay."""

    def post(self, request, pk):
        booking = get_object_or_404(selectors.visible_bookings(request.user), pk=pk)
        if payments.is_live():
            messages.error(
                request, "Simulation is disabled while live Razorpay keys are configured."
            )
            return redirect("bookings:detail", pk=pk)
        payment = booking.payments.filter(status__in=["created", "pending"]).first()
        if payment is None:
            messages.error(request, "Create a payment order first.")
            return redirect("bookings:detail", pk=pk)
        payment_id, signature = payments.simulate_payment(payment.razorpay_order_id)
        _settled, error = services.verify_payment(
            order_id=payment.razorpay_order_id,
            payment_id=payment_id,
            signature=signature,
            actor=request.user,
            request=request,
        )
        if error:
            messages.error(request, error)
        else:
            messages.success(request, "Simulated payment captured — booking confirmed.")
        return redirect("bookings:detail", pk=pk)


class BookingCancelView(ManagerRequiredMixin, View):
    def post(self, request, pk):
        booking = get_object_or_404(selectors.visible_bookings(request.user), pk=pk)
        services.cancel_booking(
            booking=booking,
            actor=request.user,
            request=request,
            reason=request.POST.get("reason", ""),
        )
        messages.success(request, f"Booking {booking.booking_number} cancelled.")
        return redirect("bookings:detail", pk=pk)


class PaymentListView(SalesRequiredMixin, TemplateView):
    template_name = "bookings/payments.html"
    page_title = "Payments"
    page_subtitle = "Razorpay orders, verification and settlement status."
    active_nav = "bookings"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form = PaymentFilterForm(self.request.GET or None)
        form.is_valid()
        data = form.cleaned_data if form.is_bound and form.is_valid() else {}
        queryset = selectors.list_payments(
            q=data.get("q", "") or "",
            status=data.get("status", "") or "",
            user=self.request.user,
        )
        ctx["filter_form"] = form
        ctx["page_obj"] = paginate(queryset, self.request.GET.get("page"))
        ctx["revenue"] = selectors.revenue_total()
        ctx["is_live_gateway"] = payments.is_live()
        return ctx
