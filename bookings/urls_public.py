from django.urls import path

from . import public_views

app_name = "public"

urlpatterns = [
    path("<str:token>/", public_views.PaymentPageView.as_view(), name="pay"),
]
