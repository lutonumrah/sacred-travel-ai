from django.contrib import admin

from .models import Customer, FollowUpTask, Lead, LeadActivity, LeadNote


class LeadNoteInline(admin.TabularInline):
    model = LeadNote
    extra = 0


class LeadActivityInline(admin.TabularInline):
    model = LeadActivity
    extra = 0
    readonly_fields = ("created_at",)


class FollowUpInline(admin.TabularInline):
    model = FollowUpTask
    extra = 0


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("first_name", "last_name", "email", "phone", "city", "is_active")
    list_filter = ("is_active", "country")
    search_fields = ("first_name", "last_name", "email", "phone")


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "status",
        "source",
        "destination",
        "assigned_to",
        "website",
        "created_at",
    )
    list_filter = ("status", "source", "website")
    search_fields = ("title", "destination", "customer__first_name", "customer__email")
    inlines = [LeadNoteInline, LeadActivityInline, FollowUpInline]


@admin.register(LeadNote)
class LeadNoteAdmin(admin.ModelAdmin):
    list_display = ("lead", "author", "is_internal", "created_at")
    search_fields = ("body",)


@admin.register(LeadActivity)
class LeadActivityAdmin(admin.ModelAdmin):
    list_display = ("lead", "activity_type", "summary", "actor", "created_at")
    list_filter = ("activity_type",)


@admin.register(FollowUpTask)
class FollowUpTaskAdmin(admin.ModelAdmin):
    list_display = ("title", "lead", "assigned_to", "due_at", "is_completed")
    list_filter = ("is_completed",)
    search_fields = ("title",)
