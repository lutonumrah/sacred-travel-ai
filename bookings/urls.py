from django.urls import path

from . import views

app_name = "bookings"

urlpatterns = [
    path("", views.BookingListView.as_view(), name="list"),
    path("add/", views.BookingCreateView.as_view(), name="create"),
    path("payments/", views.PaymentListView.as_view(), name="payments"),
    path("<int:pk>/", views.BookingDetailView.as_view(), name="detail"),
    path("<int:pk>/pay/", views.PaymentOrderCreateView.as_view(), name="payment_create"),
    path("<int:pk>/pay/simulate/", views.PaymentSimulateView.as_view(), name="payment_simulate"),
    path("<int:pk>/cancel/", views.BookingCancelView.as_view(), name="cancel"),
    path("<int:pk>/payment-link/", views.PaymentLinkIssueView.as_view(), name="payment_link"),
]
