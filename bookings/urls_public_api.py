from django.urls import path

from . import public_views

app_name = "api_pay"

urlpatterns = [
    path("<str:token>/order/", public_views.PublicOrderAPI.as_view(), name="order"),
    path("<str:token>/verify/", public_views.PublicVerifyAPI.as_view(), name="verify"),
    path("<str:token>/simulate/", public_views.PublicSimulateAPI.as_view(), name="simulate"),
]
