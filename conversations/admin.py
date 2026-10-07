from django.contrib import admin

from .models import (
    AISettings,
    Conversation,
    ConversationHandoff,
    KnowledgeArticle,
    Message,
    Recommendation,
)


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0


class RecommendationInline(admin.TabularInline):
    model = Recommendation
    extra = 0


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = (
        "session_key",
        "status",
        "website",
        "customer",
        "assigned_to",
        "last_message_at",
    )
    list_filter = ("status", "website")
    search_fields = ("session_key",)
    inlines = [MessageInline, RecommendationInline]


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("conversation", "sender_type", "created_at")
    list_filter = ("sender_type",)
    search_fields = ("content",)


@admin.register(ConversationHandoff)
class ConversationHandoffAdmin(admin.ModelAdmin):
    list_display = ("conversation", "taken_by", "created_at", "resumed_ai_at")


@admin.register(Recommendation)
class RecommendationAdmin(admin.ModelAdmin):
    list_display = ("title", "conversation", "inventory_type", "price", "is_selected")
    list_filter = ("inventory_type", "is_selected")


@admin.register(AISettings)
class AISettingsAdmin(admin.ModelAdmin):
    """Read-only here; keys and models are edited on the dashboard's AI Settings page,
    which never echoes a saved key back to the browser."""

    list_display = ("__str__", "enabled", "provider", "model", "updated_by", "updated_at")
    fields = ("enabled", "provider", "model", "anthropic_key_saved", "gemini_key_saved", "updated_by", "updated_at")
    readonly_fields = fields

    @admin.display(boolean=True, description="Anthropic key saved")
    def anthropic_key_saved(self, obj):
        return bool(obj.anthropic_api_key)

    @admin.display(boolean=True, description="Gemini key saved")
    def gemini_key_saved(self, obj):
        return bool(obj.gemini_api_key)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(KnowledgeArticle)
class KnowledgeArticleAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "website", "is_active", "updated_by", "updated_at")
    list_filter = ("category", "is_active", "website")
    search_fields = ("title", "content", "keywords")
    readonly_fields = ("updated_by", "created_at", "updated_at")

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
