from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", RedirectView.as_view(pattern_name="dashboard:overview"), name="home"),
    # Web / dashboard routes
    path("auth/", include("accounts.urls")),
    path("websites/", include("websites.urls")),
    path("inventory/", include("inventory.urls")),
    path("crm/", include("crm.urls")),
    path("conversations/", include("conversations.urls")),
    path("bookings/", include("bookings.urls")),
    path("dashboard/", include("dashboard.urls")),
    # API routes
    path("api/v1/", include("accounts.urls_api")),
    path("api/v1/websites/", include("websites.urls_api")),
    path("api/v1/inventory/", include("inventory.urls_api")),
    path("api/v1/crm/", include("crm.urls_api")),
    path("api/v1/conversations/", include("conversations.urls_api")),
    path("api/v1/bookings/", include("bookings.urls_api")),
    path("api/v1/dashboard/", include("dashboard.urls_api")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

admin.site.site_header = "Scared Travel AI OS"
admin.site.site_title = "Scared Travel AI"
admin.site.index_title = "Administration"
