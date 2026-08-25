from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.OverviewView.as_view(), name="overview"),
    path("notifications/", views.NotificationsView.as_view(), name="notifications"),
    path(
        "notifications/read/", views.NotificationReadView.as_view(), name="notifications_read_all"
    ),
    path(
        "notifications/<int:pk>/read/",
        views.NotificationReadView.as_view(),
        name="notification_read",
    ),
    path("reports/", views.ReportsView.as_view(), name="reports"),
    path("reports/export/<str:kind>/", views.ReportExportView.as_view(), name="report_export"),
    path("analytics/", views.AnalyticsView.as_view(), name="analytics"),
]
