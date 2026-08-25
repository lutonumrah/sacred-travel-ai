from django.contrib import admin

from .models import Website, WebsiteAPIKey


class WebsiteAPIKeyInline(admin.TabularInline):
    model = WebsiteAPIKey
    extra = 0


@admin.register(Website)
class WebsiteAdmin(admin.ModelAdmin):
    list_display = ("name", "brand_name", "domain", "source_identifier", "is_active", "widget_enabled")
    list_filter = ("is_active", "widget_enabled")
    search_fields = ("name", "brand_name", "domain", "source_identifier")
    prepopulated_fields = {"source_identifier": ("name",)}
    inlines = [WebsiteAPIKeyInline]


@admin.register(WebsiteAPIKey)
class WebsiteAPIKeyAdmin(admin.ModelAdmin):
    list_display = ("website", "key_name", "public_key", "is_active", "last_used_at")
    list_filter = ("is_active",)
    search_fields = ("key_name", "public_key", "website__name")
