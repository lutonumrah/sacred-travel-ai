from rest_framework import serializers

from .models import Conversation, ConversationHandoff, Message, Recommendation


class MessageSerializer(serializers.ModelSerializer):
    sender_user = serializers.CharField(
        source="sender_user.username", read_only=True, default=None
    )

    class Meta:
        model = Message
        fields = (
            "id",
            "sender_type",
            "sender_user",
            "content",
            "metadata",
            "is_internal",
            "created_at",
        )


class RecommendationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Recommendation
        fields = (
            "id",
            "inventory_type",
            "object_id",
            "title",
            "price",
            "currency",
            "payload",
            "is_selected",
        )


class HandoffSerializer(serializers.ModelSerializer):
    taken_by = serializers.CharField(source="taken_by.username", read_only=True, default=None)

    class Meta:
        model = ConversationHandoff
        fields = ("id", "taken_by", "reason", "resumed_ai_at", "notes", "created_at")


class ConversationSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(
        source="customer.full_name", read_only=True, default=None
    )
    website = serializers.CharField(
        source="website.source_identifier", read_only=True, default=None
    )
    assigned_to = serializers.CharField(
        source="assigned_to.username", read_only=True, default=None
    )
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Conversation
        fields = (
            "id",
            "session_key",
            "website",
            "customer_name",
            "lead",
            "status",
            "status_display",
            "assigned_to",
            "requirements",
            "last_message_at",
            "created_at",
        )


class ConversationDetailSerializer(ConversationSerializer):
    messages = MessageSerializer(many=True, read_only=True)
    recommendations = RecommendationSerializer(many=True, read_only=True)
    handoffs = HandoffSerializer(many=True, read_only=True)

    class Meta(ConversationSerializer.Meta):
        fields = ConversationSerializer.Meta.fields + (
            "context",
            "messages",
            "recommendations",
            "handoffs",
        )
