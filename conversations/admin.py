from django.contrib import admin

from .models import Conversation, ConversationHandoff, Message, Recommendation


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
