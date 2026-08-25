from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import AuditLog, Team, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("username", "email", "role", "is_active_employee", "is_staff", "is_active")
    list_filter = ("role", "is_active", "is_staff", "is_active_employee")
    fieldsets = DjangoUserAdmin.fieldsets + (
        ("Travel OS Profile", {"fields": ("role", "phone", "is_active_employee", "avatar")}),
    )
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        ("Travel OS Profile", {"fields": ("role", "phone")}),
    )
    search_fields = ("username", "email", "first_name", "last_name", "phone")


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "created_at")
    search_fields = ("name",)
    filter_horizontal = ("members",)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("action", "actor", "entity_type", "entity_id", "created_at")
    list_filter = ("action", "entity_type")
    search_fields = ("action", "entity_id")
    readonly_fields = ("created_at", "updated_at")
